"""Maintenance orders app configuration."""

from django.apps import AppConfig


class MaintenanceOrdersConfig(AppConfig):
    """Configuration for the third-party maintenance work order app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "maintenance_orders"
    verbose_name = "Third-Party Maintenance Orders"

    def ready(self) -> None:
        from . import signals  # noqa: F401
