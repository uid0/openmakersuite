"""
Utility functions for ForgeKey.
"""

import time
from datetime import datetime, timezone
from typing import Dict, Optional

from django.conf import settings

import jwt

from .services.jwt_signing import JwtSigningError, get_jwt_public_key_pem, load_jwt_signing_key

SERVER_JWT_SUBJECT = "oms-backend"
# Refresh the cached server JWT this many seconds before its `exp`. Bounds the
# worst-case clock-skew window between OMS and EMQX.
_SERVER_JWT_REFRESH_BUFFER_SECONDS = 60


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


def _device_acl_claims(mac_address: str) -> Dict:
    """Build the per-device EMQX ACL claims for a given MAC.

    Grants pub/sub on the device's entire MAC-scoped namespace
    (``<prefix>/<mac>/#``). The MAC namespace is the security boundary —
    different MAC means different prefix, so cross-device traffic is still
    impossible. Wildcarding here means new per-device topics (capabilities,
    future telemetry streams) work without re-issuing JWTs or backend changes.
    """
    contract_mac = _firmware_contract_mac(mac_address)
    prefix = f"{settings.MQTT_TOPIC_PREFIX}/{contract_mac}"
    return {
        "pub": [f"{prefix}/#"],
        "sub": [f"{prefix}/#"],
    }


def generate_device_jwt(
    mac_address: str,
    payload: Optional[Dict] = None,
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
        "acl": _device_acl_claims(normalized_mac),
    }
    claims.update(payload)

    token = jwt.encode(
        claims,
        private_key,
        algorithm=settings.FORGEKEY_JWT_ALGORITHM,
        headers={"kid": settings.FORGEKEY_JWT_KEY_ID},
    )

    return token


_server_jwt_cache: Dict[str, object] = {"token": None, "cached_at": 0.0, "exp": 0}


def invalidate_server_jwt_cache() -> None:
    """Drop the cached server JWT so the next call re-signs.

    Called from the MQTT connect-failure path (oms-y5p AC-3): if EMQX rejects
    our credential we mint a fresh one rather than retrying with the same
    (possibly clock-skewed or expired) token.
    """
    _server_jwt_cache["token"] = None
    _server_jwt_cache["cached_at"] = 0.0
    _server_jwt_cache["exp"] = 0


def generate_server_jwt() -> str:
    """ES256-signed JWT for the OMS backend's own MQTT connection.

    Issued by the same signing key that signs device JWTs; EMQX verifies it via
    the JWKS endpoint with no extra configuration. Subject is ``oms-backend``
    and the ACL grants pub/sub on the entire ``MQTT_TOPIC_PREFIX`` namespace
    so the backend can dispatch commands to every device and consume status
    from any of them.

    Cached per-process for ``FORGEKEY_SERVER_JWT_CACHE_SECONDS`` so we don't
    re-sign on every MQTT connect, with a small refresh buffer before ``exp``
    to absorb clock skew.

    Raises:
        JwtSigningError: if ``FORGEKEY_JWT_SIGNING_KEY`` is unconfigured.
    """
    now = time.time()
    cached_token = _server_jwt_cache["token"]
    cached_at = float(_server_jwt_cache["cached_at"])
    cached_exp = int(_server_jwt_cache["exp"])
    cache_ttl = getattr(settings, "FORGEKEY_SERVER_JWT_CACHE_SECONDS", 60 * 60 * 24)
    if (
        cached_token
        and now - cached_at < cache_ttl
        and now < cached_exp - _SERVER_JWT_REFRESH_BUFFER_SECONDS
    ):
        return str(cached_token)

    private_key = load_jwt_signing_key()
    iat = int(now)
    exp = iat + getattr(settings, "FORGEKEY_SERVER_JWT_EXPIRATION_SECONDS", 60 * 60 * 24 * 365)
    claims = {
        "iss": settings.FORGEKEY_JWT_ISSUER,
        "aud": settings.FORGEKEY_JWT_AUDIENCE,
        "sub": SERVER_JWT_SUBJECT,
        "iat": iat,
        "exp": exp,
        "acl": {
            "pub": [f"{settings.MQTT_TOPIC_PREFIX}/#"],
            "sub": [f"{settings.MQTT_TOPIC_PREFIX}/#"],
        },
    }
    token = jwt.encode(
        claims,
        private_key,
        algorithm=settings.FORGEKEY_JWT_ALGORITHM,
        headers={"kid": settings.FORGEKEY_JWT_KEY_ID},
    )
    _server_jwt_cache["token"] = token
    _server_jwt_cache["cached_at"] = now
    _server_jwt_cache["exp"] = exp
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

    Uses the firmware provisioning contract MAC encoding (lowercase hex,
    no separators) — the same form the firmware subscribes / publishes
    on and the same form the JWT ACL grants. The previous
    ``UPPER-WITH-HYPHENS`` encoding silently broke command delivery
    because the device was never subscribed to that topic.

    Args:
        mac_address: MAC address of the device (any input format)
        topic_type: Type of topic (e.g., 'command', 'status', 'data')

    Returns:
        MQTT topic string, e.g. ``forgekey/aabbccddeeff/command``.
    """
    return f"{settings.MQTT_TOPIC_PREFIX}/{_firmware_contract_mac(mac_address)}/{topic_type}"


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
