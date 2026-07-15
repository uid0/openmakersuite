"""Tests for WO material usage → inventory decrement + usage log (op-uh8z, PR3).

Covers :func:`inventory.services.work_order_material_usage.apply_material_usage`
(the single apply/reverse seam) plus the two live entry points that call it: the
manual ``toggle_material`` endpoint and the OMR ``omr_apply_mark`` path.
"""

from decimal import Decimal

import pytest

from inventory.models import (
    MaintenanceItem,
    MaintenanceMaterial,
    UsageLog,
    WorkOrder,
    WorkOrderMaterialUsage,
)
from inventory.services.work_order_ingest import omr_apply_mark
from inventory.services.work_order_material_usage import apply_material_usage
from inventory.tests.factories import AssetFactory, InventoryItemFactory
from inventory.tests.test_work_order_omr import _staff_client

pytestmark = pytest.mark.django_db


def _wo_material(
    *,
    current_stock=10,
    quantity_used=Decimal("3.00"),
    quantity_planned=Decimal("1.00"),
    linked=True,
    was_used=False,
):
    """Build a MaintenanceItem + WorkOrder + one WorkOrderMaterialUsage row.

    Returns ``(item, usage)``. ``item`` is ``None`` when ``linked`` is False
    (a flag-only material with no inventory link).
    """
    mi = MaintenanceItem.objects.create(asset=AssetFactory(), title="PM")
    wo = WorkOrder.objects.create(maintenance_item=mi)
    item = InventoryItemFactory(current_stock=current_stock) if linked else None
    material = MaintenanceMaterial.objects.create(
        maintenance_item=mi,
        name="Filter",
        quantity=quantity_planned,
        inventory_item=item,
    )
    usage = WorkOrderMaterialUsage.objects.create(
        work_order=wo,
        material=material,
        material_name="Filter",
        quantity_planned=quantity_planned,
        quantity_used=quantity_used,
        unit="ea",
        was_used=was_used,
    )
    return item, usage


# ---------------------------------------------------------------------------
# service: apply / reverse
# ---------------------------------------------------------------------------
class TestApplyMaterialUsage:
    def test_decrement_on_use(self):
        item, usage = _wo_material(current_stock=10, quantity_used=Decimal("3.00"))
        changed = apply_material_usage(usage, was_used=True)

        assert changed is True
        usage.refresh_from_db()
        item.refresh_from_db()
        assert usage.was_used is True
        assert usage.applied_quantity == 3
        assert usage.stock_applied is True
        assert item.current_stock == 7  # 10 - 3

        log = usage.usage_log
        assert log is not None
        assert log.item_id == item.id
        assert log.quantity_used == 3
        assert UsageLog.objects.filter(item=item).count() == 1

    def test_usage_log_note_names_the_work_order_and_actor(self):
        item, usage = _wo_material(current_stock=10, quantity_used=Decimal("1.00"))
        apply_material_usage(usage, was_used=True, actor="Tech Tim", source_note="via scan")
        note = usage.usage_log.notes
        assert usage.work_order.short_id in note
        assert "Filter" in note
        assert "Tech Tim" in note
        assert "via scan" in note

    def test_reverse_on_un_use_restores_stock_and_voids_log(self):
        item, usage = _wo_material(current_stock=10, quantity_used=Decimal("3.00"))
        apply_material_usage(usage, was_used=True)
        log_id = usage.usage_log_id
        assert log_id is not None

        changed = apply_material_usage(usage, was_used=False)

        assert changed is True
        usage.refresh_from_db()
        item.refresh_from_db()
        assert usage.was_used is False
        assert usage.applied_quantity is None
        assert usage.stock_applied is False
        assert usage.usage_log_id is None
        assert item.current_stock == 10  # restored
        assert not UsageLog.objects.filter(id=log_id).exists()  # voided

    def test_quantity_math_uses_quantity_used_not_planned(self):
        # planned 1.00 but actually used 5 → decrement 5, not 1.
        item, usage = _wo_material(
            current_stock=20, quantity_used=Decimal("5.00"), quantity_planned=Decimal("1.00")
        )
        apply_material_usage(usage, was_used=True)
        item.refresh_from_db()
        usage.refresh_from_db()
        assert item.current_stock == 15
        assert usage.applied_quantity == 5
        assert usage.usage_log.quantity_used == 5

    @pytest.mark.parametrize(
        "qty,units",
        [
            (Decimal("2.00"), 2),
            (Decimal("2.40"), 2),
            (Decimal("2.50"), 3),  # half-up
            (Decimal("0.50"), 1),
            (Decimal("0.40"), 0),  # rounds to 0 → no decrement, no log
        ],
    )
    def test_fractional_quantity_rounds_half_up(self, qty, units):
        item, usage = _wo_material(current_stock=10, quantity_used=qty)
        apply_material_usage(usage, was_used=True)
        item.refresh_from_db()
        usage.refresh_from_db()
        assert usage.applied_quantity == units
        assert item.current_stock == 10 - units
        if units >= 1:
            assert usage.usage_log.quantity_used == units
        else:
            assert usage.usage_log_id is None  # UsageLog requires >= 1

    def test_insufficient_stock_clamps_and_reverses_exactly(self):
        item, usage = _wo_material(current_stock=2, quantity_used=Decimal("5.00"))
        apply_material_usage(usage, was_used=True)
        item.refresh_from_db()
        usage.refresh_from_db()
        assert item.current_stock == 0  # clamped, never negative
        assert usage.applied_quantity == 2  # only what was on hand
        assert usage.usage_log.quantity_used == 5  # log records the intended use

        apply_material_usage(usage, was_used=False)
        item.refresh_from_db()
        assert item.current_stock == 2  # restores exactly what was removed


