"""Tests for membership migrations helper utilities."""

import importlib
from unittest.mock import Mock

import pytest

migration_module = importlib.import_module(
    "membership.migrations.0002_update_auth_user_custom_fields"
)


@pytest.mark.unit
class TestDropUniqueConstraint:
    """Ensure constraint dropping uses vendor-specific SQL."""

    def test_postgresql_constraint_drop(self):
        schema_editor = Mock()

        migration_module._drop_unique_constraint(
            schema_editor, "postgresql", "auth_user_handle_key"
        )

        schema_editor.execute.assert_called_once_with(
            "ALTER TABLE auth_user DROP CONSTRAINT IF EXISTS auth_user_handle_key"
        )

    def test_sqlite_index_drop(self):
        schema_editor = Mock()

        migration_module._drop_unique_constraint(
            schema_editor, "sqlite", "auth_user_handle_key"
        )

        schema_editor.execute.assert_called_once_with(
            "DROP INDEX IF EXISTS auth_user_handle_key"
        )


@pytest.mark.unit
class TestAddUniqueIndex:
    """Ensure partial indexes are created with the expected SQL."""

    def test_postgresql_index_with_condition(self):
        schema_editor = Mock()

        migration_module._add_unique_index(
            schema_editor,
            "postgresql",
            "auth_user_handle_unique_idx",
            "handle",
            "handle IS NOT NULL",
        )

        schema_editor.execute.assert_called_once_with(
            "CREATE UNIQUE INDEX IF NOT EXISTS auth_user_handle_unique_idx "
            "ON auth_user(handle) WHERE handle IS NOT NULL"
        )

    def test_sqlite_index_without_condition(self):
        schema_editor = Mock()

        migration_module._add_unique_index(
            schema_editor,
            "sqlite",
            "auth_user_handle_unique_idx",
            "handle",
        )

        schema_editor.execute.assert_called_once_with(
            "CREATE UNIQUE INDEX IF NOT EXISTS auth_user_handle_unique_idx "
            "ON auth_user(handle)"
        )
