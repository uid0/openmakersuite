"""
Tests for ForgeKey device-capabilities ingestion (oms-x3a).

Covers:
  - process_mqtt_device_capabilities task: persists list, normalizes input,
    handles unknown devices, applies firmware_version opportunistically
  - MqttWebhookView dispatch routes the capabilities suffix to the task
  - ESP32DeviceViewSet supports the ?capability= filter
"""

from __future__ import annotations

import json
from unittest.mock import patch

from django.urls import reverse

import pytest

from forgekey.models import ESP32Device
from forgekey.tasks import process_mqtt_device_capabilities
from forgekey.tests.factories import ESP32DeviceFactory

pytestmark = pytest.mark.django_db


WEBHOOK_SECRET = "test-secret-abc123"


def _topic_segment(mac: str) -> str:
    return mac.replace(":", "").lower()


def _post(api_client, body, *, secret: str | None = WEBHOOK_SECRET):
    headers = {}
    if secret is not None:
        headers["HTTP_X_FORGEKEY_WEBHOOK_SECRET"] = secret
    return api_client.post(
        reverse("forgekey:mqtt-webhook"),
        data=json.dumps(body),
        content_type="application/json",
        **headers,
    )


@pytest.fixture(autouse=True)
def configured_secret(settings):
    settings.FORGEKEY_WEBHOOK_SECRET = WEBHOOK_SECRET
    settings.FORGEKEY_WEBHOOK_ALLOWED_IPS = []
    settings.MQTT_TOPIC_PREFIX = "forgekey"
    return settings


class TestProcessCapabilitiesTask:
    def test_persists_capabilities_and_timestamp(self):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:11:22:33")
        process_mqtt_device_capabilities(
            "AA:BB:CC:11:22:33",
            {"capabilities": ["people_counter", "status_led"], "firmware_version": "1.2.0"},
        )
        device.refresh_from_db()
        assert device.capabilities == ["people_counter", "status_led"]
        assert device.capabilities_announced_at is not None
        assert device.firmware_version == "1.2.0"
        assert device.is_online is True
        assert device.last_seen is not None

    def test_overwrites_prior_capability_list(self):
        device = ESP32DeviceFactory(
            mac_address="AA:BB:CC:11:22:34",
            capabilities=["mmwave_presence"],
        )
        process_mqtt_device_capabilities(
            device.mac_address,
            {"capabilities": ["people_counter"]},
        )
        device.refresh_from_db()
        # Firmware is source of truth — we replace, not merge.
        assert device.capabilities == ["people_counter"]

    def test_normalizes_payload_drops_garbage_and_dedupes(self):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:11:22:35")
        process_mqtt_device_capabilities(
            device.mac_address,
            {
                "capabilities": [
                    "people_counter",
                    "people_counter",  # duplicate
                    "  status_led  ",
                    None,  # not a string
                    "",  # empty
                    42,  # not a string
                ],
            },
        )
        device.refresh_from_db()
        assert device.capabilities == ["people_counter", "status_led"]

    def test_empty_capabilities_clears_list(self):
        device = ESP32DeviceFactory(
            mac_address="AA:BB:CC:11:22:36",
            capabilities=["people_counter"],
        )
        process_mqtt_device_capabilities(device.mac_address, {"capabilities": []})
        device.refresh_from_db()
        assert device.capabilities == []
        assert device.capabilities_announced_at is not None

    def test_missing_capabilities_key_treated_as_empty(self):
        device = ESP32DeviceFactory(
            mac_address="AA:BB:CC:11:22:37",
            capabilities=["people_counter"],
        )
        process_mqtt_device_capabilities(device.mac_address, {"firmware_version": "9.9.9"})
        device.refresh_from_db()
        assert device.capabilities == []
        assert device.firmware_version == "9.9.9"

    def test_unknown_mac_dropped_silently(self):
        # Should not raise; should not create a device row.
        process_mqtt_device_capabilities("AA:BB:CC:99:99:99", {"capabilities": ["people_counter"]})
        assert not ESP32Device.objects.filter(mac_address="AA:BB:CC:99:99:99").exists()

    def test_malformed_mac_dropped(self):
        # Should not raise.
        process_mqtt_device_capabilities("not-a-mac", {"capabilities": ["people_counter"]})

    def test_non_dict_payload_dropped(self):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:11:22:38")
        process_mqtt_device_capabilities(device.mac_address, "not-a-dict")  # type: ignore[arg-type]
        device.refresh_from_db()
        assert device.capabilities == []  # untouched (model default)


class TestCapabilitiesWebhookDispatch:
    def test_capabilities_topic_dispatches_task(self, api_client):
        with patch("forgekey.views.process_mqtt_device_capabilities.delay") as mock_delay:
            response = _post(
                api_client,
                {
                    "topic": "forgekey/aabbccddeeff/capabilities",
                    "payload": json.dumps(
                        {"capabilities": ["people_counter"], "firmware_version": "1.2.0"}
                    ),
                },
            )
        assert response.status_code == 204
        mock_delay.assert_called_once_with(
            "AA:BB:CC:DD:EE:FF",
            {"capabilities": ["people_counter"], "firmware_version": "1.2.0"},
        )

    def test_capabilities_end_to_end_persists(self, api_client, settings):
        settings.CELERY_TASK_ALWAYS_EAGER = True
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:44:55:66")

        response = _post(
            api_client,
            {
                "topic": f"forgekey/{_topic_segment(device.mac_address)}/capabilities",
                "payload": json.dumps({"capabilities": ["mmwave_presence", "status_led"]}),
            },
        )

        assert response.status_code == 204
        device.refresh_from_db()
        assert device.capabilities == ["mmwave_presence", "status_led"]
        assert device.capabilities_announced_at is not None


class TestDeviceListCapabilityFilter:
    def test_filter_returns_only_matching_devices(self, api_client):
        match_a = ESP32DeviceFactory(capabilities=["people_counter", "status_led"])
        match_b = ESP32DeviceFactory(capabilities=["people_counter"])
        ESP32DeviceFactory(capabilities=["mmwave_presence"])  # excluded
        ESP32DeviceFactory(capabilities=[])  # excluded

        # ESP32DeviceViewSet is IsAuthenticatedOrReadOnly — anonymous can GET.
        response = api_client.get("/api/forgekey/devices/", {"capability": "people_counter"})
        assert response.status_code == 200
        body = response.json()
        rows = body.get("results") if isinstance(body, dict) else body
        ids = {row["id"] for row in rows}
        assert ids == {str(match_a.id), str(match_b.id)}

    def test_no_filter_returns_all_devices(self, api_client):
        ESP32DeviceFactory(capabilities=["people_counter"])
        ESP32DeviceFactory(capabilities=[])

        response = api_client.get("/api/forgekey/devices/")
        assert response.status_code == 200
        body = response.json()
        rows = body.get("results") if isinstance(body, dict) else body
        assert len(rows) == 2

    def test_filter_with_no_matches_returns_empty(self, api_client):
        ESP32DeviceFactory(capabilities=["people_counter"])

        response = api_client.get("/api/forgekey/devices/", {"capability": "does_not_exist"})
        assert response.status_code == 200
        body = response.json()
        rows = body.get("results") if isinstance(body, dict) else body
        assert rows == []
