"""
Tests for the ForgeKey provisioning + photo + firmware-dispatch surface area
introduced for oms-dlg.

Endpoints + service under test:
  - POST /api/forgekey/devices/register/
  - POST /api/forgekey/devices/<mac>/photo/
  - forgekey.services.firmware_dispatch.publish_firmware_update
  - forgekey.tasks.prune_device_photos
"""

from __future__ import annotations

import json
from datetime import timedelta
from io import BytesIO
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

import pytest
from PIL import Image

from forgekey.models import DeviceFirmwareUpdate, DeviceType, ESP32Device, ESP32DevicePhoto
from forgekey.tests.factories import DeviceTypeFactory, ESP32DeviceFactory, FirmwareVersionFactory

pytestmark = pytest.mark.django_db


PROVISIONING_TOKEN = "test-provisioning-token-please"


def _jpeg(name: str = "snap.jpg", color: str = "blue") -> SimpleUploadedFile:
    img = Image.new("RGB", (32, 32), color=color)
    buf = BytesIO()
    img.save(buf, format="JPEG")
    buf.seek(0)
    return SimpleUploadedFile(name=name, content=buf.read(), content_type="image/jpeg")


def _png(name: str = "snap.png") -> SimpleUploadedFile:
    img = Image.new("RGB", (32, 32), color="green")
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return SimpleUploadedFile(name=name, content=buf.read(), content_type="image/png")


@pytest.fixture(autouse=True)
def _provisioning_token(settings):
    settings.FORGEKEY_PROVISIONING_TOKEN = PROVISIONING_TOKEN
    return PROVISIONING_TOKEN


@pytest.fixture
def people_counter_type():
    return DeviceTypeFactory(code=DeviceType.TYPE_PEOPLE_COUNTER)


@pytest.fixture
def env_sensor_type():
    return DeviceTypeFactory(code=DeviceType.TYPE_ENV_SENSOR)


@pytest.fixture
def register_url():
    return reverse("forgekey:device-register")


# ---------------------------------------------------------------------------
# AC: registration
# ---------------------------------------------------------------------------


