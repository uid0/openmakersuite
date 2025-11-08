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
        import forgekey.tasks
        from forgekey.tasks import get_mqtt_client

        # Reset the global client
        forgekey.tasks._mqtt_client = None

        mock_settings.MQTT_CLIENT_ID = "test-client"
        mock_settings.MQTT_BROKER_HOST = "localhost"
        mock_settings.MQTT_BROKER_PORT = 1883
        mock_settings.MQTT_KEEPALIVE = 60
        mock_settings.MQTT_BROKER_USERNAME = ""
        mock_settings.MQTT_BROKER_PASSWORD = ""

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        client = get_mqtt_client()

        assert client is not None
        mock_client.connect.assert_called_once()
        mock_client.loop_start.assert_called_once()

    @patch("paho.mqtt.client.Client")
    @patch("forgekey.tasks.settings")
    def test_get_mqtt_client_with_auth(self, mock_settings, mock_client_class):
        """Test MQTT client creation with authentication."""
        import forgekey.tasks
        from forgekey.tasks import get_mqtt_client

        # Reset the global client
        forgekey.tasks._mqtt_client = None

        mock_settings.MQTT_CLIENT_ID = "test-client"
        mock_settings.MQTT_BROKER_HOST = "localhost"
        mock_settings.MQTT_BROKER_PORT = 1883
        mock_settings.MQTT_KEEPALIVE = 60
        mock_settings.MQTT_BROKER_USERNAME = "user"
        mock_settings.MQTT_BROKER_PASSWORD = "pass"

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        client = get_mqtt_client()

        assert client is not None
        mock_client.username_pw_set.assert_called_once_with("user", "pass")

    @patch("paho.mqtt.client.Client")
    @patch("forgekey.tasks.settings")
    def test_get_mqtt_client_connection_error(self, mock_settings, mock_client_class):
        """Test MQTT client connection error handling."""
        import forgekey.tasks
        from forgekey.tasks import get_mqtt_client

        # Reset the global client
        forgekey.tasks._mqtt_client = None

        mock_settings.MQTT_CLIENT_ID = "test-client"
        mock_settings.MQTT_BROKER_HOST = "localhost"
        mock_settings.MQTT_BROKER_PORT = 1883
        mock_settings.MQTT_KEEPALIVE = 60
        mock_settings.MQTT_BROKER_USERNAME = ""
        mock_settings.MQTT_BROKER_PASSWORD = ""

        mock_client = MagicMock()
        mock_client.connect.side_effect = Exception("Connection refused")
        mock_client_class.return_value = mock_client

        with pytest.raises(Exception, match="Connection refused"):
            get_mqtt_client()

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
