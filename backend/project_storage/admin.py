from django.contrib import admin

from .models import ProjectStorageEvent, ProjectStorageStint


class ProjectStorageEventInline(admin.TabularInline):
    model = ProjectStorageEvent
    extra = 0
    readonly_fields = ("event_type", "actor", "actor_label", "note", "created_at")
    can_delete = False
    ordering = ("-created_at",)


@admin.register(ProjectStorageStint)
class ProjectStorageStintAdmin(admin.ModelAdmin):
    list_display = (
        "stint_id",
        "username",
        "display_name",
        "started_at",
        "expires_at",
        "status_display",
        "storage_location_name",
    )
    list_filter = (
        "storage_location_name",
        ("started_at", admin.DateFieldListFilter),
        ("expires_at", admin.DateFieldListFilter),
    )
    search_fields = ("stint_id", "username", "first_name", "last_name", "email", "project_title")
    readonly_fields = ("stint_id", "created_at", "updated_at")
    date_hierarchy = "started_at"
    inlines = [ProjectStorageEventInline]

    def status_display(self, obj: ProjectStorageStint) -> str:
        return obj.compute_status()

    status_display.short_description = "Status"


@admin.register(ProjectStorageEvent)
class ProjectStorageEventAdmin(admin.ModelAdmin):
    list_display = ("stint", "event_type", "actor", "actor_label", "created_at")
    list_filter = ("event_type",)
    search_fields = ("stint__stint_id", "stint__username", "actor_label", "note")
    readonly_fields = ("stint", "event_type", "actor", "actor_label", "note", "created_at")
