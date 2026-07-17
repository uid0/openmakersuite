"""Tests for the PostgreSQL-required system check."""

from unittest import mock

from django.db import connections

from accounting.checks import check_postgres_required


def test_check_errors_when_default_db_is_sqlite():
    with mock.patch.object(connections["default"], "vendor", "sqlite"):
        errors = check_postgres_required(app_configs=None)
    assert len(errors) == 1
    assert errors[0].id == "accounting.E001"
    assert "PostgreSQL" in errors[0].msg


def test_check_passes_on_postgres():
    with mock.patch.object(connections["default"], "vendor", "postgresql"):
        assert check_postgres_required(app_configs=None) == []