# ---------------------------------------------------------------------------
# service: idempotency
# ---------------------------------------------------------------------------
class TestIdempotency:
    def test_double_apply_never_double_counts(self):
        item, usage = _wo_material(current_stock=10, quantity_used=Decimal("2.00"))
        for _ in range(3):
            apply_material_usage(usage, was_used=True)
        item.refresh_from_db()
        usage.refresh_from_db()
        assert item.current_stock == 8  # decremented once
        assert usage.applied_quantity == 2
        assert UsageLog.objects.filter(item=item).count() == 1

    def test_double_reverse_is_a_noop(self):
        item, usage = _wo_material(current_stock=10, quantity_used=Decimal("2.00"))
        apply_material_usage(usage, was_used=True)
        apply_material_usage(usage, was_used=False)
        apply_material_usage(usage, was_used=False)  # reverse again
        item.refresh_from_db()
        assert item.current_stock == 10

    def test_toggle_loop_nets_to_zero(self):
        item, usage = _wo_material(current_stock=10, quantity_used=Decimal("2.00"))
        for _ in range(5):
            apply_material_usage(usage, was_used=True)
            apply_material_usage(usage, was_used=False)
        item.refresh_from_db()
        usage.refresh_from_db()
        assert item.current_stock == 10  # net zero across the whole loop
        assert usage.applied_quantity is None
        assert UsageLog.objects.filter(item=item).count() == 0  # each void deleted


# ---------------------------------------------------------------------------
# service: flag-only (no inventory item)
# ---------------------------------------------------------------------------
class TestFlagOnly:
    def test_material_without_inventory_item_is_flag_only(self):
        item, usage = _wo_material(linked=False, quantity_used=Decimal("3.00"))
        assert item is None

        changed = apply_material_usage(usage, was_used=True)

        assert changed is True
        usage.refresh_from_db()
        assert usage.was_used is True
        assert usage.applied_quantity is None  # nothing to decrement
        assert usage.stock_applied is False
        assert usage.usage_log_id is None
        assert UsageLog.objects.count() == 0

        # un-marking is a clean no-op on the (absent) stock.
        apply_material_usage(usage, was_used=False)
        usage.refresh_from_db()
        assert usage.was_used is False

    def test_deleted_material_is_flag_only(self):
        # material FK is SET_NULL — a usage row whose spec was deleted still
        # records was_used without erroring.
        item, usage = _wo_material(linked=True)
        usage.material.delete()
        usage.refresh_from_db()
        assert usage.material is None

        apply_material_usage(usage, was_used=True)
        usage.refresh_from_db()
        assert usage.was_used is True
        assert usage.applied_quantity is None


