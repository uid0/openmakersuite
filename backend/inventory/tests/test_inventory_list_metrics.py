"""API tests for the opt-in ``?with_metrics=1`` inventory item LIST annotation (op-7wul).

The list endpoint gains a per-item ``metrics`` object identical in shape to the
``/metrics/`` detail action (issue-5), computed AFTER pagination in a bounded
number of queries — never an N+1 — and only when explicitly requested. The
default response is unchanged.
"""

from datetime import timedelta
from decimal import Decimal

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status

from donations.tests.factories import DispositionFactory, DonationFactory, DonationItemFactory
from inventory.models import (
    FixtureRefillRequest,
    MaintenanceItem,
    MaintenanceMaterial,
    WorkOrder,
    WorkOrderMaterialUsage,
)
from inventory.tests.factories import AssetFactory, FixtureFactory, InventoryItemFactory
from reorder_queue.models import PurchaseOrder, PurchaseOrderItem, ReorderRequest
from reorder_queue.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

_DEFAULT_PO_UNIT_COST = Decimal("1.0000")

METRICS_FIELDS = {
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
    # Why Cost / Lead may be blank or unbacked: the supplier scoring does not
    # punish a missing price or an empty delivery record (op-2rsp), so a
    # supplier can win carrying one, and the operator is told rather than left
    # to infer it from a blank cell.
    "supplier_scored_without_price",
    "supplier_scored_without_history",
}


@pytest.fixture
def metrics_reader(authenticated_client):
    """A signed-in client for ``?with_metrics=1``.

    The annotated row's vendor half (`lead_time_days`, `unit_cost`,
    `cost_trend`, `last_po_unit_cost`, the two `supplier_scored_without_*`
    flags) is withheld from a caller with no session
    (op-anonymous-read-posture), so the tests asserting those values read as a
    signed-in operator. The anonymous shape is pinned in
    ``inventory/tests/test_inventory_metrics.py``.
    """
    client, _user = authenticated_client
    return client


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
    po_status=PurchaseOrder.Status.SENT,
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

    def test_with_metrics_annotates_every_item_with_full_contract(self, metrics_reader):
        InventoryItemFactory(image=None, current_stock=10, reorder_quantity=4)
        InventoryItemFactory(image=None, current_stock=7, reorder_quantity=2)

        results = _results(metrics_reader.get(_list_url(with_metrics=True)))

        assert len(results) == 2
        for row in results:
            # Exactly the pinned contract per item — no more, no less.
            assert set(row["metrics"].keys()) == METRICS_FIELDS


