"""
Factory classes for generating test data for ForgeKey models.
"""

from django.contrib.auth import get_user_model

import factory
from factory import Faker, SubFactory
from factory.django import DjangoModelFactory

from forgekey.models import (
    AssetAuthorization,
    AssetDevice,
    DeviceFirmwareUpdate,
    DeviceLockout,
    DeviceType,
    DeviceUsage,
    ESP32Device,
    FirmwareVersion,
    LockoutLevel,
    OperationalMode,
    PowerMeterReading,
)
from inventory.tests.factories import AssetFactory

User = get_user_model()


class UserFactory(DjangoModelFactory):
    """Factory for creating test users."""

    class Meta:
        model = User
        django_get_or_create = ("username",)

    username = factory.Sequence(lambda n: f"user_{n}")
    email = factory.LazyAttribute(lambda obj: f"{obj.username}@example.com")
    password = factory.PostGenerationMethodCall("set_password", "testpass123")


class DeviceTypeFactory(DjangoModelFactory):
    """Factory for creating DeviceType instances."""

    class Meta:
        model = DeviceType
        django_get_or_create = ("code",)

    name = factory.Sequence(lambda n: f"Device Type {n}")
    code = DeviceType.TYPE_AC_RELAY
    description = Faker("text", max_nb_chars=200)
    is_active = True


class ESP32DeviceFactory(DjangoModelFactory):
    """Factory for creating ESP32Device instances."""

    class Meta:
        model = ESP32Device

    mac_address = factory.Sequence(
        lambda n: f"{n:02X}:{(n + 1) % 256:02X}:{(n + 2) % 256:02X}:{(n + 3) % 256:02X}:{(n + 4) % 256:02X}:{(n + 5) % 256:02X}"
    )
    device_type = SubFactory(DeviceTypeFactory)
    name = factory.Sequence(lambda n: f"ESP32 Device {n}")
    description = Faker("text", max_nb_chars=200)
    firmware_version = "1.0.0"
    is_online = False
    is_active = True


class AssetDeviceFactory(DjangoModelFactory):
    """Factory for creating AssetDevice instances."""

    class Meta:
        model = AssetDevice

    asset = SubFactory(AssetFactory)
    device = SubFactory(ESP32DeviceFactory)
    is_primary = True
    role = "power_control"


class OperationalModeFactory(DjangoModelFactory):
    """Factory for creating OperationalMode instances."""

    class Meta:
        model = OperationalMode

    asset = SubFactory(AssetFactory)
    mode = OperationalMode.MODE_AVAILABLE
    classroom_mode_enabled = False


class AssetAuthorizationFactory(DjangoModelFactory):
    """Factory for creating AssetAuthorization instances."""

    class Meta:
        model = AssetAuthorization

    asset = SubFactory(AssetFactory)
    user = SubFactory(UserFactory)
    is_active = True


class DeviceLockoutFactory(DjangoModelFactory):
    """Factory for creating DeviceLockout instances."""

    class Meta:
        model = DeviceLockout

    asset = SubFactory(AssetFactory)
    locked_by = SubFactory(UserFactory)
    lockout_level = LockoutLevel.USER
    reason = Faker("text", max_nb_chars=200)
    is_active = True


class DeviceUsageFactory(DjangoModelFactory):
    """Factory for creating DeviceUsage instances."""

    class Meta:
        model = DeviceUsage

    asset = SubFactory(AssetFactory)
    user = SubFactory(UserFactory)


class PowerMeterReadingFactory(DjangoModelFactory):
    """Factory for creating PowerMeterReading instances."""

    class Meta:
        model = PowerMeterReading

    device = SubFactory(ESP32DeviceFactory)
    asset = SubFactory(AssetFactory)
    voltage = factory.Faker("pyfloat", left_digits=3, right_digits=1, min_value=100, max_value=130)
    current = factory.Faker("pyfloat", left_digits=2, right_digits=2, min_value=0.1, max_value=20)
    power = factory.LazyAttribute(
        lambda obj: (
            obj.voltage * obj.current
            if hasattr(obj, "voltage") and hasattr(obj, "current")
            else 0.0
        )
    )


class FirmwareVersionFactory(DjangoModelFactory):
    """Factory for creating FirmwareVersion instances."""

    class Meta:
        model = FirmwareVersion

    device_type = SubFactory(DeviceTypeFactory)
    version = factory.Sequence(lambda n: f"1.{n}.0")
    release_notes = Faker("text", max_nb_chars=500)
    firmware_file = factory.django.FileField()  # Required field
    signature = factory.Sequence(lambda n: f"signature_{n}")
    is_active = True
    created_by = SubFactory(UserFactory)


class DeviceFirmwareUpdateFactory(DjangoModelFactory):
    """Factory for creating DeviceFirmwareUpdate instances."""

    class Meta:
        model = DeviceFirmwareUpdate

    device = SubFactory(ESP32DeviceFactory)
    firmware_version = SubFactory(FirmwareVersionFactory)
    status = DeviceFirmwareUpdate.STATUS_PENDING
    requested_by = SubFactory(UserFactory)
