"""
Admin interface for location check-ins.
"""

from django.contrib import admin

from .models import LocationCheckIn, LocationFeedback, LocationTask, SecurityReport


@admin.register(LocationCheckIn)
class LocationCheckInAdmin(admin.ModelAdmin):
    """Admin interface for location check-ins."""

    list_display = [
        "id",
        "location",
        "checkin_type",
        "user",
        "checked_in_at",
    ]
    list_filter = ["checkin_type", "checked_in_at", "location"]
    search_fields = ["location__name", "user__username", "notes"]
    readonly_fields = ["id", "checked_in_at"]
    date_hierarchy = "checked_in_at"


@admin.register(LocationFeedback)
class LocationFeedbackAdmin(admin.ModelAdmin):
    """Admin interface for location feedback."""

    list_display = [
        "id",
        "location",
        "feedback_type",
        "user",
        "submitted_at",
        "is_resolved",
    ]
    list_filter = ["feedback_type", "is_resolved", "submitted_at", "location"]
    search_fields = ["location__name", "message", "user__username"]
    readonly_fields = ["id", "submitted_at"]
    date_hierarchy = "submitted_at"


@admin.register(SecurityReport)
class SecurityReportAdmin(admin.ModelAdmin):
    """Admin interface for safety and cleaning reports."""

    list_display = [
        "id",
        "location",
        "report_type",
        "is_urgent",
        "user",
        "reported_at",
        "is_resolved",
        "resolved_by",
    ]
    list_filter = ["report_type", "is_urgent", "is_resolved", "reported_at", "location"]
    search_fields = ["location__name", "description", "user__username"]
    readonly_fields = ["id", "reported_at"]
    date_hierarchy = "reported_at"


@admin.register(LocationTask)
class LocationTaskAdmin(admin.ModelAdmin):
    """Admin interface for location tasks."""

    list_display = [
        "id",
        "location",
        "title",
        "status",
        "assigned_to",
        "created_at",
        "completed_at",
    ]
    list_filter = ["status", "created_at", "location"]
    search_fields = ["title", "description", "location__name"]
    readonly_fields = ["id", "created_at"]
    date_hierarchy = "created_at"
