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
    _STARTUP_GRACE_SECONDS,
    LOG_RATE_LIMIT_MAX_EVENTS,
    _connack_log_level,
    _parse_indicator_state,
    _parse_relay_channels,
    dispatch_message,
    handle_capabilities_message,
    handle_log_message,
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

    def test_handle_status_message_caches_relay_channels_and_indicator(self):
        # op-2cr: the status payload's power_relay.channels + indicator
        # sub-state must be cached on the device so the control cards can show
        # live state instead of being write-only.
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:5B:57:01")
        topic = f"forgekey/{_topic_segment(device.mac_address)}/status"
        payload = json.dumps(
            {
                "online": True,
                "power_relay": {
                    "channels": [
                        {"channel": 1, "on": True},
                        {"channel": 2, "on": False},
                    ]
                },
                "indicator": {"color": "green", "pattern": "solid"},
            }
        ).encode("utf-8")

        assert handle_status_message(topic, payload) is True
        device.refresh_from_db()
        assert device.relay_channels == [
            {"channel": 1, "on": True},
            {"channel": 2, "on": False},
        ]
        assert device.indicator_state == {"color": "green", "pattern": "solid"}

    def test_handle_status_message_caches_bool_array_channels(self):
        # Firmware may report channels as a bare boolean array positionally
        # (channel 1 = index 0); it must normalize to the 1-indexed object form.
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:5B:57:02")
        topic = f"forgekey/{_topic_segment(device.mac_address)}/status"
        payload = json.dumps({"power_relay": {"channels": [False, True]}}).encode("utf-8")

        assert handle_status_message(topic, payload) is True
        device.refresh_from_db()
        assert device.relay_channels == [
            {"channel": 1, "on": False},
            {"channel": 2, "on": True},
        ]

    def test_handle_status_message_caches_bare_string_indicator(self):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:5B:57:03")
        topic = f"forgekey/{_topic_segment(device.mac_address)}/status"
        payload = json.dumps({"indicator": "red"}).encode("utf-8")

        assert handle_status_message(topic, payload) is True
        device.refresh_from_db()
        assert device.indicator_state == {"color": "red", "pattern": None}

    def test_handle_status_message_preserves_substate_when_absent(self):
        # A bare heartbeat ping (no sub-state) must not wipe the last-known
        # cached channel/indicator state.
        device = ESP32DeviceFactory(
            mac_address="AA:BB:CC:5B:57:04",
            relay_channels=[{"channel": 1, "on": True}],
            indicator_state={"color": "green", "pattern": None},
        )
        topic = f"forgekey/{_topic_segment(device.mac_address)}/status"
        payload = json.dumps({"online": True}).encode("utf-8")

        assert handle_status_message(topic, payload) is True
        device.refresh_from_db()
        assert device.relay_channels == [{"channel": 1, "on": True}]
        assert device.indicator_state == {"color": "green", "pattern": None}

    def test_handle_capabilities_message_ingests_capability_set(self):
        # ga-c6m: the consumer must ingest the retained capabilities announce
        # so the device-detail page can render the power_relay (and other)
        # capability cards. The consumer previously dropped this topic.
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:CA:9A:01", capabilities=[])
        topic = f"forgekey/{_topic_segment(device.mac_address)}/capabilities"
        payload = json.dumps(
            {"capabilities": ["status_led", "power_relay"], "firmware_version": "1.2.3"}
        ).encode("utf-8")

        assert handle_capabilities_message(topic, payload) is True
        device.refresh_from_db()
        assert "power_relay" in device.capabilities
        assert device.capabilities_announced_at is not None

    def test_dispatch_routes_capabilities_topic(self):
        # The dispatch table + subscription must actually wire /capabilities
        # through to the handler — the bug was a missing route, not a missing
        # handler.
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:CA:9A:02", capabilities=[])
        topic = f"forgekey/{_topic_segment(device.mac_address)}/capabilities"
        payload = json.dumps({"capabilities": ["status_led", "power_relay"]}).encode("utf-8")

        dispatch_message(topic, payload)
        device.refresh_from_db()
        assert "power_relay" in device.capabilities

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

    # ---------- AC-2b: device log forwarding (forgekey/<mac>/logs) -------

    def _log_topic(self, mac: str) -> str:
        return f"forgekey/{_topic_segment(mac)}/logs"

    @pytest.fixture(autouse=True)
    def _reset_log_rate_limit(self):
        # Each test gets a fresh rate-limit window — otherwise the prior
        # test's traffic counts against this one's budget.
        from django.core.cache import cache as _cache

        _cache.clear()
        yield
        _cache.clear()

    def test_handle_log_warning_forwards_to_sentry_with_device_tags(self):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:DE:AD:01", firmware_version="1.2.3")
        payload = json.dumps(
            {"ts": 1234, "level": "warning", "tag": "lock", "msg": "latch stuck open"}
        ).encode("utf-8")
        with patch("forgekey.management.commands.mqtt_consumer.sentry_sdk") as mock_sentry:
            mock_scope = MagicMock()
            mock_sentry.new_scope.return_value.__enter__.return_value = mock_scope
            result = handle_log_message(self._log_topic(device.mac_address), payload)
        assert result is True
        mock_sentry.capture_message.assert_called_once_with("latch stuck open", level="warning")
        # Tags on the scope identify which device, firmware, tag.
        tag_calls = {c.args[0]: c.args[1] for c in mock_scope.set_tag.call_args_list}
        assert tag_calls["origin"] == "device"
        assert tag_calls["device_mac"] == device.mac_address
        assert tag_calls["device_firmware"] == "1.2.3"
        assert tag_calls["device_log_tag"] == "lock"

    def test_handle_log_info_does_not_call_sentry(self):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:DE:AD:02")
        payload = json.dumps({"ts": 1234, "level": "info", "tag": "boot", "msg": "online"}).encode(
            "utf-8"
        )
        with patch("forgekey.management.commands.mqtt_consumer.sentry_sdk") as mock_sentry:
            result = handle_log_message(self._log_topic(device.mac_address), payload)
        assert result is False
        mock_sentry.capture_message.assert_not_called()

    def test_handle_log_drops_unknown_mac(self):
        # No ESP32Device row for this MAC — must drop, not forward.
        with patch("forgekey.management.commands.mqtt_consumer.sentry_sdk") as mock_sentry:
            result = handle_log_message(
                "forgekey/aabbccddee99/logs",
                json.dumps({"level": "error", "msg": "boom"}).encode("utf-8"),
            )
        assert result is False
        mock_sentry.capture_message.assert_not_called()

    def test_handle_log_drops_malformed_json(self):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:DE:AD:03")
        with patch("forgekey.management.commands.mqtt_consumer.sentry_sdk") as mock_sentry:
            result = handle_log_message(self._log_topic(device.mac_address), b"not json")
        assert result is False
        mock_sentry.capture_message.assert_not_called()

    def test_handle_log_redacts_jwt_in_message_before_forwarding(self):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:DE:AD:04")
        # A JWT-shaped token in the device's log line must be scrubbed by
        # observability_redaction before it reaches Sentry.
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.signature_blob_long_enough_to_match"
        payload = json.dumps(
            {"level": "error", "tag": "auth", "msg": f"verify failed for token {jwt}"}
        ).encode("utf-8")
        with patch("forgekey.management.commands.mqtt_consumer.sentry_sdk") as mock_sentry:
            mock_sentry.new_scope.return_value.__enter__.return_value = MagicMock()
            handle_log_message(self._log_topic(device.mac_address), payload)
        forwarded_msg = mock_sentry.capture_message.call_args.args[0]
        assert "***REDACTED***" in forwarded_msg
        assert jwt not in forwarded_msg

    def test_handle_log_per_device_rate_limit_caps_forwards(self):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:DE:AD:05")
        payload = json.dumps({"level": "error", "tag": "spam", "msg": "x"}).encode("utf-8")
        forwarded_count = 0
        with patch("forgekey.management.commands.mqtt_consumer.sentry_sdk") as mock_sentry:
            mock_sentry.new_scope.return_value.__enter__.return_value = MagicMock()
            for _ in range(LOG_RATE_LIMIT_MAX_EVENTS + 5):
                if handle_log_message(self._log_topic(device.mac_address), payload):
                    forwarded_count += 1
        assert forwarded_count == LOG_RATE_LIMIT_MAX_EVENTS
        assert mock_sentry.capture_message.call_count == LOG_RATE_LIMIT_MAX_EVENTS

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
# op-2cr: live device sub-state parsing (power_relay.channels + indicator)
# ---------------------------------------------------------------------------


