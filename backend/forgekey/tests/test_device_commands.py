"""
Tests for the device-command audit surface added by oms-zta.

Covers:
  - DeviceCommand row creation when a command endpoint dispatches
  - identify + ping endpoints publish the right MQTT payloads
  - recent-commands endpoint orders newest-first, caps the result count,
    and reports timeouts via effective_ack_status
  - the MQTT status handler consumes cmd_ack payloads and marks the row
"""

from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from forgekey.management.commands.mqtt_consumer import handle_status_message
from forgekey.models import DeviceCommand
from forgekey.services.device_commands import apply_command_ack, publish_command
from forgekey.tasks import process_mqtt_status_message
from forgekey.tests.factories import ESP32DeviceFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_api_client(admin_user):
    client = APIClient()
    client.force_authenticate(user=admin_user)
    return client


def _command_url(device, suffix):
    return f"/api/forgekey/devices/{device.id}/command/{suffix}/"


class TestDispatchCreatesAuditRow:
    def test_restart_creates_device_command(self, admin_api_client, admin_user):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:00:00:01")
        with patch("forgekey.views.publish_command", return_value="forgekey/x/command"):
            response = admin_api_client.post(_command_url(device, "restart"))

        assert response.status_code == 200, response.data
        body = response.json()
        assert body["command_id"]

        rec = DeviceCommand.objects.get(id=body["command_id"])
        assert rec.device_id == device.id
        assert rec.command == "restart"
        assert rec.sent_by_id == admin_user.id
        assert rec.payload["command_id"] == body["command_id"]
        assert rec.payload["cmd"] == "restart"
        assert rec.ack_status == DeviceCommand.ACK_PENDING

    def test_broker_failure_drops_audit_row(self, admin_api_client):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:00:00:02")
        from forgekey.services.device_commands import DeviceCommandError

        with patch("forgekey.views.publish_command", side_effect=DeviceCommandError("boom")):
            response = admin_api_client.post(_command_url(device, "restart"))

        assert response.status_code == 502
        assert DeviceCommand.objects.filter(device=device).count() == 0


class TestIdentifyEndpoint:
    def test_identify_publishes_default_duration(self, admin_api_client):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:00:00:10")
        with patch("forgekey.views.publish_command", return_value="forgekey/x/command") as mock_pub:
            response = admin_api_client.post(_command_url(device, "identify"))

        assert response.status_code == 200, response.data
        _device_arg, payload = mock_pub.call_args.args
        assert payload["cmd"] == "identify"
        assert payload["duration_s"] == 30
        assert "command_id" in payload

    def test_identify_accepts_explicit_duration(self, admin_api_client):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:00:00:11")
        with patch("forgekey.views.publish_command", return_value="forgekey/x/command") as mock_pub:
            response = admin_api_client.post(
                _command_url(device, "identify"),
                data={"duration_s": 90},
                format="json",
            )
        assert response.status_code == 200
        _device_arg, payload = mock_pub.call_args.args
        assert payload["duration_s"] == 90

    def test_identify_rejects_garbage_duration(self, admin_api_client):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:00:00:12")
        response = admin_api_client.post(
            _command_url(device, "identify"),
            data={"duration_s": "forever"},
            format="json",
        )
        assert response.status_code == 400

    def test_identify_rejects_out_of_range(self, admin_api_client):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:00:00:13")
        response = admin_api_client.post(
            _command_url(device, "identify"),
            data={"duration_s": 9999},
            format="json",
        )
        assert response.status_code == 400


class TestPingEndpoint:
    def test_ping_publishes_status_cmd(self, admin_api_client):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:00:00:20")
        with patch("forgekey.views.publish_command", return_value="forgekey/x/command") as mock_pub:
            response = admin_api_client.post(_command_url(device, "ping"))

        assert response.status_code == 200, response.data
        _device_arg, payload = mock_pub.call_args.args
        assert payload["cmd"] == "status"
        assert "command_id" in payload

    def test_ping_requires_admin(self, authenticated_client):
        client, _user = authenticated_client
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:00:00:21")
        response = client.post(_command_url(device, "ping"))
        assert response.status_code == 403


