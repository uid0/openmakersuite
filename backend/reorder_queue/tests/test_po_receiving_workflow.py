"""The purchase-order receiving workflow (oms-po-receiving).

The flow an operator actually walks: pick the order, pick or scan the line,
scan the tracking barcode, say how much arrived, capture serials with their
optional lot and expiry, and close the order out when there is nothing left to
wait for.

Four things here are worth stating plainly, because each replaces a previous
behaviour rather than adding to it:

* **A mismatch is recorded, never rounded.** More than was ordered is accepted
  and flagged ``over_received``; less is accepted, and stays *outstanding*
  until somebody says the rest is not coming.
* **A short line ends by being closed short**, which settles it without ever
  claiming the missing units arrived. The shortfall stays readable afterwards.
* **Serials belong to the item that goes on the shelf.** On a kit line that is
  the COMPONENTS, and naming the kit is refused rather than quietly redirected.
* **Nothing the operator typed is dropped silently.** Every rejection here
  names what it refused and why.

Everything drives the real HTTP endpoints, so what is proven is the contract
ScanTTY and the web share, not a service call the clients do not make.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from inventory.models import ItemSupplier, KitComponent, SerializedComponent
from inventory.services.kits import build_kit_snapshot
from inventory.tests.factories import InventoryItemFactory, SupplierFactory
from reorder_queue.models import PurchaseOrder, PurchaseOrderItem


@pytest.fixture
def operator(django_user_model):
    return django_user_model.objects.create_user(
        username="receiving-clerk", password="pw", is_staff=True, is_superuser=True
    )


@pytest.fixture
def client(operator):
    api = APIClient()
    api.force_authenticate(user=operator)
    return api


@pytest.fixture
def supplier(db):
    return SupplierFactory(name="Grainger")


def make_item(name, *, serialized=False, stock=0, sku_barcodes=None, supplier=None):
    """An inventory item, optionally serialized, optionally with a supplier row."""
    item = InventoryItemFactory(
        name=name,
        current_stock=stock,
        minimum_stock=0,
        image=None,
        is_serialized=serialized,
    )
    if supplier is not None:
        codes = sku_barcodes or {}
        ItemSupplier.objects.create(
            item=item,
            supplier=supplier,
            supplier_sku=codes.get("supplier_sku", f"SUP-{name[:6]}"),
            package_upc=codes.get("package_upc", ""),
            unit_upc=codes.get("unit_upc", ""),
            unit_cost=Decimal("10.00"),
            quantity_per_package=1,
            is_primary=True,
        )
    return item


def make_po(supplier, operator, status_value=PurchaseOrder.Status.SENT):
    return PurchaseOrder.objects.create(
        supplier=supplier,
        status=status_value,
        order_date=timezone.now(),
        created_by=operator,
    )


def add_line(purchase_order, item, quantity):
    return PurchaseOrderItem.objects.create(
        purchase_order=purchase_order,
        item_supplier=item.item_suppliers.first(),
        quantity_ordered=quantity,
        unit_cost_ordered=Decimal("10.00"),
        kit_snapshot=build_kit_snapshot(item),
    )


def receive(client, purchase_order, lines, **extra):
    body = {"items": lines}
    body.update(extra)
    return client.post(
        reverse("purchaseorder-receive", args=[purchase_order.pk]), body, format="json"
    )


def worksheet(client, purchase_order):
    return client.get(reverse("purchaseorder-receiving", args=[purchase_order.pk]))


def line_of(payload, po_item):
    """The worksheet row for one line."""
    return next(row for row in payload["lines"] if row["purchase_order_item"] == po_item.pk)


@pytest.mark.django_db
class TestReceivingWorksheet:
    """What a client reads before it can show a receive screen."""

    def test_worksheet_reports_each_line_and_what_is_outstanding(self, client, supplier, operator):
        purchase_order = make_po(supplier, operator)
        widget = make_item("Widget", stock=0, supplier=supplier)
        gasket = make_item("Gasket", stock=0, supplier=supplier)
        widget_line = add_line(purchase_order, widget, 10)
        gasket_line = add_line(purchase_order, gasket, 4)

        receive(
            client,
            purchase_order,
            [{"purchase_order_item": gasket_line.pk, "quantity_received": 4}],
        )

        payload = worksheet(client, purchase_order).data

        assert payload["can_receive"] is True
        assert payload["unavailable_reason"] is None
        assert payload["outstanding_line_count"] == 1

        widget_row = line_of(payload, widget_line)
        assert widget_row["receipt_state"] == PurchaseOrderItem.ReceiptState.NOT_RECEIVED
        assert widget_row["is_settled"] is False
        assert widget_row["quantity_pending"] == 10

        gasket_row = line_of(payload, gasket_line)
        assert gasket_row["receipt_state"] == PurchaseOrderItem.ReceiptState.RECEIVED
        assert gasket_row["is_settled"] is True

    def test_worksheet_distinguishes_unreceivable_from_nothing_outstanding(
        self, client, supplier, operator
    ):
        """ "You may not receive this" and "there is nothing left" are different facts.

        An operator holding a box acts differently on each: one means go and
        send the order, the other means the box is a surprise. Collapsing them
        into a missing button is how the receive affordance used to read as
        broken.
        """
        draft = make_po(supplier, operator, PurchaseOrder.Status.DRAFT)
        add_line(draft, make_item("Bolt", supplier=supplier), 3)

        payload = worksheet(client, draft).data

        assert payload["can_receive"] is False
        assert "still a draft" in payload["unavailable_reason"]
        # ...and it still says what is outstanding, which is not zero.
        assert payload["outstanding_line_count"] == 1

    def test_worksheet_lists_the_codes_a_scanner_will_read(self, client, supplier, operator):
        purchase_order = make_po(supplier, operator)
        item = make_item(
            "Relay",
            supplier=supplier,
            sku_barcodes={
                "package_upc": "0123456789012",
                "unit_upc": "9876543210987",
                "supplier_sku": "GRA-RELAY-1",
            },
        )
        line = add_line(purchase_order, item, 2)

        row = line_of(worksheet(client, purchase_order).data, line)
        codes = {entry["code"]: entry["kind"] for entry in row["scan_codes"]}

        assert codes["0123456789012"] == "package_upc"
        assert codes["9876543210987"] == "unit_upc"
        assert codes["GRA-RELAY-1"] == "supplier_sku"
        assert item.sku in codes

    def test_an_unbarcoded_line_offers_no_empty_code_to_match_on(self, client, supplier, operator):
        """A line with no barcodes contributes nothing, not an empty string.

        An empty code in the list is worse than no code: a scanner emitting a
        stray "" would match every unbarcoded line on the order, and the
        operator would receive against whichever one sorted first.
        """
        purchase_order = make_po(supplier, operator)
        bare = make_item(
            "Bare",
            supplier=supplier,
            sku_barcodes={"package_upc": "", "unit_upc": "", "supplier_sku": ""},
        )
        line = add_line(purchase_order, bare, 1)

        row = line_of(worksheet(client, purchase_order).data, line)
        codes = [entry["code"] for entry in row["scan_codes"]]

        assert "" not in codes
        # The item's own SKU is auto-assigned and is a legitimate code; what
        # must not appear is a blank standing in for the three absent barcodes.
        assert codes == [bare.sku]


@pytest.mark.django_db
class TestQuantityMismatch:
    """Short and over receipts: recorded truthfully, flagged visibly."""

    def test_over_receipt_is_recorded_and_flagged_not_rounded_down(
        self, client, supplier, operator
    ):
        purchase_order = make_po(supplier, operator)
        item = make_item("Widget", stock=0, supplier=supplier)
        line = add_line(purchase_order, item, 10)

        response = receive(
            client,
            purchase_order,
            [{"purchase_order_item": line.pk, "quantity_received": 12}],
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        line.refresh_from_db()
        item.refresh_from_db()

        assert line.quantity_received == 12
        assert line.quantity_variance == 2
        assert line.receipt_state == PurchaseOrderItem.ReceiptState.OVER_RECEIVED
        # The stock that actually arrived.
        assert item.current_stock == 12
        # And the order carries the flag, which is what makes it chaseable.
        assert response.data["has_receipt_variance"] is True
        assert response.data["variance_line_count"] == 1

    def test_short_receipt_leaves_the_line_outstanding_not_short(self, client, supplier, operator):
        """8 of 10 is not yet "short" — the other 2 may still be coming.

        A line only becomes short when somebody says the rest is not arriving.
        Flagging it short on receipt would put a variance on the order for a
        backorder that turns up next week.
        """
        purchase_order = make_po(supplier, operator)
        item = make_item("Widget", stock=0, supplier=supplier)
        line = add_line(purchase_order, item, 10)

        response = receive(
            client, purchase_order, [{"purchase_order_item": line.pk, "quantity_received": 8}]
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        line.refresh_from_db()
        purchase_order.refresh_from_db()

        assert line.receipt_state == PurchaseOrderItem.ReceiptState.PARTIALLY_RECEIVED
        assert line.is_settled is False
        assert line.is_short_received is False
        assert line.quantity_variance == -2
        assert purchase_order.status == PurchaseOrder.Status.PARTIALLY_RECEIVED
        assert purchase_order.has_receipt_variance is False

    def test_closing_short_settles_the_line_and_keeps_the_shortfall(
        self, client, supplier, operator
    ):
        purchase_order = make_po(supplier, operator)
        item = make_item("Widget", stock=0, supplier=supplier)
        line = add_line(purchase_order, item, 10)
        receive(client, purchase_order, [{"purchase_order_item": line.pk, "quantity_received": 8}])

        response = client.post(
            reverse("purchaseorder-close-short", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": line.pk, "reason": "backorder cancelled"}]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        line.refresh_from_db()
        purchase_order.refresh_from_db()

        assert line.receipt_state == PurchaseOrderItem.ReceiptState.CLOSED_SHORT
        assert line.is_settled is True
        assert line.is_short_received is True
        # The record of the shortfall, still readable afterwards.
        assert line.quantity_received == 8
        assert line.quantity_variance == -2
        assert line.closed_short_reason == "backorder cancelled"
        assert line.closed_short_by == operator
        # Never rounded up to the ordered figure.
        assert line.quantity_received != line.quantity_ordered
        assert purchase_order.status == PurchaseOrder.Status.RECEIVED
        assert purchase_order.has_receipt_variance is True
        assert purchase_order.is_fully_received is False

    def test_receiving_and_closing_short_in_one_request(self, client, supplier, operator):
        """ "8 arrived and the other 2 are cancelled" is one operator action."""
        purchase_order = make_po(supplier, operator)
        item = make_item("Widget", stock=0, supplier=supplier)
        line = add_line(purchase_order, item, 10)

        response = receive(
            client,
            purchase_order,
            [
                {
                    "purchase_order_item": line.pk,
                    "quantity_received": 8,
                    "close_short": True,
                    "close_short_reason": "vendor discontinued",
                }
            ],
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        line.refresh_from_db()
        assert line.receipt_state == PurchaseOrderItem.ReceiptState.CLOSED_SHORT
        assert line.closed_short_reason == "vendor discontinued"
        assert response.data["status"] == PurchaseOrder.Status.RECEIVED

    def test_closing_short_twice_is_refused_rather_than_overwriting(
        self, client, supplier, operator
    ):
        """The first reason and actor are a record, not a draft.

        A second line is left outstanding on purpose so the order stays
        receivable: otherwise the order-level status gate answers first and
        this proves nothing about the line-level guard.
        """
        purchase_order = make_po(supplier, operator)
        line = add_line(purchase_order, make_item("Widget", supplier=supplier), 10)
        add_line(purchase_order, make_item("Gasket", supplier=supplier), 1)
        receive(client, purchase_order, [{"purchase_order_item": line.pk, "quantity_received": 8}])
        url = reverse("purchaseorder-close-short", args=[purchase_order.pk])
        client.post(
            url, {"items": [{"purchase_order_item": line.pk, "reason": "first"}]}, format="json"
        )

        response = client.post(
            url, {"items": [{"purchase_order_item": line.pk, "reason": "second"}]}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "already been closed short" in response.data["error"]
        line.refresh_from_db()
        assert line.closed_short_reason == "first"

    def test_closing_short_a_line_with_nothing_outstanding_is_refused(
        self, client, supplier, operator
    ):
        """A line that landed in full cannot be written off as short."""
        purchase_order = make_po(supplier, operator)
        line = add_line(purchase_order, make_item("Widget", supplier=supplier), 5)
        add_line(purchase_order, make_item("Gasket", supplier=supplier), 1)
        receive(client, purchase_order, [{"purchase_order_item": line.pk, "quantity_received": 5}])

        response = client.post(
            reverse("purchaseorder-close-short", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": line.pk, "reason": "nope"}]},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "nothing outstanding" in response.data["error"]
        line.refresh_from_db()
        assert line.is_closed_short is False

    def test_receiving_against_a_closed_short_line_is_refused(self, client, supplier, operator):
        """The line was declared finished; more stock arriving needs a human."""
        purchase_order = make_po(supplier, operator)
        item = make_item("Widget", stock=0, supplier=supplier)
        line = add_line(purchase_order, item, 10)
        add_line(purchase_order, make_item("Gasket", supplier=supplier), 1)
        receive(client, purchase_order, [{"purchase_order_item": line.pk, "quantity_received": 8}])
        client.post(
            reverse("purchaseorder-close-short", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": line.pk, "reason": "cancelled"}]},
            format="json",
        )

        response = receive(
            client, purchase_order, [{"purchase_order_item": line.pk, "quantity_received": 2}]
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "closed short" in response.data["error"]
        line.refresh_from_db()
        item.refresh_from_db()
        assert line.quantity_received == 8
        assert item.current_stock == 8


@pytest.mark.django_db
class TestPartialReceiptAcrossTime:
    """Line by line, over days — and unambiguous about what is still owed."""

    def test_lines_received_on_different_days_settle_independently(
        self, client, supplier, operator
    ):
        purchase_order = make_po(supplier, operator)
        first = add_line(purchase_order, make_item("Widget", supplier=supplier), 5)
        second = add_line(purchase_order, make_item("Gasket", supplier=supplier), 3)

        receive(
            client,
            purchase_order,
            [{"purchase_order_item": first.pk, "quantity_received": 5}],
            delivery_date=date(2026, 8, 1).isoformat(),
        )
        purchase_order.refresh_from_db()
        assert purchase_order.status == PurchaseOrder.Status.PARTIALLY_RECEIVED

        payload = worksheet(client, purchase_order).data
        assert payload["outstanding_line_count"] == 1
        assert line_of(payload, second)["quantity_pending"] == 3

        receive(
            client,
            purchase_order,
            [{"purchase_order_item": second.pk, "quantity_received": 3}],
            delivery_date=date(2026, 8, 8).isoformat(),
        )
        purchase_order.refresh_from_db()

        assert purchase_order.status == PurchaseOrder.Status.RECEIVED
        assert purchase_order.deliveries.count() == 2
        assert worksheet(client, purchase_order).data["outstanding_line_count"] == 0

    def test_mark_received_closes_every_outstanding_line_at_once(self, client, supplier, operator):
        purchase_order = make_po(supplier, operator)
        done = add_line(purchase_order, make_item("Widget", supplier=supplier), 5)
        partial = add_line(purchase_order, make_item("Gasket", supplier=supplier), 4)
        untouched = add_line(purchase_order, make_item("Bolt", supplier=supplier), 2)
        receive(
            client,
            purchase_order,
            [
                {"purchase_order_item": done.pk, "quantity_received": 5},
                {"purchase_order_item": partial.pk, "quantity_received": 1},
            ],
        )

        response = client.post(
            reverse("purchaseorder-mark-received", args=[purchase_order.pk]),
            {"reason": "order closed by vendor"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        purchase_order.refresh_from_db()
        assert purchase_order.status == PurchaseOrder.Status.RECEIVED

        done.refresh_from_db()
        partial.refresh_from_db()
        untouched.refresh_from_db()
        # The line that landed in full is untouched — no false shortfall.
        assert done.receipt_state == PurchaseOrderItem.ReceiptState.RECEIVED
        assert done.is_closed_short is False
        # The two that did not are written off, each carrying the reason.
        assert partial.receipt_state == PurchaseOrderItem.ReceiptState.CLOSED_SHORT
        assert partial.quantity_variance == -3
        assert untouched.receipt_state == PurchaseOrderItem.ReceiptState.CLOSED_SHORT
        assert untouched.quantity_variance == -2
        assert untouched.closed_short_reason == "order closed by vendor"
        assert purchase_order.variance_line_count == 2

    def test_mark_received_on_a_finished_order_is_refused_not_a_silent_no_op(
        self, client, supplier, operator
    ):
        """An order receiving is already done with says so, rather than nothing."""
        purchase_order = make_po(supplier, operator)
        line = add_line(purchase_order, make_item("Widget", supplier=supplier), 5)
        receive(client, purchase_order, [{"purchase_order_item": line.pk, "quantity_received": 5}])

        response = client.post(
            reverse("purchaseorder-mark-received", args=[purchase_order.pk]), {}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "must be sent, confirmed, or partially received" in response.data["error"]

    def test_mark_received_on_an_order_with_no_lines_is_refused(self, client, supplier, operator):
        """A sent order with nothing on it has nothing to close.

        The one way an order can sit in a receivable status with no
        outstanding lines, and the reason the emptiness is reported rather
        than silently advancing the order to ``received``.
        """
        purchase_order = make_po(supplier, operator)

        response = client.post(
            reverse("purchaseorder-mark-received", args=[purchase_order.pk]), {}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "already settled" in response.data["error"]
        purchase_order.refresh_from_db()
        assert purchase_order.status == PurchaseOrder.Status.SENT

    def test_a_voided_line_does_not_block_the_order_finishing(self, client, supplier, operator):
        """A struck-off line is settled: nothing is coming, and nothing should.

        It used to be counted as "not fully received", which left every order
        carrying a voided line stuck at ``partially_received`` for ever even
        after every real line had landed.
        """
        purchase_order = make_po(supplier, operator)
        live = add_line(purchase_order, make_item("Widget", supplier=supplier), 5)
        struck = add_line(purchase_order, make_item("Gasket", supplier=supplier), 3)
        struck.is_voided = True
        struck.save(update_fields=["is_voided"])

        receive(client, purchase_order, [{"purchase_order_item": live.pk, "quantity_received": 5}])
        purchase_order.refresh_from_db()

        assert purchase_order.status == PurchaseOrder.Status.RECEIVED
        assert purchase_order.outstanding_line_count == 0
        # A voided line is not a variance to chase a vendor about.
        assert purchase_order.has_receipt_variance is False


@pytest.mark.django_db
class TestTrackingBarcode:
    """The scanned parcel label, stored verbatim beside an accurate timestamp."""

    def test_tracking_barcode_is_recorded_exactly_as_scanned(self, client, supplier, operator):
        purchase_order = make_po(supplier, operator)
        line = add_line(purchase_order, make_item("Widget", supplier=supplier), 2)
        scanned = "1Z999AA10123456784"

        receive(
            client,
            purchase_order,
            [{"purchase_order_item": line.pk, "quantity_received": 2}],
            tracking_number=scanned,
            carrier="UPS",
        )

        delivery = purchase_order.deliveries.get()
        assert delivery.tracking_number == scanned
        assert delivery.carrier == "UPS"

    def test_the_receipt_keeps_an_accurate_wall_clock_timestamp(self, client, supplier, operator):
        """Transit duration is deferred, but the inputs for it are not.

        ``delivery_date`` is the date the OPERATOR states, and is coerced to
        midnight — the captain does not always record a delivery on the day it
        arrived, so it cannot be trusted as a clock. ``created_at`` is when the
        receipt was actually taken, to the second, and is what a later
        transit-duration calculation has to anchor on.
        """
        purchase_order = make_po(supplier, operator)
        line = add_line(purchase_order, make_item("Widget", supplier=supplier), 2)
        before = timezone.now()

        receive(
            client,
            purchase_order,
            [{"purchase_order_item": line.pk, "quantity_received": 2}],
            delivery_date=date(2026, 7, 1).isoformat(),
            tracking_number="1Z999AA10123456784",
        )

        delivery = purchase_order.deliveries.get()
        # The operator's stated date, exactly as given.
        assert delivery.delivery_date.date() == date(2026, 7, 1)
        # And the real one, which is not that date.
        assert delivery.created_at >= before
        assert delivery.created_at.date() != date(2026, 7, 1)


@pytest.mark.django_db
class TestSerialCapture:
    """Serials, lots and expiry — against the identity that goes on the shelf."""

    def test_serials_are_recorded_against_the_line_with_lot_and_expiry(
        self, client, supplier, operator
    ):
        purchase_order = make_po(supplier, operator)
        item = make_item("Meter", serialized=True, stock=0, supplier=supplier)
        line = add_line(purchase_order, item, 2)

        response = receive(
            client,
            purchase_order,
            [
                {
                    "purchase_order_item": line.pk,
                    "quantity_received": 2,
                    "serials": [
                        {
                            "serial_number": "SN-001",
                            "lot": "LOT-42",
                            "expiration_date": "2027-01-31",
                        },
                        {"serial_number": "SN-002"},
                    ],
                }
            ],
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        units = {u.serial_number: u for u in SerializedComponent.objects.filter(item=item)}
        assert set(units) == {"SN-001", "SN-002"}

        first = units["SN-001"]
        assert first.lot == "LOT-42"
        assert first.expiration_date == date(2027, 1, 31)
        # Optional really is optional.
        assert units["SN-002"].lot == ""
        assert units["SN-002"].expiration_date is None
        # Both ends of the provenance trail, so "where did this come from?"
        # is answerable from the unit itself.
        assert first.provenance_purchase_order_item == line
        assert first.provenance_delivery_item is not None
        assert first.provenance_delivery_item.delivery.purchase_order == purchase_order
        # A scanned serial is in stock, not merely on file.
        assert first.status == SerializedComponent.Status.IN_STOCK

    def test_fewer_serials_than_units_is_accepted_and_the_gap_stays_visible(
        self, client, supplier, operator
    ):
        """Goods that physically arrived must be recordable mid-aisle.

        Refusing the receipt because only two of five labels have been scanned
        would leave the operator holding stock they cannot enter.
        """
        purchase_order = make_po(supplier, operator)
        item = make_item("Meter", serialized=True, supplier=supplier)
        line = add_line(purchase_order, item, 5)

        response = receive(
            client,
            purchase_order,
            [
                {
                    "purchase_order_item": line.pk,
                    "quantity_received": 5,
                    "serials": [{"serial_number": "SN-1"}, {"serial_number": "SN-2"}],
                }
            ],
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert SerializedComponent.objects.filter(item=item).count() == 2
        row = line_of(worksheet(client, purchase_order).data, line)
        assert row["serials_recorded"] == 2
        assert row["serial_targets"][0]["quantity"] == 5

    def test_more_serials_than_units_is_refused_never_truncated(self, client, supplier, operator):
        """Three labels for two units means the operator has made a mistake.

        Recording two and dropping the third would discard something they
        entered without saying so.
        """
        purchase_order = make_po(supplier, operator)
        item = make_item("Meter", serialized=True, supplier=supplier)
        line = add_line(purchase_order, item, 5)

        response = receive(
            client,
            purchase_order,
            [
                {
                    "purchase_order_item": line.pk,
                    "quantity_received": 2,
                    "serials": [
                        {"serial_number": "SN-1"},
                        {"serial_number": "SN-2"},
                        {"serial_number": "SN-3"},
                    ],
                }
            ],
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "only credits 2" in response.data["error"]
        # And the whole receipt rolled back — no half-recorded delivery.
        assert SerializedComponent.objects.filter(item=item).count() == 0
        line.refresh_from_db()
        assert line.quantity_received == 0
        assert purchase_order.deliveries.count() == 0

    def test_a_duplicate_serial_rolls_the_whole_receipt_back(self, client, supplier, operator):
        purchase_order = make_po(supplier, operator)
        item = make_item("Meter", serialized=True, supplier=supplier)
        line = add_line(purchase_order, item, 3)
        receive(
            client,
            purchase_order,
            [
                {
                    "purchase_order_item": line.pk,
                    "quantity_received": 1,
                    "serials": [{"serial_number": "SN-1"}],
                }
            ],
        )

        response = receive(
            client,
            purchase_order,
            [
                {
                    "purchase_order_item": line.pk,
                    "quantity_received": 1,
                    "serials": [{"serial_number": "SN-1"}],
                }
            ],
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "already recorded" in response.data["error"]
        line.refresh_from_db()
        # Still just the first receipt.
        assert line.quantity_received == 1
        assert SerializedComponent.objects.filter(item=item).count() == 1

    def test_serials_on_a_line_with_nothing_serialized_are_refused(
        self, client, supplier, operator
    ):
        purchase_order = make_po(supplier, operator)
        item = make_item("Widget", serialized=False, supplier=supplier)
        line = add_line(purchase_order, item, 2)

        response = receive(
            client,
            purchase_order,
            [
                {
                    "purchase_order_item": line.pk,
                    "quantity_received": 2,
                    "serials": [{"serial_number": "SN-1"}],
                }
            ],
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "nothing on this line is serialized" in response.data["error"]


@pytest.mark.django_db
class TestKitSerialIdentity:
    """The kit rule: a serial never lands on the kit's own identity.

    A kit is bought as one SKU and received as stock on its COMPONENTS — the
    kit's own stock stays at zero for ever. A serial written against the kit
    would therefore name a unit that never enters stock and can never be drawn
    down. That is the documented data-corruption path, and it is defended at
    two independent layers:

    * ``KitComponent.clean`` refuses to put a serialized item in a kit at all,
      so the situation cannot normally arise; and
    * receiving refuses to record a serial against a kit identity even when
      asked to directly, which is the layer these tests exercise.

    Both matter. The first is a rule about how kits may be *configured* and can
    be relaxed by a future decision; the second is a rule about what receiving
    may *write*, and must hold regardless.
    """

    @pytest.fixture
    def kit_setup(self, supplier, operator, db):
        cable = make_item("Cable", serialized=False, supplier=supplier)
        washer = make_item("Washer", serialized=False, supplier=supplier)
        kit = InventoryItemFactory(
            name="Meter Kit", is_kit=True, current_stock=0, minimum_stock=0, image=None
        )
        KitComponent.objects.create(kit=kit, component=cable, quantity=1)
        KitComponent.objects.create(kit=kit, component=washer, quantity=2)
        ItemSupplier.objects.create(
            item=kit,
            supplier=supplier,
            supplier_sku="KIT-1",
            unit_cost=Decimal("50.00"),
            quantity_per_package=1,
            is_primary=True,
        )
        purchase_order = make_po(supplier, operator)
        line = add_line(purchase_order, kit, 2)
        return purchase_order, line, kit, cable, washer

    def test_a_serialized_item_cannot_be_put_in_a_kit_at_all(self, supplier, db):
        """The first layer, stated here so its removal is a visible change.

        This is what currently makes "a kit line with serials" unreachable
        through the UI. It is asserted rather than assumed, so if the rule is
        ever relaxed this test fails and whoever relaxes it has to look at the
        receiving-side guards below.
        """
        meter = make_item("Meter", serialized=True, supplier=supplier)
        kit = InventoryItemFactory(
            name="Serialized Kit", is_kit=True, current_stock=0, minimum_stock=0, image=None
        )

        with pytest.raises(DjangoValidationError) as excinfo:
            KitComponent.objects.create(kit=kit, component=meter, quantity=1)

        assert "Serialized items cannot be kit components" in str(excinfo.value)

    def test_a_kit_line_offers_no_serial_target_of_its_own(self, client, kit_setup):
        """The worksheet never invites a serial against the kit.

        Not merely "the kit is absent from a longer list" — the whole list is
        empty, because none of this kit's components is serialized. A client
        reading this is told, correctly, that there is nothing here to
        serialize.
        """
        purchase_order, line, kit, cable, washer = kit_setup

        row = line_of(worksheet(client, purchase_order).data, line)
        offered = {target["item"] for target in row["serial_targets"]}

        assert offered == set()
        assert str(kit.pk) not in offered
        assert row["is_kit_line"] is True

    def test_naming_the_kit_itself_is_refused_and_says_why(self, client, kit_setup):
        """The corruption path, refused loudly rather than redirected quietly.

        Silently re-pointing the serial at a component would be a guess about
        which one, on a kit that has several. The refusal names the kit and
        explains that its components are what get stocked.
        """
        purchase_order, line, kit, cable, washer = kit_setup

        response = receive(
            client,
            purchase_order,
            [
                {
                    "purchase_order_item": line.pk,
                    "quantity_received": 2,
                    "serials": [{"item": str(kit.pk), "serial_number": "K-1"}],
                }
            ],
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "never itself stocked" in response.data["error"]
        assert SerializedComponent.objects.filter(item=kit).count() == 0
        assert SerializedComponent.objects.filter(serial_number="K-1").count() == 0
        # The receipt rolled back entirely — no stock credited either.
        line.refresh_from_db()
        cable.refresh_from_db()
        assert line.quantity_received == 0
        assert cable.current_stock == 0

    def test_receiving_a_kit_never_mints_a_unit_against_the_kit(self, client, kit_setup):
        """The ordinary path: components credited, kit identity untouched."""
        purchase_order, line, kit, cable, washer = kit_setup

        response = receive(
            client,
            purchase_order,
            [{"purchase_order_item": line.pk, "quantity_received": 2}],
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert SerializedComponent.objects.filter(item=kit).count() == 0
        kit.refresh_from_db()
        cable.refresh_from_db()
        washer.refresh_from_db()
        assert kit.current_stock == 0
        assert cable.current_stock == 2
        assert washer.current_stock == 4

    def test_the_kit_rule_holds_on_an_over_receipt_too(self, client, kit_setup):
        """Three kits against an order for two: three kits' components, no kit unit."""
        purchase_order, line, kit, cable, washer = kit_setup

        response = receive(
            client,
            purchase_order,
            [{"purchase_order_item": line.pk, "quantity_received": 3}],
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert SerializedComponent.objects.filter(item=kit).count() == 0
        kit.refresh_from_db()
        cable.refresh_from_db()
        washer.refresh_from_db()
        assert kit.current_stock == 0
        assert cable.current_stock == 3
        assert washer.current_stock == 6
        line.refresh_from_db()
        assert line.is_over_received


@pytest.mark.django_db
class TestEndToEnd:
    """The captain's six steps, in order, against the real endpoints."""

    def test_the_whole_flow(self, client, supplier, operator):
        # 1. Select the purchase order.
        purchase_order = make_po(supplier, operator)
        meter = make_item(
            "Meter",
            serialized=True,
            supplier=supplier,
            sku_barcodes={"package_upc": "0123456789012"},
        )
        widget = make_item("Widget", supplier=supplier)
        meter_line = add_line(purchase_order, meter, 2)
        widget_line = add_line(purchase_order, widget, 10)

        # 2. Scan or select the line: the worksheet says what the box will read.
        payload = worksheet(client, purchase_order).data
        assert payload["can_receive"] is True
        scanned = "0123456789012"
        matched = [
            row
            for row in payload["lines"]
            if any(code["code"] == scanned for code in row["scan_codes"])
        ]
        assert [row["purchase_order_item"] for row in matched] == [meter_line.pk]

        # 3-5. Tracking barcode, quantity, and serials with lot + expiry.
        response = receive(
            client,
            purchase_order,
            [
                {
                    "purchase_order_item": meter_line.pk,
                    "quantity_received": 2,
                    "serials": [
                        {
                            "serial_number": "SN-A",
                            "lot": "L1",
                            "expiration_date": "2028-06-30",
                        },
                        {"serial_number": "SN-B"},
                    ],
                }
            ],
            tracking_number="1Z999AA10123456784",
            carrier="UPS",
        )
        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["status"] == PurchaseOrder.Status.PARTIALLY_RECEIVED

        # The other line short-ships, and the rest is cancelled.
        response = receive(
            client,
            purchase_order,
            [
                {
                    "purchase_order_item": widget_line.pk,
                    "quantity_received": 7,
                    "close_short": True,
                    "close_short_reason": "vendor short-shipped",
                }
            ],
            tracking_number="1Z999AA10123456785",
        )
        assert response.status_code == status.HTTP_200_OK, response.data

        # 6. Every line is satisfied, so the order is received.
        purchase_order.refresh_from_db()
        assert purchase_order.status == PurchaseOrder.Status.RECEIVED
        # ...and the mismatch is still on the record.
        assert purchase_order.has_receipt_variance is True
        assert purchase_order.is_fully_received is False
        widget_line.refresh_from_db()
        assert widget_line.quantity_variance == -3
        assert SerializedComponent.objects.filter(item=meter).count() == 2
        assert purchase_order.deliveries.count() == 2
        assert {d.tracking_number for d in purchase_order.deliveries.all()} == {
            "1Z999AA10123456784",
            "1Z999AA10123456785",
        }


@pytest.mark.django_db
class TestDocumentedContract:
    """The claims ``docs/PO_RECEIVING_API.md`` makes, asserted against the code.

    A documented claim the code does not honour is a defect in its own right,
    and an API doc that a second client is expected to build against is exactly
    where that costs the most. The error substrings a client is told to match on
    are pinned here so the doc and the messages cannot drift apart silently.
    """

    def test_the_documented_error_substrings_are_the_real_ones(self, client, supplier, operator):
        purchase_order = make_po(supplier, operator)
        plain = make_item("Widget", supplier=supplier)
        meter = make_item("Meter", serialized=True, supplier=supplier)
        plain_line = add_line(purchase_order, plain, 5)
        meter_line = add_line(purchase_order, meter, 2)
        other = make_item("Elsewhere", serialized=True, supplier=supplier)

        cases = [
            (
                "nothing on this line is serialized",
                [
                    {
                        "purchase_order_item": plain_line.pk,
                        "quantity_received": 1,
                        "serials": [{"serial_number": "S-1"}],
                    }
                ],
            ),
            (
                "does not credit a serialized unit",
                [
                    {
                        "purchase_order_item": meter_line.pk,
                        "quantity_received": 1,
                        "serials": [{"item": str(other.pk), "serial_number": "S-1"}],
                    }
                ],
            ),
            (
                "only credits 1 unit",
                [
                    {
                        "purchase_order_item": meter_line.pk,
                        "quantity_received": 1,
                        "serials": [
                            {"serial_number": "S-1"},
                            {"serial_number": "S-2"},
                        ],
                    }
                ],
            ),
            (
                "appears twice",
                [
                    {
                        "purchase_order_item": meter_line.pk,
                        "quantity_received": 2,
                        "serials": [
                            {"serial_number": "S-1"},
                            {"serial_number": "S-1"},
                        ],
                    }
                ],
            ),
        ]

        for substring, items in cases:
            response = receive(client, purchase_order, items)
            assert response.status_code == status.HTTP_400_BAD_REQUEST, substring
            assert substring in response.data["error"], (
                f"docs/PO_RECEIVING_API.md tells clients to match on "
                f"{substring!r}, but the API said {response.data['error']!r}"
            )

    def test_an_already_recorded_serial_says_so(self, client, supplier, operator):
        purchase_order = make_po(supplier, operator)
        meter = make_item("Meter", serialized=True, supplier=supplier)
        line = add_line(purchase_order, meter, 3)
        SerializedComponent.objects.create(item=meter, serial_number="DUP-1")

        response = receive(
            client,
            purchase_order,
            [
                {
                    "purchase_order_item": line.pk,
                    "quantity_received": 1,
                    "serials": [{"serial_number": "DUP-1"}],
                }
            ],
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "already recorded" in response.data["error"]

    def test_the_worksheet_returns_every_field_the_doc_shows(self, client, supplier, operator):
        """The documented payload shape, field by field.

        A doc that lists a key the response does not carry sends a second client
        looking for a bug in its own code.
        """
        purchase_order = make_po(supplier, operator)
        add_line(purchase_order, make_item("Widget", supplier=supplier), 5)

        payload = worksheet(client, purchase_order).data

        assert set(payload) >= {
            "purchase_order",
            "po_number",
            "supplier",
            "status",
            "status_label",
            "can_receive",
            "unavailable_reason",
            "is_settled",
            "is_fully_received",
            "has_receipt_variance",
            "outstanding_line_count",
            "variance_line_count",
            "lines",
        }
        assert set(payload["lines"][0]) >= {
            "purchase_order_item",
            "label",
            "item",
            "item_type",
            "quantity_ordered",
            "quantity_received",
            "quantity_pending",
            "quantity_variance",
            "receipt_state",
            "receipt_state_label",
            "is_settled",
            "is_voided",
            "is_closed_short",
            "closed_short_reason",
            "is_kit_line",
            "scan_codes",
            "serial_targets",
            "serials_recorded",
        }

    def test_every_documented_scan_code_kind_is_one_the_api_emits(self, client, supplier, operator):
        purchase_order = make_po(supplier, operator)
        item = make_item(
            "Relay",
            supplier=supplier,
            sku_barcodes={
                "package_upc": "0123456789012",
                "unit_upc": "9876543210987",
                "supplier_sku": "GRA-1",
            },
        )
        line = add_line(purchase_order, item, 1)

        row = line_of(worksheet(client, purchase_order).data, line)
        kinds = {entry["kind"] for entry in row["scan_codes"]}

        # The four the doc's table names, and nothing the doc does not.
        assert kinds == {"item_sku", "package_upc", "unit_upc", "supplier_sku"}

    def test_mark_delivered_and_mark_received_are_genuinely_different(
        self, client, supplier, operator
    ):
        """The doc says one stocks the shortfall and the other writes it off.

        If they behaved alike, the warning telling operators never to substitute
        one for the other would be advice about nothing.
        """
        stocked = make_po(supplier, operator)
        stocked_item = make_item("Widget", stock=0, supplier=supplier)
        stocked_line = add_line(stocked, stocked_item, 10)

        written_off = make_po(supplier, operator)
        written_item = make_item("Gasket", stock=0, supplier=supplier)
        written_line = add_line(written_off, written_item, 10)

        client.post(
            reverse("purchaseorder-mark-delivered", args=[stocked.pk]),
            {"delivery_date": date(2026, 8, 1).isoformat()},
            format="json",
        )
        client.post(
            reverse("purchaseorder-mark-received", args=[written_off.pk]),
            {"reason": "not coming"},
            format="json",
        )

        stocked_line.refresh_from_db()
        stocked_item.refresh_from_db()
        written_line.refresh_from_db()
        written_item.refresh_from_db()

        # mark-delivered: the outstanding quantity is received and STOCKED.
        assert stocked_line.quantity_received == 10
        assert stocked_item.current_stock == 10
        assert stocked_line.receipt_state == PurchaseOrderItem.ReceiptState.RECEIVED

        # mark-received: nothing stocked, the shortfall written off.
        assert written_line.quantity_received == 0
        assert written_item.current_stock == 0
        assert written_line.receipt_state == PurchaseOrderItem.ReceiptState.CLOSED_SHORT

        # Both orders end `received`; only one of them is honest about arrival.
        stocked.refresh_from_db()
        written_off.refresh_from_db()
        assert stocked.status == written_off.status == PurchaseOrder.Status.RECEIVED
        assert stocked.has_receipt_variance is False
        assert written_off.has_receipt_variance is True