class TestDeviceSubStateParsing:
    """Pure-function coverage for the status sub-state normalizers."""

    def test_relay_channels_object_form_with_explicit_channel_numbers(self):
        body = {"power_relay": {"channels": [{"channel": 2, "on": False}]}}
        assert _parse_relay_channels(body) == [{"channel": 2, "on": False}]

    def test_relay_channels_accepts_verb_and_state_keys(self):
        body = {
            "power_relay": {
                "channels": [
                    {"channel": 1, "state": "on"},
                    {"channel": 2, "action": "disable"},
                ]
            }
        }
        assert _parse_relay_channels(body) == [
            {"channel": 1, "on": True},
            {"channel": 2, "on": False},
        ]

    def test_relay_channels_accepts_bare_list_and_int_flags(self):
        # power_relay published directly as a list of 0/1 ints.
        assert _parse_relay_channels({"power_relay": [1, 0]}) == [
            {"channel": 1, "on": True},
            {"channel": 2, "on": False},
        ]

    def test_relay_channels_absent_returns_none(self):
        assert _parse_relay_channels({"online": True}) is None
        # power_relay present but without a channels list -> nothing to cache.
        assert _parse_relay_channels({"power_relay": {"foo": 1}}) is None

    def test_relay_channels_empty_list_is_reported_not_none(self):
        # An explicit empty channels report is a valid state, distinct from
        # "field absent" (which returns None to preserve the cache).
        assert _parse_relay_channels({"power_relay": {"channels": []}}) == []

    def test_indicator_object_color_only(self):
        assert _parse_indicator_state({"indicator": {"color": "blue"}}) == {
            "color": "blue",
            "pattern": None,
        }

    def test_indicator_object_pattern_off_without_color(self):
        # An off pattern is meaningful even with no colour.
        assert _parse_indicator_state({"indicator": {"pattern": "off"}}) == {
            "color": None,
            "pattern": "off",
        }

    def test_indicator_firmware_compat_name_field(self):
        # op-8ph: some firmware carries the colour NAME under `indicator`.
        assert _parse_indicator_state({"indicator": {"indicator": "yellow"}}) == {
            "color": "yellow",
            "pattern": None,
        }

    def test_indicator_absent_or_empty_returns_none(self):
        assert _parse_indicator_state({"online": True}) is None
        assert _parse_indicator_state({"indicator": ""}) is None
        assert _parse_indicator_state({"indicator": {}}) is None


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

        # Command topic uses the firmware contract MAC encoding
        # (lowercase hex, no separators) — see get_mqtt_command_topic.
        assert topic.endswith("/command")
        contract_mac = device.mac_address.replace(":", "").lower()
        assert contract_mac in topic
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


