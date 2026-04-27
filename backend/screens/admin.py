"""
Admin configuration for the screens app.
"""

from urllib.parse import urlencode

from django.contrib import admin
from django.utils.html import format_html

from .models import Screen, ScreenContentBlock, ScreenHeartbeat, SystemMessage


class ScreenContentBlockInline(admin.TabularInline):
    model = ScreenContentBlock
    extra = 0
    fields = ["order", "block_type", "title", "is_enabled"]
    ordering = ["order"]


class ScreenHeartbeatInline(admin.TabularInline):
    model = ScreenHeartbeat
    extra = 0
    fields = ["reported_at", "user_agent", "client_ip", "content_version"]
    readonly_fields = ["reported_at", "user_agent", "client_ip", "content_version"]
    can_delete = False
    max_num = 5
    ordering = ["-reported_at"]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Screen)
class ScreenAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "sig", "location", "is_active", "is_online", "updated_at"]
    list_filter = ["is_active", "sig", "location"]
    search_fields = ["name", "slug", "description"]
    readonly_fields = ["id", "preview_link", "access_token", "created_at", "updated_at"]
    inlines = [ScreenContentBlockInline, ScreenHeartbeatInline]

    def get_fields(self, request, obj=None):
        fields = [f for f in super().get_fields(request, obj) if f != "preview_link"]
        if obj is not None and not obj._state.adding:
            fields = ["preview_link"] + fields
        return fields

    @admin.display(description="Preview")
    def preview_link(self, obj):
        if obj is None or obj._state.adding:
            return "(save the screen to generate a preview link)"
        query = urlencode({"token": obj.access_token})
        url = f"/kiosk/{obj.slug}?{query}"
        return format_html(
            '<a href="{}" target="_blank" rel="noopener">Open kiosk preview ↗</a>'
            "<br/><small>Opens the public kiosk URL in a new tab.</small>",
            url,
        )


@admin.register(SystemMessage)
class SystemMessageAdmin(admin.ModelAdmin):
    list_display = ["title", "level", "is_active", "starts_at", "ends_at", "updated_at"]
    list_filter = ["level", "is_active"]
    search_fields = ["title", "body"]
    readonly_fields = ["id", "created_at", "updated_at"]


@admin.register(ScreenContentBlock)
class ScreenContentBlockAdmin(admin.ModelAdmin):
    list_display = ["screen", "block_type", "title", "order", "is_enabled"]
    list_filter = ["block_type", "is_enabled", "screen"]
    search_fields = ["title", "body", "screen__name"]


@admin.register(ScreenHeartbeat)
class ScreenHeartbeatAdmin(admin.ModelAdmin):
    list_display = ["screen", "reported_at", "client_ip", "content_version"]
    list_filter = ["screen"]
    readonly_fields = ["id", "screen", "reported_at", "user_agent", "client_ip", "content_version"]

    def has_add_permission(self, request):
        return False
