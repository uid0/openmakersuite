"""
Tests for the EMQX -> Django MQTT webhook bridge introduced for oms-2zo.

Covers:
  - Auth: missing/wrong secret rejected, IP allowlist enforced when set
  - Payload parsing: malformed JSON, non-object body, missing topic
  - Routing: each topic suffix dispatches to the matching Celery task
    (.delay) with the right args
  - End-to-end: process_mqtt_occupancy creates an OccupancyEvent row
"""

from __future__ import annotations

import json
from unittest.mock import patch

from django.urls import reverse

import pytest

from forgekey.models import OccupancyEvent
from forgekey.tests.factories import ESP32DeviceFactory

pytestmark = pytest.mark.django_db


WEBHOOK_SECRET = "test-secret-abc123"


def _topic_segment(mac: str) -> str:
    return mac.replace(":", "").lower()


def _post(api_client, body, *, secret: str | None = WEBHOOK_SECRET, remote_addr: str | None = None):
    headers = {}
    if secret is not None:
        headers["HTTP_X_FORGEKEY_WEBHOOK_SECRET"] = secret
    if remote_addr is not None:
        headers["REMOTE_ADDR"] = remote_addr
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


# ---------------------------------------------------------------------------
# AC-3: auth
# ---------------------------------------------------------------------------


class TestWebhookAuth:
    def test_missing_secret_header_rejected(self, api_client):
        response = _post(
            api_client,
            {"topic": "forgekey/aabbccddeeff/status", "payload": "{}"},
            secret=None,
        )
        assert response.status_code == 401

    def test_wrong_secret_rejected(self, api_client):
        response = _post(
            api_client,
            {"topic": "forgekey/aabbccddeeff/status", "payload": "{}"},
            secret="not-the-secret",
        )
        assert response.status_code == 401

    def test_unconfigured_server_rejects_all(self, api_client, settings):
        settings.FORGEKEY_WEBHOOK_SECRET = ""
        response = _post(
            api_client,
            {"topic": "forgekey/aabbccddeeff/status", "payload": "{}"},
            secret="anything",
        )
        assert response.status_code == 401

    def test_ip_not_in_allowlist_rejected(self, api_client, settings):
        settings.FORGEKEY_WEBHOOK_ALLOWED_IPS = ["10.0.0.5"]
        response = _post(
            api_client,
            {"topic": "forgekey/aabbccddeeff/status", "payload": "{}"},
            remote_addr="10.0.0.6",
        )
        assert response.status_code == 401

    def test_ip_in_allowlist_passes(self, api_client, settings):
        settings.FORGEKEY_WEBHOOK_ALLOWED_IPS = ["10.0.0.5"]
        with patch("forgekey.views.process_mqtt_status_message.delay") as mock_delay:
            response = _post(
                api_client,
                {"topic": "forgekey/aabbccddeeff/status", "payload": "{}"},
                remote_addr="10.0.0.5",
            )
        assert response.status_code == 204
        assert mock_delay.called

    def test_auth_runs_before_payload_parsing(self, api_client):
        # Garbage payload + bad secret must still 401, not 400.
        response = _post(
            api_client,
            {"topic": "forgekey/zzzz/status", "payload": "not-json {{{"},
            secret="wrong",
        )
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# AC-1: payload parsing
# ---------------------------------------------------------------------------


class TestWebhookPayloadParsing:
    def test_non_object_body_400(self, api_client):
        response = api_client.post(
            reverse("forgekey:mqtt-webhook"),
            data=json.dumps([1, 2, 3]),
            content_type="application/json",
            HTTP_X_FORGEKEY_WEBHOOK_SECRET=WEBHOOK_SECRET,
        )
        assert response.status_code == 400

    def test_missing_topic_400(self, api_client):
        response = _post(api_client, {"payload": "{}"})
        assert response.status_code == 400

    def test_malformed_json_payload_400(self, api_client):
        response = _post(
            api_client,
            {"topic": "forgekey/aabbccddeeff/status", "payload": "not-json {{{"},
        )
        assert response.status_code == 400

    def test_payload_as_object_accepted(self, api_client):
        # Some EMQX bridge configs forward payload as an object, not a string.
        with patch("forgekey.views.process_mqtt_status_message.delay") as mock_delay:
            response = _post(
                api_client,
                {
                    "topic": "forgekey/aabbccddeeff/status",
                    "payload": {"online": True},
                },
            )
        assert response.status_code == 204
        mock_delay.assert_called_once_with("AA:BB:CC:DD:EE:FF", {"online": True})

    def test_topic_too_short_400(self, api_client):
        response = _post(api_client, {"topic": "forgekey", "payload": "{}"})
        assert response.status_code == 400

    def test_unrouted_topic_returns_204(self, api_client):
        # Unknown suffix is dropped silently (no retry storm from EMQX).
        response = _post(
            api_client,
            {"topic": "forgekey/aabbccddeeff/something/weird", "payload": "{}"},
        )
        assert response.status_code == 204


