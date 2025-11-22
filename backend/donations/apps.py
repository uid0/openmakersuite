"""
Donations app configuration.
"""

from django.apps import AppConfig


class DonationsConfig(AppConfig):
    """Configuration for the donations management app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "donations"
    verbose_name = "Donations Management"

    def ready(self):
        """Initialize app when Django starts."""
        # Import signals to register them
        import donations.signals  # noqa: F401