# ---------------------------------------------------------------------------
# CONNACK log-level matrix (BACKEND-6 — EMQX JWKS warm-up suppression)
# ---------------------------------------------------------------------------


class TestConnackLogLevel:
    """The CONNACK-failure log level decides what's a Sentry issue vs noise.

    EMQX takes ~30s after restart to populate its JWKS cache; during that
    window every JWT-bearing CONNECT returns rc=5 even though the consumer
    will recover on its own. Promoting those to ERROR fills Sentry with
    transient startup events (BACKEND-6: 174 in a single deploy window).
    The matrix:

        * pre-first-connect + within grace window  → WARNING (don't page)
        * pre-first-connect + grace expired        → ERROR (real outage)
        * post-first-connect, any time             → ERROR (broker drop)
    """

    def test_transient_startup_failure_is_warning(self):
        assert _connack_log_level(first_connect_done=False, elapsed_seconds=5.0) == logging.WARNING

    def test_failure_at_grace_boundary_is_error(self):
        # Sitting exactly on the boundary should escalate — anything older
        # is a sustained outage worth paging on.
        assert (
            _connack_log_level(first_connect_done=False, elapsed_seconds=_STARTUP_GRACE_SECONDS)
            == logging.ERROR
        )

    def test_sustained_failure_is_error(self):
        assert (
            _connack_log_level(
                first_connect_done=False, elapsed_seconds=_STARTUP_GRACE_SECONDS + 30
            )
            == logging.ERROR
        )

    def test_post_connect_failure_is_error_regardless_of_elapsed(self):
        # Once we've connected at least once, any subsequent rc!=0 is a
        # broker drop / re-auth failure — that's real signal even at 1s.
        assert _connack_log_level(first_connect_done=True, elapsed_seconds=1.0) == logging.ERROR
