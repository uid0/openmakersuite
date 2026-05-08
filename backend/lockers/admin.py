"""Admin registrations for the Lockers app."""

from django.contrib import admin

from .models import Locker, LockerDevice, LockerOtp


class LockerDeviceInline(admin.TabularInline):
    model = LockerDevice
    extra = 0
    fields = ("device", "role", "is_primary", "notes")
    autocomplete_fields = ("device",)


@admin.register(Locker)
class LockerAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "location",
        "owning_sig",
        "power_source",
        "is_high_trust",
        "is_active",
        "current_asset",
    )
    list_filter = (
        "is_active",
        "is_high_trust",
        "power_source",
        "owning_sig",
    )
    search_fields = ("name", "slug", "description", "location__name")
    prepopulated_fields = {"slug": ("name",)}
    autocomplete_fields = ("location", "owning_sig", "current_asset")
    filter_horizontal = ("required_certifications",)
    inlines = [LockerDeviceInline]
    readonly_fields = ("created_at", "updated_at")
    ordering = ("location__name", "name")


@admin.register(LockerDevice)
class LockerDeviceAdmin(admin.ModelAdmin):
    list_display = ("locker", "device", "role", "is_primary")
    list_filter = ("role", "is_primary")
    search_fields = ("locker__name", "device__mac_address", "device__name")
    autocomplete_fields = ("locker", "device")


@admin.register(LockerOtp)
class LockerOtpAdmin(admin.ModelAdmin):
    list_display = (
        "locker",
        "requesting_user",
        "code",
        "expires_at",
        "used_at",
        "revoked_at",
        "state",
    )
    list_filter = (
        ("used_at", admin.EmptyFieldListFilter),
        ("revoked_at", admin.EmptyFieldListFilter),
    )
    search_fields = ("locker__name", "requesting_user__username", "code")
    autocomplete_fields = ("locker", "requesting_user", "revoked_by")
    readonly_fields = ("created_at",)
    ordering = ("-created_at",)