class TestRegister:
    def test_register_creates_device_with_enrollment_photo(
        self, api_client, people_counter_type, register_url
    ):
        photo = _jpeg("enroll.jpg")
        meta = {
            "mac_address": "AA:BB:CC:11:22:33",
            "firmware_version": "1.4.2",
            "device_type": DeviceType.TYPE_PEOPLE_COUNTER,
            "boot_count": 7,
            "free_heap": 102400,
            "ip": "10.0.0.42",
        }

        resp = api_client.post(
            register_url,
            data={"photo": photo, "metadata": json.dumps(meta)},
            HTTP_X_FORGEKEY_PROVISIONING_TOKEN=PROVISIONING_TOKEN,
            format="multipart",
        )

        assert resp.status_code == 201, resp.content
        body = resp.json()
        device = ESP32Device.objects.get(mac_address="AA:BB:CC:11:22:33")
        assert body["device_id"] == str(device.id)
        assert body["mqtt_topic_for_firmware"] == "forgekey/aabbcc112233/firmware"
        assert body["mqtt_topic_for_pings"] == "forgekey/aabbcc112233/people_counter/occupancy"
        assert body["jwt_token"]
        assert device.enrollment_photo  # photo persisted
        assert device.firmware_version == "1.4.2"
        assert device.boot_count == 7
        assert device.free_heap == 102400
        assert device.ip == "10.0.0.42"
        assert device.device_type == people_counter_type

    def test_register_idempotent_on_mac(self, api_client, people_counter_type, register_url):
        meta = {
            "mac_address": "AA:BB:CC:DD:EE:01",
            "device_type": DeviceType.TYPE_PEOPLE_COUNTER,
            "firmware_version": "1.0.0",
        }
        first = api_client.post(
            register_url,
            data={"photo": _jpeg(), "metadata": json.dumps(meta)},
            HTTP_X_FORGEKEY_PROVISIONING_TOKEN=PROVISIONING_TOKEN,
            format="multipart",
        )
        assert first.status_code == 201

        meta["firmware_version"] = "1.1.0"
        meta["boot_count"] = 99
        second = api_client.post(
            register_url,
            data={"photo": _jpeg(), "metadata": json.dumps(meta)},
            HTTP_X_FORGEKEY_PROVISIONING_TOKEN=PROVISIONING_TOKEN,
            format="multipart",
        )
        assert second.status_code == 200
        assert ESP32Device.objects.filter(mac_address="AA:BB:CC:DD:EE:01").count() == 1
        device = ESP32Device.objects.get(mac_address="AA:BB:CC:DD:EE:01")
        assert device.firmware_version == "1.1.0"
        assert device.boot_count == 99

    def test_register_invalid_token_returns_401(
        self, api_client, people_counter_type, register_url
    ):
        meta = {
            "mac_address": "AA:BB:CC:DD:EE:02",
            "device_type": DeviceType.TYPE_PEOPLE_COUNTER,
        }
        resp = api_client.post(
            register_url,
            data={"photo": _jpeg(), "metadata": json.dumps(meta)},
            HTTP_X_FORGEKEY_PROVISIONING_TOKEN="nope",
            format="multipart",
        )
        assert resp.status_code == 401
        body = resp.json()
        assert body["code"] == "token_mismatch"
        # Fingerprints must differ and neither must contain the actual token.
        assert body["expected_token_fingerprint"]
        assert body["supplied_token_fingerprint"]
        assert body["expected_token_fingerprint"] != body["supplied_token_fingerprint"]
        assert PROVISIONING_TOKEN not in resp.content.decode()
        assert ESP32Device.objects.filter(mac_address="AA:BB:CC:DD:EE:02").exists() is False

    def test_register_placeholder_token_returns_401(
        self, api_client, people_counter_type, register_url
    ):
        meta = {
            "mac_address": "AA:BB:CC:DD:EE:03",
            "device_type": DeviceType.TYPE_PEOPLE_COUNTER,
        }
        resp = api_client.post(
            register_url,
            data={"photo": _jpeg(), "metadata": json.dumps(meta)},
            HTTP_X_FORGEKEY_PROVISIONING_TOKEN="REPLACE_ME_PROVISIONING_TOKEN",
            format="multipart",
        )
        assert resp.status_code == 401
        assert resp.json()["code"] == "token_placeholder"
        assert ESP32Device.objects.filter(mac_address="AA:BB:CC:DD:EE:03").exists() is False

    def test_register_missing_token_returns_401(
        self, api_client, people_counter_type, register_url
    ):
        meta = {
            "mac_address": "AA:BB:CC:DD:EE:04",
            "device_type": DeviceType.TYPE_PEOPLE_COUNTER,
        }
        resp = api_client.post(
            register_url,
            data={"photo": _jpeg(), "metadata": json.dumps(meta)},
            format="multipart",
        )
        assert resp.status_code == 401
        body = resp.json()
        assert body["code"] == "token_missing"
        assert body["supplied_token_length"] == 0

    def test_register_server_unconfigured_returns_distinct_code(
        self, api_client, settings, people_counter_type, register_url
    ):
        """When the server has no token configured, the response distinguishes
        that from a client-side header problem so operators see the real cause."""
        settings.FORGEKEY_PROVISIONING_TOKEN = ""
        meta = {
            "mac_address": "AA:BB:CC:DD:EE:05",
            "device_type": DeviceType.TYPE_PEOPLE_COUNTER,
        }
        resp = api_client.post(
            register_url,
            data={"photo": _jpeg(), "metadata": json.dumps(meta)},
            HTTP_X_FORGEKEY_PROVISIONING_TOKEN="anything-here",
            format="multipart",
        )
        assert resp.status_code == 401
        body = resp.json()
        assert body["code"] == "server_unconfigured"
        assert body["expected_token_length"] == 0

    def test_register_server_placeholder_token_returns_unconfigured(
        self, api_client, settings, people_counter_type, register_url
    ):
        settings.FORGEKEY_PROVISIONING_TOKEN = "REPLACE_ME_PROVISIONING_TOKEN"
        meta = {
            "mac_address": "AA:BB:CC:DD:EE:06",
            "device_type": DeviceType.TYPE_PEOPLE_COUNTER,
        }
        resp = api_client.post(
            register_url,
            data={"photo": _jpeg(), "metadata": json.dumps(meta)},
            HTTP_X_FORGEKEY_PROVISIONING_TOKEN="REPLACE_ME_PROVISIONING_TOKEN",
            format="multipart",
        )
        assert resp.status_code == 401
        # Server unconfigured takes precedence over token_placeholder so
        # operators are pointed at the backend, not the device.
        assert resp.json()["code"] == "server_unconfigured"

    def test_register_strips_whitespace_around_token(
        self, api_client, settings, people_counter_type, register_url
    ):
        """A trailing newline in the env var or device-side string must not
        cause a spurious 401 — the most common copy/paste failure mode."""
        settings.FORGEKEY_PROVISIONING_TOKEN = PROVISIONING_TOKEN + "\n"
        meta = {
            "mac_address": "AA:BB:CC:DD:EE:07",
            "device_type": DeviceType.TYPE_PEOPLE_COUNTER,
        }
        resp = api_client.post(
            register_url,
            data={"photo": _jpeg(), "metadata": json.dumps(meta)},
            HTTP_X_FORGEKEY_PROVISIONING_TOKEN=f"  {PROVISIONING_TOKEN}  ",
            format="multipart",
        )
        assert resp.status_code == 201, resp.content

    def test_register_mismatch_response_does_not_leak_token(
        self, api_client, people_counter_type, register_url
    ):
        meta = {
            "mac_address": "AA:BB:CC:DD:EE:08",
            "device_type": DeviceType.TYPE_PEOPLE_COUNTER,
        }
        resp = api_client.post(
            register_url,
            data={"photo": _jpeg(), "metadata": json.dumps(meta)},
            HTTP_X_FORGEKEY_PROVISIONING_TOKEN="completely-wrong",
            format="multipart",
        )
        assert resp.status_code == 401
        body_text = resp.content.decode()
        assert PROVISIONING_TOKEN not in body_text
        assert "completely-wrong" not in body_text

    def test_register_firmware_contract_payload(
        self, api_client, people_counter_type, register_url
    ):
        """Literal firmware payload from oms-kla spec — both lowercase-no-sep mac and
        hyphenated sensor_kind must be accepted, response must use spec-shape topics."""
        meta = {
            "mac_address": "aabbccddeeff",
            "firmware_version": "0.1.0",
            "sensor_kind": "people-counter",
            "boot_count": 3,
            "free_heap": 123456,
            "ip": "192.168.1.42",
        }

        resp = api_client.post(
            register_url,
            data={"photo": _jpeg("boot.jpg"), "metadata": json.dumps(meta)},
            HTTP_X_FORGEKEY_PROVISIONING_TOKEN=PROVISIONING_TOKEN,
            format="multipart",
        )

        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert set(body) >= {
            "device_id",
            "mqtt_topic_for_firmware",
            "mqtt_topic_for_pings",
            "jwt_token",
        }
        assert body["device_id"]
        assert body["mqtt_topic_for_firmware"] == "forgekey/aabbccddeeff/firmware"
        assert body["mqtt_topic_for_pings"] == "forgekey/aabbccddeeff/people_counter/occupancy"
        assert body["jwt_token"]

        device = ESP32Device.objects.get(mac_address="AA:BB:CC:DD:EE:FF")
        assert device.device_type == people_counter_type
        assert device.firmware_version == "0.1.0"
        assert device.boot_count == 3
        assert device.free_heap == 123456
        assert device.ip == "192.168.1.42"

    def test_register_unknown_device_type_returns_400_with_valid_codes(
        self, api_client, register_url
    ):
        meta = {
            "mac_address": "AA:BB:CC:DD:EE:10",
            "sensor_kind": "smell-o-meter",
        }

        resp = api_client.post(
            register_url,
            data={"photo": _jpeg(), "metadata": json.dumps(meta)},
            HTTP_X_FORGEKEY_PROVISIONING_TOKEN=PROVISIONING_TOKEN,
            format="multipart",
        )

        assert resp.status_code == 400, resp.content
        body = resp.json()
        assert "smell-o-meter" in body["detail"]
        valid = body.get("valid_device_types") or []
        assert DeviceType.TYPE_PEOPLE_COUNTER in valid
        assert DeviceType.TYPE_ENV_SENSOR in valid
        assert DeviceType.TYPE_DOOR_COUNTER in valid

    def test_register_missing_device_type_returns_400_with_valid_codes(
        self, api_client, register_url
    ):
        meta = {"mac_address": "AA:BB:CC:DD:EE:11"}

        resp = api_client.post(
            register_url,
            data={"photo": _jpeg(), "metadata": json.dumps(meta)},
            HTTP_X_FORGEKEY_PROVISIONING_TOKEN=PROVISIONING_TOKEN,
            format="multipart",
        )

        assert resp.status_code == 400, resp.content
        body = resp.json()
        assert "device_type is required" in body["detail"]
        valid = body.get("valid_device_types") or []
        assert DeviceType.TYPE_PEOPLE_COUNTER in valid


