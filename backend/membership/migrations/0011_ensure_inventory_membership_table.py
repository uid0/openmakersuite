"""Repair migration: ensure the ``inventory_membership`` tables exist.

Background
----------
``Membership`` (this app) maps to the legacy table ``inventory_membership``
(its ``db_table``), a name preserved from when the model lived in the
``inventory`` app. ``0001_initial`` adopts-or-creates that table (and its M2M
``inventory_membership_users``) via ``_create_membership_table_if_not_exists``.

On at least one production database ``0001_initial`` is recorded as **applied**
while ``inventory_membership`` / ``inventory_membership_users`` are **absent** —
fallout of the ``inventory -> membership`` app move (the table was adopted into
migration state, then dropped, while the migration stayed marked applied).
Because Django considers ``0001`` applied, ``migrate`` never re-runs its create
step, so the tables stay missing and every query against ``Membership`` (auth's
active-membership check, the ``membership.active`` analytics metric) raises
``relation "inventory_membership" does not exist``.

This migration re-asserts the two tables idempotently with ``CREATE TABLE IF NOT
EXISTS``, using the exact schema from ``0001_initial``. It is a **no-op** on any
healthy database (CI, dev, fresh installs) where the tables already exist, and
recreates them (empty) where they are missing. The reverse is a deliberate
no-op — we never drop a table that may hold data.
"""

from django.db import migrations

# Verbatim from membership/0001_initial.py::_create_membership_table_if_not_exists,
# with IF NOT EXISTS so this is safe to run on databases that already have the
# tables. Order matters: the M2M references inventory_membership.
_PG_CREATE = (
    """
    CREATE TABLE IF NOT EXISTS inventory_membership (
        id SERIAL PRIMARY KEY,
        membership_type VARCHAR(20) NOT NULL DEFAULT 'monthly',
        status VARCHAR(20) NOT NULL DEFAULT 'inactive',
        start_date DATE,
        end_date DATE,
        notes TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS inventory_membership_users (
        id SERIAL PRIMARY KEY,
        membership_id INTEGER NOT NULL REFERENCES inventory_membership(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL REFERENCES auth_user(id) ON DELETE CASCADE,
        UNIQUE(membership_id, user_id)
    );
    """,
)

_SQLITE_CREATE = (
    """
    CREATE TABLE IF NOT EXISTS inventory_membership (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        membership_type VARCHAR(20) NOT NULL DEFAULT 'monthly',
        status VARCHAR(20) NOT NULL DEFAULT 'inactive',
        start_date DATE,
        end_date DATE,
        notes TEXT NOT NULL DEFAULT '',
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS inventory_membership_users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        membership_id INTEGER NOT NULL REFERENCES inventory_membership(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL REFERENCES auth_user(id) ON DELETE CASCADE,
        UNIQUE(membership_id, user_id)
    );
    """,
)


def ensure_inventory_membership_tables(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    if vendor == "postgresql":
        statements = _PG_CREATE
    elif vendor == "sqlite":
        statements = _SQLITE_CREATE
    else:
        # Unknown backend — leave table management to 0001 as before.
        return
    for sql in statements:
        schema_editor.execute(sql)


class Migration(migrations.Migration):

    dependencies = [
        ("membership", "0010_user_tokens_valid_after"),
    ]

    operations = [
        migrations.RunPython(
            ensure_inventory_membership_tables,
            migrations.RunPython.noop,
        ),
    ]
