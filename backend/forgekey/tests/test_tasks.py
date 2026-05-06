"""
Tests for ForgeKey Celery tasks (with mocked MQTT).
"""

from unittest.mock import MagicMock, patch

import pytest

from forgekey.models import ESP32Device
from forgekey.tasks import (
    disable_device,
    enable_device,
    process_mqtt_power_reading,
    process_mqtt_status_message,
    request_device_status,
    send_mqtt_command,
)
from forgekey.tests.factories import (
    DeviceFirmwareUpdateFactory,
    ESP32DeviceFactory,
    FirmwareVersionFactory,
)


@pytest.mark.django_db
class TestMQTTTasks:
    """Tests for MQTT-related Celery tasks."""

    @patch("forgekey.tasks.get_mqtt_client")
    def test_send_mqtt_command(self, mock_get_client):
        """Test sending MQTT command."""
        mock_client = MagicMock()
        mock_client.publish.return_value.rc = 0  # MQTT_ERR_SUCCESS
        mock_get_client.return_value = mock_client

        result = send_mqtt_command("AA:BB:CC:DD:EE:FF", "enable")

        assert result["success"] is True
        assert result["command"] == "enable"
        mock_client.publish.assert_called_once()

    @patch("forgekey.tasks.send_mqtt_command")
    def test_enable_device(self, mock_send_command):
        """Test enabling a device."""
        mock_send_command.return_value = {"success": True}
        result = enable_device("AA:BB:CC:DD:EE:FF")

        assert result["success"] is True
        mock_send_command.assert_called_once_with("AA:BB:CC:DD:EE:FF", "enable")

    @patch("forgekey.tasks.send_mqtt_command")
    def test_disable_device(self, mock_send_command):
        """Test disabling a device."""
        mock_send_command.return_value = {"success": True}
        result = disable_device("AA:BB:CC:DD:EE:FF", delay_seconds=10)

        assert result["success"] is True
        mock_send_command.assert_called_once()

    @patch("forgekey.tasks.send_mqtt_command")
    def test_request_device_status(self, mock_send_command):
        """Test requesting device status."""
        mock_send_command.return_value = {"success": True}
        result = request_device_status("AA:BB:CC:DD:EE:FF")

        assert result["success"] is True
        mock_send_command.assert_called_once_with("AA:BB:CC:DD:EE:FF", "status")

    def test_process_mqtt_status_message(self):
        """Test processing MQTT status message."""
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:DD:EE:FF")
        message_data = {
            "online": True,
            "firmware_version": "2.0.0",
        }

        process_mqtt_status_message(device.mac_address, message_data)

        device.refresh_from_db()
        assert device.is_online is True
        assert device.firmware_version == "2.0.0"
        assert device.last_seen is not None

    def test_process_mqtt_power_reading(self):
        """Test processing MQTT power reading."""
        from inventory.tests.factories import AssetFactory

        device = ESP32DeviceFactory()
        asset = AssetFactory()
        reading_data = {
            "voltage": 120.0,
            "current": 5.5,
            "power": 660.0,
            "energy": 0.1,
        }

        process_mqtt_power_reading(device.mac_address, str(asset.id), reading_data)

        from forgekey.models import PowerMeterReading

        reading = PowerMeterReading.objects.filter(device=device, asset=asset).first()
        assert reading is not None
        assert float(reading.voltage) == 120.0
        assert float(reading.power) == 660.0

    @patch("forgekey.tasks.get_mqtt_client")
    def test_send_mqtt_command_with_payload(self, mock_get_client):
        """Test sending MQTT command with payload."""
        mock_client = MagicMock()
        mock_client.publish.return_value.rc = 0
        mock_get_client.return_value = mock_client

        payload = {"delay_seconds": 10}
        result = send_mqtt_command("AA:BB:CC:DD:EE:FF", "disable", payload)

        assert result["success"] is True
        assert result["command"] == "disable"
        mock_client.publish.assert_called_once()

    @patch("forgekey.tasks.get_mqtt_client")
    def test_send_mqtt_command_failure(self, mock_get_client):
        """Test MQTT command failure handling."""
        mock_client = MagicMock()
        mock_client.publish.return_value.rc = 1  # MQTT error
        mock_get_client.return_value = mock_client

        with pytest.raises(Exception, match="MQTT publish failed"):
            send_mqtt_command("AA:BB:CC:DD:EE:FF", "enable")

    @patch("forgekey.tasks.get_mqtt_client")
    def test_send_mqtt_command_exception(self, mock_get_client):
        """Test MQTT command exception handling."""
        mock_get_client.side_effect = ConnectionError("Connection failed")

        with pytest.raises(ConnectionError, match="Connection failed"):
            send_mqtt_command("AA:BB:CC:DD:EE:FF", "enable")

    def test_process_mqtt_status_message_unknown_device(self):
        """Test processing status message from unknown device."""
        message_data = {"online": True}
        # Should not raise, just log warning
        process_mqtt_status_message("UNKNOWN:MAC:ADDRESS", message_data)

    def test_process_mqtt_status_message_exception(self):
        """Test processing status message with exception."""
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:DD:EE:FF")
        # This should handle exceptions gracefully
        with patch.object(ESP32Device.objects, "get", side_effect=ValueError("DB error")):
            process_mqtt_status_message(device.mac_address, {})

    def test_process_mqtt_power_reading_unknown_device(self):
        """Test processing power reading from unknown device."""
        from inventory.tests.factories import AssetFactory

        asset = AssetFactory()
        reading_data = {"voltage": 120.0}
        # Should not raise, just log warning
        process_mqtt_power_reading("UNKNOWN:MAC:ADDRESS", str(asset.id), reading_data)

    def test_process_mqtt_power_reading_unknown_asset(self):
        """Test processing power reading for unknown asset."""
        device = ESP32DeviceFactory()
        reading_data = {"voltage": 120.0}
        # Should not raise, just log warning
        process_mqtt_power_reading(
            device.mac_address, "00000000-0000-0000-0000-000000000000", reading_data
        )

    def test_process_mqtt_power_reading_exception(self):
        """Test processing power reading with exception."""
        device = ESP32DeviceFactory()
        from inventory.tests.factories import AssetFactory

        asset = AssetFactory()
        reading_data = {"voltage": 120.0}
        # This should handle exceptions gracefully
        with patch.object(ESP32Device.objects, "get", side_effect=ValueError("DB error")):
            process_mqtt_power_reading(device.mac_address, str(asset.id), reading_data)

    @patch("paho.mqtt.client.Client")
    @patch("forgekey.tasks.settings")
    def test_get_mqtt_client_creation(self, mock_settings, mock_client_class):
        """Test MQTT client creation."""
        from forgekey.tasks import _reset_mqtt_client_state_for_tests, get_mqtt_client

        _reset_mqtt_client_state_for_tests()

        mock_settings.MQTT_CLIENT_ID = "test-client"
        mock_settings.MQTT_BROKER_HOST = "localhost"
        mock_settings.MQTT_BROKER_PORT = 1883
        mock_settings.MQTT_KEEPALIVE = 60
        mock_settings.MQTT_BROKER_USERNAME = ""  # nosec B105
        mock_settings.MQTT_BROKER_PASSWORD = ""  # nosec B105
        mock_settings.MQTT_CONNECT_RETRY_COOLDOWN_SECONDS = 30

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        client = get_mqtt_client()

        assert client is not None
        mock_client.connect.assert_called_once()
        mock_client.loop_start.assert_called_once()

    @patch("paho.mqtt.client.Client")
    def test_get_mqtt_client_uses_server_jwt(self, mock_client_class):
        """oms-y5p AC-2: backend authenticates with a self-signed server JWT.

        EMQX's JWT authenticator (with anonymous disabled) accepts no other
        credential, so this must be the default path whenever
        FORGEKEY_JWT_SIGNING_KEY is configured (always true in tests via the
        session-level conftest).
        """
        from forgekey.tasks import _reset_mqtt_client_state_for_tests, get_mqtt_client
        from forgekey.utils import SERVER_JWT_SUBJECT, verify_device_jwt  # noqa: F401

        _reset_mqtt_client_state_for_tests()

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        client = get_mqtt_client()

        assert client is not None
        mock_client.username_pw_set.assert_called_once()
        username, password = mock_client.username_pw_set.call_args[0]
        assert username == SERVER_JWT_SUBJECT
        # Compact JWT: header.payload.signature
        assert password.count(".") == 2

    @patch("paho.mqtt.client.Client")
    @patch("forgekey.tasks.settings")
    def test_get_mqtt_client_falls_back_to_literal_creds(
        self, mock_settings, mock_client_class, settings
    ):
        """When JWT signing is unconfigured (dev rigs), fall back to literal
        MQTT_BROKER_USERNAME/PASSWORD. Production never hits this path because
        forgekey.W008 warns and the JWT key must be set for device auth too.
        """
        from forgekey.tasks import _reset_mqtt_client_state_for_tests, get_mqtt_client

        _reset_mqtt_client_state_for_tests()
        # Disable JWT signing so the fallback branch is taken.
        settings.FORGEKEY_JWT_SIGNING_KEY = ""

        mock_settings.MQTT_CLIENT_ID = "test-client"
        mock_settings.MQTT_BROKER_HOST = "localhost"
        mock_settings.MQTT_BROKER_PORT = 1883
        mock_settings.MQTT_KEEPALIVE = 60
        mock_settings.MQTT_BROKER_USERNAME = "legacy-user"
        mock_settings.MQTT_BROKER_PASSWORD = "legacy-pass"  # nosec B105
        mock_settings.MQTT_CONNECT_RETRY_COOLDOWN_SECONDS = 30

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        get_mqtt_client()

        mock_client.username_pw_set.assert_called_once_with("legacy-user", "legacy-pass")

    @patch("paho.mqtt.client.Client")
    @patch("forgekey.tasks.settings")
    def test_get_mqtt_client_connection_error(self, mock_settings, mock_client_class):
        """Test MQTT client connection error handling."""
        from forgekey.tasks import _reset_mqtt_client_state_for_tests, get_mqtt_client

        _reset_mqtt_client_state_for_tests()

        mock_settings.MQTT_CLIENT_ID = "test-client"
        mock_settings.MQTT_BROKER_HOST = "localhost"
        mock_settings.MQTT_BROKER_PORT = 1883
        mock_settings.MQTT_KEEPALIVE = 60
        mock_settings.MQTT_BROKER_USERNAME = ""  # nosec B105
        mock_settings.MQTT_BROKER_PASSWORD = ""  # nosec B105
        mock_settings.MQTT_CONNECT_RETRY_COOLDOWN_SECONDS = 30

        mock_client = MagicMock()
        mock_client.connect.side_effect = Exception("Connection refused")
        mock_client_class.return_value = mock_client

        with pytest.raises(Exception, match="Connection refused"):
            get_mqtt_client()


