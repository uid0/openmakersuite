"""Shared fixtures for the accounting suite.

Every ledger test posts against the chart of accounts, which is seeded by a
*data migration* (``0002_seed_chart_of_accounts``) rather than a fixture. Any
``transaction=True`` test earlier in the session flushes it back out — a
``TransactionTestCase`` teardown truncates every table and the ``post_migrate``
that follows only restores contenttypes/permissions, not data-migration rows.

Before op-t7q4 the only such test on main (``facilities/tests/test_migration.py``)
was absent from ``pytest.ini`` ``testpaths``, so CI never ran it and the accounting
suite was accidentally safe. Now that facilities is collected, this module makes
the whole accounting suite order-proof instead.
"""

import pytest


@pytest.fixture(autouse=True)
def _chart_of_accounts(db):
    """Guarantee the chart exists before a ledger test posts against it.

    Re-seeding is idempotent by design — it is the same call the migration and
    the ``seed_chart_of_accounts`` management command make — and it rolls back
    with the test, so this costs one no-op query per test.
    """
    from accounting.chart import seed_chart_of_accounts

    seed_chart_of_accounts()
