from django.apps import AppConfig


class BackupsConfig(AppConfig):
    """OMS scheduled backup task lives here.

    A separate app rather than wedging into an existing one because:
      1. Celery's `autodiscover_tasks()` picks up `<app>/tasks.py` per
         INSTALLED_APPS, so the daily backup task gets registered with
         no manual import wiring.
      2. The task crosses concerns (operations, retention, ops alerting)
         that don't belong inside donations/analytics/etc.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "backups"