class TestSeedDeviceTypesMigration:
    """Migration 0004 must seed every TYPE_CHOICES code so a fresh DB can
    register devices end-to-end without a manual fixture step (oms-f9z).
    """

    def test_seed_migration_creates_rows_for_all_choices(self, db):
        for code, _label in DeviceType.TYPE_CHOICES:
            assert DeviceType.objects.filter(code=code).exists(), (
                f"DeviceType row missing for code={code!r}; "
                "migration 0004_seed_device_types should have created it."
            )

    def test_seed_migration_creates_required_forgekey_codes(self, db):
        for code in (
            DeviceType.TYPE_PEOPLE_COUNTER,
            DeviceType.TYPE_ENV_SENSOR,
            DeviceType.TYPE_DOOR_COUNTER,
        ):
            row = DeviceType.objects.get(code=code)
            assert row.is_active is True
            assert row.name  # non-empty human-readable label

    def test_register_with_seeded_people_counter_returns_201(self, api_client, register_url):
        """No factory: relies purely on the data migration having seeded the row."""
        meta = {
            "mac_address": "AA:BB:CC:DD:EE:20",
            "sensor_kind": "people-counter",
            "firmware_version": "0.1.0",
        }
        resp = api_client.post(
            register_url,
            data={"photo": _jpeg(), "metadata": json.dumps(meta)},
            HTTP_X_FORGEKEY_PROVISIONING_TOKEN=PROVISIONING_TOKEN,
            format="multipart",
        )

        assert resp.status_code == 201, resp.content
        device = ESP32Device.objects.get(mac_address="AA:BB:CC:DD:EE:20")
        assert device.device_type.code == DeviceType.TYPE_PEOPLE_COUNTER


