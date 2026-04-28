"""Admin interfaces for the maintenance_orders app."""

from django.contrib import admin

from .models import ThirdPartyWorkOrder, ThirdPartyWorkOrderAsset, ThirdPartyWorkOrderAttachment


class ThirdPartyWorkOrderAssetInline(admin.TabularInline):
    model = ThirdPartyWorkOrderAsset
    extra = 0
    autocomplete_fields = ["asset"]
    fields = ["asset", "share_pct", "notes"]


class ThirdPartyWorkOrderAttachmentInline(admin.TabularInline):
    model = ThirdPartyWorkOrderAttachment
    extra = 0
    fields = ["kind", "file", "caption", "uploaded_by", "uploaded_at"]
    readonly_fields = ["uploaded_at"]


@admin.register(ThirdPartyWorkOrder)
class ThirdPartyWorkOrderAdmin(admin.ModelAdmin):
    list_display = [
        "short_id",
        "title",
        "vendor",
        "asset",
        "work_type",
        "is_emergency",
        "status",
        "nte_amount",
        "actual_invoice_total",
        "opened_at",
    ]
    list_filter = ["status", "work_type", "is_emergency", "warranty_recovery"]
    search_fields = [
        "id",
        "title",
        "vendor__name",
        "asset__name",
        "asset__asset_tag",
        "keyfob_id",
        "notes",
        "internal_notes",
    ]
    readonly_fields = ["id", "opened_at", "created_at", "updated_at"]
    autocomplete_fields = ["vendor", "asset", "shadow_user", "opened_by"]
    inlines = [ThirdPartyWorkOrderAssetInline, ThirdPartyWorkOrderAttachmentInline]
    date_hierarchy = "opened_at"
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "id",
                    "title",
                    "vendor",
                    "asset",
                    "work_type",
                    "is_emergency",
                    "status",
                )
            },
        ),
        (
            "Budget",
            {
                "fields": (
                    "nte_amount",
                    "par_cost_buffer",
                    "actual_invoice_total",
                    "dispatch_fee",
                    "warranty_recovery",
                )
            },
        ),
        (
            "Site Access",
            {
                "fields": (
                    "downtime_start",
                    "downtime_end",
                    "keyfob_id",
                    "shadow_user",
                )
            },
        ),
        (
            "Notes",
            {"fields": ("notes", "internal_notes")},
        ),
        (
            "Audit",
            {
                "fields": (
                    "opened_by",
                    "opened_at",
                    "closed_at",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )


@admin.register(ThirdPartyWorkOrderAsset)
class ThirdPartyWorkOrderAssetAdmin(admin.ModelAdmin):
    list_display = ["work_order", "asset", "share_pct", "created_at"]
    list_filter = []
    search_fields = ["work_order__id", "asset__name", "asset__asset_tag"]
    autocomplete_fields = ["work_order", "asset"]
    readonly_fields = ["id", "created_at"]


@admin.register(ThirdPartyWorkOrderAttachment)
class ThirdPartyWorkOrderAttachmentAdmin(admin.ModelAdmin):
    list_display = ["work_order", "kind", "caption", "uploaded_by", "uploaded_at"]
    list_filter = ["kind", "uploaded_at"]
    search_fields = ["work_order__id", "caption"]
    autocomplete_fields = ["work_order", "uploaded_by"]
    readonly_fields = ["id", "uploaded_at"]
