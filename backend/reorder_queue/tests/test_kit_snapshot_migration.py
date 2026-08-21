"""The kit snapshot's migration budget (op-8n0), AC-49 and AC-51.

``InventoryItem.is_kit`` was chosen over a standalone ``Kit`` model largely
because it left ``reorder_queue`` alone: a kit already has an ``ItemSupplier``,
so a kit line is an ordinary inventory line as far as this app's schema is
concerned. Storing the order-time breakdown spends exactly one additive field
against that budget, and nothing else.

These tests pin the spend. They read the migration graph on disk, so — like
``config.tests.test_migration_conflicts`` — most need no database.
"""

from __future__ import annotations

from importlib import import_module
from io import StringIO

from django.apps import apps
from django.core.management import call_command
from django.db import connection, models
from django.db.migrations import AddField
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.questioner import NonInteractiveMigrationQuestioner
from django.db.migrations.state import ProjectState

import pytest

from reorder_queue.models import _PO_ITEM_TARGETS, PurchaseOrderItem

# Imported by name rather than with ``import``: the module starts with a digit.
snapshot_migration = import_module("reorder_queue.migrations.0028_purchaseorderitem_kit_snapshot")


def test_ac49_the_snapshot_migration_only_adds_one_nullable_field():
    """One AddField. Not a constraint, not an index, not a second column."""
    operations = snapshot_migration.Migration.operations
    assert len(operations) == 1

    operation = operations[0]
    assert isinstance(operation, AddField)
    assert operation.model_name == "purchaseorderitem"
    assert operation.name == "kit_snapshot"

    field = operation.field
    assert isinstance(field, models.JSONField)
    assert field.null is True
    assert field.blank is True
    # No default: a default would rewrite every existing row and make the
    # "ordinary lines are NULL" contract in AC-50 unprovable.
    assert field.has_default() is False


def test_ac49_no_later_migration_touched_purchase_order_item():
    """0028 is the last word *on this table*.

    Originally "0028 is the last reorder_queue migration at all", which only
    held while op-8n0 was the newest work in the app. The budget it was pinning
    is the ``PurchaseOrderItem`` schema, not the app's migration counter — a
    later, unrelated migration elsewhere in ``reorder_queue`` (oms-po-add-item
    adds an audit-action choice, which touches ``PurchaseOrderAuditEvent``) is
    not an overspend. So the assertion is now what it always meant: after 0028,
    nothing else altered the PO line table.
    """
    loader = MigrationLoader(None, ignore_no_migrations=True)
    names = sorted(
        name for app_label, name in loader.disk_migrations if app_label == "reorder_queue"
    )
    later = [name for name in names if name > "0028_purchaseorderitem_kit_snapshot"]

    for name in later:
        migration = loader.disk_migrations[("reorder_queue", name)]
        touched = {
            getattr(operation, "model_name", getattr(operation, "name", "")).lower()
            for operation in migration.operations
        }
        assert "purchaseorderitem" not in touched, (
            f"{name} alters PurchaseOrderItem — the kit snapshot budget (AC-49) "
            "was one additive field on that table and nothing more."
        )


def test_ac49_kit_lines_added_no_purchase_order_item_target_slot():
    """A kit is an inventory line, not a fourth kind of line.

    The three-way target and its ``CheckConstraint`` are the schema this design
    exists to leave untouched — ``is_kit_line`` is derived from the item behind
    ``item_supplier``, so nothing here moves.
    """
    assert [target.token for target in _PO_ITEM_TARGETS] == [
        "inventory_item",
        "asset",
        "freeform",
    ]

    constraint_names = {c.name for c in PurchaseOrderItem._meta.constraints}
    assert "purchase_order_item_must_have_item_or_asset" in constraint_names
    assert not any("kit" in name for name in constraint_names)
    assert not any(
        "kit" in name for index in PurchaseOrderItem._meta.indexes for name in [index.name or ""]
    )
    assert all("kit_snapshot" not in fields for fields in PurchaseOrderItem._meta.unique_together)


@pytest.mark.django_db
def test_ac51_no_pending_model_changes_for_inventory_or_reorder_queue():
    """``makemigrations --check``, as a test.

    The gate the criteria name is a command in CI; this makes the same
    assertion fail under pytest, where it is cheaper to notice.
    """
    loader = MigrationLoader(connection, ignore_no_migrations=True)
    autodetector = MigrationAutodetector(
        loader.project_state(),
        ProjectState.from_apps(apps),
        NonInteractiveMigrationQuestioner(specified_apps=set(), dry_run=True),
    )
    changes = autodetector.changes(graph=loader.graph)

    assert "reorder_queue" not in changes, changes.get("reorder_queue")
    assert "inventory" not in changes, changes.get("inventory")


def test_migration_graph_still_has_a_single_leaf():
    """Adding 0028 must not branch the graph (the PR #864 failure mode)."""
    out = StringIO()
    call_command("check_migration_conflicts", stdout=out, stderr=StringIO())
    assert "single leaf node" in out.getvalue()
