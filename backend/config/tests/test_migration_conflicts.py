"""Tests for ``manage.py check_migration_conflicts``.

Regression guard for the 2026-07-08 deploy outage (PR #864): two ``0079``
inventory migrations branched off ``0078`` created a multi-leaf graph that
aborted every ``migrate`` at startup. These tests make that class of conflict
fail in CI instead of at deploy.

``check_migration_conflicts`` reads the migration graph on disk only
(``MigrationLoader(None)``), so none of these tests need a database.
"""

from __future__ import annotations

from io import StringIO
from unittest import mock

from django.core.management import call_command

import pytest

# The command builds its loader through this name; patch it here to simulate
# graph states without writing throwaway migration files to disk.
_LOADER = "config.management.commands.check_migration_conflicts.MigrationLoader"


def test_repo_migration_graph_has_no_conflicts():
    """The committed migration graph must be single-leaf per app.

    This is the canary: if two migrations ever branch off the same parent and
    both land, this goes red (locally under pytest and in the CI step that runs
    the same command). Exit 0 == no ``SystemExit`` raised.
    """
    out = StringIO()
    call_command("check_migration_conflicts", stdout=out, stderr=StringIO())
    assert "single leaf node" in out.getvalue()


def test_command_exits_nonzero_on_conflict():
    """A multi-leaf graph must make the command exit 1 so CI goes red, and the
    message must name the offending app, its leaves, and the ``--merge`` fix."""
    fake_conflicts = {"inventory": ["0079_alter_workordersubmission_source", "0079_assetmeter"]}
    with mock.patch(_LOADER) as loader_cls:
        loader_cls.return_value.detect_conflicts.return_value = fake_conflicts
        err = StringIO()
        with pytest.raises(SystemExit) as excinfo:
            call_command("check_migration_conflicts", stdout=StringIO(), stderr=err)

    assert excinfo.value.code == 1
    message = err.getvalue()
    assert "multiple leaf nodes" in message
    assert "inventory: 0079_alter_workordersubmission_source, 0079_assetmeter" in message
    assert "makemigrations --merge" in message


def test_command_succeeds_on_clean_graph():
    """No conflicts => exit 0 with a success message (no ``SystemExit``)."""
    with mock.patch(_LOADER) as loader_cls:
        loader_cls.return_value.detect_conflicts.return_value = {}
        out = StringIO()
        call_command("check_migration_conflicts", stdout=out, stderr=StringIO())

    assert "single leaf node" in out.getvalue()


def test_multiple_apps_conflicting_are_all_reported():
    """Every conflicting app is listed, sorted, so one CI run surfaces them all."""
    fake_conflicts = {
        "inventory": ["0079_a", "0079_b"],
        "membership": ["0005_x", "0005_y"],
    }
    with mock.patch(_LOADER) as loader_cls:
        loader_cls.return_value.detect_conflicts.return_value = fake_conflicts
        err = StringIO()
        with pytest.raises(SystemExit) as excinfo:
            call_command("check_migration_conflicts", stdout=StringIO(), stderr=err)

    assert excinfo.value.code == 1
    message = err.getvalue()
    assert "inventory: 0079_a, 0079_b" in message
    assert "membership: 0005_x, 0005_y" in message
