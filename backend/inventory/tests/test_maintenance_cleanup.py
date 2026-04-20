"""
Tests for the Preventive Maintenance cleanup work:

* ``instructions`` was removed from MaintenanceItem (replaced by MaintenanceTask).
* MaintenanceMaterial can point at an InventoryItem and surface reorder alerts.
* Completing a WorkOrder clears overdue state on its MaintenanceItem.
* Asset maintenance schedules can be cloned from one asset to another.
* An asset-report rollup summarizes completed PM activity.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from inventory.models import (
    MaintenanceItem,
    MaintenanceLog,
    MaintenanceMaterial,
    MaintenanceTask,
    WorkOrder,
    WorkOrderTaskCompletion,
)
from inventory.tests.factories import AssetFactory, InventoryItemFactory
from reorder_queue.models import ReorderRequest

pytestmark = pytest.mark.django_db


User = get_user_model()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _auth_client(user=None):
    client = APIClient()
    user = user or User.objects.create_user(username="tech", password="pw")  # nosec B106
    client.force_authenticate(user=user)
    return client, user


def _make_item(asset=None, **kwargs):
    asset = asset or AssetFactory()
    return MaintenanceItem.objects.create(
        asset=asset,
        title=kwargs.pop("title", "Monthly check"),
        description=kwargs.pop("description", ""),
        interval_days=kwargs.pop("interval_days", 30),
        **kwargs,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Schema / model: instructions is gone
# ─────────────────────────────────────────────────────────────────────────────


def test_maintenance_item_has_no_instructions_field():
    """The instructions field was removed in favor of MaintenanceTask rows."""
    field_names = {f.name for f in MaintenanceItem._meta.get_fields()}
    assert "instructions" not in field_names


# ─────────────────────────────────────────────────────────────────────────────
# MaintenanceMaterial ↔ InventoryItem linkage
# ─────────────────────────────────────────────────────────────────────────────


def test_material_display_name_falls_back_to_inventory_item():
    item = _make_item()
    inv = InventoryItemFactory(name="Air filter (20x25)")
    mat = MaintenanceMaterial.objects.create(
        maintenance_item=item,
        inventory_item=inv,
        quantity=Decimal("1.00"),
    )
    assert mat.display_name == "Air filter (20x25)"


def test_material_display_name_uses_legacy_name_when_no_inventory_item():
    item = _make_item()
    mat = MaintenanceMaterial.objects.create(
        maintenance_item=item,
        name="Shop rag",
        quantity=Decimal("2.00"),
    )
    assert mat.display_name == "Shop rag"


def test_material_surfaces_pending_reorder_from_inventory_item():
    item = _make_item()
    inv = InventoryItemFactory(current_stock=0, minimum_stock=1)
    ReorderRequest.objects.create(item=inv, status="pending", quantity=1)
    mat = MaintenanceMaterial.objects.create(maintenance_item=item, inventory_item=inv)
    assert mat.has_pending_reorder is True
    assert mat.inventory_reorder_status in {"pending", "approved", "ordered"}


def test_material_serializer_exposes_reorder_fields():
    client, _ = _auth_client()
    item = _make_item()
    inv = InventoryItemFactory(name="Grease cartridge")
    mat = MaintenanceMaterial.objects.create(
        maintenance_item=item, inventory_item=inv, quantity=Decimal("1.00")
    )
    resp = client.get(f"/api/inventory/maintenance-materials/{mat.id}/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["inventory_item"] == str(inv.id)
    assert body["inventory_item_name"] == "Grease cartridge"
    assert body["display_name"] == "Grease cartridge"
    assert "has_pending_reorder" in body


def test_material_requires_inventory_item_or_name():
    client, _ = _auth_client()
    item = _make_item()
    resp = client.post(
        "/api/inventory/maintenance-materials/",
        {"maintenance_item": str(item.id), "quantity": "1.00"},
        format="json",
    )
    assert resp.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# Completing a WorkOrder clears overdue
# ─────────────────────────────────────────────────────────────────────────────


def test_completing_work_order_stamps_item_last_completed_at():
    """When a WO transitions to completed, the parent item is no longer overdue."""
    asset = AssetFactory()
    item = MaintenanceItem.objects.create(asset=asset, title="Overdue", interval_days=30)
    # Overdue: interval set, never completed
    assert item.is_overdue is True

    wo = WorkOrder.objects.create(maintenance_item=item, due_date=date.today())
    wo.status = WorkOrder.STATUS_COMPLETED
    wo.save()

    item.refresh_from_db()
    assert item.last_completed_at is not None
    assert item.is_overdue is False


def test_completing_work_order_respects_later_existing_completion():
    """Don't rewind ``last_completed_at`` if the WO completed earlier."""
    item = _make_item()
    future = timezone.now()
    item.last_completed_at = future
    item.save(update_fields=["last_completed_at"])

    wo = WorkOrder.objects.create(maintenance_item=item)
    wo.completed_at = future - timedelta(days=5)
    wo.status = WorkOrder.STATUS_COMPLETED
    wo.save()

    item.refresh_from_db()
    assert item.last_completed_at == future


