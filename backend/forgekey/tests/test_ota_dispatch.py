"""
Tests for the OTA firmware dispatch flow (oms-k2f).

Covers:
  * Signed download tokens (mint + verify, expiry, tamper-proofing)
  * /api/forgekey/firmware/<id>/download endpoint with Range support
  * /api/forgekey/firmware/public-key endpoint
  * trigger_ota celery task + ota_dispatch service
  * mqtt_consumer OTA status handler updates DeviceFirmwareUpdate
  * FirmwareSigningKey rotation + load_signing_key DB-first resolution
"""

from __future__ import annotations

import json
import time
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from rest_framework.test import APIClient

from forgekey.management.commands.mqtt_consumer import (
    _topic_matches_ota_status,
    dispatch_message,
    handle_ota_status_message,
)
from forgekey.models import DeviceFirmwareUpdate, DeviceType, FirmwareSigningKey, FirmwareVersion
from forgekey.services.firmware_download_token import make_download_token, verify_download_token
from forgekey.services.firmware_signing import (
    derive_public_pem,
    generate_signing_keypair,
    load_signing_key,
)
from forgekey.services.ota_dispatch import OTADispatchError, build_ota_payload, publish_ota_trigger
from forgekey.tests.factories import DeviceTypeFactory, ESP32DeviceFactory

pytestmark = pytest.mark.django_db


def _make_keypair() -> tuple[str, str]:
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
def people_counter_type():
    return DeviceTypeFactory(code=DeviceType.TYPE_PEOPLE_COUNTER)


@pytest.fixture
def signing_keypair(settings):
    private_pem, public_pem = _make_keypair()
    settings.FORGEKEY_FIRMWARE_SIGNING_KEY = private_pem
    return private_pem, public_pem


def _firmware_file(content: bytes = b"\x00\x01\x02firmware-bytes", name: str = "fw.bin"):
    return SimpleUploadedFile(name=name, content=content, content_type="application/octet-stream")


# ---------------------------------------------------------------------------
# Download tokens
# ---------------------------------------------------------------------------


class TestDownloadTokens:
    def test_make_and_verify_roundtrip(self):
        token, exp = make_download_token("abc-123", ttl_seconds=60)
        assert token
        assert exp > int(time.time())
        assert verify_download_token("abc-123", token, exp)

    def test_verify_rejects_wrong_firmware_id(self):
        token, exp = make_download_token("abc-123", ttl_seconds=60)
        assert not verify_download_token("other-id", token, exp)

    def test_verify_rejects_tampered_token(self):
        token, exp = make_download_token("abc-123", ttl_seconds=60)
        bad = token[:-1] + ("A" if token[-1] != "A" else "B")
        assert not verify_download_token("abc-123", bad, exp)

    def test_verify_rejects_expired(self):
        token, exp = make_download_token("abc-123", ttl_seconds=60)
        # Pretend "now" is 10 minutes after expiry.
        assert not verify_download_token("abc-123", token, exp, now=exp + 600)

    def test_verify_rejects_mismatched_expiry(self):
        token, exp = make_download_token("abc-123", ttl_seconds=60)
        # Same token, different expiry baked into the verifying digest.
        assert not verify_download_token("abc-123", token, exp + 5)

    def test_empty_inputs_rejected(self):
        assert not verify_download_token("", "x", 1)
        assert not verify_download_token("id", "", 1)
        assert not verify_download_token("id", "x", 0)


# ---------------------------------------------------------------------------
# Download endpoint (Range support, auth)
# ---------------------------------------------------------------------------


