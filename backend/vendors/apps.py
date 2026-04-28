"""Vendors app configuration."""

from django.apps import AppConfig


class VendorsConfig(AppConfig):
    """Configuration for the third-party vendors app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "vendors"
    verbose_name = "Third-Party Vendors"
