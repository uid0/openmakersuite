"""Admin interface for the vendors app."""

from django.contrib import admin

from .models import Vendor


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    """Admin interface for third-party service vendors."""

    list_display = [
        "name",
        "vendor_kind",
        "phone",
        "email",
        "tdlr_license_number",
        "tdlr_license_expires_at",
        "coi_expires_at",
        "is_active",
    ]
    list_filter = ["vendor_kind", "is_active"]
    search_fields = [
        "name",
        "contact_name",
        "phone",
        "email",
        "tdlr_license_number",
        "coi_policy_number",
    ]
    readonly_fields = ["id", "created_at", "updated_at"]
    fieldsets = (
        (None, {"fields": ("id", "name", "vendor_kind", "is_active", "notes")}),
        (
            "Contact",
            {"fields": ("contact_name", "phone", "email", "website", "address")},
        ),
        (
            "TDLR License",
            {"fields": ("tdlr_license_number", "tdlr_license_expires_at")},
        ),
        (
            "Certificate of Insurance",
            {"fields": ("coi_provider", "coi_policy_number", "coi_expires_at")},
        ),
        ("Audit", {"fields": ("created_at", "updated_at")}),
    )
