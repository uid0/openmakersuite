"""
Dashboard app configuration.
"""

from django.apps import AppConfig


class DashboardConfig(AppConfig):
    """Configuration for the dashboard management app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "dashboard"
    verbose_name = "TV Dashboard Management"

    def ready(self):
        """Initialize app when Django starts."""
        # Import any signals here if needed in the future
        pass
