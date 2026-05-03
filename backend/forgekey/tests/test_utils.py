"""
Tests for ForgeKey utility functions.
"""

import base64
import json

from django.conf import settings

import jwt
import pytest

from forgekey.services.jwt_signing import (
    JwtSigningError,
    generate_jwt_signing_keypair,
    get_jwt_public_key_pem,
)
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
        result = normalize_mac_address("AA:BB:CC:DD:EE:FF")
        assert result == "AA:BB:CC:DD:EE:FF"

    def test_normalize_with_dashes(self):
        result = normalize_mac_address("aa-bb-cc-dd-ee-ff")
        assert result == "AA:BB:CC:DD:EE:FF"

    def test_normalize_no_separators(self):
        result = normalize_mac_address("aabbccddeeff")
        assert result == "AA:BB:CC:DD:EE:FF"

    def test_normalize_mixed_case(self):
        result = normalize_mac_address("Aa:Bb:Cc:Dd:Ee:Ff")
        assert result == "AA:BB:CC:DD:EE:FF"


@pytest.mark.unit
class TestMQTTTopics:
    """Tests for MQTT topic generation."""

    def test_command_topic(self):
        topic = get_mqtt_command_topic("AA:BB:CC:DD:EE:FF")
        assert topic == f"{settings.MQTT_TOPIC_PREFIX}/AA-BB-CC-DD-EE-FF/command"

    def test_status_topic(self):
        topic = get_mqtt_status_topic("AA:BB:CC:DD:EE:FF")
        assert topic == f"{settings.MQTT_TOPIC_PREFIX}/AA-BB-CC-DD-EE-FF/status"

    def test_data_topic(self):
        topic = get_mqtt_data_topic("AA:BB:CC:DD:EE:FF")
        assert topic == f"{settings.MQTT_TOPIC_PREFIX}/AA-BB-CC-DD-EE-FF/data"


@pytest.mark.unit
class TestJWTGeneration:
    """Tests for ES256 device JWT generation and verification (oms-6h1)."""

    MAC = "AA:BB:CC:DD:EE:FF"

    def test_generate_jwt_returns_compact_es256_token(self):
        token = generate_device_jwt(self.MAC)
        assert isinstance(token, str)
        # Compact JWT: header.payload.signature
        assert token.count(".") == 2
        header_b64 = token.split(".")[0]
        header_b64 += "=" * (-len(header_b64) % 4)
        header = json.loads(base64.urlsafe_b64decode(header_b64))
        assert header["alg"] == "ES256"
        assert header["kid"] == settings.FORGEKEY_JWT_KEY_ID

    def test_generated_token_carries_required_claims(self):
        token = generate_device_jwt(self.MAC)
        payload = verify_device_jwt(token, self.MAC)
        assert payload is not None
        assert payload["iss"] == settings.FORGEKEY_JWT_ISSUER
        assert payload["aud"] == settings.FORGEKEY_JWT_AUDIENCE
        assert payload["sub"] == normalize_mac_address(self.MAC)
        assert payload["mac"] == normalize_mac_address(self.MAC)
        assert "iat" in payload and "exp" in payload
        assert payload["exp"] > payload["iat"]

    def test_acl_claim_grants_per_mac_wildcard(self):
        token = generate_device_jwt(self.MAC)
        payload = verify_device_jwt(token, self.MAC)
        contract_mac = "aabbccddeeff"
        prefix = f"{settings.MQTT_TOPIC_PREFIX}/{contract_mac}"
        acl = payload["acl"]
        # Per-MAC wildcard: device may pub/sub anywhere under its own MAC
        # namespace, including future topics (e.g. /capabilities) without
        # requiring backend changes. The MAC namespace is the security
        # boundary — different MAC means different prefix.
        assert acl == {
            "pub": [f"{prefix}/#"],
            "sub": [f"{prefix}/#"],
        }

    def test_verify_invalid_jwt(self):
        invalid = "invalid.token.here"  # nosec B105 — fixed test sentinel
        assert verify_device_jwt(invalid, self.MAC) is None

    def test_verify_wrong_mac(self):
        token = generate_device_jwt(self.MAC)
        assert verify_device_jwt(token, "11:22:33:44:55:66") is None

    def test_token_signed_with_other_key_is_rejected(self):
        token = generate_device_jwt(self.MAC)
        # Resign with a different keypair → signature must not verify.
        other_priv, _ = generate_jwt_signing_keypair()
        forged = jwt.encode(
            jwt.decode(token, options={"verify_signature": False}),
            other_priv,
            algorithm="ES256",
        )
        assert verify_device_jwt(forged, self.MAC) is None

    def test_extra_payload_is_merged(self):
        token = generate_device_jwt(self.MAC, payload={"custom_field": "x"})
        payload = verify_device_jwt(token, self.MAC)
        assert payload["custom_field"] == "x"

    def test_signing_unconfigured_raises(self, settings):
        settings.FORGEKEY_JWT_SIGNING_KEY = ""
        with pytest.raises(JwtSigningError):
            generate_device_jwt(self.MAC)

    def test_verify_returns_none_when_unconfigured(self, settings):
        token = generate_device_jwt(self.MAC)
        settings.FORGEKEY_JWT_SIGNING_KEY = ""
        assert verify_device_jwt(token, self.MAC) is None

    def test_es256_verifies_against_advertised_public_key(self):
        token = generate_device_jwt(self.MAC)
        # Use the public key directly (the path EMQX takes via JWKS).
        public_pem = get_jwt_public_key_pem()
        payload = jwt.decode(
            token,
            public_pem,
            algorithms=["ES256"],
            audience=settings.FORGEKEY_JWT_AUDIENCE,
            issuer=settings.FORGEKEY_JWT_ISSUER,
        )
        assert payload["mac"] == normalize_mac_address(self.MAC)
