"""Tests for ForgeKey temperature/humidity telemetry.

Covers both ingest paths (the long-running consumer's handle_reading_message
and the EMQX webhook's process_mqtt_reading task) plus the device-detail
temperature history endpoint.
"""

from __future__ import annotations

import json
from datetime import timedelta

from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from forgekey.management.commands.mqtt_consumer import handle_reading_message
from forgekey.models import TemperatureReading
from forgekey.tasks import process_mqtt_reading
from forgekey.tests.factories import ESP32DeviceFactory

pytestmark = pytest.mark.django_db


def _topic_segment(mac: str) -> str:
    return mac.replace(":", "").replace("-", "").lower()


def _reading_topic(mac: str) -> str:
    return f"forgekey/{_topic_segment(mac)}/temperature_sensor/reading"


class TestConsumerReadingIngest:
    def test_handle_reading_creates_row_and_touches_device(self):
        device = ESP32DeviceFactory(
            mac_address="AA:BB:CC:00:00:10", is_online=False, last_seen=None
        )
        payload = json.dumps({"tempC": 21.4, "humidity": 47.1, "timestamp": 12345}).encode("utf-8")

        reading = handle_reading_message(_reading_topic(device.mac_address), payload)

        assert reading is not None
        assert reading.device_id == device.id
        assert reading.temperature_c == pytest.approx(21.4)
        assert reading.humidity_percent == pytest.approx(47.1)
        assert reading.sensor_kind == "temperature_sensor"
        device.refresh_from_db()
        assert device.is_online is True
        assert device.last_seen is not None

    def test_handle_reading_humidity_optional(self):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:00:00:11")
        payload = json.dumps({"tempC": 19.0}).encode("utf-8")

        reading = handle_reading_message(_reading_topic(device.mac_address), payload)

        assert reading is not None
        assert reading.humidity_percent is None

    def test_handle_reading_drops_missing_temp(self):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:00:00:12")
        payload = json.dumps({"humidity": 50}).encode("utf-8")

        assert handle_reading_message(_reading_topic(device.mac_address), payload) is None
        assert TemperatureReading.objects.count() == 0

    def test_handle_reading_drops_unknown_mac(self):
        payload = json.dumps({"tempC": 20.0}).encode("utf-8")

        result = handle_reading_message("forgekey/aabbccddeeff/temperature_sensor/reading", payload)

        assert result is None
        assert TemperatureReading.objects.count() == 0


class TestWebhookReadingTask:
    def test_process_mqtt_reading_persists(self):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:00:00:20")

        process_mqtt_reading(
            device.mac_address, "temperature_sensor", {"tempC": 22.5, "humidity": 40}
        )

        reading = TemperatureReading.objects.get(device=device)
        assert reading.temperature_c == pytest.approx(22.5)
        assert reading.humidity_percent == pytest.approx(40)

    def test_process_mqtt_reading_drops_missing_temp(self):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:00:00:21")

        process_mqtt_reading(device.mac_address, "temperature_sensor", {"humidity": 40})

        assert TemperatureReading.objects.filter(device=device).count() == 0


class TestTemperatureEndpoint:
    @pytest.fixture
    def api_client(self, admin_user):
        client = APIClient()
        client.force_authenticate(user=admin_user)
        return client

    def test_temperature_endpoint_returns_readings_and_latest(self, api_client):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:00:00:30")
        old = TemperatureReading.objects.create(
            device=device,
            sensor_kind="temperature_sensor",
            temperature_c=18.0,
            humidity_percent=44.0,
        )
        # Force the first reading an hour into the past so "latest" is deterministic.
        TemperatureReading.objects.filter(pk=old.pk).update(
            recorded_at=timezone.now() - timedelta(hours=1)
        )
        TemperatureReading.objects.create(
            device=device,
            sensor_kind="temperature_sensor",
            temperature_c=20.0,
            humidity_percent=45.0,
        )

        response = api_client.get(f"/api/forgekey/devices/{device.id}/temperature/")

        assert response.status_code == 200, response.data
        body = response.json()
        assert len(body["readings"]) == 2
        assert body["latest_temperature_c"] == pytest.approx(20.0)
        assert body["latest_humidity_percent"] == pytest.approx(45.0)
