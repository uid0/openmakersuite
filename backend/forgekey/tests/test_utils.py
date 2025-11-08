"""
Tests for ForgeKey utility functions.
"""

from django.conf import settings

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
        invalid_token = "invalid.token.here"
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
