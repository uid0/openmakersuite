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

from inventory.models import MaintenanceItem, MaintenanceMaterial, WorkOrder, WorkOrderMaterialUsage
from inventory.services.work_order_material_usage import apply_material_usage
from inventory.tests.factories import AssetFactory, InventoryItemFactory
from reorder_queue.models import PurchaseOrder, PurchaseOrderItem
from reorder_queue.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

CONTRACT_FIELDS = {
    "current_stock",
    "quantity_on_order",
    "quantity_available",
    "quantity_committed",
    "committed_breakdown",
    "quantity_in_transit",
    "reorder_point",
    "lead_time_days",
    "unit_cost",
    "cost_trend",
    "last_po_unit_cost",
    "is_case_based",
    "case_size",
}

BREAKDOWN_FIELDS = {
    "work_order_id",
    "work_order_short_id",
    "asset_id",
    "asset_name",
    "quantity",
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


def _keep_in_receiving(purchase_order, *, created_by=None):
    """Give ``purchase_order`` an outstanding line for some OTHER item.

    A purchase order re-derives its own status whenever one of its lines is
    written, so an order made only of settled lines will not sit in
    ``partially_received`` waiting to be measured. This is what a real
    part-received order has and a hand-built one forgets: something still owed.
    The line is for a different item, so it contributes to no figure being
    asserted about the item under test.
    """
    other = InventoryItemFactory(image=None, current_stock=0, reorder_quantity=1)
    return PurchaseOrderItem.objects.create(
        purchase_order=purchase_order,
        item_supplier=other.primary_item_supplier,
        quantity_ordered=1,
        quantity_received=0,
        unit_cost_ordered=_DEFAULT_PO_UNIT_COST,
    )


def _committed_material(item, asset, *, quantity, wo_statuses):
    """Attach ``quantity`` of ``item`` to a fresh task carrying ``wo_statuses``."""
    material, _work_orders = _template_material(
        item, asset, quantity=quantity, wo_statuses=wo_statuses
    )
    return material


def _template_material(item, asset, *, quantity, wo_statuses):
    """A PM template material plus the work orders generated off its task.

    Returns ``(material, work_orders)`` so a test can materialise the frozen
    usage rows a real generated work order carries (``_materialise_usage``).
    """
    task = MaintenanceItem.objects.create(asset=asset, title="task")
    work_orders = [
        WorkOrder.objects.create(maintenance_item=task, status=wo_status)
        for wo_status in wo_statuses
    ]
    material = MaintenanceMaterial.objects.create(
        maintenance_item=task,
        inventory_item=item,
        name="widget",
        quantity=Decimal(quantity),
    )
    return material, work_orders


def _materialise_usage(work_order, material, *, quantity=None):
    """The frozen usage row a *generated* work order carries per template material.

    Mirrors ``generate_work_order``: quantity planned and quantity used both
    start at the template's quantity, and the row starts un-used.
    """
    quantity = material.quantity if quantity is None else Decimal(quantity)
    return WorkOrderMaterialUsage.objects.create(
        work_order=work_order,
        material=material,
        material_name=material.name,
        quantity_planned=quantity,
        quantity_used=quantity,
        unit=material.unit,
    )


def _ad_hoc_usage(item, *, work_order, quantity, material_name="ad-hoc widget"):
    """An ad-hoc material line typed in during the job (op-768w), linked to stock."""
    return WorkOrderMaterialUsage.objects.create(
        work_order=work_order,
        material=None,
        is_ad_hoc=True,
        inventory_item=item,
        material_name=material_name,
        quantity_planned=Decimal(quantity),
        quantity_used=Decimal(quantity),
    )


def _corrective_work_order(asset, *, status=WorkOrder.Status.OPEN):
    """A template-less work order — the only kind ad-hoc materials can hang off."""
    return WorkOrder.objects.create(asset=asset, status=status)


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
        _po_line(item, po_status=PurchaseOrder.Status.SENT, quantity_ordered=5, created_by=user)
        _po_line(
            item, po_status=PurchaseOrder.Status.CONFIRMED, quantity_ordered=7, created_by=user
        )
        _po_line(
            item,
            po_status=PurchaseOrder.Status.PARTIALLY_RECEIVED,
            quantity_ordered=10,
            quantity_received=4,
            created_by=user,
        )
        # Not counted — draft / received / cancelled.
        _po_line(item, po_status=PurchaseOrder.Status.DRAFT, quantity_ordered=100, created_by=user)
        _po_line(
            item,
            po_status=PurchaseOrder.Status.RECEIVED,
            quantity_ordered=100,
            quantity_received=100,
            created_by=user,
        )
        _po_line(
            item, po_status=PurchaseOrder.Status.CANCELLED, quantity_ordered=100, created_by=user
        )
        # Not counted — a voided line on an otherwise-open PO.
        _po_line(
            item,
            po_status=PurchaseOrder.Status.SENT,
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
            po_status=PurchaseOrder.Status.PARTIALLY_RECEIVED,
            quantity_ordered=8 + 2,
            quantity_received=4,
            created_by=user,
        )
        # A fully-received line on a partial PO contributes nothing in transit.
        # It needs a line of its own holding that order in receiving: a line's
        # save re-derives its order now (``reorder_queue.settlement_signals``),
        # so an order whose only line is fully received settles itself and stops
        # being a partially-received order to measure. The companion line is for
        # a DIFFERENT item, so it changes neither figure asserted below.
        fully_received = _po_line(
            item,
            po_status=PurchaseOrder.Status.PARTIALLY_RECEIVED,
            quantity_ordered=8,
            quantity_received=8,
            created_by=user,
        )
        _keep_in_receiving(fully_received.purchase_order, created_by=user)
        # A plain sent line is on order but not yet in transit.
        _po_line(item, po_status=PurchaseOrder.Status.SENT, quantity_ordered=5, created_by=user)

        data = api_client.get(_metrics_url(item)).json()

        assert data["quantity_in_transit"] == 6
        # QIT is a subset of QOO by definition.
        assert data["quantity_on_order"] == 23  # 10 + 8 + 5
        assert data["quantity_in_transit"] <= data["quantity_on_order"]


class TestQuantityCommittedAndAvailable:
    def test_counts_only_open_work_order_materials(self, api_client):
        item = InventoryItemFactory(image=None, current_stock=20, reorder_quantity=1)
        asset = AssetFactory()

        _committed_material(item, asset, quantity="3", wo_statuses=[WorkOrder.Status.OPEN])
        _committed_material(item, asset, quantity="2", wo_statuses=[WorkOrder.Status.IN_PROGRESS])
        _committed_material(item, asset, quantity="1", wo_statuses=[WorkOrder.Status.BLOCKED])
        # Completed work order -> not committed.
        _committed_material(item, asset, quantity="100", wo_statuses=[WorkOrder.Status.COMPLETED])
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
            wo_statuses=[WorkOrder.Status.OPEN, WorkOrder.Status.IN_PROGRESS],
        )

        data = api_client.get(_metrics_url(item)).json()

        assert data["quantity_committed"] == 4.0  # not 8.0
        assert data["quantity_available"] == 6.0

    def test_available_can_go_negative_when_overcommitted(self, api_client):
        item = InventoryItemFactory(image=None, current_stock=1, reorder_quantity=1)
        asset = AssetFactory()

        _committed_material(item, asset, quantity="5", wo_statuses=[WorkOrder.Status.OPEN])

        data = api_client.get(_metrics_url(item)).json()

        assert data["quantity_committed"] == 5.0
        assert data["quantity_available"] == -4.0


