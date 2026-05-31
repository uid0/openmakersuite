"""Tests for lock-status ingestion + the locker monitoring API."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.urls import reverse

import pytest
from rest_framework.test import APIClient

from forgekey.models import DeviceType, ESP32Device
from inventory.tests.factories import LocationFactory
from lockers.models import Locker, LockerDevice, LockerStatus

User = get_user_model()
pytestmark = pytest.mark.django_db

LATCH_MAC = "AA:BB:CC:44:55:66"


@pytest.fixture
def staff_client():
    user = User.objects.create_user(
        username="ops", email="o@example.com", password="x" * 24, is_staff=True
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def locker():
    sig = Group.objects.create(name="Metal Shop SIG")
    lk = Locker.objects.create(name="M-1", slug="m-1", location=LocationFactory(), owning_sig=sig)
    latch_type, _ = DeviceType.objects.get_or_create(
        code="locker_latch", defaults={"name": "Locker latch controller"}
    )
    latch = ESP32Device.objects.create(
        mac_address=LATCH_MAC, device_type=latch_type, is_online=True
    )
    LockerDevice.objects.create(
        locker=lk, device=latch, role=LockerDevice.ROLE_LATCH, is_primary=True
    )
    return lk


def _status_payload(**overrides):
    base = {
        "mac": LATCH_MAC,
        "secure": True,
        "state": "SECURE",
        "reed_closed": True,
        "latch_locked": True,
        "ir_broken": False,
        "mortise_active": False,
        "item_present": True,
        "last_trigger": "signed_command",
        "firmware_version": "0.1.0",
    }
    base.update(overrides)
    return base


class TestLockStatusIngest:
    def test_ingests_and_upserts_in_place(self, staff_client, locker):
        url = reverse("lockers:event-lock-status")

        resp = staff_client.post(url, _status_payload(), format="json")
        assert resp.status_code == 202, resp.content
        row = LockerStatus.objects.get(locker=locker)
        assert row.secure is True
        assert row.state == "SECURE"
        assert row.device.mac_address == LATCH_MAC

        # A later, insecure heartbeat updates the same row (no second row).
        resp2 = staff_client.post(
            url, _status_payload(secure=False, state="ALARM", latch_locked=False), format="json"
        )
        assert resp2.status_code == 202
        assert LockerStatus.objects.filter(locker=locker).count() == 1
        row.refresh_from_db()
        assert row.secure is False
        assert row.state == "ALARM"
        assert row.is_insecure is True
        assert row.is_alarm is True

    def test_unknown_mac_returns_404(self, staff_client):
        resp = staff_client.post(
            reverse("lockers:event-lock-status"),
            _status_payload(mac="00:00:00:00:00:99"),
            format="json",
        )
        assert resp.status_code == 404

    def test_requires_auth(self, locker):
        resp = APIClient().post(
            reverse("lockers:event-lock-status"), _status_payload(), format="json"
        )
        assert resp.status_code in (401, 403)


class TestLockerMonitoringAPI:
    def test_list_requires_auth(self):
        assert APIClient().get(reverse("lockers:locker-list")).status_code in (401, 403)

    def test_list_includes_devices_and_status(self, staff_client, locker):
        LockerStatus.objects.create(
            locker=locker, secure=False, state="ALARM", reed_closed=False, latch_locked=True
        )

        resp = staff_client.get(reverse("lockers:locker-list"))
        assert resp.status_code == 200, resp.content
        body = resp.json()
        results = body["results"] if isinstance(body, dict) and "results" in body else body
        assert len(results) == 1
        row = results[0]
        assert row["name"] == "M-1"
        assert len(row["devices"]) == 1
        assert row["devices"][0]["role"] == "latch"
        assert row["status"]["state"] == "ALARM"
        assert row["status"]["is_insecure"] is True

    def test_locker_without_status_serializes_null(self, staff_client, locker):
        resp = staff_client.get(reverse("lockers:locker-detail", kwargs={"pk": locker.pk}))
        assert resp.status_code == 200, resp.content
        assert resp.json()["status"] is None
