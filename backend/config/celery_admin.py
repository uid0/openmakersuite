"""
Custom admin configuration for Celery task results.
"""

from django.contrib import admin
from django.utils.html import format_html
from django_celery_results.admin import TaskResultAdmin
from django_celery_results.models import TaskResult

# Unregister the default admin
admin.site.unregister(TaskResult)


@admin.register(TaskResult)
class CustomTaskResultAdmin(TaskResultAdmin):
    """Enhanced admin interface for Celery task results."""

    list_display = [
        "task_name_display",
        "status_display",
        "date_created",
        "date_done",
        "worker",
        "task_args_display",
    ]
    list_filter = ["status", "task_name", "date_created", "worker"]
    search_fields = ["task_name", "task_id", "task_args", "task_kwargs"]
    readonly_fields = [
        "task_id",
        "task_name",
        "task_args",
        "task_kwargs",
        "status",
        "worker",
        "date_created",
        "date_done",
        "result",
        "traceback",
        "meta",
    ]
    date_hierarchy = "date_created"
    list_per_page = 25

    fieldsets = (
        (
            "Task Information",
            {
                "fields": (
                    "task_id",
                    "task_name",
                    "status",
                    "worker",
                )
            },
        ),
        (
            "Task Input",
            {
                "fields": ("task_args", "task_kwargs"),
                "classes": ("collapse",),
            },
        ),
        (
            "Task Output",
            {
                "fields": ("result", "traceback"),
            },
        ),
        (
            "Timing",
            {
                "fields": ("date_created", "date_done"),
            },
        ),
        (
            "Additional Metadata",
            {
                "fields": ("meta",),
                "classes": ("collapse",),
            },
        ),
    )

    @admin.display(description="Task Name", ordering="task_name")
    def task_name_display(self, obj):
        """Display task name with color coding."""
        if obj.task_name:
            # Shorten long task names
            task_name = obj.task_name.split(".")[-1]
            return format_html("<strong>{}</strong>", task_name)
        return "—"

    @admin.display(description="Status", ordering="status")
    def status_display(self, obj):
        """Display status with color coding."""
        colors = {
            "SUCCESS": "green",
            "FAILURE": "red",
            "PENDING": "orange",
            "STARTED": "blue",
            "RETRY": "purple",
            "REVOKED": "gray",
        }
        color = colors.get(obj.status, "black")
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.status,
        )

    @admin.display(description="Arguments")
    def task_args_display(self, obj):
        """Display abbreviated task arguments."""
        if obj.task_args:
            args_str = str(obj.task_args)
            if len(args_str) > 50:
                return args_str[:47] + "..."
            return args_str
        return "—"

    def has_add_permission(self, request):
        """Disable adding task results manually."""
        return False

    def has_change_permission(self, request, obj=None):
        """Make task results read-only."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Allow deletion of old task results."""
        return request.user.is_superuser

    class Meta:
        verbose_name = "Celery Task Result"
        verbose_name_plural = "Celery Task Results"
