from django.contrib import admin

from fiducials.services.allocator import get_active_tag_id

from .models import ProjectStorageEvent, ProjectStorageStint, StorageSlot
from .services.storage_slots import ensure_slot_tag


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


@admin.register(StorageSlot)
class StorageSlotAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "rack",
        "level",
        "position",
        "requires_pallet_jack",
        "is_active",
        "owning_group",
        "april_tag_id",
    )
    list_filter = ("rack", "level", "requires_pallet_jack", "is_active", "owning_group")
    search_fields = ("code", "notes")
    # Derived from rack/level/position on save — never typed by hand.
    readonly_fields = ("code", "created_at", "updated_at")
    ordering = ("rack", "level", "position")

    @admin.display(description="AprilTag")
    def april_tag_id(self, obj: StorageSlot) -> str:
        tag_id = get_active_tag_id(obj)
        return "—" if tag_id is None else str(tag_id)

    def save_model(self, request, obj, form, change):
        """Admin-created slots get their permanent marker like API ones do."""
        super().save_model(request, obj, form, change)
        ensure_slot_tag(obj)


@admin.register(ProjectStorageEvent)
class ProjectStorageEventAdmin(admin.ModelAdmin):
    list_display = ("stint", "event_type", "actor", "actor_label", "created_at")
    list_filter = ("event_type",)
    search_fields = ("stint__stint_id", "stint__username", "actor_label", "note")
    readonly_fields = ("stint", "event_type", "actor", "actor_label", "note", "created_at")
