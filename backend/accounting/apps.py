from django.apps import AppConfig


class AccountingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounting"
    verbose_name = "Accounting ledger"

    def ready(self) -> None:
        # Imported for its side effect: registers the PostgreSQL-required
        # system check with Django's check framework. See accounting/checks.py.
        from . import checks  # noqa: F401