class TestListMetricsValues:
    def test_computes_correct_values_independently_per_item(self, api_client):
        asset = AssetFactory()
        # Item A: on order + in transit + committed against 20 on hand.
        a = InventoryItemFactory(image=None, current_stock=20, reorder_quantity=5)
        _open_po_line(a, quantity_ordered=8, po_status=PurchaseOrder.Status.SENT)
        _open_po_line(
            a,
            quantity_ordered=10,
            quantity_received=4,
            po_status=PurchaseOrder.Status.PARTIALLY_RECEIVED,
        )
        _commit_material(a, asset, quantity="3", wo_statuses=[WorkOrder.Status.OPEN])
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
            wo_statuses=[WorkOrder.Status.OPEN, WorkOrder.Status.IN_PROGRESS],
        )

        results = _results(api_client.get(_list_url(with_metrics=True)))
        metrics = _row_for(results, item)["metrics"]

        assert metrics["quantity_committed"] == 4.0  # not 8.0
        assert metrics["quantity_available"] == 6.0

    def test_ad_hoc_work_order_demand_is_committed_and_attributed_per_row(self, api_client):
        """The batch path counts ad-hoc usage rows and attributes them, per item."""
        asset = AssetFactory(name="Lathe")
        a = InventoryItemFactory(image=None, current_stock=10, reorder_quantity=1)
        b = InventoryItemFactory(image=None, current_stock=10, reorder_quantity=1)
        work_order = WorkOrder.objects.create(asset=asset, status=WorkOrder.Status.OPEN)
        WorkOrderMaterialUsage.objects.create(
            work_order=work_order,
            is_ad_hoc=True,
            inventory_item=a,
            material_name="ad-hoc widget",
            quantity_planned=Decimal("2.00"),
            quantity_used=Decimal("2.00"),
        )

        results = _results(api_client.get(_list_url(with_metrics=True)))
        ma = _row_for(results, a)["metrics"]
        mb = _row_for(results, b)["metrics"]

        assert ma["quantity_committed"] == 2.0
        assert ma["quantity_available"] == 8.0
        assert ma["committed_breakdown"] == [
            {
                "work_order_id": str(work_order.id),
                "work_order_short_id": work_order.short_id,
                "asset_id": str(asset.id),
                "asset_name": "Lathe",
                "quantity": 2.0,
            }
        ]
        # ... and the other item on the same page is untouched by it.
        assert mb["quantity_committed"] == 0.0
        assert mb["committed_breakdown"] == []

    def test_case_based_item_reports_case_cost_and_size(self, metrics_reader):
        item = InventoryItemFactory(
            image=None,
            reorder_quantity=1,
            current_stock=100,
            use_case_based_reorder=True,
            unit_cost=Decimal("2.00"),
            quantity_per_package=12,
        )

        results = _results(metrics_reader.get(_list_url(with_metrics=True)))
        metrics = _row_for(results, item)["metrics"]

        assert metrics["is_case_based"] is True
        assert metrics["case_size"] == 12
        assert Decimal(metrics["unit_cost"]) == Decimal("24.00")  # per-case cost

    def test_list_metrics_match_detail_endpoint(self, metrics_reader):
        """The list annotation and the /metrics/ action share one implementation."""
        item = InventoryItemFactory(
            image=None, current_stock=12, reorder_quantity=3, unit_cost=Decimal("5.00")
        )
        _open_po_line(
            item,
            quantity_ordered=6,
            po_status=PurchaseOrder.Status.CONFIRMED,
            unit_cost_ordered=Decimal("4.0000"),
        )

        results = _results(metrics_reader.get(_list_url(with_metrics=True)))
        list_metrics = _row_for(results, item)["metrics"]
        detail_metrics = metrics_reader.get(_detail_metrics_url(item)).json()

        assert list_metrics == detail_metrics
        assert list_metrics["cost_trend"] == "up"  # current 5.00 > last PO 4.00


def _count_queries(metrics_reader, url):
    with CaptureQueriesContext(connection) as ctx:
        response = metrics_reader.get(url)
        assert response.status_code == status.HTTP_200_OK
    return len(ctx.captured_queries)


def _make_active_item(asset):
    """An item every metrics aggregate hits rows for.

    A PO line, a PM-template committed material, and an ad-hoc work-order usage
    row — so the committed pair of queries (template + actual usage) is
    exercised with real rows and an N+1 in either would show up here.
    """
    item = InventoryItemFactory(image=None, current_stock=15, reorder_quantity=2)
    _open_po_line(item, quantity_ordered=5, po_status=PurchaseOrder.Status.SENT)
    _commit_material(item, asset, quantity="1", wo_statuses=[WorkOrder.Status.OPEN])
    WorkOrderMaterialUsage.objects.create(
        work_order=WorkOrder.objects.create(asset=asset, status=WorkOrder.Status.OPEN),
        is_ad_hoc=True,
        inventory_item=item,
        material_name="ad-hoc widget",
        quantity_planned=Decimal("1.00"),
        quantity_used=Decimal("1.00"),
    )
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
        # ...and that constant is exactly the five grouped aggregates the batch
        # helper runs: on-order, in-transit, committed-from-usage,
        # committed-from-template, last PO cost. The supplier selection adds a
        # SIXTH only when it has to, and it does not have to here for TWO
        # reasons: this list prefetches through ``item_suppliers_prefetch()``,
        # so ``select_suppliers_for`` resolves the supplier rows from that cache
        # rather than re-reading identical rows, AND the delivery record the
        # performance term needs rides in on that same prefetch's row
        # annotations. Reverting this list to the bare
        # ``"item_suppliers__supplier"`` string would still give the right
        # answer, but only via the grouped-aggregate fallback — a sixth query
        # (op-2rsp). It was six before the prefetch.
        assert metrics_6 - base_6 == 5

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