class TestFirmwareDownloadView:
    def _setup_firmware(self, device_type, content=b"firmware-binary-payload-12345"):
        return FirmwareVersion.objects.create(
            device_type=device_type,
            version="ota-1.0",
            firmware_file=_firmware_file(content),
            signature="",
        )

    def test_full_download_with_valid_token(self, signing_keypair, people_counter_type):
        binary = b"abcdefghij" * 50  # 500 bytes
        firmware = self._setup_firmware(people_counter_type, binary)

        token, exp = make_download_token(str(firmware.id))
        url = reverse("forgekey:firmware-download", kwargs={"firmware_id": str(firmware.id)})

        client = APIClient()
        response = client.get(url, {"token": token, "exp": exp})
        assert response.status_code == 200
        assert b"".join(response.streaming_content) == binary
        assert response["Content-Length"] == str(len(binary))
        assert response["Accept-Ranges"] == "bytes"

    def test_range_download_partial_content(self, signing_keypair, people_counter_type):
        binary = b"".join(bytes([i % 256]) for i in range(1024))  # 1024 bytes
        firmware = self._setup_firmware(people_counter_type, binary)

        token, exp = make_download_token(str(firmware.id))
        url = reverse("forgekey:firmware-download", kwargs={"firmware_id": str(firmware.id)})

        client = APIClient()
        response = client.get(
            url,
            {"token": token, "exp": exp},
            HTTP_RANGE="bytes=100-199",
        )
        assert response.status_code == 206
        body = b"".join(response.streaming_content)
        assert body == binary[100:200]
        assert response["Content-Range"] == f"bytes 100-199/{len(binary)}"
        assert response["Content-Length"] == "100"

    def test_range_open_ended_returns_remainder(self, signing_keypair, people_counter_type):
        binary = b"0123456789" * 10  # 100 bytes
        firmware = self._setup_firmware(people_counter_type, binary)

        token, exp = make_download_token(str(firmware.id))
        url = reverse("forgekey:firmware-download", kwargs={"firmware_id": str(firmware.id)})

        client = APIClient()
        response = client.get(
            url,
            {"token": token, "exp": exp},
            HTTP_RANGE="bytes=80-",
        )
        assert response.status_code == 206
        assert b"".join(response.streaming_content) == binary[80:]
        assert response["Content-Range"] == f"bytes 80-99/{len(binary)}"

    def test_unauthorized_without_token(self, signing_keypair, people_counter_type):
        firmware = self._setup_firmware(people_counter_type)
        url = reverse("forgekey:firmware-download", kwargs={"firmware_id": str(firmware.id)})

        client = APIClient()
        response = client.get(url)
        assert response.status_code == 401

    def test_invalid_token_rejected(self, signing_keypair, people_counter_type):
        firmware = self._setup_firmware(people_counter_type)
        url = reverse("forgekey:firmware-download", kwargs={"firmware_id": str(firmware.id)})

        client = APIClient()
        response = client.get(url, {"token": "bogus", "exp": int(time.time()) + 60})
        assert response.status_code == 401

    def test_missing_firmware_returns_404(self, signing_keypair):
        import uuid

        url = reverse("forgekey:firmware-download", kwargs={"firmware_id": str(uuid.uuid4())})
        token, exp = make_download_token("anything")
        client = APIClient()
        response = client.get(url, {"token": token, "exp": exp})
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Public-key endpoint
# ---------------------------------------------------------------------------


class TestPublicKeyEndpoint:
    def test_returns_pem(self, signing_keypair):
        _, public_pem = signing_keypair
        client = APIClient()
        response = client.get(reverse("forgekey:firmware-public-key"))
        assert response.status_code == 200
        assert response["Content-Type"].startswith("application/x-pem-file")
        assert response.content.decode("ascii").strip() == public_pem.strip()

    def test_returns_404_without_signing(self, settings):
        settings.FORGEKEY_FIRMWARE_SIGNING_KEY = ""
        client = APIClient()
        response = client.get(reverse("forgekey:firmware-public-key"))
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# OTA dispatch + payload
# ---------------------------------------------------------------------------


