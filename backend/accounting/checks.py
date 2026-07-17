"""System checks for the accounting app.

The accounting ledger is built on django-hordak, which relies on
PostgreSQL-only features: a balance-enforcing deferred constraint trigger on
``hordak.Leg`` and the ``get_balance`` SQL function behind
``Account.objects.with_balances()``. On sqlite, hordak's
``0002_check_leg_trigger`` migration raises a cryptic ``NotImplementedError``
partway through ``migrate``.

This check turns that late, opaque failure into a clear, actionable error the
moment ``migrate`` / ``runserver`` / ``check`` runs against a sqlite database,
without a hard import-time crash (so tooling that never touches the DB still
imports cleanly).
"""

from django.core import checks
from django.db import connections

SQLITE_REQUIRES_POSTGRES = checks.Error(
    "OpenMakerSuite now requires PostgreSQL (the accounting ledger uses "
    "Postgres-only features). Set DATABASE_URL=postgres://… or use "
    "docker-compose; see docs/accounting.md.",
    id="accounting.E001",
)


@checks.register()
def check_postgres_required(app_configs, **kwargs):
    """Error when the default database connection is sqlite."""
    try:
        vendor = connections["default"].vendor
    except Exception:  # pragma: no cover - defensive: malformed DATABASES config
        return []
    if vendor == "sqlite":
        return [SQLITE_REQUIRES_POSTGRES]
    return []
