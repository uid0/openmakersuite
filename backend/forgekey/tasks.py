"""
Celery tasks for ForgeKey MQTT operations.
"""

import json
import logging
from typing import Any, Dict, Optional

from django.conf import settings
from django.utils import timezone

import paho.mqtt.client as mqtt
from celery import shared_task

from .models import ESP32Device, PowerMeterReading
from .utils import get_mqtt_command_topic, normalize_mac_address

logger = logging.getLogger(__name__)

# Global MQTT client instance (will be initialized on first use)
_mqtt_client: Optional[mqtt.Client] = None


def get_mqtt_client() -> mqtt.Client:
    """
    Get or create the MQTT client instance.
    This is a singleton pattern to reuse the same client across tasks.
    """
    global _mqtt_client

    if _mqtt_client is None:
        client = mqtt.Client(
            client_id=settings.MQTT_CLIENT_ID,
            protocol=mqtt.MQTTv5,
        )

        if settings.MQTT_BROKER_USERNAME:
            client.username_pw_set(
                settings.MQTT_BROKER_USERNAME,
                settings.MQTT_BROKER_PASSWORD,
            )

        def on_connect(client, userdata, flags, rc, properties=None):
            if rc == 0:
                logger.info("MQTT client connected successfully")
            else:
                logger.error(f"MQTT client connection failed with code {rc}")

        def on_disconnect(client, userdata, rc, properties=None):
            if rc != 0:
                logger.warning(f"MQTT client disconnected unexpectedly (rc={rc})")

        client.on_connect = on_connect
        client.on_disconnect = on_disconnect

        try:
            client.connect(
                settings.MQTT_BROKER_HOST,
                settings.MQTT_BROKER_PORT,
                settings.MQTT_KEEPALIVE,
            )
            client.loop_start()
            _mqtt_client = client
        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker: {e}")
            raise

    return _mqtt_client


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_mqtt_command(
    self, mac_address: str, command: str, payload: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Send a command to an ESP32 device via MQTT.

    Args:
        mac_address: MAC address of the target device
        command: Command name (e.g., 'enable', 'disable', 'status')
        payload: Additional command payload data

    Returns:
        Dictionary with result status
    """
    try:
        client = get_mqtt_client()
        topic = get_mqtt_command_topic(mac_address)

        message = {
            "command": command,
            "timestamp": timezone.now().isoformat(),
        }

        if payload:
            message.update(payload)

        message_json = json.dumps(message)
        result = client.publish(topic, message_json, qos=1)

        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            logger.info(f"Sent command '{command}' to device {mac_address}")
            return {
                "success": True,
                "mac_address": mac_address,
                "command": command,
                "topic": topic,
            }
        else:
            logger.error(f"Failed to send command to {mac_address}: MQTT error {result.rc}")
            raise Exception(f"MQTT publish failed with code {result.rc}")

    except Exception as e:
        logger.error(f"Error sending MQTT command to {mac_address}: {e}")
        raise self.retry(exc=e)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def enable_device(self, mac_address: str) -> Dict[str, Any]:
    """
    Enable an ESP32 device (turn on power, etc.).

    Args:
        mac_address: MAC address of the device to enable

    Returns:
        Result dictionary
    """
    return send_mqtt_command(mac_address, "enable")


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def disable_device(self, mac_address: str, delay_seconds: int = 0) -> Dict[str, Any]:
    """
    Disable an ESP32 device (turn off power, etc.).

    Args:
        mac_address: MAC address of the device to disable
        delay_seconds: Delay before disabling (for power-off delay)

    Returns:
        Result dictionary
    """
    payload = {"delay_seconds": delay_seconds} if delay_seconds > 0 else None
    return send_mqtt_command(mac_address, "disable", payload)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def request_device_status(self, mac_address: str) -> Dict[str, Any]:
    """
    Request status from an ESP32 device.

    Args:
        mac_address: MAC address of the device

    Returns:
        Result dictionary
    """
    return send_mqtt_command(mac_address, "status")


@shared_task
def process_mqtt_status_message(mac_address: str, message_data: Dict[str, Any]) -> None:
    """
    Process a status message received from an ESP32 device via MQTT.

    Args:
        mac_address: MAC address of the device
        message_data: Status message data
    """
    try:
        normalized_mac = normalize_mac_address(mac_address)
        device = ESP32Device.objects.get(mac_address=normalized_mac)

        # Update device online status and last seen
        device.is_online = message_data.get("online", True)
        device.last_seen = timezone.now()

        if "firmware_version" in message_data:
            device.firmware_version = message_data["firmware_version"]

        device.save(update_fields=["is_online", "last_seen", "firmware_version"])

        logger.info(f"Updated status for device {mac_address}")

    except ESP32Device.DoesNotExist:
        logger.warning(f"Received status message from unknown device: {mac_address}")
    except Exception as e:
        logger.error(f"Error processing status message from {mac_address}: {e}")


@shared_task
def process_mqtt_power_reading(
    mac_address: str, asset_id: str, reading_data: Dict[str, Any]
) -> None:
    """
    Process a power reading from an ESP32 power measurement device.

    Args:
        mac_address: MAC address of the power measurement device
        asset_id: UUID of the asset this reading is for
        reading_data: Power reading data (voltage, current, power, energy)
    """
    try:
        from inventory.models import Asset

        normalized_mac = normalize_mac_address(mac_address)
        device = ESP32Device.objects.get(mac_address=normalized_mac)
        asset = Asset.objects.get(id=asset_id)

        PowerMeterReading.objects.create(
            device=device,
            asset=asset,
            voltage=reading_data.get("voltage"),
            current=reading_data.get("current"),
            power=reading_data.get("power"),
            energy=reading_data.get("energy"),
        )

        logger.info(f"Recorded power reading for asset {asset_id} from device {mac_address}")

    except ESP32Device.DoesNotExist:
        logger.warning(f"Received power reading from unknown device: {mac_address}")
    except Asset.DoesNotExist:
        logger.warning(f"Received power reading for unknown asset: {asset_id}")
    except Exception as e:
        logger.error(f"Error processing power reading from {mac_address}: {e}")


@shared_task
def process_mqtt_firmware_update_response(
    mac_address: str, update_id: str, status: str, error_message: Optional[str] = None
) -> None:
    """
    Process a firmware update response from an ESP32 device.

    Args:
        mac_address: MAC address of the device
        update_id: UUID of the firmware update record
        status: Update status ('completed', 'failed', etc.)
        error_message: Error message if update failed
    """
    try:
        from .models import DeviceFirmwareUpdate

        normalized_mac = normalize_mac_address(mac_address)
        device = ESP32Device.objects.get(mac_address=normalized_mac)
        update = DeviceFirmwareUpdate.objects.get(id=update_id, device=device)

        if status == "completed":
            update.status = DeviceFirmwareUpdate.STATUS_COMPLETED
            update.completed_at = timezone.now()
            device.firmware_version = update.firmware_version.version
            device.save(update_fields=["firmware_version"])
        elif status == "failed":
            update.status = DeviceFirmwareUpdate.STATUS_FAILED
            update.completed_at = timezone.now()
            if error_message:
                update.error_message = error_message

        update.save()

        logger.info(f"Updated firmware update {update_id} status to {status}")

    except ESP32Device.DoesNotExist:
        logger.warning(f"Received firmware update response from unknown device: {mac_address}")
    except DeviceFirmwareUpdate.DoesNotExist:
        logger.warning(f"Received firmware update response for unknown update: {update_id}")
    except Exception as e:
        logger.error(f"Error processing firmware update response from {mac_address}: {e}")