def _supplier_query_count(api_client, url):
    """Number of captured queries that read the ItemSupplier table."""
    with CaptureQueriesContext(connection) as ctx:
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
    return sum(1 for q in ctx.captured_queries if "inventory_itemsupplier" in q["sql"])


class TestDefaultListSupplierQueryBudget:
    """Regression guard for the primary-supplier compat-field N+1 (issue #882)."""

    def test_supplier_compat_fields_do_not_add_a_query_per_row(self, api_client):
        """The seven flat primary-supplier compat fields are served from the list
        viewset's ``item_suppliers`` prefetch, not a per-row query.

        Before the prefetch-friendly ``primary_item_supplier`` (and the matching
        ``lowest_unit_cost`` fix behind ``total_value``), serialising the flat
        compat fields fired several ItemSupplier queries PER item, so a page of 6
        cost far more supplier queries than a page of 2. Now a single prefetch
        serves the whole page: the ItemSupplier query count is constant — and
        equal to one — regardless of page size.
        """
        for i in range(2):
            InventoryItemFactory(image=None, name=f"supbudget-a-{i}")
        # Warm one-time caches (content types, etc.) so the counts are stable.
        _supplier_query_count(api_client, _list_url())
        sup_2 = _supplier_query_count(api_client, _list_url())

        for i in range(4):  # grow the page from 2 to 6 items
            InventoryItemFactory(image=None, name=f"supbudget-b-{i}")
        sup_6 = _supplier_query_count(api_client, _list_url())

        # The heart of the regression: the supplier-table query count does NOT
        # grow with the number of rows — an N+1 would make sup_6 > sup_2 ...
        assert sup_2 == sup_6, (sup_2, sup_6)
        # ... and it is a single prefetch query, not a per-row handful.
        assert sup_6 == 1, sup_6


def _table_query_count(client, url, table):
    """Number of captured queries whose SQL reads ``table``.

    An N+1 grows this with the row count; a prefetch/annotate keeps it flat.
    Table names are the Django defaults (``<app>_<model>``).
    """
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(url)
        assert response.status_code == status.HTTP_200_OK
    return sum(1 for q in ctx.captured_queries if table in q["sql"])


def _make_item_with_reorders(name):
    """An item carrying reorder requests in the states the default-list
    accessors read — one PENDING (active set) and one ORDERED (the separate
    ``-ordered_at`` set) — so both order-preserving prefetches and all four
    accessors (has_pending_reorder / reorder_status / active_reorder_request /
    expected_delivery_date) have rows to resolve.
    """
    item = InventoryItemFactory(image=None, name=name)
    ReorderRequest.objects.create(item=item, quantity=1, status=ReorderRequest.Status.PENDING)
    ReorderRequest.objects.create(
        item=item,
        quantity=1,
        status=ReorderRequest.Status.ORDERED,
        ordered_at=timezone.now(),
    )
    return item


