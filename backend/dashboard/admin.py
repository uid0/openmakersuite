"""
Django admin configuration for dashboard management.
"""

from django.contrib import admin
from django.utils.html import format_html

from .models import DashboardConfig, DashboardMessage


@admin.register(DashboardMessage)
class DashboardMessageAdmin(admin.ModelAdmin):
    """Admin interface for managing dashboard messages."""

    list_display = ["message_preview", "is_active", "order", "created_at", "updated_at"]
    list_filter = ["is_active", "created_at", "updated_at"]
    list_editable = ["is_active", "order"]
    search_fields = ["message"]
    ordering = ["order", "created_at"]

    fieldsets = (
        ("Message Content", {"fields": ("message", "is_active")}),
        (
            "Display Settings",
            {"fields": ("order",), "description": "Lower order numbers are displayed first"},
        ),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    readonly_fields = ["created_at", "updated_at"]

    def message_preview(self, obj):
        """Show truncated message with styling."""
        if len(obj.message) > 50:
            preview = obj.message[:47] + "..."
        else:
            preview = obj.message

        color = "#28a745" if obj.is_active else "#6c757d"
        return format_html('<span style="color: {};">{}</span>', color, preview)

    message_preview.short_description = "Message"
    message_preview.admin_order_field = "message"

    actions = ["activate_messages", "deactivate_messages"]

    def activate_messages(self, request, queryset):
        """Bulk activate selected messages."""
        count = queryset.update(is_active=True)
        self.message_user(request, f"{count} message(s) activated successfully.")

    activate_messages.short_description = "Activate selected messages"

    def deactivate_messages(self, request, queryset):
        """Bulk deactivate selected messages."""
        count = queryset.update(is_active=False)
        self.message_user(request, f"{count} message(s) deactivated successfully.")

    deactivate_messages.short_description = "Deactivate selected messages"


@admin.register(DashboardConfig)
class DashboardConfigAdmin(admin.ModelAdmin):
    """Admin interface for dashboard configuration."""

    list_display = [
        "config_summary",
        "rotation_interval_seconds",
        "auto_refresh_seconds",
        "is_maintenance_mode",
        "updated_at",
    ]

    fieldsets = (
        (
            "Message Settings",
            {
                "fields": ("rotation_interval_seconds",),
                "description": "Controls how fast messages rotate in the footer",
            },
        ),
        (
            "Refresh Settings",
            {
                "fields": ("auto_refresh_seconds",),
                "description": "Controls how often dashboard data updates",
            },
        ),
        (
            "Maintenance Mode",
            {
                "fields": ("is_maintenance_mode", "maintenance_message"),
                "description": "Enable to show maintenance message instead of normal dashboard",
            },
        ),
        (
            "Advanced",
            {
                "fields": ("custom_css",),
                "classes": ("collapse",),
                "description": "Optional custom styling (for advanced users)",
            },
        ),
        ("System Info", {"fields": ("updated_at",), "classes": ("collapse",)}),
    )

    readonly_fields = ["updated_at"]

    def config_summary(self, obj):
        """Show configuration summary."""
        status = "Maintenance" if obj.is_maintenance_mode else "Normal"
        return format_html(
            "<strong>{}</strong><br><small>{} active messages</small>",
            status,
            DashboardMessage.objects.filter(is_active=True).count(),
        )

    config_summary.short_description = "Status"

    def has_add_permission(self, request):
        """Only allow one configuration instance."""
        return not DashboardConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        """Don't allow deleting the configuration."""
        return False

    def save_model(self, request, obj, form, change):
        """Ensure only one config instance exists."""
        if not change:  # Creating new object
            # Delete any existing configs (shouldn't happen, but safety check)
            DashboardConfig.objects.all().delete()
        super().save_model(request, obj, form, change)