class TestOTAPayload:
    def test_payload_shape_includes_required_fields(self, signing_keypair, people_counter_type):
        binary = b"esp32-firmware-payload"
        firmware = FirmwareVersion.objects.create(
            device_type=people_counter_type,
            version="2.0.1",
            firmware_file=_firmware_file(binary),
            signature="",
        )
        payload = build_ota_payload(firmware)
        assert set(payload) >= {"version", "url", "signature", "size"}
        assert payload["version"] == "2.0.1"
        assert payload["size"] == len(binary)
        assert payload["signature"]
        assert "token=" in payload["url"]
        assert payload["firmware_id"] == str(firmware.id)


class TestPublishOTATrigger:
    def test_publishes_to_ota_trigger_topic(self, signing_keypair, people_counter_type):
        firmware = FirmwareVersion.objects.create(
            device_type=people_counter_type,
            version="2.0.2",
            firmware_file=_firmware_file(b"binary"),
            signature="",
        )
        device = ESP32DeviceFactory(
            mac_address="DE:AD:BE:EF:00:21", device_type=people_counter_type
        )

        client = mock.MagicMock()
        client.publish.return_value.rc = 0
        with mock.patch("forgekey.tasks.get_mqtt_client", return_value=client):
            from forgekey import tasks  # noqa: F401 (warm import path)

            record = publish_ota_trigger(device, firmware)

        assert record.status == DeviceFirmwareUpdate.STATUS_PENDING
        topic, body = client.publish.call_args[0][:2]
        assert topic == f"forgekey/{device.mac_address.replace(':', '').lower()}/ota/trigger"
        decoded = json.loads(body)
        assert decoded["update_id"] == str(record.id)
        assert decoded["firmware_id"] == str(firmware.id)

    def test_broker_failure_raises_dispatch_error(self, signing_keypair, people_counter_type):
        firmware = FirmwareVersion.objects.create(
            device_type=people_counter_type,
            version="2.0.3",
            firmware_file=_firmware_file(b"binary"),
            signature="",
        )
        device = ESP32DeviceFactory(
            mac_address="DE:AD:BE:EF:00:22", device_type=people_counter_type
        )

        client = mock.MagicMock()
        client.publish.return_value.rc = 5  # arbitrary failure
        with mock.patch("forgekey.tasks.get_mqtt_client", return_value=client):
            with pytest.raises(OTADispatchError):
                publish_ota_trigger(device, firmware)

        # Audit row is still recorded for ops to inspect.
        assert DeviceFirmwareUpdate.objects.filter(device=device).count() == 1


class TestTriggerOTATask:
    def test_unknown_device_returns_failure_no_retry(self, signing_keypair, people_counter_type):
        from forgekey.tasks import trigger_ota

        firmware = FirmwareVersion.objects.create(
            device_type=people_counter_type,
            version="2.0.4",
            firmware_file=_firmware_file(b"x"),
            signature="",
        )
        result = trigger_ota.run("00000000-0000-0000-0000-000000000000", str(firmware.id))
        assert result["success"] is False
        assert result["error"] == "device_not_found"

    def test_unknown_firmware_returns_failure(self, signing_keypair, people_counter_type):
        from forgekey.tasks import trigger_ota

        device = ESP32DeviceFactory(
            mac_address="DE:AD:BE:EF:00:23", device_type=people_counter_type
        )
        result = trigger_ota.run(str(device.id), "00000000-0000-0000-0000-000000000000")
        assert result["success"] is False
        assert result["error"] == "firmware_not_found"

    def test_happy_path_publishes(self, signing_keypair, people_counter_type):
        from forgekey.tasks import trigger_ota

        firmware = FirmwareVersion.objects.create(
            device_type=people_counter_type,
            version="2.0.5",
            firmware_file=_firmware_file(b"binary"),
            signature="",
        )
        device = ESP32DeviceFactory(
            mac_address="DE:AD:BE:EF:00:24", device_type=people_counter_type
        )

        client = mock.MagicMock()
        client.publish.return_value.rc = 0
        with mock.patch("forgekey.tasks.get_mqtt_client", return_value=client):
            result = trigger_ota.run(str(device.id), str(firmware.id))

        assert result["success"] is True
        assert result["mac_address"] == device.mac_address
        assert result["update_id"]


