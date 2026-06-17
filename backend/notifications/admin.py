"""
Admin configuration for notifications app.
"""

from django.contrib import admin

from .models import AccountSecurityAuditEvent, KnownDevice, Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    """Admin interface for Notification model."""

    list_display = ["id", "user", "type", "title", "read", "created_at"]
    list_filter = ["type", "read", "created_at"]
    search_fields = ["title", "message", "user__username", "user__email"]
    readonly_fields = ["created_at"]
    date_hierarchy = "created_at"

    fieldsets = (
        ("Basic Information", {"fields": ("user", "type", "title", "message")}),
        ("Status", {"fields": ("read", "created_at")}),
        ("Actions", {"fields": ("action_url", "metadata"), "classes": ("collapse",)}),
    )


@admin.register(KnownDevice)
class KnownDeviceAdmin(admin.ModelAdmin):
    """Read-only-ish admin for inspecting known login devices and alerts."""

    list_display = ["id", "user", "ip_address", "label", "first_seen", "last_seen"]
    list_filter = ["first_seen", "last_seen"]
    search_fields = ["user__username", "user__email", "device_token", "ip_address"]
    readonly_fields = ["device_token", "fingerprint_hash", "first_seen", "last_seen"]
    date_hierarchy = "last_seen"


@admin.register(AccountSecurityAuditEvent)
class AccountSecurityAuditEventAdmin(admin.ModelAdmin):
    """Read-only view of the append-only account-security audit trail."""

    list_display = ["id", "action", "actor", "ip_address", "created_at"]
    list_filter = ["action", "created_at"]
    search_fields = ["actor__username", "actor__email", "ip_address"]
    readonly_fields = [
        "id",
        "action",
        "actor",
        "ip_address",
        "user_agent",
        "metadata",
        "created_at",
    ]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
