"""
Tests for ECDSA(P-256) firmware signing (oms-0xq).

Covers:
  - sign_firmware_bytes / verify_firmware_signature roundtrip
  - FirmwareVersion.save() populates signature when key is configured
  - publish_firmware_update payload always includes a `signature` field
  - backfill_firmware_signatures management command (idempotent)
  - rotate_provisioning_token publishes to forgekey/<mac>/config
  - check_firmware_signing_key system check (production warnings)
"""

from __future__ import annotations

import base64
import json
from io import StringIO
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from forgekey.checks import W_DEFAULT, W_INVALID, W_MISSING, check_firmware_signing_key
from forgekey.models import DeviceType, FirmwareVersion
from forgekey.services import firmware_dispatch
from forgekey.services.firmware_signing import (
    FirmwareSigningError,
    get_public_key_pem,
    is_signing_configured,
    load_signing_key,
    sign_firmware_bytes,
    verify_firmware_signature,
)
from forgekey.tests.factories import DeviceTypeFactory, ESP32DeviceFactory, FirmwareVersionFactory

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_keypair() -> tuple[str, str]:
    """Return (private_pem, public_pem) for a fresh P-256 keypair."""
    private = ec.generate_private_key(ec.SECP256R1())
    private_pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_pem = (
        private.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    return private_pem, public_pem


@pytest.fixture
def signing_keypair(settings):
    private_pem, public_pem = _make_keypair()
    settings.FORGEKEY_FIRMWARE_SIGNING_KEY = private_pem
    return private_pem, public_pem


@pytest.fixture
def people_counter_type():
    return DeviceTypeFactory(code=DeviceType.TYPE_PEOPLE_COUNTER)


def _firmware_file(content: bytes = b"\x00\x01\x02firmware-bytes", name: str = "fw.bin"):
    return SimpleUploadedFile(name=name, content=content, content_type="application/octet-stream")


# ---------------------------------------------------------------------------
# Pure crypto roundtrip
# ---------------------------------------------------------------------------


class TestSignVerifyRoundtrip:
    def test_sign_then_verify_succeeds(self, signing_keypair):
        _, public_pem = signing_keypair
        data = b"the firmware binary contents"

        signature = sign_firmware_bytes(data)

        # base64-encoded DER
        raw = base64.b64decode(signature)
        assert raw  # non-empty
        assert verify_firmware_signature(data, signature, public_pem) is True

    def test_verify_rejects_tampered_payload(self, signing_keypair):
        _, public_pem = signing_keypair
        data = b"original payload"
        signature = sign_firmware_bytes(data)

        assert verify_firmware_signature(b"tampered payload", signature, public_pem) is False

    def test_verify_rejects_wrong_key(self, signing_keypair):
        data = b"original payload"
        signature = sign_firmware_bytes(data)

        # New unrelated public key
        _, other_public = _make_keypair()
        assert verify_firmware_signature(data, signature, other_public) is False

    def test_sign_without_key_raises(self, settings):
        settings.FORGEKEY_FIRMWARE_SIGNING_KEY = ""
        assert is_signing_configured() is False
        with pytest.raises(FirmwareSigningError):
            sign_firmware_bytes(b"data")

    def test_load_rejects_non_p256_key(self, settings):
        # secp384r1 keypair — should be rejected.
        bad = ec.generate_private_key(ec.SECP384R1())
        settings.FORGEKEY_FIRMWARE_SIGNING_KEY = bad.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("ascii")
        with pytest.raises(FirmwareSigningError):
            load_signing_key()

    def test_get_public_key_pem_matches_private(self, signing_keypair):
        _, public_pem = signing_keypair
        derived = get_public_key_pem()
        # Both PEM-encoded SubjectPublicKeyInfo for the same key.
        assert derived.strip() == public_pem.strip()


# ---------------------------------------------------------------------------
# Model: signature persisted on save()
# ---------------------------------------------------------------------------


class TestFirmwareVersionAutoSign:
    def test_save_populates_signature_when_key_configured(
        self, signing_keypair, people_counter_type
    ):
        _, public_pem = signing_keypair
        binary = b"esp32-firmware-payload"

        fw = FirmwareVersion.objects.create(
            device_type=people_counter_type,
            version="9.0.0",
            firmware_file=_firmware_file(binary),
            signature="",  # explicitly empty so save() fills it
        )

        assert fw.signature
        assert verify_firmware_signature(binary, fw.signature, public_pem) is True

    def test_save_skips_signing_when_key_missing(self, settings, people_counter_type):
        settings.FORGEKEY_FIRMWARE_SIGNING_KEY = ""
        fw = FirmwareVersion.objects.create(
            device_type=people_counter_type,
            version="9.0.1",
            firmware_file=_firmware_file(b"unsigned"),
            signature="",
        )
        assert fw.signature == ""

    def test_save_does_not_overwrite_existing_signature(self, signing_keypair, people_counter_type):
        fw = FirmwareVersion.objects.create(
            device_type=people_counter_type,
            version="9.0.2",
            firmware_file=_firmware_file(b"already-signed"),
            signature="prior-signature",
        )
        assert fw.signature == "prior-signature"


# ---------------------------------------------------------------------------
# Dispatch payload includes signature
# ---------------------------------------------------------------------------


class TestDispatchPayload:
    def test_payload_always_contains_signature_field(self, signing_keypair, people_counter_type):
        _, public_pem = signing_keypair
        binary = b"dispatch-test-binary"
        firmware = FirmwareVersion.objects.create(
            device_type=people_counter_type,
            version="9.1.0",
            firmware_file=_firmware_file(binary),
            signature="",
        )
        device = ESP32DeviceFactory(
            mac_address="DE:AD:BE:EF:00:11", device_type=people_counter_type
        )

        client = mock.MagicMock()
        client.publish.return_value.rc = 0
        with mock.patch("forgekey.services.firmware_dispatch.get_mqtt_client", return_value=client):
            firmware_dispatch.publish_firmware_update(device, firmware)

        topic, payload = client.publish.call_args[0][:2]
        body = json.loads(payload)
        assert set(body) >= {"url", "sha256", "signature", "version", "mandatory"}
        assert body["signature"] == firmware.signature
        assert verify_firmware_signature(binary, body["signature"], public_pem) is True

    def test_payload_signature_is_empty_string_when_unsigned(self, settings, people_counter_type):
        settings.FORGEKEY_FIRMWARE_SIGNING_KEY = ""
        firmware = FirmwareVersionFactory(
            device_type=people_counter_type, version="9.1.1", signature=""
        )
        device = ESP32DeviceFactory(
            mac_address="DE:AD:BE:EF:00:12", device_type=people_counter_type
        )

        client = mock.MagicMock()
        client.publish.return_value.rc = 0
        with mock.patch("forgekey.services.firmware_dispatch.get_mqtt_client", return_value=client):
            firmware_dispatch.publish_firmware_update(device, firmware)

        body = json.loads(client.publish.call_args[0][1])
        assert body["signature"] == ""


# ---------------------------------------------------------------------------
# backfill_firmware_signatures management command
# ---------------------------------------------------------------------------


class TestBackfillCommand:
    def test_backfill_signs_unsigned_rows(self, signing_keypair, people_counter_type):
        _, public_pem = signing_keypair

        binary = b"backfill-payload-A"
        unsigned = FirmwareVersion(
            device_type=people_counter_type,
            version="bf.1.0",
            firmware_file=_firmware_file(binary, name="fw_a.bin"),
            signature="",
        )
        # Bypass save() auto-sign by passing an explicit signature, then clearing it.
        unsigned.signature = "tmp"
        unsigned.save()
        FirmwareVersion.objects.filter(pk=unsigned.pk).update(signature="")
        unsigned.refresh_from_db()
        assert unsigned.signature == ""

        # Already-signed row — should be left alone.
        signed_pre = FirmwareVersion.objects.create(
            device_type=people_counter_type,
            version="bf.1.1",
            firmware_file=_firmware_file(b"already-signed-payload", name="fw_b.bin"),
            signature="placeholder-sig",
        )

        out = StringIO()
        call_command("backfill_firmware_signatures", stdout=out)

        unsigned.refresh_from_db()
        signed_pre.refresh_from_db()

        assert unsigned.signature
        assert verify_firmware_signature(binary, unsigned.signature, public_pem) is True
        assert signed_pre.signature == "placeholder-sig"
        assert "signed=1" in out.getvalue()
        assert "skipped=1" in out.getvalue()

    def test_backfill_is_idempotent(self, signing_keypair, people_counter_type):
        # Run twice — second invocation should sign nothing new.
        FirmwareVersion.objects.create(
            device_type=people_counter_type,
            version="bf.2.0",
            firmware_file=_firmware_file(b"payload-c"),
            signature="",
        )

        out1 = StringIO()
        call_command("backfill_firmware_signatures", stdout=out1)
        out2 = StringIO()
        call_command("backfill_firmware_signatures", stdout=out2)

        assert "signed=0" in out2.getvalue()

    def test_backfill_force_resigns(self, signing_keypair, people_counter_type):
        _, public_pem = signing_keypair
        binary = b"force-resign"
        fw = FirmwareVersion.objects.create(
            device_type=people_counter_type,
            version="bf.3.0",
            firmware_file=_firmware_file(binary),
            signature="bogus-prior-signature",
        )

        call_command("backfill_firmware_signatures", "--force", stdout=StringIO())
        fw.refresh_from_db()

        assert fw.signature != "bogus-prior-signature"
        assert verify_firmware_signature(binary, fw.signature, public_pem) is True

    def test_backfill_requires_signing_key(self, settings, people_counter_type):
        settings.FORGEKEY_FIRMWARE_SIGNING_KEY = ""
        with pytest.raises(CommandError):
            call_command("backfill_firmware_signatures", stdout=StringIO())


# ---------------------------------------------------------------------------
# rotate_provisioning_token management command
# ---------------------------------------------------------------------------


class TestRotateProvisioningTokenCommand:
    def test_publishes_to_each_active_device_config_topic(self, people_counter_type):
        a = ESP32DeviceFactory(
            mac_address="11:22:33:44:55:66", device_type=people_counter_type, is_active=True
        )
        b = ESP32DeviceFactory(
            mac_address="11:22:33:44:55:77", device_type=people_counter_type, is_active=True
        )
        ESP32DeviceFactory(
            mac_address="11:22:33:44:55:88", device_type=people_counter_type, is_active=False
        )

        client = mock.MagicMock()
        client.publish.return_value.rc = 0

        with mock.patch(
            "forgekey.management.commands.rotate_provisioning_token.get_mqtt_client",
            return_value=client,
        ):
            call_command(
                "rotate_provisioning_token",
                "--token=brand-new-token",
                "--valid-after=2026-05-01T00:00:00+00:00",
                stdout=StringIO(),
            )

        topics = [call.args[0] for call in client.publish.call_args_list]
        assert sorted(topics) == sorted(
            [
                f"forgekey/{a.mac_address.replace(':', '-')}/config",
                f"forgekey/{b.mac_address.replace(':', '-')}/config",
            ]
        )

        bodies = [json.loads(call.args[1]) for call in client.publish.call_args_list]
        for body in bodies:
            assert body["provisioning_token"] == "brand-new-token"
            assert body["valid_after"] == "2026-05-01T00:00:00+00:00"

    def test_dry_run_publishes_nothing(self, people_counter_type):
        ESP32DeviceFactory(
            mac_address="22:22:22:22:22:22", device_type=people_counter_type, is_active=True
        )

        client = mock.MagicMock()
        with mock.patch(
            "forgekey.management.commands.rotate_provisioning_token.get_mqtt_client",
            return_value=client,
        ):
            call_command(
                "rotate_provisioning_token",
                "--token=t",
                "--dry-run",
                stdout=StringIO(),
            )

        client.publish.assert_not_called()

    def test_rejects_empty_token(self):
        with pytest.raises(CommandError):
            call_command("rotate_provisioning_token", "--token=   ", stdout=StringIO())


# ---------------------------------------------------------------------------
# Doctor check
# ---------------------------------------------------------------------------


class TestSigningDoctorCheck:
    def test_no_warnings_in_debug(self, settings):
        settings.DEBUG = True
        settings.FORGEKEY_FIRMWARE_SIGNING_KEY = ""
        assert check_firmware_signing_key(app_configs=None) == []

    def test_warns_in_production_when_unset(self, settings):
        settings.DEBUG = False
        settings.FORGEKEY_FIRMWARE_SIGNING_KEY = ""
        warnings = check_firmware_signing_key(app_configs=None)
        assert [w.id for w in warnings] == [W_MISSING]

    def test_warns_when_value_is_placeholder(self, settings):
        settings.DEBUG = False
        settings.FORGEKEY_FIRMWARE_SIGNING_KEY = "change-me-in-production"
        warnings = check_firmware_signing_key(app_configs=None)
        assert any(w.id == W_DEFAULT for w in warnings)

    def test_warns_when_value_is_unparseable(self, settings):
        settings.DEBUG = False
        settings.FORGEKEY_FIRMWARE_SIGNING_KEY = "not a pem-encoded key at all"
        warnings = check_firmware_signing_key(app_configs=None)
        assert any(w.id == W_INVALID for w in warnings)

    def test_no_warnings_for_valid_production_key(self, settings):
        settings.DEBUG = False
        private_pem, _ = _make_keypair()
        settings.FORGEKEY_FIRMWARE_SIGNING_KEY = private_pem
        assert check_firmware_signing_key(app_configs=None) == []