# ---------------------------------------------------------------------------
# AC: periodic photo upload
# ---------------------------------------------------------------------------


class TestPhotoUpload:
    def test_photo_upload_with_jwt_succeeds(self, api_client, people_counter_type):
        device = ESP32DeviceFactory(
            mac_address="11:22:33:44:55:66", device_type=people_counter_type
        )
        from forgekey.utils import generate_device_jwt

        token = generate_device_jwt(device.mac_address)
        url = reverse("forgekey:device-photo-upload", kwargs={"mac": device.mac_address})

        resp = api_client.post(
            url,
            data={"photo": _jpeg("hourly.jpg")},
            HTTP_AUTHORIZATION=f"Bearer {token}",
            format="multipart",
        )

        assert resp.status_code == 201, resp.content
        record = ESP32DevicePhoto.objects.get(device=device)
        assert resp.json()["photo_id"] == str(record.id)
        device.refresh_from_db()
        assert device.last_photo
        assert device.is_online is True

    def test_photo_upload_rejects_non_jpeg(self, api_client, people_counter_type):
        device = ESP32DeviceFactory(
            mac_address="11:22:33:44:55:77", device_type=people_counter_type
        )
        from forgekey.utils import generate_device_jwt

        token = generate_device_jwt(device.mac_address)
        url = reverse("forgekey:device-photo-upload", kwargs={"mac": device.mac_address})

        resp = api_client.post(
            url,
            data={"photo": _png("bad.png")},
            HTTP_AUTHORIZATION=f"Bearer {token}",
            format="multipart",
        )

        assert resp.status_code == 400
        assert ESP32DevicePhoto.objects.filter(device=device).count() == 0


