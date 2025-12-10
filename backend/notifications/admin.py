"""
Admin configuration for notifications app.
"""

from django.contrib import admin

from .models import Notification


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