# ---------------------------------------------------------------------------
# MQTT consumer OTA status handler
# ---------------------------------------------------------------------------


class TestOTAStatusHandler:
    def test_topic_matcher(self):
        assert _topic_matches_ota_status("forgekey/aabbccddeeff/ota/status")
        assert not _topic_matches_ota_status("forgekey/aabbccddeeff/ota/trigger")
        assert not _topic_matches_ota_status("forgekey/aabbccddeeff/status")

    def _setup(self, device_type):
        firmware = FirmwareVersion.objects.create(
            device_type=device_type,
            version="2.1.0",
            firmware_file=_firmware_file(b"x"),
            signature="",
        )
        device = ESP32DeviceFactory(
            mac_address="11:22:33:44:55:66",
            device_type=device_type,
        )
        update = DeviceFirmwareUpdate.objects.create(
            device=device,
            firmware_version=firmware,
            status=DeviceFirmwareUpdate.STATUS_PENDING,
        )
        return device, firmware, update

    def test_in_progress_status_marks_started(self, signing_keypair, people_counter_type):
        device, _firmware, update = self._setup(people_counter_type)
        topic = f"forgekey/{device.mac_address.replace(':', '').lower()}/ota/status"
        body = json.dumps({"update_id": str(update.id), "status": "in_progress"}).encode()

        assert handle_ota_status_message(topic, body) is True
        update.refresh_from_db()
        assert update.status == DeviceFirmwareUpdate.STATUS_IN_PROGRESS
        assert update.started_at is not None

    def test_completed_status_marks_completed_and_bumps_device(
        self, signing_keypair, people_counter_type
    ):
        device, firmware, update = self._setup(people_counter_type)
        topic = f"forgekey/{device.mac_address.replace(':', '').lower()}/ota/status"
        body = json.dumps(
            {"update_id": str(update.id), "status": "completed", "version": "2.1.0"}
        ).encode()

        assert handle_ota_status_message(topic, body) is True
        update.refresh_from_db()
        device.refresh_from_db()
        assert update.status == DeviceFirmwareUpdate.STATUS_COMPLETED
        assert update.completed_at is not None
        assert device.firmware_version == "2.1.0"

    def test_failed_status_records_error(self, signing_keypair, people_counter_type):
        device, _firmware, update = self._setup(people_counter_type)
        topic = f"forgekey/{device.mac_address.replace(':', '').lower()}/ota/status"
        body = json.dumps(
            {
                "update_id": str(update.id),
                "status": "failed",
                "error_message": "signature verification failed",
            }
        ).encode()

        assert handle_ota_status_message(topic, body) is True
        update.refresh_from_db()
        assert update.status == DeviceFirmwareUpdate.STATUS_FAILED
        assert "signature verification failed" in update.error_message

    def test_unknown_update_id_dropped(self, signing_keypair, people_counter_type):
        device, _firmware, _update = self._setup(people_counter_type)
        topic = f"forgekey/{device.mac_address.replace(':', '').lower()}/ota/status"
        body = json.dumps(
            {"update_id": "00000000-0000-0000-0000-000000000000", "status": "completed"}
        ).encode()
        assert handle_ota_status_message(topic, body) is False

    def test_dispatch_routes_ota_status_first(self, signing_keypair, people_counter_type):
        device, _firmware, update = self._setup(people_counter_type)
        topic = f"forgekey/{device.mac_address.replace(':', '').lower()}/ota/status"
        body = json.dumps({"update_id": str(update.id), "status": "in_progress"}).encode()
        # Should not raise; goes through OTA handler, not the generic status handler.
        dispatch_message(topic, body)
        update.refresh_from_db()
        assert update.status == DeviceFirmwareUpdate.STATUS_IN_PROGRESS


