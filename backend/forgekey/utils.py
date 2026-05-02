"""
Utility functions for ForgeKey.
"""

from datetime import datetime, timezone
from typing import Dict, Optional

from django.conf import settings

import jwt

from .services.jwt_signing import JwtSigningError, get_jwt_public_key_pem, load_jwt_signing_key


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


def _device_acl_claims(mac_address: str, sensor_kind: Optional[str] = None) -> Dict:
    """Build the per-device EMQX ACL claims for a given MAC.

    Returns a least-privilege grant: the device may publish only to its own
    telemetry / status / OTA-status / firmware-response topics, and subscribe
    only to its own command / firmware / config / OTA-trigger topics. No
    wildcards, no cross-device traffic.
    """
    contract_mac = _firmware_contract_mac(mac_address)
    prefix = f"{settings.MQTT_TOPIC_PREFIX}/{contract_mac}"
    pub_topics = [
        f"{prefix}/status",
        f"{prefix}/firmware/response",
        f"{prefix}/ota/status",
    ]
    # Sensor-kind-specific occupancy stream. Devices typically know one kind
    # but for first-registration calls where sensor_kind is unknown, we grant
    # both well-known occupancy topics so the device can pick.
    kind = normalize_sensor_kind(sensor_kind) if sensor_kind else ""
    if kind:
        pub_topics.insert(0, f"{prefix}/{kind}/occupancy")
    else:
        pub_topics = [
            f"{prefix}/people_counter/occupancy",
            f"{prefix}/door_counter/occupancy",
        ] + pub_topics
    sub_topics = [
        f"{prefix}/command",
        f"{prefix}/firmware",
        f"{prefix}/config",
        f"{prefix}/ota/trigger",
    ]
    return {"pub": pub_topics, "sub": sub_topics}


def generate_device_jwt(
    mac_address: str,
    payload: Optional[Dict] = None,
    sensor_kind: Optional[str] = None,
) -> str:
    """
    Generate an ES256-signed JWT for an ESP32 device.

    The token is verifiable by EMQX via the JWKS endpoint
    (``/api/forgekey/jwks/``). Claims include standard registered claims
    (``iss``, ``aud``, ``sub``, ``iat``, ``exp``) plus ``mac`` and an ``acl``
    claim that EMQX maps to per-device pub/sub permissions.

    Args:
        mac_address: MAC address of the device (any format).
        payload: Extra claims to merge into the token.
        sensor_kind: Optional device-type code; narrows the ``acl.pub`` grant
            to only the matching occupancy topic when known.

    Returns:
        Compact-serialized JWT string.

    Raises:
        JwtSigningError: if ``FORGEKEY_JWT_SIGNING_KEY`` is unconfigured or
            unparseable.
    """
    private_key = load_jwt_signing_key()
    normalized_mac = normalize_mac_address(mac_address)

    if payload is None:
        payload = {}

    now = datetime.now(timezone.utc)
    claims = {
        "iss": settings.FORGEKEY_JWT_ISSUER,
        "aud": settings.FORGEKEY_JWT_AUDIENCE,
        "sub": normalized_mac,
        "mac": normalized_mac,
        "iat": int(now.timestamp()),
        "exp": int(now.timestamp()) + settings.FORGEKEY_JWT_EXPIRATION_SECONDS,
        "acl": _device_acl_claims(normalized_mac, sensor_kind=sensor_kind),
    }
    claims.update(payload)

    token = jwt.encode(
        claims,
        private_key,
        algorithm=settings.FORGEKEY_JWT_ALGORITHM,
        headers={"kid": settings.FORGEKEY_JWT_KEY_ID},
    )

    return token


def verify_device_jwt(token: str, mac_address: str) -> Optional[Dict]:
    """
    Verify a device JWT and confirm it is bound to ``mac_address``.

    Args:
        token: Compact JWT string.
        mac_address: Expected MAC address (any format).

    Returns:
        Decoded claim dict on success, ``None`` on any signature, expiry,
        audience, issuer, or MAC-binding failure.
    """
    try:
        public_pem = get_jwt_public_key_pem()
    except JwtSigningError:
        return None
    try:
        payload = jwt.decode(
            token,
            public_pem,
            algorithms=[settings.FORGEKEY_JWT_ALGORITHM],
            audience=settings.FORGEKEY_JWT_AUDIENCE,
            issuer=settings.FORGEKEY_JWT_ISSUER,
        )
    except jwt.InvalidTokenError:
        return None
    expected_mac = normalize_mac_address(mac_address)
    if payload.get("mac") != expected_mac:
        return None
    return payload


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


def get_mqtt_ota_trigger_topic(mac_address: str) -> str:
    """MQTT topic the firmware listens on for OTA download triggers.

    Firmware contract: ``forgekey/<lowercase-no-sep-mac>/ota/trigger``.
    """
    return f"{settings.MQTT_TOPIC_PREFIX}/{_firmware_contract_mac(mac_address)}/ota/trigger"


def get_mqtt_ota_status_topic(mac_address: str) -> str:
    """MQTT topic the firmware publishes OTA progress / completion to.

    Firmware contract: ``forgekey/<lowercase-no-sep-mac>/ota/status``.
    """
    return f"{settings.MQTT_TOPIC_PREFIX}/{_firmware_contract_mac(mac_address)}/ota/status"
