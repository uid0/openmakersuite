"""
Tests for ForgeKey utility functions.
"""

import base64
import hashlib
import hmac
import json

from django.conf import settings

import jwt
import pytest

from forgekey.utils import (
    generate_device_jwt,
    get_mqtt_command_topic,
    get_mqtt_data_topic,
    get_mqtt_status_topic,
    normalize_mac_address,
    verify_device_jwt,
)


@pytest.mark.unit
class TestMACAddressNormalization:
    """Tests for MAC address normalization."""

    def test_normalize_with_colons(self):
        """Test normalizing MAC address with colons."""
        result = normalize_mac_address("AA:BB:CC:DD:EE:FF")
        assert result == "AA:BB:CC:DD:EE:FF"

    def test_normalize_with_dashes(self):
        """Test normalizing MAC address with dashes."""
        result = normalize_mac_address("aa-bb-cc-dd-ee-ff")
        assert result == "AA:BB:CC:DD:EE:FF"

    def test_normalize_no_separators(self):
        """Test normalizing MAC address without separators."""
        result = normalize_mac_address("aabbccddeeff")
        assert result == "AA:BB:CC:DD:EE:FF"

    def test_normalize_mixed_case(self):
        """Test normalizing MAC address with mixed case."""
        result = normalize_mac_address("Aa:Bb:Cc:Dd:Ee:Ff")
        assert result == "AA:BB:CC:DD:EE:FF"


@pytest.mark.unit
class TestMQTTTopics:
    """Tests for MQTT topic generation."""

    def test_command_topic(self):
        """Test command topic generation."""
        topic = get_mqtt_command_topic("AA:BB:CC:DD:EE:FF")
        assert topic == f"{settings.MQTT_TOPIC_PREFIX}/AA-BB-CC-DD-EE-FF/command"

    def test_status_topic(self):
        """Test status topic generation."""
        topic = get_mqtt_status_topic("AA:BB:CC:DD:EE:FF")
        assert topic == f"{settings.MQTT_TOPIC_PREFIX}/AA-BB-CC-DD-EE-FF/status"

    def test_data_topic(self):
        """Test data topic generation."""
        topic = get_mqtt_data_topic("AA:BB:CC:DD:EE:FF")
        assert topic == f"{settings.MQTT_TOPIC_PREFIX}/AA-BB-CC-DD-EE-FF/data"


@pytest.mark.unit
class TestJWTGeneration:
    """Tests for JWT token generation and verification."""

    def test_generate_jwt(self):
        """Test generating a JWT token."""
        mac_address = "AA:BB:CC:DD:EE:FF"
        token = generate_device_jwt(mac_address)
        assert token is not None
        assert isinstance(token, str)

    def test_verify_valid_jwt(self):
        """Test verifying a valid JWT token."""
        mac_address = "AA:BB:CC:DD:EE:FF"
        token = generate_device_jwt(mac_address)
        payload = verify_device_jwt(token, mac_address)
        assert payload is not None
        assert payload["mac"] == normalize_mac_address(mac_address)

    def test_verify_invalid_jwt(self):
        """Test verifying an invalid JWT token."""
        mac_address = "AA:BB:CC:DD:EE:FF"
        invalid_token = "invalid.token.here"  # nosec B105
        payload = verify_device_jwt(invalid_token, mac_address)
        assert payload is None

    def test_verify_wrong_mac(self):
        """Test verifying JWT with wrong MAC address."""
        mac_address = "AA:BB:CC:DD:EE:FF"
        token = generate_device_jwt(mac_address)
        wrong_mac = "11:22:33:44:55:66"
        payload = verify_device_jwt(token, wrong_mac)
        assert payload is None

    def test_jwt_with_payload(self):
        """Test generating JWT with additional payload."""
        mac_address = "AA:BB:CC:DD:EE:FF"
        token = generate_device_jwt(mac_address, {"custom_field": "custom_value"})
        payload = verify_device_jwt(token, mac_address)
        assert payload is not None
        assert payload["custom_field"] == "custom_value"


@pytest.mark.unit
class TestJWTCritHeaderValidation:
    """Regression tests for RFC 7515 §4.1.11 `crit` header validation.

    PyJWT < 2.12.0 accepted tokens whose `crit` array listed extensions the
    library did not understand, violating the RFC's MUST-reject requirement
    (same class as CVE-2025-59420). This verifies the upgrade still enforces
    rejection (oms-6s5).
    """

    @staticmethod
    def _b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    def _craft_token(self, secret: str, header: dict, payload: dict) -> str:
        h = self._b64url(json.dumps(header, separators=(",", ":")).encode())
        p = self._b64url(json.dumps(payload, separators=(",", ":")).encode())
        signing_input = f"{h}.{p}".encode()
        sig = self._b64url(hmac.new(secret.encode(), signing_input, hashlib.sha256).digest())
        return f"{h}.{p}.{sig}"

    def test_unknown_crit_extension_rejected(self):
        """Token with crit=['unknown-ext'] must raise InvalidTokenError."""
        secret = "a" * 64
        token = self._craft_token(
            secret,
            header={
                "alg": "HS256",
                "crit": ["x-custom-policy"],
                "x-custom-policy": "require-mfa",
            },
            payload={"sub": "attacker", "role": "admin"},
        )
        with pytest.raises(jwt.InvalidTokenError):
            jwt.decode(token, secret, algorithms=["HS256"])

    def test_device_jwt_rejects_unknown_crit(self):
        """verify_device_jwt must return None for tokens with unknown crit."""
        mac_address = "AA:BB:CC:DD:EE:FF"
        normalized_mac = normalize_mac_address(mac_address)
        message = f"{normalized_mac}:{settings.FORGEKEY_SHARED_SECRET}"
        device_secret = hashlib.sha256(message.encode()).hexdigest()

        token = self._craft_token(
            device_secret,
            header={
                "alg": settings.FORGEKEY_JWT_ALGORITHM,
                "crit": ["x-custom-policy"],
                "x-custom-policy": "require-mfa",
            },
            payload={"mac": normalized_mac, "sub": "attacker"},
        )
        assert verify_device_jwt(token, mac_address) is None