# ---------------------------------------------------------------------------
# FirmwareSigningKey rotation + DB-first resolution
# ---------------------------------------------------------------------------


class TestFirmwareSigningKeyRotation:
    def test_db_active_key_takes_precedence_over_env(self, settings):
        env_private, _ = _make_keypair()
        db_private, db_public = _make_keypair()
        settings.FORGEKEY_FIRMWARE_SIGNING_KEY = env_private

        FirmwareSigningKey.objects.create(
            label="prod-2026",
            public_key_pem=db_public,
            private_key_pem_encrypted=FirmwareSigningKey.encrypt_private_pem(db_private),
            is_active=True,
        )

        loaded = load_signing_key()
        derived_public = (
            loaded.public_key()
            .public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode("ascii")
        )
        assert derived_public.strip() == db_public.strip()

    def test_inactive_db_key_falls_back_to_env(self, settings):
        env_private, env_public = _make_keypair()
        db_private, _ = _make_keypair()
        settings.FORGEKEY_FIRMWARE_SIGNING_KEY = env_private

        FirmwareSigningKey.objects.create(
            label="retired",
            public_key_pem="x",
            private_key_pem_encrypted=FirmwareSigningKey.encrypt_private_pem(db_private),
            is_active=False,
        )
        loaded = load_signing_key()
        derived = (
            loaded.public_key()
            .public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode("ascii")
        )
        assert derived.strip() == env_public.strip()

    def test_encrypt_decrypt_roundtrip(self):
        private_pem, public_pem = generate_signing_keypair()
        ciphertext = FirmwareSigningKey.encrypt_private_pem(private_pem)
        row = FirmwareSigningKey.objects.create(
            label="r1",
            public_key_pem=public_pem,
            private_key_pem_encrypted=ciphertext,
            is_active=True,
        )
        assert row.decrypt_private_pem().strip() == private_pem.strip()

    def test_derive_public_pem_validates_curve(self):
        # secp384r1 should be rejected.
        wrong = ec.generate_private_key(ec.SECP384R1())
        wrong_pem = wrong.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("ascii")
        from forgekey.services.firmware_signing import FirmwareSigningError

        with pytest.raises(FirmwareSigningError):
            derive_public_pem(wrong_pem)


# ---------------------------------------------------------------------------
# Admin "Deploy OTA to fleet" action
# ---------------------------------------------------------------------------


class TestDeployToFleetAdmin:
    def test_action_queues_one_task_per_active_device(self, signing_keypair, people_counter_type):
        firmware = FirmwareVersion.objects.create(
            device_type=people_counter_type,
            version="3.0.0",
            firmware_file=_firmware_file(b"x"),
            signature="",
        )
        # 2 active devices, 1 inactive — only the active ones receive triggers.
        ESP32DeviceFactory(
            mac_address="AA:BB:CC:DD:EE:01", device_type=people_counter_type, is_active=True
        )
        ESP32DeviceFactory(
            mac_address="AA:BB:CC:DD:EE:02", device_type=people_counter_type, is_active=True
        )
        ESP32DeviceFactory(
            mac_address="AA:BB:CC:DD:EE:03", device_type=people_counter_type, is_active=False
        )

        from forgekey.admin import FirmwareVersionAdmin

        site = mock.MagicMock()
        admin_instance = FirmwareVersionAdmin(FirmwareVersion, site)

        request = mock.MagicMock()
        User = get_user_model()
        request.user = User.objects.create_user(
            username="ops", email="ops@example.com", password="x"
        )

        with mock.patch("forgekey.tasks.trigger_ota.delay") as delay_mock:
            admin_instance.deploy_ota_to_fleet(
                request, FirmwareVersion.objects.filter(pk=firmware.pk)
            )

        assert delay_mock.call_count == 2
