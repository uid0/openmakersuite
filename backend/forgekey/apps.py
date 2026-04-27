from django.apps import AppConfig


class ForgekeyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "forgekey"
    verbose_name = "ForgeKey - ESP32 Device Management"

    def ready(self) -> None:
        # Import for side-effect: registers system checks.
        from . import checks  # noqa: F401
