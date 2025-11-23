"""
Admin configuration for checklist models.
"""

from django.contrib import admin

from .models import Checklist, ChecklistCompletion, ChecklistStep, ChecklistStepCompletion


class ChecklistStepInline(admin.TabularInline):
    """Inline admin for checklist steps."""

    model = ChecklistStep
    extra = 1
    fields = [
        "step_number",
        "name",
        "asset",
        "location",
        "inventory_item",
        "required",
        "notes",
    ]
    ordering = ["step_number"]


@admin.register(Checklist)
class ChecklistAdmin(admin.ModelAdmin):
    """Admin interface for Checklist model."""

    list_display = ["name", "sig", "is_active", "is_public", "created_by", "created_at"]
    list_filter = ["is_active", "is_public", "sig", "created_at"]
    search_fields = ["name", "description", "sig__name"]
    readonly_fields = ["id", "created_at", "updated_at"]
    inlines = [ChecklistStepInline]
    fieldsets = (
        (
            "Basic Information",
            {
                "fields": ("id", "name", "description", "sig"),
            },
        ),
        (
            "Settings",
            {
                "fields": ("is_active", "is_public", "created_by"),
            },
        ),
        (
            "Timestamps",
            {
                "fields": ("created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )


class ChecklistStepCompletionInline(admin.TabularInline):
    """Inline admin for step completions."""

    model = ChecklistStepCompletion
    extra = 0
    readonly_fields = [
        "scanned_at",
        "scanned_asset",
        "scanned_location",
        "scanned_item",
    ]
    fields = [
        "step",
        "scanned_at",
        "scanned_asset",
        "scanned_location",
        "scanned_item",
        "notes",
    ]


@admin.register(ChecklistCompletion)
class ChecklistCompletionAdmin(admin.ModelAdmin):
    """Admin interface for ChecklistCompletion model."""

    list_display = [
        "checklist",
        "user",
        "user_name",
        "status",
        "started_at",
        "completed_at",
    ]
    list_filter = ["status", "checklist", "started_at", "completed_at"]
    search_fields = [
        "checklist__name",
        "user__username",
        "user_name",
    ]
    readonly_fields = ["id", "started_at", "created_at", "updated_at"]
    inlines = [ChecklistStepCompletionInline]
    fieldsets = (
        (
            "Basic Information",
            {
                "fields": ("id", "checklist", "user", "user_name"),
            },
        ),
        (
            "Status",
            {
                "fields": ("status", "started_at", "completed_at"),
            },
        ),
        (
            "Timestamps",
            {
                "fields": ("created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )


@admin.register(ChecklistStep)
class ChecklistStepAdmin(admin.ModelAdmin):
    """Admin interface for ChecklistStep model."""

    list_display = [
        "checklist",
        "step_number",
        "name",
        "asset",
        "location",
        "inventory_item",
        "required",
    ]
    list_filter = ["checklist", "required"]
    search_fields = ["name", "checklist__name"]
    readonly_fields = ["id"]
    ordering = ["checklist", "step_number"]


@admin.register(ChecklistStepCompletion)
class ChecklistStepCompletionAdmin(admin.ModelAdmin):
    """Admin interface for ChecklistStepCompletion model."""

    list_display = [
        "completion",
        "step",
        "scanned_at",
        "scanned_asset",
        "scanned_location",
        "scanned_item",
    ]
    list_filter = ["scanned_at", "completion__checklist"]
    search_fields = [
        "completion__checklist__name",
        "step__name",
        "notes",
    ]
    readonly_fields = ["id", "scanned_at"]
