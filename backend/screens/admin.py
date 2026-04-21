"""
Admin configuration for the screens app.
"""

from django.contrib import admin

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
    readonly_fields = ["id", "access_token", "created_at", "updated_at"]
    inlines = [ScreenContentBlockInline, ScreenHeartbeatInline]


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
