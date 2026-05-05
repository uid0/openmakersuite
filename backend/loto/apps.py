from django.apps import AppConfig


class LotoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "loto"
    verbose_name = "Lockout / Tagout"

    def ready(self) -> None:
        from . import signals  # noqa: F401
