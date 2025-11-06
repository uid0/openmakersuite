from django.apps import AppConfig


class ReorderQueueConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "reorder_queue"

    def ready(self):
        """Import signal handlers when the app is ready."""
        import reorder_queue.signals  # noqa: F401
