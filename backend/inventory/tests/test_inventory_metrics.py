"""API tests for the inventory item computed-metrics endpoint (issue-5).

Covers the ``GET /api/inventory/items/<id>/metrics/`` contract that powers the
``SKU · QOH · QOO · QA · QC · QIT · RP · Lead · Cost`` row on the web
item-detail page and the paired ScanTTY TUI row. Each computed field
(QOO/QIT/QC/QA/cost trend/case cost) has fixture-backed coverage.
"""

from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status

from inventory.models import MaintenanceItem, MaintenanceMaterial, WorkOrder
from inventory.tests.factories import AssetFactory, InventoryItemFactory
from reorder_queue.models import PurchaseOrder, PurchaseOrderItem
from reorder_queue.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

CONTRACT_FIELDS = {
    "current_stock",
    "quantity_on_order",
    "quantity_available",
    "quantity_committed",
    "quantity_in_transit",
    "reorder_point",
    "lead_time_days",
    "unit_cost",
    "cost_trend",
    "last_po_unit_cost",
    "is_case_based",
    "case_size",
}


_DEFAULT_PO_UNIT_COST = Decimal("1.0000")


def _metrics_url(item):
    return reverse("inventoryitem-metrics", kwargs={"pk": str(item.id)})


def _po_line(
    item,
    *,
    po_status,
    quantity_ordered,
    quantity_received=0,
    unit_cost_ordered=_DEFAULT_PO_UNIT_COST,
    is_voided=False,
    created_by=None,
):
    """Create a PurchaseOrder + a single line for ``item``'s primary supplier."""
    item_supplier = item.primary_item_supplier
    po = PurchaseOrder.objects.create(
        supplier=item_supplier.supplier,
        created_by=created_by or UserFactory(),
        status=po_status,
    )
    return PurchaseOrderItem.objects.create(
        purchase_order=po,
        item_supplier=item_supplier,
        quantity_ordered=quantity_ordered,
        quantity_received=quantity_received,
        unit_cost_ordered=unit_cost_ordered,
        is_voided=is_voided,
    )


def _committed_material(item, asset, *, quantity, wo_statuses):
    """Attach ``quantity`` of ``item`` to a fresh task carrying ``wo_statuses``."""
    task = MaintenanceItem.objects.create(asset=asset, title="task")
    for wo_status in wo_statuses:
        WorkOrder.objects.create(maintenance_item=task, status=wo_status)
    return MaintenanceMaterial.objects.create(
        maintenance_item=task,
        inventory_item=item,
        name="widget",
        quantity=Decimal(quantity),
    )


class TestMetricsEndpointContract:
    def test_endpoint_is_public_and_returns_exact_contract(self, api_client):
        item = InventoryItemFactory(image=None, current_stock=10, reorder_quantity=4)

        response = api_client.get(_metrics_url(item))

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        # Exactly the pinned contract — no more, no less (ScanTTY depends on it).
        assert set(data.keys()) == CONTRACT_FIELDS
        assert data["current_stock"] == 10
        assert data["reorder_point"] == 4

    def test_reorder_point_and_lead_time_echo_the_item(self, api_client):
        item = InventoryItemFactory(
            image=None, current_stock=5, reorder_quantity=9, average_lead_time=14
        )

        data = api_client.get(_metrics_url(item)).json()

        assert data["reorder_point"] == 9  # RP == reorder_quantity
        assert data["lead_time_days"] == 14  # Lead == average_lead_time


