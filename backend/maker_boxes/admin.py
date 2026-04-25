"""Admin registration for the maker box app."""

from django.contrib import admin

from .models import MakerBox


@admin.register(MakerBox)
class MakerBoxAdmin(admin.ModelAdmin):
    list_display = [
        "bin_id",
        "assigned_username",
        "first_name",
        "last_name",
        "status",
        "expires_at",
        "last_verified_at",
    ]
    list_filter = ["status"]
    search_fields = ["bin_id", "assigned_username", "first_name", "last_name", "email"]
    readonly_fields = ["created_at", "updated_at", "last_verified_at"]
    fieldsets = (
        (None, {"fields": ("bin_id", "status", "notes")}),
        (
            "Assignment",
            {
                "fields": (
                    "assigned_username",
                    "first_name",
                    "last_name",
                    "email",
                    "assigned_at",
                    "expires_at",
                    "paid_at",
                )
            },
        ),
        ("Audit", {"fields": ("last_verified_at", "created_at", "updated_at")}),
    )