class TestCommittedFromWorkOrderMaterialUsage:
    """QC counts the ACTUAL material rows too, not just PM-template specs.

    A corrective work order has no template at all, so every material on it is
    an ad-hoc ``WorkOrderMaterialUsage`` row — exactly the demand that used to
    be invisible to QC.
    """

    def test_ad_hoc_line_on_an_open_work_order_is_committed(self, api_client):
        item = InventoryItemFactory(image=None, current_stock=20, reorder_quantity=1)
        _ad_hoc_usage(item, work_order=_corrective_work_order(AssetFactory()), quantity="3")

        data = api_client.get(_metrics_url(item)).json()

        assert data["quantity_committed"] == 3.0
        assert data["quantity_available"] == 17.0  # 20 on hand - 3 assigned to the job

    @pytest.mark.parametrize(
        "wo_status",
        [WorkOrder.Status.OPEN, WorkOrder.Status.IN_PROGRESS, WorkOrder.Status.BLOCKED],
    )
    def test_every_open_status_commits(self, api_client, wo_status):
        item = InventoryItemFactory(image=None, current_stock=10, reorder_quantity=1)
        work_order = _corrective_work_order(AssetFactory(), status=wo_status)
        _ad_hoc_usage(item, work_order=work_order, quantity="2")

        data = api_client.get(_metrics_url(item)).json()

        assert data["quantity_committed"] == 2.0

    def test_completed_work_order_is_not_committed(self, api_client):
        item = InventoryItemFactory(image=None, current_stock=10, reorder_quantity=1)
        work_order = _corrective_work_order(AssetFactory(), status=WorkOrder.Status.COMPLETED)
        _ad_hoc_usage(item, work_order=work_order, quantity="4")

        data = api_client.get(_metrics_url(item)).json()

        assert data["quantity_committed"] == 0.0
        assert data["quantity_available"] == 10.0
        assert data["committed_breakdown"] == []

    def test_consuming_the_line_moves_it_out_of_committed_not_double_counts(self, api_client):
        """The same line before and after it is marked used: never subtracted twice.

        Before: stock is untouched and the units are committed. After: the units
        have left ``current_stock``, so QC must drop them — available is the
        same number either side of the consume.
        """
        item = InventoryItemFactory(image=None, current_stock=20, reorder_quantity=1)
        usage = _ad_hoc_usage(item, work_order=_corrective_work_order(AssetFactory()), quantity="3")

        before = api_client.get(_metrics_url(item)).json()
        assert before["current_stock"] == 20
        assert before["quantity_committed"] == 3.0
        assert before["quantity_available"] == 17.0

        apply_material_usage(usage, was_used=True)  # the one apply seam (op-uh8z)

        after = api_client.get(_metrics_url(item)).json()
        assert after["current_stock"] == 17  # stock actually moved
        assert after["quantity_committed"] == 0.0  # ... so it is no longer committed
        assert after["quantity_available"] == 17.0  # ... and available did not move twice
        assert after["committed_breakdown"] == []

    def test_reversing_the_consume_puts_the_line_back_into_committed(self, api_client):
        item = InventoryItemFactory(image=None, current_stock=20, reorder_quantity=1)
        usage = _ad_hoc_usage(item, work_order=_corrective_work_order(AssetFactory()), quantity="3")
        apply_material_usage(usage, was_used=True)
        apply_material_usage(usage, was_used=False)  # un-marked: stock restored

        data = api_client.get(_metrics_url(item)).json()

        assert data["current_stock"] == 20
        assert data["quantity_committed"] == 3.0
        assert data["quantity_available"] == 17.0

    def test_out_of_pocket_line_with_no_stock_link_commits_nothing(self, api_client):
        """An unlinked (flag-only) line moves no stock, so it reserves none either."""
        item = InventoryItemFactory(image=None, current_stock=10, reorder_quantity=1)
        WorkOrderMaterialUsage.objects.create(
            work_order=_corrective_work_order(AssetFactory()),
            material=None,
            is_ad_hoc=True,
            inventory_item=None,
            material_name="bought at the hardware store",
            quantity_planned=Decimal("5.00"),
            quantity_used=Decimal("5.00"),
            unit_cost=Decimal("9.99"),
        )

        data = api_client.get(_metrics_url(item)).json()

        assert data["quantity_committed"] == 0.0
        assert data["quantity_available"] == 10.0

    def test_ad_hoc_and_template_demand_add_up(self, api_client):
        """Mixed sources on one item: a PM template WO plus a corrective WO."""
        item = InventoryItemFactory(image=None, current_stock=20, reorder_quantity=1)
        asset = AssetFactory()
        _committed_material(item, asset, quantity="3", wo_statuses=[WorkOrder.Status.OPEN])
        _ad_hoc_usage(item, work_order=_corrective_work_order(asset), quantity="2")

        data = api_client.get(_metrics_url(item)).json()

        assert data["quantity_committed"] == 5.0  # 3 template + 2 ad-hoc
        assert data["quantity_available"] == 15.0

    def test_multiple_lines_on_one_work_order_sum(self, api_client):
        item = InventoryItemFactory(image=None, current_stock=20, reorder_quantity=1)
        work_order = _corrective_work_order(AssetFactory())
        _ad_hoc_usage(item, work_order=work_order, quantity="2", material_name="first")
        _ad_hoc_usage(item, work_order=work_order, quantity="3", material_name="second")

        data = api_client.get(_metrics_url(item)).json()

        assert data["quantity_committed"] == 5.0
        assert len(data["committed_breakdown"]) == 1  # one work order, one entry
        assert data["committed_breakdown"][0]["quantity"] == 5.0