class TestQuantityOnOrder:
    def test_counts_open_pos_and_excludes_closed_and_voided(self, api_client):
        item = InventoryItemFactory(image=None, current_stock=0, reorder_quantity=1)
        user = UserFactory()

        # Counted — the three open statuses.
        _po_line(item, po_status=PurchaseOrder.SENT, quantity_ordered=5, created_by=user)
        _po_line(item, po_status=PurchaseOrder.CONFIRMED, quantity_ordered=7, created_by=user)
        _po_line(
            item,
            po_status=PurchaseOrder.PARTIALLY_RECEIVED,
            quantity_ordered=10,
            quantity_received=4,
            created_by=user,
        )
        # Not counted — draft / received / cancelled.
        _po_line(item, po_status=PurchaseOrder.DRAFT, quantity_ordered=100, created_by=user)
        _po_line(
            item,
            po_status=PurchaseOrder.RECEIVED,
            quantity_ordered=100,
            quantity_received=100,
            created_by=user,
        )
        _po_line(item, po_status=PurchaseOrder.CANCELLED, quantity_ordered=100, created_by=user)
        # Not counted — a voided line on an otherwise-open PO.
        _po_line(
            item,
            po_status=PurchaseOrder.SENT,
            quantity_ordered=100,
            is_voided=True,
            created_by=user,
        )

        data = api_client.get(_metrics_url(item)).json()

        assert data["quantity_on_order"] == 22  # 5 + 7 + 10

    def test_zero_when_no_purchase_orders(self, api_client):
        item = InventoryItemFactory(image=None, current_stock=3, reorder_quantity=1)

        data = api_client.get(_metrics_url(item)).json()

        assert data["quantity_on_order"] == 0
        assert data["quantity_in_transit"] == 0


class TestQuantityInTransit:
    def test_is_pending_on_partially_received_lines_only(self, api_client):
        item = InventoryItemFactory(image=None, current_stock=0, reorder_quantity=1)
        user = UserFactory()

        # Partially received: 10 ordered, 4 received -> 6 in transit.
        _po_line(
            item,
            po_status=PurchaseOrder.PARTIALLY_RECEIVED,
            quantity_ordered=10,
            quantity_received=4,
            created_by=user,
        )
        # A fully-received line on a partial PO contributes nothing in transit.
        _po_line(
            item,
            po_status=PurchaseOrder.PARTIALLY_RECEIVED,
            quantity_ordered=8,
            quantity_received=8,
            created_by=user,
        )
        # A plain sent line is on order but not yet in transit.
        _po_line(item, po_status=PurchaseOrder.SENT, quantity_ordered=5, created_by=user)

        data = api_client.get(_metrics_url(item)).json()

        assert data["quantity_in_transit"] == 6
        # QIT is a subset of QOO by definition.
        assert data["quantity_on_order"] == 23  # 10 + 8 + 5
        assert data["quantity_in_transit"] <= data["quantity_on_order"]


class TestQuantityCommittedAndAvailable:
    def test_counts_only_open_work_order_materials(self, api_client):
        item = InventoryItemFactory(image=None, current_stock=20, reorder_quantity=1)
        asset = AssetFactory()

        _committed_material(item, asset, quantity="3", wo_statuses=[WorkOrder.STATUS_OPEN])
        _committed_material(item, asset, quantity="2", wo_statuses=[WorkOrder.STATUS_IN_PROGRESS])
        _committed_material(item, asset, quantity="1", wo_statuses=[WorkOrder.STATUS_BLOCKED])
        # Completed work order -> not committed.
        _committed_material(item, asset, quantity="100", wo_statuses=[WorkOrder.STATUS_COMPLETED])
        # Material with no work order at all -> not committed.
        _committed_material(item, asset, quantity="50", wo_statuses=[])

        data = api_client.get(_metrics_url(item)).json()

        assert data["quantity_committed"] == 6.0  # 3 + 2 + 1
        assert data["quantity_available"] == 14.0  # 20 on hand - 6 committed

    def test_material_not_double_counted_across_multiple_open_work_orders(self, api_client):
        """A task with several open work orders must not multiply its material."""
        item = InventoryItemFactory(image=None, current_stock=10, reorder_quantity=1)
        asset = AssetFactory()

        _committed_material(
            item,
            asset,
            quantity="4",
            wo_statuses=[WorkOrder.STATUS_OPEN, WorkOrder.STATUS_IN_PROGRESS],
        )

        data = api_client.get(_metrics_url(item)).json()

        assert data["quantity_committed"] == 4.0  # not 8.0
        assert data["quantity_available"] == 6.0

    def test_available_can_go_negative_when_overcommitted(self, api_client):
        item = InventoryItemFactory(image=None, current_stock=1, reorder_quantity=1)
        asset = AssetFactory()

        _committed_material(item, asset, quantity="5", wo_statuses=[WorkOrder.STATUS_OPEN])

        data = api_client.get(_metrics_url(item)).json()

        assert data["quantity_committed"] == 5.0
        assert data["quantity_available"] == -4.0


