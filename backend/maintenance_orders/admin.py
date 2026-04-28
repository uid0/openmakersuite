"""Admin interfaces for the maintenance_orders app."""

from django.contrib import admin

from .models import (
    AssetWarranty,
    EmergencyAuthorization,
    RecoveryTask,
    ThirdPartyWorkOrder,
    ThirdPartyWorkOrderAsset,
    ThirdPartyWorkOrderAttachment,
    ThirdPartyWorkOrderAuditLog,
    ThirdPartyWorkOrderQuote,
)


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


@admin.register(AssetWarranty)
class AssetWarrantyAdmin(admin.ModelAdmin):
    list_display = ["asset", "provider", "install_date", "end_date", "duration_days", "active"]
    list_filter = ["provider"]
    search_fields = ["asset__name", "asset__asset_tag", "provider", "policy_number"]
    autocomplete_fields = ["asset"]
    readonly_fields = ["id", "created_at", "updated_at"]
    fieldsets = (
        (None, {"fields": ("id", "asset", "provider", "policy_number")}),
        ("Coverage", {"fields": ("install_date", "duration_days", "end_date")}),
        ("Notes", {"fields": ("notes",)}),
        ("Audit", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(boolean=True, description="Active")
    def active(self, obj):
        return obj.is_active


@admin.register(ThirdPartyWorkOrderQuote)
class ThirdPartyWorkOrderQuoteAdmin(admin.ModelAdmin):
    list_display = ["work_order", "vendor", "amount", "submitted_by", "created_at"]
    search_fields = ["work_order__id", "vendor__name", "notes"]
    autocomplete_fields = ["work_order", "vendor", "submitted_by"]
    readonly_fields = ["id", "created_at"]


@admin.register(EmergencyAuthorization)
class EmergencyAuthorizationAdmin(admin.ModelAdmin):
    list_display = [
        "work_order",
        "authorized_by",
        "authorized_at",
        "expires_at",
        "revoked_at",
    ]
    list_filter = ["revoked_at"]
    search_fields = ["work_order__id", "reason"]
    autocomplete_fields = ["work_order", "authorized_by"]
    readonly_fields = ["id", "authorized_at", "expires_at"]


@admin.register(ThirdPartyWorkOrderAttachment)
class ThirdPartyWorkOrderAttachmentAdmin(admin.ModelAdmin):
    list_display = ["work_order", "kind", "caption", "uploaded_by", "uploaded_at"]
    list_filter = ["kind", "uploaded_at"]
    search_fields = ["work_order__id", "caption"]
    autocomplete_fields = ["work_order", "uploaded_by"]
    readonly_fields = ["id", "uploaded_at"]


@admin.register(ThirdPartyWorkOrderAuditLog)
class ThirdPartyWorkOrderAuditLogAdmin(admin.ModelAdmin):
    list_display = [
        "created_at",
        "work_order",
        "action",
        "actor",
        "old_state",
        "new_state",
    ]
    list_filter = ["action", "created_at"]
    search_fields = [
        "work_order__id",
        "work_order__title",
        "actor__username",
        "notes",
    ]
    readonly_fields = [
        "id",
        "work_order",
        "actor",
        "action",
        "old_state",
        "new_state",
        "notes",
        "metadata",
        "created_at",
    ]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(RecoveryTask)
class RecoveryTaskAdmin(admin.ModelAdmin):
    list_display = [
        "title",
        "status",
        "vendor",
        "amount",
        "assigned_group",
        "assigned_to",
        "created_at",
    ]
    list_filter = ["status", "assigned_group"]
    search_fields = [
        "title",
        "description",
        "work_order__id",
        "vendor__name",
    ]
    autocomplete_fields = ["work_order", "vendor", "assigned_to"]
    readonly_fields = ["id", "created_at", "updated_at"]
    date_hierarchy = "created_at"
