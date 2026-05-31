"""Tests for staged firmware rollout campaigns."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from rest_framework.test import APIClient

from forgekey.models import DeviceFirmwareUpdate, FirmwareRollout
from forgekey.services.firmware_rollout import advance_rollout, rollout_progress
from forgekey.tasks import advance_firmware_rollouts
from forgekey.tests.factories import DeviceTypeFactory, ESP32DeviceFactory, FirmwareVersionFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def mqtt_ok():
    """Patch the MQTT client so OTA dispatch 'succeeds' (rc=0) without a broker."""
    client = MagicMock()
    client.publish.return_value = MagicMock(rc=0)
    with patch("forgekey.tasks.get_mqtt_client", return_value=client):
        yield client


@pytest.fixture
def api_client(admin_user):
    client = APIClient()
    client.force_authenticate(user=admin_user)
    return client


def _rollout(num_devices, *, batch_percent=50, status=FirmwareRollout.STATUS_DRAFT):
    dt = DeviceTypeFactory()
    fw = FirmwareVersionFactory(device_type=dt, version="2.0.0")
    for _ in range(num_devices):
        ESP32DeviceFactory(device_type=dt, firmware_version="1.0.0", is_active=True)
    return FirmwareRollout.objects.create(
        firmware_version=fw, batch_size_percent=batch_percent, status=status
    )


class TestAdvanceRollout:
    def test_advance_dispatches_one_wave(self, mqtt_ok):
        rollout = _rollout(4, batch_percent=50, status=FirmwareRollout.STATUS_ACTIVE)

        dispatched = advance_rollout(rollout)

        assert dispatched == 2  # 50% of 4
        assert DeviceFirmwareUpdate.objects.filter(rollout=rollout).count() == 2
        rollout.refresh_from_db()
        assert rollout.status == FirmwareRollout.STATUS_ACTIVE
        assert rollout.last_advanced_at is not None

    def test_advance_skips_non_active(self, mqtt_ok):
        rollout = _rollout(4, status=FirmwareRollout.STATUS_DRAFT)

        assert advance_rollout(rollout) == 0
        assert DeviceFirmwareUpdate.objects.filter(rollout=rollout).count() == 0

    def test_waves_drain_fleet_then_complete(self, mqtt_ok):
        rollout = _rollout(3, batch_percent=50, status=FirmwareRollout.STATUS_ACTIVE)
        # ceil(3 * 0.5) = 2 per wave.

        assert advance_rollout(rollout) == 2
        rollout.refresh_from_db()
        assert rollout.status == FirmwareRollout.STATUS_ACTIVE

        assert advance_rollout(rollout) == 1  # last device
        rollout.refresh_from_db()
        assert rollout.status == FirmwareRollout.STATUS_COMPLETED
        assert rollout.completed_at is not None
        assert DeviceFirmwareUpdate.objects.filter(rollout=rollout).count() == 3

    def test_progress_counts(self, mqtt_ok):
        rollout = _rollout(4, batch_percent=50, status=FirmwareRollout.STATUS_ACTIVE)
        advance_rollout(rollout)

        progress = rollout_progress(rollout)
        assert progress["total"] == 4
        assert progress["pending"] == 2
        assert progress["remaining"] == 2
        assert progress["on_target"] == 0


class TestRolloutAPI:
    def test_create_start_pause_resume_cancel(self, mqtt_ok, api_client):
        rollout = _rollout(6, batch_percent=25, status=FirmwareRollout.STATUS_DRAFT)
        base = f"/api/forgekey/firmware-rollouts/{rollout.id}/"

        start = api_client.post(base + "start/")
        assert start.status_code == 200, start.data
        assert start.json()["status"] == "active"
        assert start.json()["dispatched"] == 2  # ceil(6 * 0.25)
        assert start.json()["progress"]["pending"] == 2

        pause = api_client.post(base + "pause/")
        assert pause.status_code == 200
        assert pause.json()["status"] == "paused"

        resume = api_client.post(base + "start/")
        assert resume.status_code == 200
        assert resume.json()["status"] == "active"  # 2 more dispatched, 2 still remaining

        cancel = api_client.post(base + "cancel/")
        assert cancel.status_code == 200
        assert cancel.json()["status"] == "cancelled"

    def test_advance_requires_active(self, mqtt_ok, api_client):
        rollout = _rollout(2, status=FirmwareRollout.STATUS_DRAFT)
        resp = api_client.post(f"/api/forgekey/firmware-rollouts/{rollout.id}/advance/")
        assert resp.status_code == 400

    def test_create_draft_via_api(self, api_client):
        dt = DeviceTypeFactory()
        fw = FirmwareVersionFactory(device_type=dt, version="3.0.0")

        resp = api_client.post(
            "/api/forgekey/firmware-rollouts/",
            {
                "firmware_version": str(fw.id),
                "batch_size_percent": 25,
                "interval_minutes": 30,
                "name": "Q3 rollout",
            },
            format="json",
        )

        assert resp.status_code == 201, resp.data
        assert resp.json()["status"] == "draft"
        assert resp.json()["batch_size_percent"] == 25

    def test_create_rejects_out_of_range_percent(self, api_client):
        dt = DeviceTypeFactory()
        fw = FirmwareVersionFactory(device_type=dt, version="3.1.0")

        resp = api_client.post(
            "/api/forgekey/firmware-rollouts/",
            {"firmware_version": str(fw.id), "batch_size_percent": 150},
            format="json",
        )
        assert resp.status_code == 400


class TestBeatTask:
    def test_beat_advances_active_rollout(self, mqtt_ok):
        rollout = _rollout(2, batch_percent=100, status=FirmwareRollout.STATUS_ACTIVE)

        advance_firmware_rollouts()  # last_advanced_at is None → eligible immediately

        assert DeviceFirmwareUpdate.objects.filter(rollout=rollout).count() == 2