class TestRecentCommandsEndpoint:
    def test_returns_newest_first_with_default_limit(self, admin_api_client):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:00:00:30")
        for i in range(15):
            DeviceCommand.objects.create(
                device=device,
                command=f"cmd{i}",
                payload={"cmd": f"cmd{i}"},
            )
        url = f"/api/forgekey/devices/{device.id}/recent-commands/"
        response = admin_api_client.get(url)
        assert response.status_code == 200, response.data
        body = response.json()
        assert body["device"] == device.mac_address
        assert len(body["results"]) == 10
        # Newest-first ordering: cmd14, cmd13, ...
        assert body["results"][0]["command"] == "cmd14"
        assert body["results"][-1]["command"] == "cmd5"

    def test_respects_explicit_limit(self, admin_api_client):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:00:00:31")
        for i in range(5):
            DeviceCommand.objects.create(device=device, command=f"cmd{i}", payload={})
        url = f"/api/forgekey/devices/{device.id}/recent-commands/?limit=2"
        response = admin_api_client.get(url)
        assert response.status_code == 200
        assert len(response.json()["results"]) == 2

    def test_old_pending_rows_report_as_timeout(self, admin_api_client):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:00:00:32")
        rec = DeviceCommand.objects.create(device=device, command="restart", payload={})
        # Bypass auto_now_add by going through the queryset.
        DeviceCommand.objects.filter(pk=rec.pk).update(
            sent_at=timezone.now() - timedelta(seconds=30)
        )
        url = f"/api/forgekey/devices/{device.id}/recent-commands/"
        response = admin_api_client.get(url)
        assert response.status_code == 200
        body = response.json()
        assert body["results"][0]["effective_ack_status"] == "timeout"
        assert body["results"][0]["ack_status"] == "pending"

    def test_requires_admin(self, authenticated_client):
        client, _user = authenticated_client
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:00:00:33")
        url = f"/api/forgekey/devices/{device.id}/recent-commands/"
        assert client.get(url).status_code == 403


class TestApplyCommandAck:
    def test_marks_pending_row_as_acked(self):
        device = ESP32DeviceFactory()
        rec = DeviceCommand.objects.create(device=device, command="restart", payload={})
        ok = apply_command_ack(command_id=str(rec.id), status="ok")
        assert ok is True
        rec.refresh_from_db()
        assert rec.ack_status == DeviceCommand.ACK_OK
        assert rec.ack_at is not None

    def test_marks_error_with_payload(self):
        device = ESP32DeviceFactory()
        rec = DeviceCommand.objects.create(device=device, command="restart", payload={})
        ok = apply_command_ack(
            command_id=str(rec.id),
            status="unknown_command",
            ack_payload={"error": "no such cmd"},
        )
        assert ok is True
        rec.refresh_from_db()
        assert rec.ack_status == DeviceCommand.ACK_ERROR
        assert rec.ack_payload == {"error": "no such cmd"}

    def test_unknown_id_is_silent_drop(self):
        ok = apply_command_ack(command_id="00000000-0000-0000-0000-000000000000", status="ok")
        assert ok is False

    def test_already_acked_row_is_not_overwritten(self):
        device = ESP32DeviceFactory()
        rec = DeviceCommand.objects.create(
            device=device,
            command="restart",
            payload={},
            ack_status=DeviceCommand.ACK_OK,
            ack_at=timezone.now(),
        )
        ok = apply_command_ack(command_id=str(rec.id), status="error")
        # Idempotent ack: a late error message after a successful ack should
        # not flip the row back to error.
        assert ok is False
        rec.refresh_from_db()
        assert rec.ack_status == DeviceCommand.ACK_OK


class TestStatusHandlerProcessesCmdAck:
    def test_status_payload_with_cmd_ack_marks_row(self):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:00:00:40")
        rec = DeviceCommand.objects.create(device=device, command="restart", payload={})
        topic = f"forgekey/{device.mac_address.replace(':', '').lower()}/status"
        body = json.dumps(
            {
                "online": True,
                "cmd_ack": {"command_id": str(rec.id), "status": "ok"},
            }
        ).encode("utf-8")
        assert handle_status_message(topic, body) is True
        rec.refresh_from_db()
        assert rec.ack_status == DeviceCommand.ACK_OK

    def test_status_without_cmd_ack_still_updates_device(self):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:00:00:41")
        topic = f"forgekey/{device.mac_address.replace(':', '').lower()}/status"
        assert handle_status_message(topic, b'{"online": true}') is True


class TestPublishCommandStillReturnsTopic:
    """Regression: existing callers patch publish_command to return a string."""

    def test_publish_command_returns_topic(self):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:00:00:50")
        client = MagicMock()
        client.publish.return_value = MagicMock(rc=0)
        topic = publish_command(device, {"cmd": "restart"}, client=client)
        assert isinstance(topic, str)
        assert topic.endswith("/command")


