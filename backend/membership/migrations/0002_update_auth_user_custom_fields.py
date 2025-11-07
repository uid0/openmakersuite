import logging

from django.db import migrations

logger = logging.getLogger(__name__)


def _get_existing_columns(connection, vendor):
    columns = set()
    with connection.cursor() as cursor:
        if vendor == "postgresql":
            cursor.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'auth_user'
                """
            )
            columns = {row[0] for row in cursor.fetchall()}
        elif vendor == "sqlite":
            cursor.execute("PRAGMA table_info('auth_user')")
            columns = {row[1] for row in cursor.fetchall()}
    return columns


def _add_column(schema_editor, column_sql):
    schema_editor.execute(column_sql)


def _drop_index_if_exists(schema_editor, vendor, name):
    if vendor in {"postgresql", "sqlite"}:
        schema_editor.execute(f"DROP INDEX IF EXISTS {name}")


def _add_unique_index(schema_editor, vendor, name, expression, condition=None):
    if vendor == "postgresql":
        if condition:
            sql = f"CREATE UNIQUE INDEX IF NOT EXISTS {name} ON auth_user({expression}) WHERE {condition}"
        else:
            sql = f"CREATE UNIQUE INDEX IF NOT EXISTS {name} ON auth_user({expression})"
        schema_editor.execute(sql)
    elif vendor == "sqlite":
        if condition:
            sql = f"CREATE UNIQUE INDEX IF NOT EXISTS {name} ON auth_user({expression}) WHERE {condition}"
        else:
            sql = f"CREATE UNIQUE INDEX IF NOT EXISTS {name} ON auth_user({expression})"
        schema_editor.execute(sql)


def _ensure_columns(apps, schema_editor):
    connection = schema_editor.connection
    vendor = connection.vendor

    # Only handle databases we explicitly support.
    if vendor not in {"postgresql", "sqlite"}:
        logger.warning("Skipping auth_user column migration for unsupported vendor '%s'", vendor)
        return

    existing_columns = _get_existing_columns(connection, vendor)

    def ensure_column(name, postgres_sql, sqlite_sql=None):
        if name in existing_columns:
            return
        if vendor == "postgresql":
            _add_column(schema_editor, postgres_sql)
        else:
            sql = sqlite_sql or postgres_sql
            _add_column(schema_editor, sql)

    ensure_column(
        "handle",
        "ALTER TABLE auth_user ADD COLUMN handle VARCHAR(150)",
    )
    ensure_column(
        "active_directory_username",
        "ALTER TABLE auth_user ADD COLUMN active_directory_username VARCHAR(150) NOT NULL DEFAULT ''",
    )
    ensure_column(
        "badge_number",
        "ALTER TABLE auth_user ADD COLUMN badge_number VARCHAR(50)",
    )
    ensure_column(
        "discord_username",
        "ALTER TABLE auth_user ADD COLUMN discord_username VARCHAR(150) NOT NULL DEFAULT ''",
    )
    ensure_column(
        "discourse_username",
        "ALTER TABLE auth_user ADD COLUMN discourse_username VARCHAR(150) NOT NULL DEFAULT ''",
    )
    ensure_column(
        "is_board_member",
        "ALTER TABLE auth_user ADD COLUMN is_board_member BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE auth_user ADD COLUMN is_board_member BOOLEAN NOT NULL DEFAULT 0",
    )
    ensure_column(
        "is_officer",
        "ALTER TABLE auth_user ADD COLUMN is_officer BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE auth_user ADD COLUMN is_officer BOOLEAN NOT NULL DEFAULT 0",
    )
    ensure_column(
        "is_director",
        "ALTER TABLE auth_user ADD COLUMN is_director BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE auth_user ADD COLUMN is_director BOOLEAN NOT NULL DEFAULT 0",
    )

    # Ensure partial unique indexes for nullable unique fields exist consistently.
    unique_index_specs = [
        (
            "auth_user_handle_unique_idx",
            "handle",
            "handle IS NOT NULL",
            "auth_user_handle_key",
        ),
        (
            "auth_user_badge_number_unique_idx",
            "badge_number",
            "badge_number IS NOT NULL",
            "auth_user_badge_number_key",
        ),
    ]

    for index_name, column, condition, legacy_index_name in unique_index_specs:
        # Drop legacy unique indexes created when the column definition included UNIQUE.
        _drop_index_if_exists(schema_editor, vendor, legacy_index_name)
        _add_unique_index(
            schema_editor,
            vendor,
            index_name,
            column,
            condition,
        )


def _noop_reverse(apps, schema_editor):
    """Intentionally left empty; we do not remove columns on reverse migrations."""


def ensure_auth_user_columns(apps, schema_editor):
    _ensure_columns(apps, schema_editor)


class Migration(migrations.Migration):

    dependencies = [
        ("membership", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(ensure_auth_user_columns, _noop_reverse),
    ]
