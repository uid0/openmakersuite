"""
Serializers for ForgeKey models.
"""

from rest_framework import serializers

from .models import (
    AssetAuthorization,
    AssetDevice,
    DeviceFirmwareUpdate,
    DeviceLockout,
    DeviceType,
    DeviceUsage,
    ESP32Device,
    FirmwareVersion,
    OccupancyEvent,
    OperationalMode,
    PowerMeterReading,
)


class DeviceTypeSerializer(serializers.ModelSerializer):
    """Serializer for DeviceType model."""

    class Meta:
        model = DeviceType
        fields = "__all__"


class ESP32DeviceSerializer(serializers.ModelSerializer):
    """Serializer for ESP32Device model."""

    device_type_name = serializers.CharField(source="device_type.name", read_only=True)

    class Meta:
        model = ESP32Device
        fields = "__all__"
        read_only_fields = ["id", "created_at", "updated_at"]


class AssetDeviceSerializer(serializers.ModelSerializer):
    """Serializer for AssetDevice model."""

    asset_name = serializers.CharField(source="asset.name", read_only=True)
    device_mac_address = serializers.CharField(source="device.mac_address", read_only=True)
    device_name = serializers.CharField(source="device.name", read_only=True)

    class Meta:
        model = AssetDevice
        fields = "__all__"
        read_only_fields = ["created_at"]


class OperationalModeSerializer(serializers.ModelSerializer):
    """Serializer for OperationalMode model."""

    asset_name = serializers.CharField(source="asset.name", read_only=True)
    classroom_mode_enabled_by_username = serializers.CharField(
        source="classroom_mode_enabled_by.username", read_only=True
    )

    class Meta:
        model = OperationalMode
        fields = "__all__"
        read_only_fields = ["updated_at"]


class AssetAuthorizationSerializer(serializers.ModelSerializer):
    """Serializer for AssetAuthorization model."""

    asset_name = serializers.CharField(source="asset.name", read_only=True)
    username = serializers.CharField(source="user.username", read_only=True)
    authorized_by_username = serializers.CharField(source="authorized_by.username", read_only=True)

    class Meta:
        model = AssetAuthorization
        fields = "__all__"
        read_only_fields = ["authorized_at"]


class DeviceLockoutSerializer(serializers.ModelSerializer):
    """Serializer for DeviceLockout model."""

    asset_name = serializers.CharField(source="asset.name", read_only=True)
    locked_by_username = serializers.CharField(source="locked_by.username", read_only=True)
    unlocked_by_username = serializers.CharField(source="unlocked_by.username", read_only=True)

    class Meta:
        model = DeviceLockout
        fields = "__all__"
        read_only_fields = ["id", "locked_at", "lockout_level"]


class DeviceUsageSerializer(serializers.ModelSerializer):
    """Serializer for DeviceUsage model."""

    asset_name = serializers.CharField(source="asset.name", read_only=True)
    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = DeviceUsage
        fields = "__all__"
        read_only_fields = ["id", "started_at"]


class PowerMeterReadingSerializer(serializers.ModelSerializer):
    """Serializer for PowerMeterReading model."""

    asset_name = serializers.CharField(source="asset.name", read_only=True)
    device_mac_address = serializers.CharField(source="device.mac_address", read_only=True)

    class Meta:
        model = PowerMeterReading
        fields = "__all__"
        read_only_fields = ["id", "timestamp"]


class FirmwareVersionSerializer(serializers.ModelSerializer):
    """Serializer for FirmwareVersion model."""

    device_type_name = serializers.CharField(source="device_type.name", read_only=True)
    created_by_username = serializers.CharField(source="created_by.username", read_only=True)

    class Meta:
        model = FirmwareVersion
        fields = "__all__"
        read_only_fields = ["id", "created_at"]


class OccupancyEventSerializer(serializers.ModelSerializer):
    """Serializer for OccupancyEvent rows, used by the device-detail chart."""

    occupancy_delta = serializers.IntegerField(read_only=True)

    class Meta:
        model = OccupancyEvent
        fields = [
            "id",
            "device",
            "sensor_kind",
            "count_in",
            "count_out",
            "occupancy_delta",
            "event_timestamp_utc",
            "ingested_at",
            "raw_payload",
        ]
        read_only_fields = ["id", "ingested_at", "occupancy_delta"]


class DeviceFirmwareUpdateSerializer(serializers.ModelSerializer):
    """Serializer for DeviceFirmwareUpdate model."""

    device_mac_address = serializers.CharField(source="device.mac_address", read_only=True)
    firmware_version_string = serializers.CharField(
        source="firmware_version.version", read_only=True
    )
    requested_by_username = serializers.CharField(source="requested_by.username", read_only=True)

    class Meta:
        model = DeviceFirmwareUpdate
        fields = "__all__"
        read_only_fields = ["id", "requested_at", "updated_at"]
