"""Admin interface for the vendors app."""

from datetime import timedelta

from django.contrib import admin
from django.utils import timezone

from .models import Vendor


class _ComplianceFilterBase(admin.SimpleListFilter):
    """Shared 'expiring soon / expired' filter scaffold for compliance dates."""

    expires_field: str = ""

    def lookups(self, request, model_admin):
        return (
            ("expiring_soon", "Expiring within 30 days"),
            ("expired", "Expired"),
            ("missing", "No date on file"),
        )

    def queryset(self, request, queryset):
        today = timezone.now().date()
        soon = today + timedelta(days=30)
        field = self.expires_field
        if self.value() == "expiring_soon":
            return queryset.filter(**{f"{field}__gte": today, f"{field}__lte": soon})
        if self.value() == "expired":
            return queryset.filter(**{f"{field}__lt": today})
        if self.value() == "missing":
            return queryset.filter(**{f"{field}__isnull": True})
        return queryset


class TDLRComplianceFilter(_ComplianceFilterBase):
    title = "TDLR license status"
    parameter_name = "tdlr_status"
    expires_field = "tdlr_license_expires_at"


class COIComplianceFilter(_ComplianceFilterBase):
    title = "COI status"
    parameter_name = "coi_status"
    expires_field = "coi_expires_at"


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
    list_filter = [
        "vendor_kind",
        "is_active",
        TDLRComplianceFilter,
        COIComplianceFilter,
    ]
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
