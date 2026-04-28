"""
Utility functions for ForgeKey.
"""

import hashlib
from datetime import datetime, timezone
from typing import Dict, Optional

from django.conf import settings

import jwt


def normalize_mac_address(mac_address: str) -> str:
    """
    Normalize MAC address to uppercase with colons.

    Args:
        mac_address: MAC address in any format

    Returns:
        Normalized MAC address (XX:XX:XX:XX:XX:XX)
    """
    # Remove any separators and convert to uppercase
    mac = mac_address.replace("-", "").replace(":", "").upper()
    # Add colons every 2 characters
    return ":".join(mac[i : i + 2] for i in range(0, len(mac), 2))


def generate_device_jwt(mac_address: str, payload: Optional[Dict] = None) -> str:
    """
    Generate a JWT token for an ESP32 device based on its MAC address.

    Args:
        mac_address: MAC address of the device
        payload: Additional payload data to include in the token

    Returns:
        JWT token string
    """
    secret = _get_device_secret(mac_address)

    if payload is None:
        payload = {}

    now = datetime.now(timezone.utc)
    payload.update(
        {
            "mac": normalize_mac_address(mac_address),
            "iat": int(now.timestamp()),
            "exp": int(now.timestamp()) + settings.FORGEKEY_JWT_EXPIRATION_SECONDS,
        }
    )

    token = jwt.encode(
        payload,
        secret,
        algorithm=settings.FORGEKEY_JWT_ALGORITHM,
    )

    return token


def verify_device_jwt(token: str, mac_address: str) -> Optional[Dict]:
    """
    Verify a JWT token from an ESP32 device.

    Args:
        token: JWT token string
        mac_address: Expected MAC address of the device

    Returns:
        Decoded payload if valid, None otherwise
    """
    try:
        secret = _get_device_secret(mac_address)
        payload = jwt.decode(
            token,
            secret,
            algorithms=[settings.FORGEKEY_JWT_ALGORITHM],
        )
        return payload
    except jwt.InvalidTokenError:
        return None


def _get_device_secret(mac_address: str) -> str:
    """
    Generate a secret for a device based on its MAC address and shared secret.

    Args:
        mac_address: MAC address of the device

    Returns:
        Secret string for JWT signing
    """
    normalized_mac = mac_address.upper().replace("-", ":")
    message = f"{normalized_mac}:{settings.FORGEKEY_SHARED_SECRET}"
    return hashlib.sha256(message.encode()).hexdigest()


def get_mqtt_topic(mac_address: str, topic_type: str) -> str:
    """
    Generate MQTT topic for a device.

    Args:
        mac_address: MAC address of the device
        topic_type: Type of topic (e.g., 'command', 'status', 'data')

    Returns:
        MQTT topic string
    """
    normalized_mac = normalize_mac_address(mac_address).replace(":", "-")
    return f"{settings.MQTT_TOPIC_PREFIX}/{normalized_mac}/{topic_type}"


def get_mqtt_command_topic(mac_address: str) -> str:
    """Get MQTT topic for sending commands to a device."""
    return get_mqtt_topic(mac_address, "command")


def get_mqtt_status_topic(mac_address: str) -> str:
    """Get MQTT topic for receiving status from a device."""
    return get_mqtt_topic(mac_address, "status")


def get_mqtt_data_topic(mac_address: str) -> str:
    """Get MQTT topic for receiving data from a device."""
    return get_mqtt_topic(mac_address, "data")


def _firmware_contract_mac(mac_address: str) -> str:
    """MAC formatted per the firmware provisioning contract: lowercase hex, no separators."""
    return normalize_mac_address(mac_address).replace(":", "").lower()


def normalize_sensor_kind(sensor_kind: str) -> str:
    """Normalize sensor_kind to the DeviceType.code form (hyphens → underscores)."""
    return (sensor_kind or "").strip().replace("-", "_").lower()


def get_mqtt_firmware_topic(mac_address: str) -> str:
    """MQTT topic the firmware subscribes to for OTA advertisements.

    Firmware contract: ``forgekey/<lowercase-no-sep-mac>/firmware``.
    """
    return f"{settings.MQTT_TOPIC_PREFIX}/{_firmware_contract_mac(mac_address)}/firmware"


def get_mqtt_ping_topic(mac_address: str, sensor_kind: str) -> str:
    """MQTT topic the firmware publishes sensor pings to.

    Firmware contract: ``forgekey/<lowercase-no-sep-mac>/<sensor_kind>/occupancy``.
    """
    kind = normalize_sensor_kind(sensor_kind)
    return f"{settings.MQTT_TOPIC_PREFIX}/{_firmware_contract_mac(mac_address)}/{kind}/occupancy"