class TestCommittedTemplateVsUsageReconciliation:
    """A generated work order carries BOTH a template spec and a frozen usage
    row for it. Counting each would double the demand, so the actual rows win
    per work order and the template is the fallback for a work order that has
    not materialised any.
    """

    def test_template_material_still_counts_when_no_usage_rows_exist(self, api_client):
        """Pre-existing behavior, unchanged: the spec is the only signal there is."""
        item = InventoryItemFactory(image=None, current_stock=10, reorder_quantity=1)
        _committed_material(item, AssetFactory(), quantity="4", wo_statuses=[WorkOrder.Status.OPEN])

        data = api_client.get(_metrics_url(item)).json()

        assert data["quantity_committed"] == 4.0
        assert data["quantity_available"] == 6.0

    def test_materialised_usage_row_supersedes_its_template_spec(self, api_client):
        item = InventoryItemFactory(image=None, current_stock=10, reorder_quantity=1)
        material, work_orders = _template_material(
            item, AssetFactory(), quantity="4", wo_statuses=[WorkOrder.Status.OPEN]
        )
        _materialise_usage(work_orders[0], material)

        data = api_client.get(_metrics_url(item)).json()

        assert data["quantity_committed"] == 4.0  # not 8.0 — counted once
        assert data["quantity_available"] == 6.0
        assert len(data["committed_breakdown"]) == 1

    def test_usage_row_quantity_wins_over_the_template_quantity(self, api_client):
        """Edited on the job (2 instead of the planned 4): QC follows the real row."""
        item = InventoryItemFactory(image=None, current_stock=10, reorder_quantity=1)
        material, work_orders = _template_material(
            item, AssetFactory(), quantity="4", wo_statuses=[WorkOrder.Status.OPEN]
        )
        _materialise_usage(work_orders[0], material, quantity="2")

        data = api_client.get(_metrics_url(item)).json()

        assert data["quantity_committed"] == 2.0
        assert data["quantity_available"] == 8.0

    def test_consumed_template_material_is_not_transiently_double_counted(self, api_client):
        """The flagged bug: a material consumed while its work order is STILL open.

        Stock has already dropped, so the template spec must stop being counted
        as committed — otherwise the same units are subtracted twice.
        """
        item = InventoryItemFactory(image=None, current_stock=10, reorder_quantity=1)
        material, work_orders = _template_material(
            item, AssetFactory(), quantity="4", wo_statuses=[WorkOrder.Status.OPEN]
        )
        usage = _materialise_usage(work_orders[0], material)

        apply_material_usage(usage, was_used=True)
        work_orders[0].refresh_from_db()
        assert work_orders[0].status == WorkOrder.Status.OPEN  # job is not finished

        data = api_client.get(_metrics_url(item)).json()

        assert data["current_stock"] == 6  # 10 - 4 consumed
        assert data["quantity_committed"] == 0.0  # NOT 4.0 (the double-count)
        assert data["quantity_available"] == 6.0  # NOT 2.0

    def test_template_falls_back_to_the_work_order_that_has_no_usage_rows(self, api_client):
        """Two open work orders off one task, only one of them materialised.

        The material is still needed for the un-materialised job, so it counts
        once — attributed to that work order, not to the materialised one.
        """
        item = InventoryItemFactory(image=None, current_stock=10, reorder_quantity=1)
        material, work_orders = _template_material(
            item,
            AssetFactory(),
            quantity="4",
            wo_statuses=[WorkOrder.Status.OPEN, WorkOrder.Status.IN_PROGRESS],
        )
        materialised, bare = work_orders
        usage = _materialise_usage(materialised, material)
        apply_material_usage(usage, was_used=True)  # that job consumed its share

        data = api_client.get(_metrics_url(item)).json()

        assert data["current_stock"] == 6
        assert data["quantity_committed"] == 4.0  # the still-unstarted job's share
        assert [entry["work_order_id"] for entry in data["committed_breakdown"]] == [str(bare.id)]

    def test_template_material_not_double_counted_across_open_work_orders(self, api_client):
        """Pre-existing rule kept: one material, one count, however many WOs."""
        item = InventoryItemFactory(image=None, current_stock=10, reorder_quantity=1)
        _committed_material(
            item,
            AssetFactory(),
            quantity="4",
            wo_statuses=[WorkOrder.Status.OPEN, WorkOrder.Status.IN_PROGRESS],
        )

        data = api_client.get(_metrics_url(item)).json()

        assert data["quantity_committed"] == 4.0  # not 8.0
        assert len(data["committed_breakdown"]) == 1  # attributed to exactly one WO


