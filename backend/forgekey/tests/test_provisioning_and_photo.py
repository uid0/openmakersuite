"""
Tests for the ForgeKey photo + firmware-dispatch + retention surface area
originally introduced for oms-dlg.

The legacy ``/devices/register/`` coverage moved to :mod:`test_enrollment`
when the endpoint was replaced by ``/devices/enroll/`` (oms-d2axqu /
[[forgekey-trust-refactor]]).

Endpoints + services under test:
  - POST /api/forgekey/devices/<mac>/photo/
  - forgekey.services.firmware_dispatch.publish_firmware_update
  - forgekey.tasks.prune_device_photos
  - forgekey.tasks.mark_stale_devices_offline
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

    def test_file_upload_threshold_keeps_device_photos_off_the_heap(self):
        """oms-9t2 regression: the gunicorn web container OOM'd in part because
        ESP32 device photos (3 MP, 5–10 MB JPEGs) were held in worker heap by
        Django's MemoryFileUploadHandler. FILE_UPLOAD_MAX_MEMORY_SIZE must stay
        small enough that any realistic device photo spools to /tmp via
        TemporaryFileUploadHandler. If you bump this, you need a separate plan
        for bounding worker RSS during photo upload bursts."""
        from django.conf import settings

        assert settings.FILE_UPLOAD_MAX_MEMORY_SIZE <= 2 * 1024 * 1024, (
            f"FILE_UPLOAD_MAX_MEMORY_SIZE={settings.FILE_UPLOAD_MAX_MEMORY_SIZE} "
            "is large enough that ESP32 device photos may be buffered in heap; "
            "see docs/INCIDENTS/oom-backend.md."
        )


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


class TestStaleDeviceOfflineSweep:
    def test_stale_device_flips_offline(self, people_counter_type):
        from forgekey.tasks import mark_stale_devices_offline

        device = ESP32DeviceFactory(
            device_type=people_counter_type,
            is_online=True,
        )

        stale_at = timezone.now() - timedelta(hours=6)
        ESP32Device.objects.filter(pk=device.pk).update(last_seen=stale_at)

        result = mark_stale_devices_offline()

        device.refresh_from_db()
        assert result["updated"] == 1
        assert device.is_online is False

    def test_fresh_device_unchanged(self, people_counter_type):
        from forgekey.tasks import mark_stale_devices_offline

        device = ESP32DeviceFactory(
            device_type=people_counter_type,
            is_online=True,
        )

        recent_at = timezone.now() - timedelta(minutes=10)
        ESP32Device.objects.filter(pk=device.pk).update(last_seen=recent_at)

        result = mark_stale_devices_offline()

        device.refresh_from_db()
        assert result["updated"] == 0
        assert device.is_online is True

    def test_null_last_seen_device_ignored(self, people_counter_type):
        from forgekey.tasks import mark_stale_devices_offline

        device = ESP32DeviceFactory(
            device_type=people_counter_type,
            is_online=True,
        )

        ESP32Device.objects.filter(pk=device.pk).update(last_seen=None)

        result = mark_stale_devices_offline()

        device.refresh_from_db()
        assert result["updated"] == 0
        assert device.is_online is True

    def test_threshold_kwarg_override(self, people_counter_type):
        from forgekey.tasks import mark_stale_devices_offline

        stale_by_four = ESP32DeviceFactory(
            device_type=people_counter_type,
            is_online=True,
        )
        stale_by_six = ESP32DeviceFactory(
            device_type=people_counter_type,
            is_online=True,
        )

        now = timezone.now()
        ESP32Device.objects.filter(pk=stale_by_four.pk).update(last_seen=now - timedelta(hours=4))
        ESP32Device.objects.filter(pk=stale_by_six.pk).update(last_seen=now - timedelta(hours=6))

        result = mark_stale_devices_offline(threshold_hours=5)

        stale_by_four.refresh_from_db()
        stale_by_six.refresh_from_db()
        assert result["updated"] == 1
        assert stale_by_four.is_online is True
        assert stale_by_six.is_online is False

    def test_already_offline_device_skipped(self, people_counter_type):
        from forgekey.tasks import mark_stale_devices_offline

        device = ESP32DeviceFactory(
            device_type=people_counter_type,
            is_online=False,
        )

        stale_at = timezone.now() - timedelta(hours=6)
        ESP32Device.objects.filter(pk=device.pk).update(last_seen=stale_at)

        result = mark_stale_devices_offline()

        device.refresh_from_db()
        assert result["updated"] == 0
        assert device.is_online is False

    def test_multiple_stale_in_one_run(self, people_counter_type):
        from forgekey.tasks import mark_stale_devices_offline

        devices = [
            ESP32DeviceFactory(device_type=people_counter_type, is_online=True),
            ESP32DeviceFactory(device_type=people_counter_type, is_online=True),
            ESP32DeviceFactory(device_type=people_counter_type, is_online=True),
        ]

        stale_at = timezone.now() - timedelta(hours=6)
        ESP32Device.objects.filter(pk__in=[device.pk for device in devices]).update(
            last_seen=stale_at
        )

        result = mark_stale_devices_offline()

        for device in devices:
            device.refresh_from_db()
            assert device.is_online is False
        assert result["updated"] == 3