class TestCostAndTrend:
    def test_no_history_when_item_has_no_purchase_orders(self, api_client):
        item = InventoryItemFactory(image=None, reorder_quantity=1, unit_cost=Decimal("4.00"))

        data = api_client.get(_metrics_url(item)).json()

        assert data["cost_trend"] == "no_history"
        assert data["last_po_unit_cost"] is None

    @pytest.mark.parametrize(
        "current, last_po, expected",
        [
            (Decimal("5.00"), Decimal("4.0000"), "up"),
            (Decimal("3.00"), Decimal("4.0000"), "down"),
            (Decimal("4.00"), Decimal("4.0000"), "flat"),
        ],
    )
    def test_trend_compares_current_cost_to_last_po(self, api_client, current, last_po, expected):
        item = InventoryItemFactory(image=None, reorder_quantity=1, unit_cost=current)
        _po_line(
            item,
            po_status=PurchaseOrder.RECEIVED,
            quantity_ordered=1,
            quantity_received=1,
            unit_cost_ordered=last_po,
        )

        data = api_client.get(_metrics_url(item)).json()

        assert data["cost_trend"] == expected
        assert Decimal(data["last_po_unit_cost"]) == last_po
        assert Decimal(data["unit_cost"]) == current

    def test_trend_uses_the_most_recent_po_line(self, api_client):
        item = InventoryItemFactory(image=None, reorder_quantity=1, unit_cost=Decimal("5.00"))
        older = _po_line(
            item,
            po_status=PurchaseOrder.RECEIVED,
            quantity_ordered=1,
            quantity_received=1,
            unit_cost_ordered=Decimal("9.0000"),
        )
        newer = _po_line(
            item,
            po_status=PurchaseOrder.RECEIVED,
            quantity_ordered=1,
            quantity_received=1,
            unit_cost_ordered=Decimal("4.0000"),
        )
        # auto_now_add can tie in tests; stamp created_at so ordering is stable.
        now = timezone.now()
        PurchaseOrderItem.objects.filter(pk=older.pk).update(created_at=now - timedelta(days=2))
        PurchaseOrderItem.objects.filter(pk=newer.pk).update(created_at=now - timedelta(days=1))

        data = api_client.get(_metrics_url(item)).json()

        assert Decimal(data["last_po_unit_cost"]) == Decimal("4.0000")
        assert data["cost_trend"] == "up"  # current 5.00 > most-recent 4.00


class TestCaseBasedCost:
    def test_case_based_reports_case_cost_and_size(self, api_client):
        # package_cost is derived as unit_cost * quantity_per_package = 2.00 * 12.
        item = InventoryItemFactory(
            image=None,
            reorder_quantity=1,
            current_stock=100,
            use_case_based_reorder=True,
            unit_cost=Decimal("2.00"),
            quantity_per_package=12,
        )

        data = api_client.get(_metrics_url(item)).json()

        assert data["is_case_based"] is True
        assert data["case_size"] == 12
        assert Decimal(data["unit_cost"]) == Decimal("24.00")  # per-case cost

    def test_per_item_cost_when_not_case_based(self, api_client):
        item = InventoryItemFactory(
            image=None,
            reorder_quantity=1,
            current_stock=100,
            unit_cost=Decimal("2.50"),
            quantity_per_package=1,
        )

        data = api_client.get(_metrics_url(item)).json()

        assert data["is_case_based"] is False
        assert Decimal(data["unit_cost"]) == Decimal("2.50")  # per-unit cost