class TestDefaultListReorderQueryBudget:
    """PRIMARY regression guard for the default-list reorder_requests N+1 (issue #890).

    ``InventoryItemSerializer`` renders four fields that each fired a per-row
    ``reorder_requests`` query — ``has_pending_reorder``, ``reorder_status``,
    ``active_reorder_request`` and ``expected_delivery_date`` — while the list
    queryset prefetched only suppliers. Two order-preserving
    ``Prefetch(to_attr=...)`` (``-requested_at`` for the active set,
    ``-ordered_at`` for the ordered set — the ordering trap) now serve all four
    from cache, so the ReorderRequest-table query count is a constant two,
    independent of page size.
    """

    _TABLE = "reorder_queue_reorderrequest"

    def test_reorder_fields_do_not_add_a_query_per_row(self, api_client):
        for i in range(2):
            _make_item_with_reorders(f"reorderbudget-a-{i}")
        # Warm one-time caches (content types, etc.) so the counts are stable.
        _table_query_count(api_client, _list_url(), self._TABLE)
        reorder_2 = _table_query_count(api_client, _list_url(), self._TABLE)

        for i in range(4):  # grow the page from 2 to 6 items
            _make_item_with_reorders(f"reorderbudget-b-{i}")
        reorder_6 = _table_query_count(api_client, _list_url(), self._TABLE)

        # The heart of the regression: the ReorderRequest-table query count does
        # NOT grow with the number of rows. Before the fix each row fired
        # several of these queries (has_pending_reorder + expected_delivery_date
        # + active_reorder_request, unconditionally), so reorder_6 > reorder_2.
        assert reorder_2 == reorder_6, (reorder_2, reorder_6)
        # ... and it is exactly the two order-preserving Prefetches.
        assert reorder_6 == 2, reorder_6


class TestDefaultListReorderValueParity:
    """Byte-identical guard: the prefetched default-list values equal the live
    (fallback) accessor values, including the ``-ordered_at`` ORDERING TRAP.

    Builds an item whose most-recent reorder request by ``requested_at`` is a
    DIFFERENT row than the most-recent ORDERED request by ``ordered_at``. A
    naive single ``-requested_at`` prefetch shared by
    ``get_expected_delivery_date`` would then read the wrong ``ordered_at``; the
    two separate ``Prefetch(to_attr=...)`` keep every rendered value identical
    to the per-instance live query.
    """

    def test_list_values_match_live_query_including_ordering_trap(self, api_client):
        now = timezone.now()
        item = InventoryItemFactory(image=None, current_stock=0, minimum_stock=5)
        # A lead time on the primary supplier so expected_delivery_date resolves.
        item.item_suppliers.update(average_lead_time=7)

        newest_ordered = ReorderRequest.objects.create(
            item=item, quantity=1, status=ReorderRequest.Status.ORDERED
        )
        latest_requested = ReorderRequest.objects.create(
            item=item, quantity=1, status=ReorderRequest.Status.ORDERED
        )
        # requested_at is auto_now_add; .update() bypasses it to set explicit
        # times. The two ORDERED rows are ranked in REVERSE by the two orders:
        #   -requested_at head -> latest_requested ; -ordered_at head -> newest_ordered
        ReorderRequest.objects.filter(pk=newest_ordered.pk).update(
            requested_at=now - timedelta(days=10), ordered_at=now - timedelta(days=1)
        )
        ReorderRequest.objects.filter(pk=latest_requested.pk).update(
            requested_at=now - timedelta(days=2), ordered_at=now - timedelta(days=8)
        )

        row = _row_for(_results(api_client.get(_list_url())), item)
        fresh = type(item).objects.get(pk=item.pk)  # no prefetch -> live path

        # Prefetch path == live path (byte-identical) ...
        assert fresh.has_pending_reorder() is True
        assert row["has_pending_reorder"] is True
        assert fresh.reorder_status == "ordered"
        assert row["reorder_status"] == "ordered"
        assert row["active_reorder_request"]["id"] == fresh.get_active_reorder_request().id
        # ... active request is the latest by -requested_at ...
        assert fresh.get_active_reorder_request().id == latest_requested.id
        # ... and expected delivery derives from the latest by -ordered_at (the
        # trap): newest_ordered.ordered_at.date() + 7-day lead time.
        expected = fresh.get_expected_delivery_date()
        assert expected == (now - timedelta(days=1)).date() + timedelta(days=7)
        assert row["expected_delivery_date"] == expected.isoformat()