# ---------------------------------------------------------------------------
# AC: firmware dispatch via MQTT
# ---------------------------------------------------------------------------


class TestFirmwareDispatch:
    @pytest.fixture
    def mqtt_client(self):
        client = mock.MagicMock()
        result = mock.MagicMock()
        result.rc = 0
        client.publish.return_value = result
        with mock.patch(
            "forgekey.services.firmware_dispatch.get_mqtt_client",
            return_value=client,
        ):
            yield client

    def test_firmware_dispatch_publishes_to_correct_topic(self, mqtt_client, people_counter_type):
        from forgekey.services.firmware_dispatch import publish_firmware_update

        device = ESP32DeviceFactory(
            mac_address="DE:AD:BE:EF:00:01", device_type=people_counter_type
        )
        firmware = FirmwareVersionFactory(
            device_type=people_counter_type,
            version="2.0.0",
            mandatory=True,
        )

        records = publish_firmware_update(device, firmware)

        assert len(records) == 1
        assert mqtt_client.publish.called
        topic, payload = mqtt_client.publish.call_args[0][:2]
        assert topic == "forgekey/deadbeef0001/firmware"
        body = json.loads(payload)
        assert body["version"] == "2.0.0"
        assert body["mandatory"] is True
        assert body["sha256"] == firmware.sha256

    def test_bulk_dispatch_filters_by_device_type(
        self, mqtt_client, people_counter_type, env_sensor_type
    ):
        from forgekey.services.firmware_dispatch import dispatch_to_device_type

        people_a = ESP32DeviceFactory(
            mac_address="01:01:01:01:01:01", device_type=people_counter_type
        )
        people_b = ESP32DeviceFactory(
            mac_address="01:01:01:01:01:02", device_type=people_counter_type
        )
        ESP32DeviceFactory(mac_address="02:02:02:02:02:02", device_type=env_sensor_type)
        firmware = FirmwareVersionFactory(device_type=people_counter_type, version="3.1.0")

        records = dispatch_to_device_type(firmware)

        assert {r.device_id for r in records} == {people_a.id, people_b.id}
        assert mqtt_client.publish.call_count == 2

    def test_dispatch_creates_devicefirmwareupdate_pending(self, mqtt_client, people_counter_type):
        from forgekey.services.firmware_dispatch import publish_firmware_update

        device = ESP32DeviceFactory(
            mac_address="AA:AA:AA:AA:AA:01", device_type=people_counter_type
        )
        firmware = FirmwareVersionFactory(device_type=people_counter_type, version="4.0.0")

        publish_firmware_update(device, firmware)

        update = DeviceFirmwareUpdate.objects.get(device=device, firmware_version=firmware)
        assert update.status == DeviceFirmwareUpdate.STATUS_PENDING


# ---------------------------------------------------------------------------
# AC: 30-day photo retention
# ---------------------------------------------------------------------------


class TestPhotoRetention:
    def test_pruner_deletes_photos_older_than_30_days(self, people_counter_type):
        from forgekey.tasks import prune_device_photos

        device = ESP32DeviceFactory(
            mac_address="BE:EF:BE:EF:BE:EF", device_type=people_counter_type
        )

        old = ESP32DevicePhoto.objects.create(device=device, image=_jpeg("old.jpg"))
        recent = ESP32DevicePhoto.objects.create(device=device, image=_jpeg("new.jpg"))

        # Backdate one row past the 30-day cutoff. ``received_at`` is
        # auto_now_add so we have to update via queryset.
        cutoff = timezone.now() - timedelta(days=45)
        ESP32DevicePhoto.objects.filter(pk=old.pk).update(received_at=cutoff)

        result = prune_device_photos(retention_days=30)

        assert result["deleted"] == 1
        assert ESP32DevicePhoto.objects.filter(pk=recent.pk).exists()
        assert not ESP32DevicePhoto.objects.filter(pk=old.pk).exists()
