"""
Admin interface for ForgeKey models.
"""

from django.contrib import admin

from .models import (
    AssetAuthorization,
    AssetDevice,
    DeviceFirmwareUpdate,
    DeviceLockout,
    DeviceType,
    DeviceUsage,
    ESP32Device,
    FirmwareVersion,
    OperationalMode,
    PowerMeterReading,
)


@admin.register(DeviceType)
class DeviceTypeAdmin(admin.ModelAdmin):
    """Admin interface for device types."""

    list_display = ["name", "code", "is_active"]
    list_filter = ["is_active", "code"]
    search_fields = ["name", "code", "description"]


@admin.register(ESP32Device)
class ESP32DeviceAdmin(admin.ModelAdmin):
    """Admin interface for ESP32 devices."""

    list_display = [
        "mac_address",
        "name",
        "device_type",
        "firmware_version",
        "is_online",
        "is_active",
        "last_seen",
    ]
    list_filter = ["device_type", "is_online", "is_active"]
    search_fields = ["mac_address", "name", "description"]
    readonly_fields = ["id", "created_at", "updated_at"]


@admin.register(AssetDevice)
class AssetDeviceAdmin(admin.ModelAdmin):
    """Admin interface for asset-device relationships."""

    list_display = ["asset", "device", "role", "is_primary", "power_off_delay_seconds"]
    list_filter = ["is_primary", "role"]
    search_fields = ["asset__name", "device__mac_address", "device__name"]
    raw_id_fields = ["asset", "device"]


@admin.register(OperationalMode)
class OperationalModeAdmin(admin.ModelAdmin):
    """Admin interface for operational modes."""

    list_display = [
        "asset",
        "mode",
        "classroom_mode_enabled",
        "classroom_mode_enabled_by",
        "updated_at",
    ]
    list_filter = ["mode", "classroom_mode_enabled"]
    search_fields = ["asset__name"]
    raw_id_fields = ["asset", "classroom_mode_enabled_by"]


@admin.register(AssetAuthorization)
class AssetAuthorizationAdmin(admin.ModelAdmin):
    """Admin interface for asset authorizations."""

    list_display = ["asset", "user", "authorized_by", "authorized_at", "is_active"]
    list_filter = ["is_active", "authorized_at"]
    search_fields = ["asset__name", "user__username", "user__email"]
    raw_id_fields = ["asset", "user", "authorized_by"]


@admin.register(DeviceLockout)
class DeviceLockoutAdmin(admin.ModelAdmin):
    """Admin interface for device lockouts."""

    list_display = [
        "asset",
        "locked_by",
        "lockout_level",
        "locked_at",
        "is_active",
        "unlocked_at",
        "unlocked_by",
    ]
    list_filter = ["lockout_level", "is_active", "locked_at"]
    search_fields = ["asset__name", "locked_by__username", "reason"]
    readonly_fields = ["id", "locked_at"]
    raw_id_fields = ["asset", "locked_by", "unlocked_by"]


@admin.register(DeviceUsage)
class DeviceUsageAdmin(admin.ModelAdmin):
    """Admin interface for device usage sessions."""

    list_display = [
        "asset",
        "user",
        "started_at",
        "ended_at",
        "duration_seconds",
        "power_consumption_kwh",
    ]
    list_filter = ["started_at", "ended_at"]
    search_fields = ["asset__name", "user__username"]
    readonly_fields = ["id", "started_at"]
    raw_id_fields = ["asset", "user"]


@admin.register(PowerMeterReading)
class PowerMeterReadingAdmin(admin.ModelAdmin):
    """Admin interface for power meter readings."""

    list_display = [
        "asset",
        "device",
        "timestamp",
        "voltage",
        "current",
        "power",
        "energy",
    ]
    list_filter = ["timestamp"]
    search_fields = ["asset__name", "device__mac_address"]
    readonly_fields = ["id", "timestamp"]
    raw_id_fields = ["asset", "device", "usage_session"]


@admin.register(FirmwareVersion)
class FirmwareVersionAdmin(admin.ModelAdmin):
    """Admin interface for firmware versions."""

    list_display = ["version", "device_type", "is_active", "created_at", "created_by"]
    list_filter = ["device_type", "is_active", "created_at"]
    search_fields = ["version", "device_type__name", "release_notes"]
    readonly_fields = ["id", "created_at"]
    raw_id_fields = ["device_type", "created_by"]


@admin.register(DeviceFirmwareUpdate)
class DeviceFirmwareUpdateAdmin(admin.ModelAdmin):
    """Admin interface for firmware updates."""

    list_display = [
        "device",
        "firmware_version",
        "status",
        "requested_at",
        "requested_by",
        "completed_at",
    ]
    list_filter = ["status", "requested_at"]
    search_fields = ["device__mac_address", "device__name", "firmware_version__version"]
    readonly_fields = ["id", "requested_at"]
    raw_id_fields = ["device", "firmware_version", "requested_by"]
