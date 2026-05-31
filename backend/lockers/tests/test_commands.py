"""Tests for `lockers.services.commands` (Phase 3 publishers)."""

from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import MagicMock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import override_settings
from django.utils import timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from forgekey.models import DeviceType, ESP32Device
from forgekey.services.jwt_signing import generate_jwt_signing_keypair
from inventory.tests.factories import LocationFactory
from lockers.models import Locker, LockerDevice, LockerOtp
from lockers.services.commands import (
    NoSuchLockerDevice,
    publish_clear_lockout,
    publish_init_ack,
    publish_lockout,
    publish_unlock,
)

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture
def keypair():
    return generate_jwt_signing_keypair()


@pytest.fixture
def signed_settings(keypair):
    private_pem, _ = keypair
    with override_settings(
        FORGEKEY_JWT_SIGNING_KEY=private_pem,
        FORGEKEY_JWT_KEY_ID="test-kid",
    ):
        yield


@pytest.fixture
def locker_with_latch():
    sig = Group.objects.create(name="Wood Shop SIG")
    locker = Locker.objects.create(
        name="L-1", slug="l-1", location=LocationFactory(), owning_sig=sig
    )
    latch_type, _ = DeviceType.objects.get_or_create(
        code="locker_latch", defaults={"name": "Locker latch controller"}
    )
    device = ESP32Device.objects.create(mac_address="AA:BB:CC:11:22:33", device_type=latch_type)
    LockerDevice.objects.create(
        locker=locker, device=device, role=LockerDevice.ROLE_LATCH, is_primary=True
    )
    return locker, device


@pytest.fixture
def staff():
    return User.objects.create_user(
        username="admin", email="a@example.com", password="x" * 24, is_staff=True
    )


def _published_payload(client_mock):
    """Pull the JSON body out of `client.publish.call_args`."""
    args, _ = client_mock.publish.call_args
    _, body, *_rest = args
    return json.loads(body)


def _verify_es256_jwt(token: str, public_pem: str) -> dict:
    """Minimal ES256 verifier — fail loudly if signature doesn't match."""
    import base64

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric.utils import (
        encode_dss_signature,
    )

    header_b64, payload_b64, signature_b64 = token.split(".")
    pad = "=" * (-len(signature_b64) % 4)
    signature = base64.urlsafe_b64decode(signature_b64 + pad)
    r = int.from_bytes(signature[:32], "big")
    s = int.from_bytes(signature[32:], "big")
    der = encode_dss_signature(r, s)
    pub = serialization.load_pem_public_key(public_pem.encode("ascii"))
    pub.verify(der, f"{header_b64}.{payload_b64}".encode("ascii"), ec.ECDSA(hashes.SHA256()))
    pad2 = "=" * (-len(payload_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(payload_b64 + pad2))


class TestPublishUnlock:
    def test_publishes_jwt_and_unlock_cmd(self, locker_with_latch, signed_settings, keypair, staff):
        locker, device = locker_with_latch
        client = MagicMock()
        client.publish.return_value = MagicMock(rc=0)
        topic = publish_unlock(locker=locker, actor=staff, client=client)
        # The command topic uses the firmware-contract MAC encoding
        # (lowercase hex, no separators) — see forgekey.utils.get_mqtt_topic.
        assert device.mac_address.replace(":", "").lower() in topic
        body = _published_payload(client)
        assert body["cmd"] == "unlock"
        # JWT verifies + carries (mac, cmd).
        _, public_pem = keypair
        payload = _verify_es256_jwt(body["jwt"], public_pem)
        assert payload["mac"] == device.mac_address
        assert payload["cmd"] == "unlock"

    def test_carries_otp_id_when_supplied(self, locker_with_latch, signed_settings, staff):
        locker, _ = locker_with_latch
        otp = LockerOtp.objects.create(
            locker=locker,
            requesting_user=staff,
            code="123456",
            expires_at=timezone.now() + timedelta(minutes=30),
        )
        client = MagicMock()
        client.publish.return_value = MagicMock(rc=0)
        publish_unlock(locker=locker, otp=otp, actor=staff, client=client)
        assert _published_payload(client)["otp_id"] == str(otp.id)

    def test_no_latch_role_raises(self, signed_settings):
        sig = Group.objects.create(name="Empty SIG")
        locker = Locker.objects.create(
            name="naked", slug="naked", location=LocationFactory(), owning_sig=sig
        )
        with pytest.raises(NoSuchLockerDevice):
            publish_unlock(locker=locker, client=MagicMock())


class TestPublishLockout:
    def test_publishes_lockout_with_reason(self, locker_with_latch, signed_settings, staff):
        locker, _ = locker_with_latch
        client = MagicMock()
        client.publish.return_value = MagicMock(rc=0)
        publish_lockout(locker=locker, reason="tamper detected", actor=staff, client=client)
        body = _published_payload(client)
        assert body["cmd"] == "lockout"
        assert body["reason"] == "tamper detected"
        assert body["jwt"]


class TestPublishClearLockout:
    def test_publishes_clear_lockout(self, locker_with_latch, signed_settings, staff):
        locker, _ = locker_with_latch
        client = MagicMock()
        client.publish.return_value = MagicMock(rc=0)
        publish_clear_lockout(locker=locker, actor=staff, client=client)
        assert _published_payload(client)["cmd"] == "clear_lockout"


class TestPublishInitAck:
    def test_publishes_init_ack(self, locker_with_latch, signed_settings):
        locker, _ = locker_with_latch
        client = MagicMock()
        client.publish.return_value = MagicMock(rc=0)
        publish_init_ack(locker=locker, client=client)
        assert _published_payload(client)["cmd"] == "init_ack"
