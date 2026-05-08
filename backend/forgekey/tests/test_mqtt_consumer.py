"""
Tests for the MQTT subscriber + bidirectional command surface introduced
for oms-yyg.

Covers:
  - OccupancyEvent model aggregates (current_occupancy_for, occupancy_delta)
  - mqtt_consumer message dispatch: occupancy + status, malformed payloads,
    unknown MACs, bad topics
  - publish_command service: topic + payload shape, audit log line
  - DRF endpoints: command/{restart,blink,capture-photo,firmware-update}
    require admin auth and publish the correct JSON
  - occupancy GET endpoint: returns events within the requested window
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.utils import timezone

import pytest

from forgekey.management.commands.mqtt_consumer import (
    dispatch_message,
    handle_occupancy_message,
    handle_status_message,
)
from forgekey.models import OccupancyEvent
from forgekey.services.device_commands import DeviceCommandError, publish_command
from forgekey.tests.factories import ESP32DeviceFactory, OccupancyEventFactory

pytestmark = pytest.mark.django_db


def _topic_segment(mac: str) -> str:
    return mac.replace(":", "").lower()


# ---------------------------------------------------------------------------
# AC-1: model
# ---------------------------------------------------------------------------


class TestOccupancyEventModel:
    def test_occupancy_delta_is_in_minus_out(self):
        event = OccupancyEventFactory.build(count_in=3, count_out=1)
        assert event.occupancy_delta == 2

    def test_current_occupancy_aggregates_per_device(self):
        device = ESP32DeviceFactory()
        OccupancyEventFactory(device=device, count_in=2, count_out=0)
        OccupancyEventFactory(device=device, count_in=1, count_out=3)
        # An event for a different device must not bleed in.
        other = ESP32DeviceFactory()
        OccupancyEventFactory(device=other, count_in=10, count_out=0)

        # Net for `device` = (2 + 1) - (0 + 3) = 0
        assert OccupancyEvent.current_occupancy_for(device) == 0

    def test_current_occupancy_clamps_negative_to_zero(self):
        device = ESP32DeviceFactory()
        OccupancyEventFactory(device=device, count_in=0, count_out=5)
        assert OccupancyEvent.current_occupancy_for(device) == 0


# ---------------------------------------------------------------------------
# AC-2: consumer message dispatch
# ---------------------------------------------------------------------------


class TestMqttConsumer:
    def test_handle_occupancy_message_creates_event(self, settings):
        settings.MQTT_TOPIC_PREFIX = "forgekey"
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:11:22:33")
        topic = f"forgekey/{_topic_segment(device.mac_address)}/people_counter/occupancy"
        payload = json.dumps({"in": 1, "out": 0, "ts": "2026-05-01T03:00:00Z"}).encode("utf-8")

        event = handle_occupancy_message(topic, payload)

        assert event is not None
        assert event.device_id == device.id
        assert event.sensor_kind == "people_counter"
        assert event.count_in == 1
        assert event.count_out == 0
        assert event.event_timestamp_utc.tzinfo is not None
        assert event.raw_payload == {"in": 1, "out": 0, "ts": "2026-05-01T03:00:00Z"}

        device.refresh_from_db()
        assert device.is_online is True
        assert device.last_seen is not None

    def test_handle_occupancy_message_drops_malformed_json(self, settings):
        settings.MQTT_TOPIC_PREFIX = "forgekey"
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:11:22:34")
        topic = f"forgekey/{_topic_segment(device.mac_address)}/people_counter/occupancy"

        result = handle_occupancy_message(topic, b"not json {{{")

        assert result is None
        assert OccupancyEvent.objects.filter(device=device).count() == 0

    def test_handle_occupancy_message_drops_unknown_mac(self):
        topic = "forgekey/aabbccddeeff/people_counter/occupancy"
        payload = json.dumps({"in": 1, "out": 0}).encode("utf-8")

        result = handle_occupancy_message(topic, payload)
        assert result is None

    def test_handle_occupancy_message_drops_bad_topic(self):
        # Missing the trailing /occupancy segment entirely.
        result = handle_occupancy_message("forgekey/aabbccddeeff", b"{}")
        assert result is None

    def test_handle_occupancy_message_handles_non_dict_payload(self, settings):
        settings.MQTT_TOPIC_PREFIX = "forgekey"
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:11:22:35")
        topic = f"forgekey/{_topic_segment(device.mac_address)}/people_counter/occupancy"

        # JSON-valid but not an object — must drop, not crash.
        result = handle_occupancy_message(topic, b"[1, 2, 3]")
        assert result is None

    def test_handle_status_message_updates_last_seen(self):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:99:88:77", boot_count=0)
        topic = f"forgekey/{_topic_segment(device.mac_address)}/status"
        payload = json.dumps({"online": True, "firmware_version": "9.9.9", "boot_count": 5}).encode(
            "utf-8"
        )

        assert handle_status_message(topic, payload) is True
        device.refresh_from_db()
        assert device.is_online is True
        assert device.firmware_version == "9.9.9"
        assert device.boot_count == 5
        assert device.last_seen is not None

    def test_handle_status_message_unknown_mac_returns_false(self):
        topic = "forgekey/aabbccddeeff/status"
        assert handle_status_message(topic, b"{}") is False

    def test_dispatch_message_swallows_handler_exceptions(self, caplog):
        # Even an internal explosion in a handler must not propagate — the
        # consumer loop relies on this guarantee.
        with patch(
            "forgekey.management.commands.mqtt_consumer.handle_occupancy_message",
            side_effect=RuntimeError("boom"),
        ):
            with caplog.at_level(logging.ERROR):
                dispatch_message("forgekey/aabbccddeeff/people_counter/occupancy", b"{}")
        assert any("Unhandled error" in r.message for r in caplog.records)

    def test_handle_occupancy_message_redacts_secret_shaped_payload_keys(self, settings):
        """gh #378: payload keys that match the redactor's sensitive-name
        list are scrubbed before they hit OccupancyEvent.raw_payload."""
        settings.MQTT_TOPIC_PREFIX = "forgekey"
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:11:22:36")
        topic = f"forgekey/{_topic_segment(device.mac_address)}/people_counter/occupancy"
        # A misbehaving firmware reflects a provisioning token back into
        # the occupancy payload. The counts must persist; the token must not.
        payload = json.dumps(
            {
                "in": 2,
                "out": 1,
                "device_token": "tok_abcdef0123456789",
            }
        ).encode("utf-8")

        event = handle_occupancy_message(topic, payload)
        assert event is not None
        assert event.raw_payload["in"] == 2
        assert event.raw_payload["out"] == 1
        # The sensitive key is rewritten — not dropped — so dashboards
        # still see the shape but not the value.
        assert event.raw_payload["device_token"] == "***REDACTED***"


# ---------------------------------------------------------------------------
# AC-3: command publish service + endpoints
# ---------------------------------------------------------------------------


class TestPublishCommandService:
    def test_publish_command_uses_command_topic_and_emits_audit(self, caplog):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:DD:EE:FF")
        client = MagicMock()
        client.publish.return_value = MagicMock(rc=0)

        with caplog.at_level(logging.INFO, logger="forgekey.audit"):
            topic = publish_command(
                device,
                {"cmd": "restart"},
                actor=MagicMock(id="user-123", username="alice"),
                audit_action="restart",
                client=client,
            )

        # Command topic uses the colon→hyphen form per get_mqtt_command_topic
        assert topic.endswith("/command")
        assert device.mac_address.replace(":", "-") in topic
        # Payload has the command and a server-applied timestamp
        published_topic, body, *_rest = client.publish.call_args[0]
        body_obj = json.loads(body)
        assert body_obj["cmd"] == "restart"
        assert "timestamp" in body_obj
        # Audit log line is structured JSON
        audit_records = [r for r in caplog.records if r.name == "forgekey.audit"]
        assert audit_records, "expected an audit record"
        audit_payload = json.loads(audit_records[-1].message)
        assert audit_payload["event"] == "forgekey.device_command"
        assert audit_payload["action"] == "restart"
        assert audit_payload["device_mac"] == device.mac_address
        assert audit_payload["actor_username"] == "alice"

    def test_publish_command_raises_on_broker_failure(self):
        device = ESP32DeviceFactory()
        client = MagicMock()
        client.publish.return_value = MagicMock(rc=4)

        with pytest.raises(DeviceCommandError):
            publish_command(device, {"cmd": "restart"}, client=client)


@pytest.fixture
def admin_api_client(admin_user):
    from rest_framework.test import APIClient

    client = APIClient()
    client.force_authenticate(user=admin_user)
    return client


@pytest.fixture
def user_api_client(authenticated_client):
    client, _user = authenticated_client
    return client


def _command_url(device, suffix):
    return f"/api/forgekey/devices/{device.id}/command/{suffix}/"


class TestCommandEndpoints:
    def test_restart_publishes_correct_payload(self, admin_api_client):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:DD:EE:01")
        with patch("forgekey.views.publish_command", return_value="forgekey/x/command") as mock_pub:
            response = admin_api_client.post(_command_url(device, "restart"))

        assert response.status_code == 200, response.data
        assert mock_pub.called
        _device_arg, payload = mock_pub.call_args.args
        assert payload["cmd"] == "restart"
        # The view injects an audit row id so firmware can echo back the ack.
        assert "command_id" in payload
        kwargs = mock_pub.call_args.kwargs
        assert kwargs["audit_action"] == "restart"

    def test_capture_photo_includes_optional_upload_url(self, admin_api_client):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:DD:EE:02")
        with patch("forgekey.views.publish_command", return_value="forgekey/x/command") as mock_pub:
            response = admin_api_client.post(
                _command_url(device, "capture-photo"),
                data={"upload_url": "https://example.test/upload"},
                format="json",
            )

        assert response.status_code == 200, response.data
        _device_arg, payload = mock_pub.call_args.args
        assert payload["cmd"] == "capture"
        assert payload["upload_url"] == "https://example.test/upload"

    def test_blink_validates_duration(self, admin_api_client):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:DD:EE:03")
        response = admin_api_client.post(
            _command_url(device, "blink"),
            data={"duration_s": "not-a-number"},
            format="json",
        )
        assert response.status_code == 400

    def test_blink_publishes_pattern_and_duration(self, admin_api_client):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:DD:EE:04")
        with patch("forgekey.views.publish_command", return_value="forgekey/x/command") as mock_pub:
            response = admin_api_client.post(
                _command_url(device, "blink"),
                data={"pattern": "sos", "duration_s": 5},
                format="json",
            )

        assert response.status_code == 200
        _device_arg, payload = mock_pub.call_args.args
        assert payload["cmd"] == "blink"
        assert payload["pattern"] == "sos"
        assert payload["duration_s"] == 5
        assert "command_id" in payload

    def test_firmware_update_adhoc_publish(self, admin_api_client):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:DD:EE:05")
        with patch("forgekey.views.publish_command", return_value="forgekey/x/command") as mock_pub:
            response = admin_api_client.post(
                _command_url(device, "firmware-update"),
                data={"version": "2.3.4", "url": "https://example.test/fw.bin"},
                format="json",
            )

        assert response.status_code == 200, response.data
        _device_arg, payload = mock_pub.call_args.args
        assert payload["cmd"] == "ota"
        assert payload["version"] == "2.3.4"
        assert payload["url"] == "https://example.test/fw.bin"
        assert "command_id" in payload

    def test_firmware_update_requires_version_or_id(self, admin_api_client):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:DD:EE:06")
        response = admin_api_client.post(
            _command_url(device, "firmware-update"), data={}, format="json"
        )
        assert response.status_code == 400

    def test_command_requires_admin(self, user_api_client):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:DD:EE:07")
        response = user_api_client.post(_command_url(device, "restart"))
        assert response.status_code == 403

    def test_command_anonymous_denied(self, api_client):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:DD:EE:08")
        response = api_client.post(_command_url(device, "restart"))
        assert response.status_code in (401, 403)


class TestOccupancyEndpoint:
    def test_occupancy_endpoint_returns_recent_events(self, admin_api_client):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:DD:EE:10")
        now = timezone.now()
        OccupancyEventFactory(
            device=device,
            count_in=1,
            count_out=0,
            event_timestamp_utc=now - timedelta(hours=1),
        )
        # An old one outside the window should not appear.
        OccupancyEventFactory(
            device=device,
            count_in=1,
            count_out=0,
            event_timestamp_utc=now - timedelta(days=3),
        )

        url = f"/api/forgekey/devices/{device.id}/occupancy/?since=24h"
        response = admin_api_client.get(url)
        assert response.status_code == 200, response.data
        body = response.json()
        assert body["device"] == device.mac_address
        assert len(body["events"]) == 1
        assert body["current_occupancy"] == 2  # both events sum across history

    def test_occupancy_endpoint_rejects_bad_since(self, admin_api_client):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:DD:EE:11")
        url = f"/api/forgekey/devices/{device.id}/occupancy/?since=banana"
        response = admin_api_client.get(url)
        assert response.status_code == 400
