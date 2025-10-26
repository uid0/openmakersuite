"""
App configuration for the config module.
"""

from django.apps import AppConfig


class ConfigConfig(AppConfig):
    """Configuration app settings."""

    name = "config"
    verbose_name = "Configuration"

    def ready(self):
        """Import custom admin configurations when Django starts."""
        # Import celery admin customizations
        try:
            import config.celery_admin  # noqa: F401
        except ImportError:
            pass
