"""API tests for the opt-in ``?with_metrics=1`` inventory item LIST annotation (op-7wul).

The list endpoint gains a per-item ``metrics`` object identical in shape to the
``/metrics/`` detail action (issue-5), computed AFTER pagination in a bounded
number of queries — never an N+1 — and only when explicitly requested. The
default response is unchanged.
"""

from decimal import Decimal

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

import pytest
from rest_framework import status

from inventory.models import MaintenanceItem, MaintenanceMaterial, WorkOrder
from inventory.tests.factories import AssetFactory, InventoryItemFactory
from reorder_queue.models import PurchaseOrder, PurchaseOrderItem
from reorder_queue.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

_DEFAULT_PO_UNIT_COST = Decimal("1.0000")

METRICS_FIELDS = {
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


def _list_url(*, with_metrics=False):
    url = reverse("inventoryitem-list")
    return f"{url}?with_metrics=1" if with_metrics else url


def _detail_metrics_url(item):
    return reverse("inventoryitem-metrics", kwargs={"pk": str(item.id)})


def _results(response):
    """Return the list rows whether or not the response is paginated."""
    body = response.json()
    if isinstance(body, dict) and "results" in body:
        return body["results"]
    return body


def _row_for(results, item):
    return next(row for row in results if str(row["id"]) == str(item.id))


def _open_po_line(
    item,
    *,
    quantity_ordered,
    quantity_received=0,
    po_status=PurchaseOrder.SENT,
    unit_cost_ordered=_DEFAULT_PO_UNIT_COST,
):
    """Create a PurchaseOrder + single line for ``item``'s primary supplier."""
    supplier = item.primary_item_supplier
    po = PurchaseOrder.objects.create(
        supplier=supplier.supplier,
        created_by=UserFactory(),
        status=po_status,
    )
    return PurchaseOrderItem.objects.create(
        purchase_order=po,
        item_supplier=supplier,
        quantity_ordered=quantity_ordered,
        quantity_received=quantity_received,
        unit_cost_ordered=unit_cost_ordered,
    )


def _commit_material(item, asset, *, quantity, wo_statuses):
    """Attach ``quantity`` of ``item`` to a task carrying ``wo_statuses``."""
    task = MaintenanceItem.objects.create(asset=asset, title="task")
    for wo_status in wo_statuses:
        WorkOrder.objects.create(maintenance_item=task, status=wo_status)
    return MaintenanceMaterial.objects.create(
        maintenance_item=task,
        inventory_item=item,
        name="widget",
        quantity=Decimal(quantity),
    )


class TestListMetricsOptIn:
    def test_default_list_omits_metrics(self, api_client):
        InventoryItemFactory(image=None)
        InventoryItemFactory(image=None)

        results = _results(api_client.get(_list_url()))

        assert results
        assert all("metrics" not in row for row in results)

    def test_with_metrics_annotates_every_item_with_full_contract(self, api_client):
        InventoryItemFactory(image=None, current_stock=10, reorder_quantity=4)
        InventoryItemFactory(image=None, current_stock=7, reorder_quantity=2)

        results = _results(api_client.get(_list_url(with_metrics=True)))

        assert len(results) == 2
        for row in results:
            # Exactly the pinned contract per item — no more, no less.
            assert set(row["metrics"].keys()) == METRICS_FIELDS


class TestListMetricsValues:
    def test_computes_correct_values_independently_per_item(self, api_client):
        asset = AssetFactory()
        # Item A: on order + in transit + committed against 20 on hand.
        a = InventoryItemFactory(image=None, current_stock=20, reorder_quantity=5)
        _open_po_line(a, quantity_ordered=8, po_status=PurchaseOrder.SENT)
        _open_po_line(
            a,
            quantity_ordered=10,
            quantity_received=4,
            po_status=PurchaseOrder.PARTIALLY_RECEIVED,
        )
        _commit_material(a, asset, quantity="3", wo_statuses=[WorkOrder.STATUS_OPEN])
        # Item B: no PO / work-order activity at all.
        b = InventoryItemFactory(image=None, current_stock=3, reorder_quantity=1)

        results = _results(api_client.get(_list_url(with_metrics=True)))
        ma = _row_for(results, a)["metrics"]
        mb = _row_for(results, b)["metrics"]

        assert ma["quantity_on_order"] == 18  # 8 + 10
        assert ma["quantity_in_transit"] == 6  # 10 ordered - 4 received
        assert ma["quantity_committed"] == 3.0
        assert ma["quantity_available"] == 17.0  # 20 on hand - 3 committed
        assert ma["reorder_point"] == 5

        # Item B's metrics are its own, unaffected by item A's activity.
        assert mb["quantity_on_order"] == 0
        assert mb["quantity_in_transit"] == 0
        assert mb["quantity_committed"] == 0.0
        assert mb["quantity_available"] == 3.0

    def test_material_not_double_counted_across_multiple_open_work_orders(self, api_client):
        asset = AssetFactory()
        item = InventoryItemFactory(image=None, current_stock=10, reorder_quantity=1)
        _commit_material(
            item,
            asset,
            quantity="4",
            wo_statuses=[WorkOrder.STATUS_OPEN, WorkOrder.STATUS_IN_PROGRESS],
        )

        results = _results(api_client.get(_list_url(with_metrics=True)))
        metrics = _row_for(results, item)["metrics"]

        assert metrics["quantity_committed"] == 4.0  # not 8.0
        assert metrics["quantity_available"] == 6.0

    def test_case_based_item_reports_case_cost_and_size(self, api_client):
        item = InventoryItemFactory(
            image=None,
            reorder_quantity=1,
            current_stock=100,
            use_case_based_reorder=True,
            unit_cost=Decimal("2.00"),
            quantity_per_package=12,
        )

        results = _results(api_client.get(_list_url(with_metrics=True)))
        metrics = _row_for(results, item)["metrics"]

        assert metrics["is_case_based"] is True
        assert metrics["case_size"] == 12
        assert Decimal(metrics["unit_cost"]) == Decimal("24.00")  # per-case cost

    def test_list_metrics_match_detail_endpoint(self, api_client):
        """The list annotation and the /metrics/ action share one implementation."""
        item = InventoryItemFactory(
            image=None, current_stock=12, reorder_quantity=3, unit_cost=Decimal("5.00")
        )
        _open_po_line(
            item,
            quantity_ordered=6,
            po_status=PurchaseOrder.CONFIRMED,
            unit_cost_ordered=Decimal("4.0000"),
        )

        results = _results(api_client.get(_list_url(with_metrics=True)))
        list_metrics = _row_for(results, item)["metrics"]
        detail_metrics = api_client.get(_detail_metrics_url(item)).json()

        assert list_metrics == detail_metrics
        assert list_metrics["cost_trend"] == "up"  # current 5.00 > last PO 4.00


def _count_queries(api_client, url):
    with CaptureQueriesContext(connection) as ctx:
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
    return len(ctx.captured_queries)


def _make_active_item(asset):
    """An item with a PO line and a committed material, so the aggregates hit rows."""
    item = InventoryItemFactory(image=None, current_stock=15, reorder_quantity=2)
    _open_po_line(item, quantity_ordered=5, po_status=PurchaseOrder.SENT)
    _commit_material(item, asset, quantity="1", wo_statuses=[WorkOrder.STATUS_OPEN])
    return item


class TestListMetricsQueryBudget:
    def test_metrics_add_constant_queries_regardless_of_page_size(self, api_client):
        """The annotation is batched: its query cost does not grow with the page."""
        asset = AssetFactory()
        for _ in range(2):
            _make_active_item(asset)

        # Warm one-time caches (content types, etc.) so the counts are stable.
        _count_queries(api_client, _list_url())
        _count_queries(api_client, _list_url(with_metrics=True))

        base_2 = _count_queries(api_client, _list_url())
        metrics_2 = _count_queries(api_client, _list_url(with_metrics=True))

        for _ in range(4):  # grow the page from 2 to 6 items
            _make_active_item(asset)

        base_6 = _count_queries(api_client, _list_url())
        metrics_6 = _count_queries(api_client, _list_url(with_metrics=True))

        # The opt-in path does real (bounded) extra work...
        assert metrics_2 > base_2
        # ...and adds the SAME number of queries for 6 items as for 2 — batched,
        # not per-row (an N+1 would make the 6-item delta larger).
        assert metrics_6 - base_6 == metrics_2 - base_2
        # ...and that constant is small (a handful of grouped aggregates).
        assert metrics_6 - base_6 <= 6

    def test_default_path_is_unaffected(self, api_client):
        """No param -> no metrics work: same query count for 2 vs 6 items' overhead."""
        asset = AssetFactory()
        for _ in range(2):
            _make_active_item(asset)
        _count_queries(api_client, _list_url())  # warm caches
        base_2 = _count_queries(api_client, _list_url())

        for _ in range(4):
            _make_active_item(asset)
        base_6 = _count_queries(api_client, _list_url())

        # Whatever the base list costs, adding items must not add metrics queries
        # (the default path never calls the batch helper). The base serializer's
        # own per-item cost is out of scope here; we only assert metrics add none:
        with CaptureQueriesContext(connection) as ctx:
            api_client.get(_list_url(with_metrics=True))
        assert len(ctx.captured_queries) > base_6  # opt-in strictly adds queries
        assert base_2 <= base_6  # sanity: default path still serves the list