class TestCommittedBreakdown:
    """``committed_breakdown`` — WHERE the committed stock is going."""

    def test_lists_each_work_order_with_its_asset_and_quantity(self, api_client):
        item = InventoryItemFactory(image=None, current_stock=20, reorder_quantity=1)
        lathe = AssetFactory(name="Lathe")
        mill = AssetFactory(name="Mill")
        lathe_wo = _corrective_work_order(lathe)
        mill_wo = _corrective_work_order(mill)
        _ad_hoc_usage(item, work_order=lathe_wo, quantity="3")
        _ad_hoc_usage(item, work_order=mill_wo, quantity="2")
        # Oldest first, whatever order the rows were written in.
        now = timezone.now()
        WorkOrder.objects.filter(pk=mill_wo.pk).update(created_at=now - timedelta(days=2))
        WorkOrder.objects.filter(pk=lathe_wo.pk).update(created_at=now - timedelta(days=1))

        data = api_client.get(_metrics_url(item)).json()

        assert data["quantity_committed"] == 5.0
        assert data["committed_breakdown"] == [
            {
                "work_order_id": str(mill_wo.id),
                "work_order_short_id": mill_wo.short_id,
                "asset_id": str(mill.id),
                "asset_name": "Mill",
                "quantity": 2.0,
            },
            {
                "work_order_id": str(lathe_wo.id),
                "work_order_short_id": lathe_wo.short_id,
                "asset_id": str(lathe.id),
                "asset_name": "Lathe",
                "quantity": 3.0,
            },
        ]

    def test_entries_sum_to_quantity_committed(self, api_client):
        item = InventoryItemFactory(image=None, current_stock=50, reorder_quantity=1)
        asset = AssetFactory()
        _committed_material(item, asset, quantity="3", wo_statuses=[WorkOrder.Status.OPEN])
        _ad_hoc_usage(item, work_order=_corrective_work_order(asset), quantity="2")
        _ad_hoc_usage(item, work_order=_corrective_work_order(asset), quantity="7")

        data = api_client.get(_metrics_url(item)).json()

        entries = data["committed_breakdown"]
        assert len(entries) == 3
        assert all(set(entry) == BREAKDOWN_FIELDS for entry in entries)
        assert sum(entry["quantity"] for entry in entries) == data["quantity_committed"] == 12.0

    def test_is_empty_when_nothing_is_committed(self, api_client):
        item = InventoryItemFactory(image=None, current_stock=5, reorder_quantity=1)

        data = api_client.get(_metrics_url(item)).json()

        assert data["quantity_committed"] == 0.0
        assert data["committed_breakdown"] == []

    def test_template_material_is_attributed_to_its_work_orders_asset(self, api_client):
        item = InventoryItemFactory(image=None, current_stock=10, reorder_quantity=1)
        asset = AssetFactory(name="Bandsaw")
        _material, work_orders = _template_material(
            item, asset, quantity="4", wo_statuses=[WorkOrder.Status.OPEN]
        )

        entry = api_client.get(_metrics_url(item)).json()["committed_breakdown"][0]

        assert entry["work_order_id"] == str(work_orders[0].id)
        assert entry["work_order_short_id"] == work_orders[0].short_id
        assert entry["asset_id"] == str(asset.id)
        assert entry["asset_name"] == "Bandsaw"
        assert entry["quantity"] == 4.0

    def test_another_items_demand_never_leaks_into_this_ones_breakdown(self, api_client):
        mine = InventoryItemFactory(image=None, current_stock=10, reorder_quantity=1)
        theirs = InventoryItemFactory(image=None, current_stock=10, reorder_quantity=1)
        work_order = _corrective_work_order(AssetFactory())
        _ad_hoc_usage(mine, work_order=work_order, quantity="1", material_name="mine")
        _ad_hoc_usage(theirs, work_order=work_order, quantity="9", material_name="theirs")

        data = api_client.get(_metrics_url(mine)).json()

        assert data["quantity_committed"] == 1.0
        assert [entry["quantity"] for entry in data["committed_breakdown"]] == [1.0]


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
            po_status=PurchaseOrder.Status.RECEIVED,
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
            po_status=PurchaseOrder.Status.RECEIVED,
            quantity_ordered=1,
            quantity_received=1,
            unit_cost_ordered=Decimal("9.0000"),
        )
        newer = _po_line(
            item,
            po_status=PurchaseOrder.Status.RECEIVED,
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