class TestPublishCommandWaitsForBrokerAck:
    """publish_command must wait for the broker PUBACK so callers don't get a
    success return for a publish the broker actually dropped."""

    def test_pubacked_publish_returns_topic(self):
        from forgekey.services.device_commands import publish_command

        device = ESP32DeviceFactory(mac_address="AA:BB:CC:00:00:60")
        result = MagicMock(rc=0)
        result.wait_for_publish.return_value = True
        client = MagicMock()
        client.publish.return_value = result

        topic = publish_command(device, {"cmd": "restart"}, client=client)
        assert topic.endswith("/command")
        result.wait_for_publish.assert_called_once()

    def test_puback_timeout_raises_device_command_error(self):
        from forgekey.services.device_commands import DeviceCommandError, publish_command

        device = ESP32DeviceFactory(mac_address="AA:BB:CC:00:00:61")
        result = MagicMock(rc=0)
        result.wait_for_publish.return_value = False
        client = MagicMock()
        client.publish.return_value = result

        with pytest.raises(DeviceCommandError, match="PUBACK"):
            publish_command(device, {"cmd": "restart"}, client=client)


class TestPublishCommandCircuitBreaker:
    """When the shared "mqtt" breaker is open, publish_command fails fast with
    DeviceCommandError so dispatch_command still drops its phantom audit row
    and the broker is never touched."""

    def test_open_breaker_fails_fast_with_device_command_error(self, settings):
        from forgekey.services.device_commands import DeviceCommandError
        from resilience.circuit import InMemoryStorage, get_breaker, reset_storage

        settings.CIRCUIT_BREAKERS_ENABLED = True
        settings.CIRCUIT_BREAKER_USE_REDIS = False
        reset_storage(InMemoryStorage())
        try:
            get_breaker("mqtt").storage.trip_open("mqtt")

            device = ESP32DeviceFactory(mac_address="AA:BB:CC:00:0C:01")
            client = MagicMock()
            client.publish.return_value = MagicMock(rc=0)

            with pytest.raises(DeviceCommandError, match="circuit breaker"):
                publish_command(device, {"cmd": "restart"}, client=client)
            client.publish.assert_not_called()
        finally:
            reset_storage(None)


class TestStatusHandlerAcceptsFlatCmdAck:
    """Current firmware emits ``cmd_ack: "<verb>"`` (a flat string) rather
    than the richer object form. The consumer must accept both."""

    def test_flat_cmd_ack_with_sibling_command_id(self):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:00:00:70")
        rec = DeviceCommand.objects.create(device=device, command="status", payload={})
        topic = f"forgekey/{device.mac_address.replace(':', '').lower()}/status"
        body = json.dumps(
            {
                "online": True,
                "cmd_ack": "status",
                "command_id": str(rec.id),
                "status": "ok",
            }
        ).encode("utf-8")

        assert handle_status_message(topic, body) is True
        rec.refresh_from_db()
        assert rec.ack_status == DeviceCommand.ACK_OK

    def test_flat_cmd_ack_falls_back_to_most_recent_pending_of_verb(self):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:00:00:71")
        # Older pending row of the same verb — should be skipped in favour
        # of the newer one.
        DeviceCommand.objects.create(
            device=device,
            command="status",
            payload={},
            sent_at=timezone.now() - timedelta(seconds=30),
        )
        newer = DeviceCommand.objects.create(device=device, command="status", payload={})
        topic = f"forgekey/{device.mac_address.replace(':', '').lower()}/status"
        body = json.dumps({"online": True, "cmd_ack": "status"}).encode("utf-8")

        assert handle_status_message(topic, body) is True
        newer.refresh_from_db()
        assert newer.ack_status == DeviceCommand.ACK_OK

    def test_flat_cmd_ack_for_unknown_device_is_silent(self):
        topic = "forgekey/aabbcc000099/status"
        body = json.dumps({"online": True, "cmd_ack": "status"}).encode("utf-8")
        # Unknown MAC: handle_status_message returns False (nothing to do)
        # rather than raising.
        assert handle_status_message(topic, body) is False


