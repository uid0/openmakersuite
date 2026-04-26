"""
Admin interface for ForgeKey models.
"""

from django.contrib import admin, messages
from django.utils.html import format_html

from .models import (
    AssetAuthorization,
    AssetDevice,
    DeviceFirmwareUpdate,
    DeviceLockout,
    DeviceType,
    DeviceUsage,
    ESP32Device,
    ESP32DevicePhoto,
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
        "location",
        "firmware_version",
        "is_online",
        "is_active",
        "last_seen",
        "last_photo_thumb",
    ]
    list_filter = ["device_type", "is_online", "is_active", "location"]
    search_fields = ["mac_address", "name", "description"]
    readonly_fields = [
        "id",
        "created_at",
        "updated_at",
        "enrollment_photo_preview",
        "last_photo_preview",
    ]
    raw_id_fields = ["device_type", "location"]
    fields = (
        "id",
        "mac_address",
        "name",
        "device_type",
        "description",
        "firmware_version",
        "location",
        "is_online",
        "is_active",
        "last_seen",
        "boot_count",
        "free_heap",
        "ip",
        "enrollment_photo",
        "enrollment_photo_preview",
        "last_photo",
        "last_photo_preview",
        "created_at",
        "updated_at",
    )

    @admin.display(description="Last photo")
    def last_photo_thumb(self, obj):
        if obj.last_photo:
            return format_html('<img src="{}" style="max-height: 48px;"/>', obj.last_photo.url)
        return "—"

    @admin.display(description="Enrollment photo")
    def enrollment_photo_preview(self, obj):
        if obj.enrollment_photo:
            return format_html(
                '<img src="{}" style="max-height: 320px;"/>',
                obj.enrollment_photo.url,
            )
        return "—"

    @admin.display(description="Latest periodic photo")
    def last_photo_preview(self, obj):
        if obj.last_photo:
            return format_html('<img src="{}" style="max-height: 320px;"/>', obj.last_photo.url)
        return "—"


@admin.register(ESP32DevicePhoto)
class ESP32DevicePhotoAdmin(admin.ModelAdmin):
    """Read-only gallery of periodic surveillance photos."""

    list_display = ["device", "received_at", "captured_at", "thumbnail"]
    list_filter = ["device__device_type", "received_at"]
    search_fields = ["device__mac_address", "device__name"]
    readonly_fields = ["id", "device", "image", "captured_at", "received_at", "preview"]
    fields = ("id", "device", "image", "preview", "captured_at", "received_at")
    raw_id_fields: list = []

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    @admin.display(description="Thumbnail")
    def thumbnail(self, obj):
        if obj.image:
            return format_html('<img src="{}" style="max-height: 48px;"/>', obj.image.url)
        return "—"

    @admin.display(description="Preview")
    def preview(self, obj):
        if obj.image:
            return format_html('<img src="{}" style="max-height: 480px;"/>', obj.image.url)
        return "—"


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

    list_display = [
        "version",
        "device_type",
        "mandatory",
        "sha256_short",
        "is_active",
        "created_at",
        "created_by",
    ]
    list_filter = ["device_type", "mandatory", "is_active", "created_at"]
    search_fields = ["version", "device_type__name", "release_notes", "sha256"]
    readonly_fields = ["id", "created_at", "sha256"]
    raw_id_fields = ["device_type", "created_by"]
    actions = ["dispatch_firmware_update"]

    @admin.display(description="SHA-256 (short)")
    def sha256_short(self, obj):
        return obj.sha256[:12] if obj.sha256 else "—"

    @admin.action(description="Dispatch firmware update via MQTT")
    def dispatch_firmware_update(self, request, queryset):
        from .services.firmware_dispatch import dispatch_to_device_type

        total = 0
        for firmware in queryset:
            records = dispatch_to_device_type(firmware, requested_by=request.user)
            total += len(records)
        self.message_user(
            request,
            f"Dispatched {queryset.count()} firmware version(s) to {total} device(s).",
            level=messages.SUCCESS,
        )


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