@pytest.mark.django_db
class TestMQTTClientCooldown:
    """Circuit breaker around get_mqtt_client(): when the broker is unreachable,
    repeated calls within the cooldown window must short-circuit instead of
    instantiating a fresh paho.Client every time. Per-call instantiation under
    a broker outage was the root cause of the backend OOM (oms-9t2).
    """

    @patch("paho.mqtt.client.Client")
    @patch("forgekey.tasks.settings")
    def test_repeated_failures_inside_cooldown_do_not_reinstantiate(
        self, mock_settings, mock_client_class
    ):
        from forgekey.tasks import (
            MQTTConnectCooldown,
            _reset_mqtt_client_state_for_tests,
            get_mqtt_client,
        )

        _reset_mqtt_client_state_for_tests()

        mock_settings.MQTT_CLIENT_ID = "test-client"
        mock_settings.MQTT_BROKER_HOST = "localhost"
        mock_settings.MQTT_BROKER_PORT = 1883
        mock_settings.MQTT_KEEPALIVE = 60
        mock_settings.MQTT_BROKER_USERNAME = ""  # nosec B105
        mock_settings.MQTT_BROKER_PASSWORD = ""  # nosec B105
        mock_settings.MQTT_CONNECT_RETRY_COOLDOWN_SECONDS = 30

        mock_client = MagicMock()
        mock_client.connect.side_effect = Exception("Connection refused")
        mock_client_class.return_value = mock_client

        with pytest.raises(Exception, match="Connection refused"):
            get_mqtt_client()
        assert mock_client_class.call_count == 1

        # 50 more calls within the cooldown window should all raise the
        # cooldown sentinel WITHOUT instantiating another paho.Client. This is
        # the assertion that would have caught oms-9t2 in CI.
        for _ in range(50):
            with pytest.raises(MQTTConnectCooldown):
                get_mqtt_client()
        assert mock_client_class.call_count == 1, (
            "paho.Client must not be re-instantiated during cooldown — "
            "leaks here are what OOM'd the backend container."
        )

    @patch("paho.mqtt.client.Client")
    @patch("forgekey.tasks.settings")
    def test_failed_connect_releases_partial_client_resources(
        self, mock_settings, mock_client_class
    ):
        from forgekey.tasks import _reset_mqtt_client_state_for_tests, get_mqtt_client

        _reset_mqtt_client_state_for_tests()

        mock_settings.MQTT_CLIENT_ID = "test-client"
        mock_settings.MQTT_BROKER_HOST = "localhost"
        mock_settings.MQTT_BROKER_PORT = 1883
        mock_settings.MQTT_KEEPALIVE = 60
        mock_settings.MQTT_BROKER_USERNAME = ""  # nosec B105
        mock_settings.MQTT_BROKER_PASSWORD = ""  # nosec B105
        mock_settings.MQTT_CONNECT_RETRY_COOLDOWN_SECONDS = 30

        mock_client = MagicMock()
        mock_client.connect.side_effect = Exception("Connection refused")
        mock_client_class.return_value = mock_client

        with pytest.raises(Exception, match="Connection refused"):
            get_mqtt_client()

        # Even on failed connect we must call loop_stop + disconnect on the
        # half-built client so its background thread + socket are released.
        mock_client.loop_stop.assert_called_once()
        mock_client.disconnect.assert_called_once()

    @patch("paho.mqtt.client.Client")
    @patch("forgekey.tasks.settings")
    def test_cooldown_expiry_allows_retry(self, mock_settings, mock_client_class):
        from forgekey.tasks import (
            MQTTConnectCooldown,
            _reset_mqtt_client_state_for_tests,
            get_mqtt_client,
        )

        _reset_mqtt_client_state_for_tests()

        mock_settings.MQTT_CLIENT_ID = "test-client"
        mock_settings.MQTT_BROKER_HOST = "localhost"
        mock_settings.MQTT_BROKER_PORT = 1883
        mock_settings.MQTT_KEEPALIVE = 60
        mock_settings.MQTT_BROKER_USERNAME = ""  # nosec B105
        mock_settings.MQTT_BROKER_PASSWORD = ""  # nosec B105
        # Zero cooldown means each call gets through — proves the gate is
        # gated on the cooldown setting, not hard-coded.
        mock_settings.MQTT_CONNECT_RETRY_COOLDOWN_SECONDS = 0

        mock_client = MagicMock()
        mock_client.connect.side_effect = Exception("Connection refused")
        mock_client_class.return_value = mock_client

        with pytest.raises(Exception, match="Connection refused"):
            get_mqtt_client()
        with pytest.raises(Exception, match="Connection refused"):
            get_mqtt_client()
        # Two failed connects, two paho.Client instantiations — the cooldown
        # is what bounds this in production.
        assert mock_client_class.call_count == 2

        # And MQTTConnectCooldown is never raised because cooldown is 0.
        with pytest.raises(Exception) as exc_info:
            get_mqtt_client()
        assert not isinstance(exc_info.value, MQTTConnectCooldown)

    @patch("paho.mqtt.client.Client")
    @patch("forgekey.tasks.settings")
    def test_successful_connect_clears_cooldown(self, mock_settings, mock_client_class):
        from forgekey.tasks import _reset_mqtt_client_state_for_tests, get_mqtt_client

        _reset_mqtt_client_state_for_tests()

        mock_settings.MQTT_CLIENT_ID = "test-client"
        mock_settings.MQTT_BROKER_HOST = "localhost"
        mock_settings.MQTT_BROKER_PORT = 1883
        mock_settings.MQTT_KEEPALIVE = 60
        mock_settings.MQTT_BROKER_USERNAME = ""  # nosec B105
        mock_settings.MQTT_BROKER_PASSWORD = ""  # nosec B105
        mock_settings.MQTT_CONNECT_RETRY_COOLDOWN_SECONDS = 30

        mock_client = MagicMock()  # connect succeeds (no side_effect)
        mock_client_class.return_value = mock_client

        first = get_mqtt_client()
        # Singleton: subsequent calls return the same instance, no new
        # paho.Client instantiations, no cooldown checks at all.
        for _ in range(20):
            assert get_mqtt_client() is first
        assert mock_client_class.call_count == 1
        mock_client.connect.assert_called_once()
        mock_client.loop_start.assert_called_once()

    def test_process_mqtt_firmware_update_response_completed(self):
        """Test processing firmware update response - completed."""
        from forgekey.models import DeviceFirmwareUpdate
        from forgekey.tasks import process_mqtt_firmware_update_response

        device = ESP32DeviceFactory()
        firmware_version = FirmwareVersionFactory(device_type=device.device_type)
        update = DeviceFirmwareUpdateFactory(
            device=device,
            firmware_version=firmware_version,
            status=DeviceFirmwareUpdate.STATUS_PENDING,
        )

        process_mqtt_firmware_update_response(device.mac_address, str(update.id), "completed")

        update.refresh_from_db()
        device.refresh_from_db()
        assert update.status == DeviceFirmwareUpdate.STATUS_COMPLETED
        assert update.completed_at is not None
        assert device.firmware_version == firmware_version.version

    def test_process_mqtt_firmware_update_response_failed(self):
        """Test processing firmware update response - failed."""
        from forgekey.models import DeviceFirmwareUpdate
        from forgekey.tasks import process_mqtt_firmware_update_response

        device = ESP32DeviceFactory()
        firmware_version = FirmwareVersionFactory(device_type=device.device_type)
        update = DeviceFirmwareUpdateFactory(
            device=device,
            firmware_version=firmware_version,
            status=DeviceFirmwareUpdate.STATUS_PENDING,
        )

        process_mqtt_firmware_update_response(
            device.mac_address, str(update.id), "failed", error_message="Update failed"
        )

        update.refresh_from_db()
        assert update.status == DeviceFirmwareUpdate.STATUS_FAILED
        assert update.completed_at is not None
        assert update.error_message == "Update failed"

    def test_process_mqtt_firmware_update_response_unknown_device(self):
        """Test processing firmware update response from unknown device."""
        import uuid

        from forgekey.tasks import process_mqtt_firmware_update_response

        # Should not raise, just log warning
        process_mqtt_firmware_update_response("UNKNOWN:MAC:ADDRESS", str(uuid.uuid4()), "completed")

    def test_process_mqtt_firmware_update_response_unknown_update(self):
        """Test processing firmware update response for unknown update."""
        import uuid

        from forgekey.tasks import process_mqtt_firmware_update_response

        device = ESP32DeviceFactory()
        # Should not raise, just log warning
        process_mqtt_firmware_update_response(device.mac_address, str(uuid.uuid4()), "completed")

    def test_process_mqtt_firmware_update_response_exception(self):
        """Test processing firmware update response with exception."""
        import uuid

        from forgekey.tasks import process_mqtt_firmware_update_response

        device = ESP32DeviceFactory()
        # This should handle exceptions gracefully
        with patch.object(ESP32Device.objects, "get", side_effect=Exception("DB error")):
            process_mqtt_firmware_update_response(
                device.mac_address, str(uuid.uuid4()), "completed"
            )
