from django.contrib import admin

from .models import PMSchedule, PMServiceLog


class PMServiceLogInline(admin.TabularInline):
    model = PMServiceLog
    extra = 0
    fields = ("performed_at", "performed_by", "notes")
    readonly_fields = ("created_at",)
    ordering = ("-performed_at",)


@admin.register(PMSchedule)
class PMScheduleAdmin(admin.ModelAdmin):
    list_display = (
        "task_name",
        "asset",
        "interval_days",
        "is_active",
        "_status",
        "_days_since",
    )
    list_filter = ("is_active",)
    search_fields = ("task_name", "asset__name", "asset__asset_tag")
    inlines = [PMServiceLogInline]

    @admin.display(description="Status")
    def _status(self, obj: PMSchedule) -> str:
        return obj.status()

    @admin.display(description="Last service")
    def _days_since(self, obj: PMSchedule) -> str:
        d = obj.days_since_last()
        return "never" if d is None else f"{d}d"


@admin.register(PMServiceLog)
class PMServiceLogAdmin(admin.ModelAdmin):
    list_display = ("schedule", "performed_at", "performed_by")
    list_filter = ("schedule__asset",)
    search_fields = ("schedule__task_name", "schedule__asset__name")
    autocomplete_fields = ("performed_by",)