class TestWebhookTaskProcessesCmdAck:
    """The EMQX webhook path (``process_mqtt_status_message`` Celery task)
    must absorb cmd_ack identically to ``mqtt_consumer.handle_status_message``.
    See oms-v433rt: PR #400 fixed the consumer path but missed this one, so
    commands appeared to fail ("no ack") whenever the long-running consumer
    container was down even though the broker was still relaying acks via
    the webhook."""

    def test_object_form_marks_row_acked(self):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:00:01:00")
        rec = DeviceCommand.objects.create(device=device, command="restart", payload={})

        process_mqtt_status_message(
            device.mac_address,
            {
                "online": True,
                "cmd_ack": {"command_id": str(rec.id), "status": "ok"},
            },
        )

        rec.refresh_from_db()
        assert rec.ack_status == DeviceCommand.ACK_OK
        assert rec.ack_payload == {"command_id": str(rec.id), "status": "ok"}

    def test_object_form_error_status_marks_row_error(self):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:00:01:0A")
        rec = DeviceCommand.objects.create(device=device, command="unlock", payload={})

        process_mqtt_status_message(
            device.mac_address,
            {
                "online": True,
                "cmd_ack": {
                    "command_id": str(rec.id),
                    "status": "error",
                    "reason": "invalid_token",
                },
            },
        )

        rec.refresh_from_db()
        assert rec.ack_status == DeviceCommand.ACK_ERROR

    def test_flat_form_with_sibling_command_id_marks_row(self):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:00:01:01")
        rec = DeviceCommand.objects.create(device=device, command="status", payload={})

        process_mqtt_status_message(
            device.mac_address,
            {
                "online": True,
                "cmd_ack": "status",
                "command_id": str(rec.id),
                "status": "ok",
            },
        )

        rec.refresh_from_db()
        assert rec.ack_status == DeviceCommand.ACK_OK

    def test_flat_form_without_sibling_falls_back_to_most_recent_pending_of_verb(self):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:00:01:02")
        # Older pending row of the same verb — newer one should be picked.
        DeviceCommand.objects.create(
            device=device,
            command="status",
            payload={},
            sent_at=timezone.now() - timedelta(seconds=30),
        )
        newer = DeviceCommand.objects.create(device=device, command="status", payload={})

        process_mqtt_status_message(
            device.mac_address,
            {"online": True, "cmd_ack": "status"},
        )

        newer.refresh_from_db()
        assert newer.ack_status == DeviceCommand.ACK_OK

    def test_no_cmd_ack_does_not_touch_device_command_rows(self):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:00:01:03")
        rec = DeviceCommand.objects.create(device=device, command="restart", payload={})

        process_mqtt_status_message(device.mac_address, {"online": True})

        rec.refresh_from_db()
        assert rec.ack_status == DeviceCommand.ACK_PENDING

    def test_malformed_cmd_ack_does_not_break_online_update(self):
        device = ESP32DeviceFactory(
            mac_address="AA:BB:CC:00:01:04",
            is_online=False,
            firmware_version="1.0.0",
        )

        # ``cmd_ack: 42`` is neither dict nor str — the cmd_ack block must
        # silently ignore it AND the prior online/last_seen/firmware update
        # must still land.
        process_mqtt_status_message(
            device.mac_address,
            {"online": True, "cmd_ack": 42, "firmware_version": "9.9.9"},
        )

        device.refresh_from_db()
        assert device.is_online is True
        assert device.firmware_version == "9.9.9"

    def test_idempotent_with_consumer_path_for_same_payload(self):
        """Both paths fire on the same broker message in prod. The second
        ``apply_command_ack`` call must be a no-op (the row is no longer
        ACK_PENDING) so the state matches whichever path ran first."""
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:00:01:05")
        rec = DeviceCommand.objects.create(device=device, command="restart", payload={})

        payload = {
            "online": True,
            "cmd_ack": {"command_id": str(rec.id), "status": "ok"},
        }
        topic = f"forgekey/{device.mac_address.replace(':', '').lower()}/status"

        # Consumer fires first.
        handle_status_message(topic, json.dumps(payload).encode("utf-8"))
        rec.refresh_from_db()
        first_ack_at = rec.ack_at
        assert rec.ack_status == DeviceCommand.ACK_OK
        assert first_ack_at is not None

        # Webhook fires second on the same payload — must be a no-op.
        process_mqtt_status_message(device.mac_address, payload)
        rec.refresh_from_db()
        assert rec.ack_status == DeviceCommand.ACK_OK
        assert rec.ack_at == first_ack_at
