"""Tests for the ForgeKey fleet-summary dashboard endpoint."""

from __future__ import annotations

from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from forgekey.models import EPaperDisplay
from forgekey.tests.factories import DeviceTypeFactory, ESP32DeviceFactory

pytestmark = pytest.mark.django_db

FLEET_SUMMARY_URL = "/api/forgekey/devices/fleet-summary/"


@pytest.fixture
def api_client(admin_user):
    client = APIClient()
    client.force_authenticate(user=admin_user)
    return client


def test_fleet_summary_requires_authentication():
    response = APIClient().get(FLEET_SUMMARY_URL)
    assert response.status_code in (401, 403)


def test_fleet_summary_counts_online_offline_and_aggregates(api_client):
    dt = DeviceTypeFactory()
    ESP32DeviceFactory(
        device_type=dt,
        is_online=True,
        is_active=True,
        firmware_version="1.0.0",
        capabilities=["status_led", "people_counter"],
        last_seen=timezone.now(),
    )
    ESP32DeviceFactory(
        device_type=dt,
        is_online=False,
        is_active=True,
        firmware_version="1.0.0",
        capabilities=["status_led"],
        last_seen=timezone.now(),
    )
    # An inactive device must not count toward the active online/offline tallies.
    ESP32DeviceFactory(device_type=dt, is_online=False, is_active=False, firmware_version="0.9.0")

    response = api_client.get(FLEET_SUMMARY_URL)
    assert response.status_code == 200, response.data
    body = response.json()

    assert body["devices"]["total"] == 3
    assert body["devices"]["active"] == 2
    assert body["devices"]["online"] == 1
    assert body["devices"]["offline"] == 1

    caps = {c["capability"]: c["count"] for c in body["devices"]["by_capability"]}
    assert caps["status_led"] == 2
    assert caps["people_counter"] == 1

    fw = {f["version"]: f["count"] for f in body["devices"]["by_firmware"]}
    assert fw["1.0.0"] == 2
    assert fw["0.9.0"] == 1

    # The offline (active) device surfaces in the attention feed.
    assert len(body["attention"]["offline"]) == 1


def test_fleet_summary_flags_low_battery_epaper(api_client):
    EPaperDisplay.objects.create(battery_percent=80, is_active=True)
    EPaperDisplay.objects.create(battery_percent=5, is_active=True)

    response = api_client.get(FLEET_SUMMARY_URL)
    assert response.status_code == 200, response.data
    body = response.json()

    assert body["epaper"]["total"] == 2
    assert body["epaper"]["low_battery"] == 1
    low = body["attention"]["low_battery"]
    assert len(low) == 1
    assert low[0]["battery_percent"] == 5
