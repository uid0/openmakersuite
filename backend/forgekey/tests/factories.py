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
    IndicatorBinding,
    IndicatorStatus,
    LockoutLevel,
    OccupancyEvent,
    OperationalMode,
    PowerMeterReading,
    RoomOperationalMode,
)
from inventory.tests.factories import AssetFactory, LocationFactory

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

    # Encode the factory sequence into the low four octets under a fixed
    # locally-administered ``DE:AD`` prefix. The previous form left the first
    # octet as ``{n:02X}`` (no ``% 256``), so once a test run created 256+
    # devices the octet overflowed to three hex digits and produced an invalid
    # MAC that no longer round-tripped through ``normalize_mac_address`` (a
    # cross-test-ordering flake). Big-endian packing stays valid + unique for
    # any sequence value a suite will realistically reach.
    mac_address = factory.Sequence(
        lambda n: "DE:AD:{:02X}:{:02X}:{:02X}:{:02X}".format(
            (n >> 24) & 0xFF, (n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF
        )
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


class OccupancyEventFactory(DjangoModelFactory):
    """Factory for creating OccupancyEvent instances."""

    class Meta:
        model = OccupancyEvent

    device = SubFactory(ESP32DeviceFactory)
    sensor_kind = "people_counter"
    count_in = 1
    count_out = 0
    event_timestamp_utc = factory.LazyFunction(
        lambda: __import__("django.utils", fromlist=["timezone"]).timezone.now()
    )
    raw_payload = factory.LazyAttribute(lambda obj: {"in": obj.count_in, "out": obj.count_out})


class DeviceFirmwareUpdateFactory(DjangoModelFactory):
    """Factory for creating DeviceFirmwareUpdate instances."""

    class Meta:
        model = DeviceFirmwareUpdate

    device = SubFactory(ESP32DeviceFactory)
    firmware_version = SubFactory(FirmwareVersionFactory)
    status = DeviceFirmwareUpdate.STATUS_PENDING
    requested_by = SubFactory(UserFactory)


class IndicatorDeviceTypeFactory(DeviceTypeFactory):
    """DeviceType pinned to the indicator code."""

    code = DeviceType.TYPE_INDICATOR
    name = "Indicator/Status Light"


class IndicatorDeviceFactory(ESP32DeviceFactory):
    """An indicator-type ESP32 device (online by default)."""

    device_type = SubFactory(IndicatorDeviceTypeFactory)
    is_online = True


class RoomOperationalModeFactory(DjangoModelFactory):
    """Factory for creating RoomOperationalMode instances."""

    class Meta:
        model = RoomOperationalMode

    location = SubFactory(LocationFactory)
    mode = IndicatorStatus.AVAILABLE


class IndicatorBindingFactory(DjangoModelFactory):
    """Factory for creating IndicatorBinding instances (asset-bound by default)."""

    class Meta:
        model = IndicatorBinding

    device = SubFactory(IndicatorDeviceFactory)
    asset = SubFactory(AssetFactory)
    location = None
