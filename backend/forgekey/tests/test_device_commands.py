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