# ---------------------------------------------------------------------------
# entry point: manual toggle endpoint
# ---------------------------------------------------------------------------
class TestToggleMaterialEndpoint:
    def _url(self, wo_id, usage_id):
        return f"/api/inventory/work-orders/{wo_id}/materials/{usage_id}/toggle/"

    def test_toggle_true_decrements_then_false_restores(self):
        item, usage = _wo_material(current_stock=10, quantity_used=Decimal("4.00"))
        client, _user = _staff_client()

        resp = client.patch(
            self._url(usage.work_order_id, usage.id), {"was_used": True}, format="json"
        )
        assert resp.status_code == 200
        assert resp.data["was_used"] is True
        assert resp.data["applied_quantity"] == 4
        assert resp.data["stock_applied"] is True
        item.refresh_from_db()
        assert item.current_stock == 6

        resp = client.patch(
            self._url(usage.work_order_id, usage.id), {"was_used": False}, format="json"
        )
        assert resp.status_code == 200
        assert resp.data["applied_quantity"] is None
        assert resp.data["stock_applied"] is False
        item.refresh_from_db()
        assert item.current_stock == 10

    def test_toggle_can_set_quantity_used_before_applying(self):
        item, usage = _wo_material(current_stock=10, quantity_used=Decimal("1.00"))
        client, _user = _staff_client()

        resp = client.patch(
            self._url(usage.work_order_id, usage.id),
            {"was_used": True, "quantity_used": "3"},
            format="json",
        )
        assert resp.status_code == 200
        assert resp.data["applied_quantity"] == 3
        item.refresh_from_db()
        assert item.current_stock == 7

    def test_quantity_used_locked_once_applied(self):
        item, usage = _wo_material(current_stock=10, quantity_used=Decimal("2.00"))
        client, _user = _staff_client()
        client.patch(self._url(usage.work_order_id, usage.id), {"was_used": True}, format="json")

        # Trying to change the quantity while applied is ignored (no re-decrement).
        resp = client.patch(
            self._url(usage.work_order_id, usage.id),
            {"was_used": True, "quantity_used": "9"},
            format="json",
        )
        assert resp.status_code == 200
        usage.refresh_from_db()
        item.refresh_from_db()
        assert usage.quantity_used == Decimal("2.00")  # unchanged
        assert usage.applied_quantity == 2
        assert item.current_stock == 8  # still only the original decrement

    def test_negative_quantity_rejected(self):
        item, usage = _wo_material(current_stock=10)
        client, _user = _staff_client()
        resp = client.patch(
            self._url(usage.work_order_id, usage.id),
            {"was_used": True, "quantity_used": "-1"},
            format="json",
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# entry point: OMR / HITL apply mark
# ---------------------------------------------------------------------------
class TestOmrApplyMark:
    def test_omr_apply_mark_decrements_and_reverses(self):
        item, usage = _wo_material(current_stock=10, quantity_used=Decimal("2.00"))
        wo = usage.work_order

        assert omr_apply_mark(wo, f"material_{usage.id}", marked=True, actor="scanner") == 1
        item.refresh_from_db()
        usage.refresh_from_db()
        assert usage.was_used is True
        assert usage.applied_quantity == 2
        assert item.current_stock == 8

        # Rejecting the scanned mark (review reject) reverses the decrement.
        assert omr_apply_mark(wo, f"material_{usage.id}", marked=False) == 1
        item.refresh_from_db()
        assert item.current_stock == 10

    def test_omr_apply_mark_idempotent_when_state_unchanged(self):
        item, usage = _wo_material(current_stock=10, quantity_used=Decimal("2.00"))
        wo = usage.work_order
        omr_apply_mark(wo, f"material_{usage.id}", marked=True)
        # Same target state again → guarded no-op, no second decrement.
        assert omr_apply_mark(wo, f"material_{usage.id}", marked=True) == 0
        item.refresh_from_db()
        assert item.current_stock == 8