# ─────────────────────────────────────────────────────────────────────────────
# Asset clone_maintenance endpoint
# ─────────────────────────────────────────────────────────────────────────────


def test_clone_maintenance_copies_items_tasks_and_materials():
    client, _ = _auth_client()
    source = AssetFactory()
    dest = AssetFactory()

    inv = InventoryItemFactory(name="O-ring")
    item = MaintenanceItem.objects.create(
        asset=source,
        title="Quarterly service",
        description="Detailed description",
        interval_days=90,
        estimated_time_minutes=30,
    )
    MaintenanceTask.objects.create(
        maintenance_item=item, order=0, title="Power down", is_required=True
    )
    MaintenanceTask.objects.create(
        maintenance_item=item, order=1, title="Replace o-ring", is_required=True
    )
    MaintenanceMaterial.objects.create(
        maintenance_item=item, inventory_item=inv, quantity=Decimal("2.00")
    )

    resp = client.post(
        f"/api/inventory/assets/{dest.id}/clone-maintenance/",
        {"source_asset": str(source.id)},
        format="json",
    )
    assert resp.status_code == 201, resp.content
    body = resp.json()
    assert body["cloned_count"] == 1

    cloned = MaintenanceItem.objects.filter(asset=dest)
    assert cloned.count() == 1
    clone = cloned.first()
    assert clone.id != item.id
    assert clone.tasks.count() == 2
    assert clone.materials.count() == 1
    clone_mat = clone.materials.first()
    assert clone_mat.inventory_item_id == inv.id
    # reset_schedule defaults to True → fresh schedule
    assert clone.last_completed_at is None


def test_clone_maintenance_rejects_same_asset():
    client, _ = _auth_client()
    asset = AssetFactory()
    resp = client.post(
        f"/api/inventory/assets/{asset.id}/clone-maintenance/",
        {"source_asset": str(asset.id)},
        format="json",
    )
    assert resp.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# Maintenance rollup report
# ─────────────────────────────────────────────────────────────────────────────


def test_maintenance_rollup_aggregates_completed_logs():
    client, user = _auth_client()
    asset = AssetFactory(name="Lathe")
    item = MaintenanceItem.objects.create(asset=asset, title="Weekly lube", interval_days=7)

    MaintenanceLog.objects.create(
        maintenance_item=item,
        completed_by=user,
        time_spent_minutes=15,
        cost_incurred=Decimal("2.50"),
    )
    MaintenanceLog.objects.create(
        maintenance_item=item,
        completed_by=user,
        time_spent_minutes=20,
        cost_incurred=Decimal("3.00"),
    )

    resp = client.get("/api/inventory/reports/assets/maintenance-rollup/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["totals"]["completions"] == 2
    assert body["totals"]["minutes"] == 35
    assert Decimal(body["totals"]["cost"]) == Decimal("5.50")
    assert body["by_asset"][0]["asset_name"] == "Lathe"
    assert body["by_asset"][0]["completions"] == 2


def test_maintenance_rollup_respects_date_range():
    client, user = _auth_client()
    asset = AssetFactory()
    item = MaintenanceItem.objects.create(asset=asset, title="Seasonal", interval_days=180)

    old_log = MaintenanceLog.objects.create(maintenance_item=item, completed_by=user)
    MaintenanceLog.objects.filter(pk=old_log.pk).update(
        completed_at=timezone.now() - timedelta(days=120)
    )
    MaintenanceLog.objects.create(maintenance_item=item, completed_by=user)

    start = (timezone.now() - timedelta(days=30)).date().isoformat()
    resp = client.get("/api/inventory/reports/assets/maintenance-rollup/", {"start_date": start})
    assert resp.status_code == 200
    assert resp.json()["totals"]["completions"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# WorkOrderTaskCompletion safety against the instructions removal
# (exercises the PDF path indirectly — generate_work_order_pdf must not
# reference item.instructions.)
# ─────────────────────────────────────────────────────────────────────────────


def test_work_order_pdf_generation_without_instructions_field():
    from inventory.utils.work_order_pdf import generate_work_order_pdf

    asset = AssetFactory()
    item = MaintenanceItem.objects.create(asset=asset, title="Quick check")
    MaintenanceTask.objects.create(maintenance_item=item, title="Look around", order=0)
    wo = WorkOrder.objects.create(maintenance_item=item)
    WorkOrderTaskCompletion.objects.create(
        work_order=wo, task_title="Look around", task_order=0, is_required=True
    )
    pdf_bytes = generate_work_order_pdf(wo)
    assert pdf_bytes.startswith(b"%PDF")