# ---------------------------------------------------------------------------
# AC-1: routing
# ---------------------------------------------------------------------------


class TestWebhookRouting:
    def test_status_topic_dispatches_status_task(self, api_client):
        with patch("forgekey.views.process_mqtt_status_message.delay") as mock_delay:
            response = _post(
                api_client,
                {
                    "topic": "forgekey/aabbccddeeff/status",
                    "payload": json.dumps({"online": True, "boot_count": 3}),
                },
            )
        assert response.status_code == 204
        mock_delay.assert_called_once_with(
            "AA:BB:CC:DD:EE:FF",
            {"online": True, "boot_count": 3},
        )

    def test_occupancy_topic_dispatches_occupancy_task(self, api_client):
        with patch("forgekey.views.process_mqtt_occupancy.delay") as mock_delay:
            response = _post(
                api_client,
                {
                    "topic": "forgekey/aabbccddeeff/people_counter/occupancy",
                    "payload": json.dumps({"in": 1, "out": 0}),
                },
            )
        assert response.status_code == 204
        mock_delay.assert_called_once_with(
            "AA:BB:CC:DD:EE:FF",
            "people_counter",
            {"in": 1, "out": 0},
        )

    def test_power_topic_dispatches_power_task(self, api_client):
        with patch("forgekey.views.process_mqtt_power_reading.delay") as mock_delay:
            response = _post(
                api_client,
                {
                    "topic": "forgekey/aabbccddeeff/power",
                    "payload": json.dumps(
                        {
                            "asset_id": "11111111-1111-1111-1111-111111111111",
                            "voltage": 120.0,
                            "current": 1.5,
                        }
                    ),
                },
            )
        assert response.status_code == 204
        mock_delay.assert_called_once()
        args, _kwargs = mock_delay.call_args
        assert args[0] == "AA:BB:CC:DD:EE:FF"
        assert args[1] == "11111111-1111-1111-1111-111111111111"
        assert args[2]["voltage"] == 120.0

    def test_power_topic_without_asset_id_dropped(self, api_client):
        with patch("forgekey.views.process_mqtt_power_reading.delay") as mock_delay:
            response = _post(
                api_client,
                {
                    "topic": "forgekey/aabbccddeeff/power",
                    "payload": json.dumps({"voltage": 120.0}),
                },
            )
        assert response.status_code == 204
        assert not mock_delay.called

    def test_firmware_response_dispatches_update_task(self, api_client):
        with patch("forgekey.views.process_mqtt_firmware_update_response.delay") as mock_delay:
            response = _post(
                api_client,
                {
                    "topic": "forgekey/aabbccddeeff/firmware/response",
                    "payload": json.dumps(
                        {
                            "update_id": "22222222-2222-2222-2222-222222222222",
                            "status": "completed",
                        }
                    ),
                },
            )
        assert response.status_code == 204
        mock_delay.assert_called_once_with(
            "AA:BB:CC:DD:EE:FF",
            "22222222-2222-2222-2222-222222222222",
            "completed",
            None,
        )

    def test_firmware_response_missing_fields_dropped(self, api_client):
        with patch("forgekey.views.process_mqtt_firmware_update_response.delay") as mock_delay:
            response = _post(
                api_client,
                {
                    "topic": "forgekey/aabbccddeeff/firmware/response",
                    "payload": json.dumps({"status": "completed"}),
                },
            )
        assert response.status_code == 204
        assert not mock_delay.called


# ---------------------------------------------------------------------------
# AC-2: end-to-end occupancy persistence
# ---------------------------------------------------------------------------


class TestWebhookOccupancyEndToEnd:
    def test_occupancy_webhook_creates_event(self, api_client, settings):
        # CELERY_TASK_ALWAYS_EAGER should be on for tests (typical in Django
        # test settings); if not, force it here so .delay runs synchronously.
        settings.CELERY_TASK_ALWAYS_EAGER = True
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:11:22:33")

        response = _post(
            api_client,
            {
                "topic": f"forgekey/{_topic_segment(device.mac_address)}/people_counter/occupancy",
                "payload": json.dumps({"in": 1, "out": 0, "ts": "2026-05-01T03:00:00Z"}),
            },
        )

        assert response.status_code == 204
        events = OccupancyEvent.objects.filter(device=device)
        assert events.count() == 1
        event = events.get()
        assert event.count_in == 1
        assert event.count_out == 0
        assert event.sensor_kind == "people_counter"

    def test_occupancy_webhook_drops_unknown_mac(self, api_client, settings):
        settings.CELERY_TASK_ALWAYS_EAGER = True
        response = _post(
            api_client,
            {
                "topic": "forgekey/aabbccddeeff/people_counter/occupancy",
                "payload": json.dumps({"in": 1, "out": 0}),
            },
        )
        assert response.status_code == 204
        assert OccupancyEvent.objects.count() == 0