class TestPendingRequestStillLightsBadge:
    """BLAST-RADIUS GUARD for op-tm70: a PENDING request still shows on the item.

    op-tm70 narrowed *purchasing* to approved requests only — the PO-candidate
    pad, its per-line flags, the cart links and the send-a-PO sweep (see
    ``reorder_queue/tests/test_approval_gate.py``). The inventory accessors are
    the other half of that split and must NOT move: they answer "has anyone
    asked for this?", which is exactly what a member wants to see the moment
    they scan a shelf label — before any approver has looked at it. If this
    test goes red because someone narrowed ``get_active_reorder_request`` /
    ``has_pending_reorder`` to approved-only, that is the bug, not this test:
    the member would file a request and see no sign of it anywhere.
    """

    def test_pending_request_shows_on_the_list_row_and_the_model(self, api_client):
        # Low stock so ``reorder_status`` reaches its request branch at all —
        # it must read "pending" (somebody already asked), not "needs_order".
        item = InventoryItemFactory(image=None, current_stock=0, minimum_stock=5)
        pending = ReorderRequest.objects.create(
            item=item, quantity=3, status=ReorderRequest.Status.PENDING
        )

        row = _row_for(_results(api_client.get(_list_url())), item)
        fresh = type(item).objects.get(pk=item.pk)  # no prefetch -> live path

        # Prefetched list row ...
        assert row["has_pending_reorder"] is True
        assert row["reorder_status"] == "pending"
        assert row["active_reorder_request"]["id"] == pending.id
        # ... and the live accessors agree.
        assert fresh.has_pending_reorder() is True
        assert fresh.get_active_reorder_request().id == pending.id

    def test_purchasing_ignores_the_very_same_pending_request(self, api_client):
        """The two halves of the split, asserted side by side on one row."""
        from reorder_queue import services

        item = InventoryItemFactory(image=None)
        ReorderRequest.objects.create(item=item, quantity=3, status=ReorderRequest.Status.PENDING)
        fresh = type(item).objects.get(pk=item.pk)

        assert fresh.get_active_reorder_request() is not None  # badge: yes
        assert services.get_approved_reorder_request(fresh) is None  # purchasing: no


class TestDashboardOverviewSupplierQueryBudget:
    """Regression guard for the dashboard overview total_value N+1 (issue #890).

    ``get_inventory_summary`` sums ``total_value`` over every active item;
    ``total_value`` -> ``lowest_unit_cost`` reads ``item_suppliers.all()``, so
    without a prefetch the ItemSupplier table was read once per active item. The
    sum now iterates ``prefetch_related("item_suppliers")``, so that table is
    read exactly once regardless of how many active items exist.

    MUST use a LOGGED-IN client. The valuation is withheld from an anonymous
    caller and is no longer computed for one, so an anonymous request reads the
    ItemSupplier table zero times — this guard would pass while covering
    nothing, which is the failure mode it exists to catch.
    """

    _TABLE = "inventory_itemsupplier"

    def test_total_value_sum_does_not_add_a_query_per_item(self, authenticated_client):
        client, _user = authenticated_client
        url = reverse("inventory_summary")
        for i in range(2):
            InventoryItemFactory(image=None, name=f"dashbudget-a-{i}")
        _table_query_count(client, url, self._TABLE)  # warm caches
        sup_2 = _table_query_count(client, url, self._TABLE)

        for i in range(4):  # grow from 2 to 6 active items
            InventoryItemFactory(image=None, name=f"dashbudget-b-{i}")
        sup_6 = _table_query_count(client, url, self._TABLE)

        assert sup_2 == sup_6, (sup_2, sup_6)
        assert sup_6 == 1, sup_6  # the single supplier prefetch for the value sum

    def test_an_anonymous_request_does_not_read_the_supplier_table_at_all(self, api_client):
        """The other half of the gate: no valuation means no supplier walk.

        Pins the reason the guard above had to move to a logged-in client, so a
        future reader cannot quietly move it back.
        """
        url = reverse("inventory_summary")
        for i in range(3):
            InventoryItemFactory(image=None, name=f"dashbudget-anon-{i}")

        assert _table_query_count(api_client, url, self._TABLE) == 0


