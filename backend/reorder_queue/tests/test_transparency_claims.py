"""The transparency feed may not state as fact what its data does not support.

DERIVED FROM ONE QUESTION — "where does this page state something as fact that
the data does not support?" — rather than from the one place it was noticed. The
supplier attribution is where it was reported; the live re-quote, the budget
verdict computed from it, and a recorded zero published as "unknown" are the
same defect wearing different keys.

``ReorderRequest`` HAS NO SUPPLIER RELATIONSHIP AND NO RECORDED ESTIMATE. Its
fields are in ``reorder_queue/models.py``: an ``item`` FK, a quantity, the
timeline, ``actual_cost``, and the paperwork strings. Everything vendor-shaped
this feed used to publish per order was resolved from
``order.item`` — through ``InventoryItem.supplier`` and
``ReorderRequest.estimated_cost``, both of which run
``inventory.services.supplier_selection`` AT REQUEST TIME. So the value the page
printed was never the order's; it was the item's, as of the moment the page was
loaded, wearing the order's name.
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

import pytest

from inventory.models import ItemSupplier
from inventory.tests.factories import InventoryItemFactory, SupplierFactory
from reorder_queue.models import PurchaseOrder, ReorderRequest
from reorder_queue.tests.factories import ReorderRequestFactory
from reorder_queue.views import ORDER_VENDOR_KEYS

TRANSPARENCY = "analytics-transparency"

User = get_user_model()


def _placed_order(**kwargs) -> ReorderRequest:
    """A received order carrying the financial data the feed selects on."""
    defaults = {
        "status": ReorderRequest.Status.RECEIVED,
        "ordered_at": timezone.now(),
        "actual_cost": Decimal("150.25"),
        "order_number": "ORD-CLAIMS-1",
        "invoice_number": "INV-CLAIMS-1",
        "quantity": 10,
    }
    defaults.update(kwargs)
    return ReorderRequestFactory(**defaults)


def _make_primary_after_the_fact(order: ReorderRequest, name: str, unit_cost: str):
    """Give the ORDER'S ITEM a new flagged-primary supplier, created just now.

    Nothing about ``order`` is touched: this is a link between the item and a
    vendor that did not exist when the order was placed, received and paid for.
    """
    supplier = SupplierFactory(name=name)
    ItemSupplier.objects.create(
        item=order.item,
        supplier=supplier,
        supplier_sku="AFTER-THE-ORDER",
        unit_cost=Decimal(unit_cost),
        is_primary=True,
        is_active=True,
    )
    ItemSupplier.objects.filter(item=order.item).exclude(supplier=supplier).update(is_primary=False)
    return supplier


@pytest.mark.django_db
class TestNoFabricatedOrderFacts:
    def test_no_order_row_reports_a_supplier_of_its_own(self, authenticated_client):
        """``supplier_name`` on an order row named a vendor the order never had.

        Watched failing: on base both rows carried ``supplier_name``, and its
        value FOLLOWED a supplier link created after the order was received —
        so the page named, as this order's supplier, a vendor it could not have
        bought from.
        """
        client, _user = authenticated_client
        order = _placed_order()
        later = _make_primary_after_the_fact(order, "Vendor-That-Did-Not-Exist-Yet", "1.00")

        payload = client.get(reverse(TRANSPARENCY)).data
        row = payload["orders"][0]
        ledger_row = payload["ledger"][0]

        assert "supplier_name" not in row
        assert "supplier_name" not in ledger_row
        # And the substituted value is not merely renamed onto the order: the
        # vendor invented after the fact must not be attributed to it anywhere
        # a reader would take for the order's own.
        assert later.name not in [value for value in row.values() if isinstance(value, str)]
        assert later.name not in [value for value in ledger_row.values() if isinstance(value, str)]

    def test_the_item_supplier_is_published_as_the_items_and_says_so(self, authenticated_client):
        """What replaces it is true: the ITEM's current supplier derivation.

        The key is item-scoped and carries the whole
        ``SupplierChoiceSerializer`` object — winner, alternatives and the
        reason — rather than a bare name, so a reader is told which question
        was answered.
        """
        client, _user = authenticated_client
        order = _placed_order()
        later = _make_primary_after_the_fact(order, "Todays-Supplier", "1.00")

        payload = client.get(reverse(TRANSPARENCY)).data
        choice = payload["orders"][0]["item_supplier_choice"]

        assert choice["supplier_name"] == later.name
        # The alternatives are the rest of the item's field, so "one supplier"
        # is never implied where there are two.
        assert len(choice["alternatives"]) == 1
        assert payload["ledger"][0]["item_supplier_choice"]["supplier_name"] == later.name

    def test_no_budget_verdict_is_published_for_an_order_with_no_budget(self, authenticated_client):
        """``cost_variance`` rendered an over/under-budget verdict off a re-quote.

        Watched failing: on base the SAME untouched order reported
        ``cost_variance`` of one sign before a supplier link was edited and the
        other sign after — "under budget" became "over budget" with nothing
        about the order having changed. ``ReorderRequest`` records no estimate,
        so there is no budget for a variance to be measured against.
        """
        client, _user = authenticated_client
        order = _placed_order(actual_cost=Decimal("100.00"))
        before = client.get(reverse(TRANSPARENCY)).data["orders"][0]

        _make_primary_after_the_fact(order, "Cheap-Vendor", "1.00")
        after = client.get(reverse(TRANSPARENCY)).data["orders"][0]

        assert "cost_variance" not in before
        assert "cost_variance" not in after
        # ``estimated_cost`` is gone under that name too: it was the same
        # re-quote, and a key sitting beside ``actual_cost`` invites the reader
        # to do the subtraction the server no longer does.
        assert "estimated_cost" not in before
        assert "estimated_cost" not in after

    def test_the_re_quote_is_published_under_a_name_that_says_whose_it_is(
        self, authenticated_client
    ):
        """The figure is kept, honestly named — and it MOVES, which is the point.

        ``item_estimated_cost_today`` changes when the item's supplier links
        change, while every value the order actually owns stays put. That is
        exactly why it cannot be called the order's estimate.
        """
        client, _user = authenticated_client
        order = _placed_order(actual_cost=Decimal("100.00"), quantity=4)

        before = client.get(reverse(TRANSPARENCY)).data["orders"][0]
        _make_primary_after_the_fact(order, "Two-Dollar-Vendor", "2.00")
        after = client.get(reverse(TRANSPARENCY)).data["orders"][0]

        assert after["item_estimated_cost_today"] == 8.0  # 4 units at 2.00, today
        assert before["item_estimated_cost_today"] != after["item_estimated_cost_today"]
        # The order's own facts did not move with it.
        assert before["actual_cost"] == after["actual_cost"] == 100.0
        assert before["order_number"] == after["order_number"]

    def test_a_recorded_zero_cost_is_published_as_zero_not_as_unknown(self, authenticated_client):
        """``null`` means "no figure recorded" in this payload — 0.00 is a figure.

        Watched failing: a donated order with ``actual_cost`` of ``0.00`` was
        published as ``actual_cost: null`` and ``cost_per_unit: null``, which
        says we do not know what it cost. We do.
        """
        client, _user = authenticated_client
        _placed_order(actual_cost=Decimal("0.00"), quantity=4, order_number="ORD-DONATED")

        payload = client.get(reverse(TRANSPARENCY)).data

        assert payload["orders"][0]["actual_cost"] == 0.0
        assert payload["orders"][0]["cost_per_unit"] == 0.0
        assert payload["ledger"][0]["actual_cost"] == 0.0


@pytest.mark.django_db
class TestTheAggregatesAreTotals:
    """The summary says "total". It has to be one.

    The arrays are deliberately capped — a hundred rows is a page, not a
    ledger — but the COUNTS and the MONEY are the accountability this page
    exists to provide, and they were computed by walking the capped slice. On a
    space with more than a hundred qualifying orders the page published less
    than the space had spent.

    Firstmate ruled on this (op-transparency-substituted-supplier): the figure
    an anonymous reader sees goes UP because it becomes correct. Aggregates were
    already public; only their arithmetic changed.
    """

    def test_the_totals_cover_every_qualifying_order_not_just_the_page(self, api_client):
        """Watched failing: 105 orders worth $1050.00 published as 100 / $1000.00."""
        item = InventoryItemFactory()
        for index in range(105):
            _placed_order(item=item, actual_cost=Decimal("10.00"), order_number=f"ORD-{index}")

        payload = api_client.get(reverse(TRANSPARENCY)).data

        assert payload["summary"]["total_orders_with_financial_data"] == 105
        assert payload["summary"]["total_amount_spent"] == 1050.00
        # The PAGE is still a page. If this ever equals 105 the assertions above
        # stopped testing anything: they would agree with a walk of the slice.
        assert len(payload["orders"]) == 100
        assert len(payload["ledger"]) == 100

    def test_the_totals_count_the_same_orders_the_feed_lists(self, api_client):
        """One definition of "qualifying", not two.

        The aggregate and the rows come off the same queryset. An order with no
        financial data is on neither, so a second copy of that filter cannot
        drift into counting rows the page does not show.
        """
        item = InventoryItemFactory()
        _placed_order(item=item, actual_cost=Decimal("10.00"), order_number="ORD-REAL")
        # Nothing to publish: no cost, no paperwork, no order number.
        ReorderRequestFactory(
            item=item,
            status=ReorderRequest.Status.PENDING,
            actual_cost=None,
            order_number="",
            invoice_number="",
        )

        payload = api_client.get(reverse(TRANSPARENCY)).data

        assert payload["summary"]["total_orders_with_financial_data"] == 1
        assert len(payload["orders"]) == 1
        assert payload["summary"]["total_amount_spent"] == 10.00

    def test_the_purchase_order_totals_cover_every_order_too(self, api_client):
        """The same cap, on the same summary, over a different model."""
        from inventory.tests.factories import SupplierFactory

        user = User.objects.create_user(username="po-totals", password="pw")
        supplier = SupplierFactory(name="Bulk Vendor")
        for index in range(52):
            PurchaseOrder.objects.create(
                po_number=f"PO-BULK-{index}",
                supplier=supplier,
                status=PurchaseOrder.Status.RECEIVED,
                created_by=user,
                actual_total=Decimal("5.00"),
            )

        payload = api_client.get(reverse(TRANSPARENCY)).data

        assert payload["summary"]["total_purchase_orders"] == 52
        assert payload["summary"]["total_po_amount_spent"] == 260.00
        assert len(payload["purchase_orders"]) == 50

    def test_an_empty_feed_reports_zero_spend_not_null(self, api_client):
        """CONTROL. ``SUM`` over no rows is SQL ``NULL``; the page must say 0.00.

        The walk this replaced started at ``Decimal("0.00")`` and so could not
        publish ``null`` here. An aggregate can.
        """
        payload = api_client.get(reverse(TRANSPARENCY)).data

        assert payload["summary"]["total_amount_spent"] == 0.0
        assert payload["summary"]["total_po_amount_spent"] == 0.0
        assert payload["summary"]["total_orders_with_financial_data"] == 0
        assert payload["summary"]["total_purchase_orders"] == 0


@pytest.mark.django_db
class TestTheGateItself:
    def test_an_unauthenticated_request_gets_aggregates_and_no_vendor_anything(
        self, api_client, authenticated_client
    ):
        """PROVEN BY REQUEST, not by reading the key list.

        The assertions are on the RAW BYTES, so a vendor fact that reached the
        wire under some key nobody thought to check still fails here.
        """
        client, _user = authenticated_client
        order = _placed_order(order_number="ORD-SECRET-1", invoice_number="INV-SECRET-1")
        vendor = order.item.supplier
        # A SECOND order, so the per-order price and the public aggregate are
        # different numbers. Without it the sentinel below is also the value of
        # ``total_amount_spent``, which is deliberately public, and the check
        # would be asserting the opposite of what it says.
        _placed_order(actual_cost=Decimal("49.75"), order_number="ORD-SECRET-2")

        response = api_client.get(reverse(TRANSPARENCY))
        body = response.content

        assert response.status_code == 200
        assert vendor.name.encode() not in body
        assert b"ORD-SECRET-1" not in body
        assert b"INV-SECRET-1" not in body
        assert b"150.25" not in body  # the per-order price we paid a vendor
        assert b"item_supplier_choice" not in body
        assert b"item_estimated_cost_today" not in body

        # The accountability the page exists for is still there.
        summary = response.data["summary"]
        assert summary["total_amount_spent"] == 200.00
        assert summary["vendor_data_withheld"] is True
        assert order.item.name in [row["item_name"] for row in response.data["orders"]]

        # CONTROL: every one of those sentinels IS on the wire for a signed-in
        # reader, so the assertions above are testing the gate and not a
        # feed that simply never carries them.
        signed_in = client.get(reverse(TRANSPARENCY))
        assert vendor.name.encode() in signed_in.content
        assert b"ORD-SECRET-1" in signed_in.content
        assert b"INV-SECRET-1" in signed_in.content
        assert b"item_supplier_choice" in signed_in.content

    def test_orders_and_ledger_are_gated_by_the_same_list(self, api_client, authenticated_client):
        """The trap the constant exists for: a key dropped from one array only.

        Both arrays render the SAME order. This walks every vendor key a
        signed-in reader gets on each array and requires it gone from the
        anonymous render of that same array — so a key withheld from ``orders``
        and left on ``ledger`` fails here.
        """
        client, _user = authenticated_client
        _placed_order()

        signed_in = client.get(reverse(TRANSPARENCY)).data
        anonymous = api_client.get(reverse(TRANSPARENCY)).data

        checked = 0
        for array in ("orders", "ledger"):
            open_row = signed_in[array][0]
            gated_row = anonymous[array][0]
            for key in ORDER_VENDOR_KEYS:
                if key not in open_row:
                    continue
                checked += 1
                assert key not in gated_row, f"{key} survived on {array}"
            # Nothing on the anonymous row may be a vendor key by any route.
            assert not set(gated_row) & set(ORDER_VENDOR_KEYS)
        # The loop above is worthless if it walked nothing: both arrays must
        # actually have carried vendor keys to the signed-in reader.
        assert checked >= 2 * 3

    def test_the_signed_in_note_does_not_claim_everything_is_public(
        self, api_client, authenticated_client
    ):
        """A page about accountability may not carry a claim its payload denies.

        Watched failing: the note served to a signed-in reader said "All
        purchase information is publicly available" while the anonymous
        response asserted in the same test proves it is not.
        """
        client, _user = authenticated_client
        order = _placed_order()

        note = client.get(reverse(TRANSPARENCY)).data["summary"]["transparency_note"]
        assert "publicly available" not in note

        # The proof that the old sentence was false, taken here rather than
        # assumed: the same feed withholds this vendor from a caller with no
        # session.
        anonymous = api_client.get(reverse(TRANSPARENCY))
        assert order.item.supplier.name.encode() not in anonymous.content

    def test_publishing_the_whole_choice_costs_no_extra_queries(self, authenticated_client):
        """The item's derivation rides the prefetch the queryset already sets up.

        Naming the winner used to be one ``order.item.supplier`` read per row;
        naming the whole choice must not become one QUERY per row. Measured by
        running the feed over one order and then over ten DISTINCT items: the
        count is identical, which a per-row query could not be.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        client, _user = authenticated_client
        _placed_order()

        with CaptureQueriesContext(connection) as one_row:
            client.get(reverse(TRANSPARENCY))

        # Distinct ITEMS, not just distinct orders: a per-row supplier lookup
        # is invisible when ten orders share one item's prefetched links.
        for index in range(9):
            _placed_order(order_number=f"ORD-BULK-{index}")

        with CaptureQueriesContext(connection) as ten_rows:
            response = client.get(reverse(TRANSPARENCY))

        assert len(response.data["orders"]) == 10
        assert len(ten_rows.captured_queries) == len(one_row.captured_queries)

    def test_the_anonymous_note_is_unchanged(self, api_client):
        """The line the captain drew twice is not this change's to move."""
        _placed_order()

        note = api_client.get(reverse(TRANSPARENCY)).data["summary"]["transparency_note"]

        assert note == (
            "Dallas Makerspace publishes what it spends. Totals, items, "
            "quantities and dates are public; supplier names and "
            "per-order costs are shown to signed-in members."
        )
