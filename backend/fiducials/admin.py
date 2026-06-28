from django.contrib import admin

from .models import AprilTagAssignment


@admin.register(AprilTagAssignment)
class AprilTagAssignmentAdmin(admin.ModelAdmin):
    list_display = (
        "family",
        "tag_id",
        "content_type",
        "object_id",
        "allocated_at",
        "released_at",
        "is_active",
    )
    list_filter = ("family", "content_type", ("released_at", admin.EmptyFieldListFilter))
    search_fields = ("tag_id", "object_id")
    readonly_fields = ("allocated_at",)
    ordering = ("family", "tag_id")