class TestFixtureListRefillQueryBudget:
    """Regression guard for the fixture-list pending_requests_count N+1 (issue #890).

    ``FixtureSerializer.pending_requests_count`` filtered ``refill_requests`` to
    PENDING and counted them per row. A ``Prefetch(to_attr=...)`` pre-filtered
    to PENDING now serves the count via ``len()``, so the FixtureRefillRequest
    table is read once regardless of page size.
    """

    _TABLE = "inventory_fixturerefillrequest"

    def _make_fixture_with_pending(self):
        fixture = FixtureFactory()
        # Status defaults to PENDING.
        FixtureRefillRequest.objects.create(fixture=fixture)
        return fixture

    def test_pending_count_does_not_add_a_query_per_row(self, api_client):
        url = reverse("fixture-list")
        for _ in range(2):
            self._make_fixture_with_pending()
        _table_query_count(api_client, url, self._TABLE)  # warm caches
        refill_2 = _table_query_count(api_client, url, self._TABLE)

        for _ in range(4):  # grow the page from 2 to 6 fixtures
            self._make_fixture_with_pending()
        refill_6 = _table_query_count(api_client, url, self._TABLE)

        assert refill_2 == refill_6, (refill_2, refill_6)
        assert refill_6 == 1, refill_6  # the single PENDING prefetch


class TestDonationListQueryBudget:
    """Regression guards for the donations list N+1s (issue #890)."""

    def test_donation_item_list_dispositions_stay_flat(self, authenticated_client):
        """``DonationItemSerializer.remaining_quantity`` sums ``dispositions.all()``
        per row; the viewset now prefetches ``dispositions`` so the disposition
        table is read once regardless of row count."""
        client, _user = authenticated_client
        url = reverse("donation-item-list")
        donation = DonationFactory()
        for _ in range(2):
            DispositionFactory(donation_item=DonationItemFactory(donation=donation))
        _table_query_count(client, url, "donations_disposition")  # warm caches
        disp_2 = _table_query_count(client, url, "donations_disposition")

        for _ in range(4):  # grow the list from 2 to 6 items
            DispositionFactory(donation_item=DonationItemFactory(donation=donation))
        disp_6 = _table_query_count(client, url, "donations_disposition")

        assert disp_2 == disp_6, (disp_2, disp_6)
        assert disp_6 == 1, disp_6  # the single dispositions prefetch

    def test_donation_list_items_stay_flat(self, authenticated_client):
        """Lock-in: the Donation list serves ``total_items`` (items.count()) and
        ``total_quantity`` (items.all()) from the list ``items`` prefetch. The
        #890 refactor split get_queryset into list/detail branches — the list
        branch must keep prefetching items, so the DonationItem-table read stays
        flat as donations grow."""
        client, _user = authenticated_client
        url = reverse("donation-list")
        for _ in range(2):
            DonationItemFactory(donation=DonationFactory())
        _table_query_count(client, url, "donations_donationitem")  # warm caches
        items_2 = _table_query_count(client, url, "donations_donationitem")

        for _ in range(4):  # grow the list from 2 to 6 donations
            DonationItemFactory(donation=DonationFactory())
        items_6 = _table_query_count(client, url, "donations_donationitem")

        assert items_2 == items_6, (items_2, items_6)
        assert items_6 == 1, items_6  # the single items prefetch
