"""
Admin configuration for membership app.
"""

from django.contrib import admin
from django.contrib.auth import get_user_model

from .models import Membership

User = get_user_model()


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    """Admin interface for custom user model."""

    list_display = [
        "username",
        "handle",
        "email",
        "first_name",
        "last_name",
        "active_directory_username",
        "badge_number",
        "discord_username",
        "discourse_username",
        "is_board_member",
        "is_officer",
        "is_director",
        "is_staff",
        "is_active",
        "date_joined",
    ]
    list_filter = [
        "is_staff",
        "is_superuser",
        "is_active",
        "is_board_member",
        "is_officer",
        "is_director",
        "groups",
        "date_joined",
    ]
    search_fields = [
        "username",
        "handle",
        "email",
        "first_name",
        "last_name",
        "active_directory_username",
        "badge_number",
        "discord_username",
        "discourse_username",
    ]
    fieldsets = (
        (
            "Basic Information",
            {
                "fields": (
                    "username",
                    "handle",
                    "password",
                    "email",
                    "first_name",
                    "last_name",
                )
            },
        ),
        (
            "Makerspace Information",
            {
                "fields": (
                    "active_directory_username",
                    "badge_number",
                    "discord_username",
                    "discourse_username",
                    "is_board_member",
                    "is_officer",
                    "is_director",
                )
            },
        ),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        (
            "Important dates",
            {
                "fields": (
                    "last_login",
                    "date_joined",
                )
            },
        ),
    )
    readonly_fields = ["last_login", "date_joined"]


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    """Admin interface for Membership model."""

    list_display = [
        "id",
        "membership_type",
        "status",
        "get_users",
        "start_date",
        "end_date",
        "created_at",
    ]
    list_filter = [
        "membership_type",
        "status",
        "start_date",
        "end_date",
        "created_at",
    ]
    search_fields = [
        "users__username",
        "users__email",
        "users__handle",
        "notes",
    ]
    filter_horizontal = ["users"]
    fieldsets = (
        (
            "Membership Information",
            {
                "fields": (
                    "membership_type",
                    "status",
                    "users",
                )
            },
        ),
        (
            "Dates",
            {
                "fields": (
                    "start_date",
                    "end_date",
                )
            },
        ),
        (
            "Notes",
            {
                "fields": ("notes",),
            },
        ),
    )
    readonly_fields = ["created_at", "updated_at"]

    def get_users(self, obj):
        """Display users associated with this membership."""
        users = obj.users.all()
        if users:
            return ", ".join([user.username for user in users[:5]])
        return "No users"
    get_users.short_description = "Users"
