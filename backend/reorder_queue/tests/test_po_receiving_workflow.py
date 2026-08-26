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

import uuid
from datetime import date, timedelta
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
from reorder_queue import services
from reorder_queue.models import (
    LeadTimeLog,
    PurchaseOrder,
    PurchaseOrderAuditEvent,
    PurchaseOrderItem,
)


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
class TestWorksheetIdentifierTypes:
    """Two kinds of id ride in this payload and they are not interchangeable.

    A purchase order and its lines are integer primary keys; an inventory item
    is a UUID. They look alike in a JSON example and a client that types the
    order id as a string builds against something the endpoint never sends.
    """

    def test_the_order_and_line_ids_are_integers_and_the_item_id_is_a_uuid(
        self, client, supplier, operator
    ):
        purchase_order = make_po(supplier, operator)
        item = make_item("Widget", supplier=supplier)
        line = add_line(purchase_order, item, 4)

        payload = worksheet(client, purchase_order).data
        row = line_of(payload, line)

        assert payload["purchase_order"] == purchase_order.pk
        assert isinstance(payload["purchase_order"], int)
        assert isinstance(row["purchase_order_item"], int)
        assert row["item"] == str(item.pk)
        assert uuid.UUID(row["item"])


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
    """The kit rule: serials land on the COMPONENTS, never on the kit.

    A kit is bought as one SKU and stocked as its parts — its own stock stays
    at zero for ever — so a serial written against the kit names a unit that
    can never be drawn down. That is the documented data-corruption path.

    Serialized items were once banned from kits outright to prevent it. That
    ban was lifted (oms-po-receiving) once receiving could record a serial
    against the right component; what defends the rule now is this refusal, at
    the point of the write, plus the ``serials_outstanding`` gap that keeps an
    uncaptured serial visible instead of silently lost.
    """

    @pytest.fixture
    def kit_setup(self, supplier, operator, db):
        """A kit with one serialized component and one plain one."""
        meter = make_item("Meter", serialized=True, supplier=supplier)
        cable = make_item("Cable", serialized=False, supplier=supplier)
        kit = InventoryItemFactory(
            name="Meter Kit", is_kit=True, current_stock=0, minimum_stock=0, image=None
        )
        KitComponent.objects.create(kit=kit, component=meter, quantity=1)
        KitComponent.objects.create(kit=kit, component=cable, quantity=2)
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
        return purchase_order, line, kit, meter, cable

    def test_a_serialized_item_may_now_be_a_kit_component(self, supplier, db):
        """The lifted rule, asserted at the model.

        It used to raise "Serialized items cannot be kit components". Receiving
        can record those serials now, so refusing the configuration guarded
        against nothing and blocked a real one.
        """
        meter = make_item("Meter", serialized=True, supplier=supplier)
        kit = InventoryItemFactory(
            name="Serialized Kit", is_kit=True, current_stock=0, minimum_stock=0, image=None
        )

        row = KitComponent.objects.create(kit=kit, component=meter, quantity=1)

        assert row.pk is not None
        assert kit.kit_components.get().component == meter
        # The rule that did NOT move: the kit itself is still never serialized.
        kit.is_serialized = True
        with pytest.raises(DjangoValidationError) as excinfo:
            kit.full_clean()
        assert "kit cannot be serialized" in str(excinfo.value)

    def test_a_kit_line_offers_its_serialized_components_never_the_kit(self, client, kit_setup):
        purchase_order, line, kit, meter, cable = kit_setup

        row = line_of(worksheet(client, purchase_order).data, line)
        offered = {target["item"] for target in row["serial_targets"]}

        assert offered == {str(meter.pk)}
        # Never the kit — that is the corruption path.
        assert str(kit.pk) not in offered
        # Nor the component that is not serialized.
        assert str(cable.pk) not in offered
        # Two kits ordered, one meter each.
        assert row["serial_targets"][0]["quantity"] == 2

    def test_serials_on_a_kit_line_land_on_the_component(self, client, kit_setup):
        purchase_order, line, kit, meter, cable = kit_setup

        response = receive(
            client,
            purchase_order,
            [
                {
                    "purchase_order_item": line.pk,
                    "quantity_received": 2,
                    "serials": [
                        {"item": str(meter.pk), "serial_number": "M-1", "lot": "L9"},
                        {"item": str(meter.pk), "serial_number": "M-2"},
                    ],
                }
            ],
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        units = SerializedComponent.objects.filter(item=meter)
        assert set(units.values_list("serial_number", flat=True)) == {"M-1", "M-2"}
        # Nothing whatsoever attached to the kit's identity.
        assert SerializedComponent.objects.filter(item=kit).count() == 0
        kit.refresh_from_db()
        assert kit.current_stock == 0
        # The components were still credited exactly as before.
        meter.refresh_from_db()
        cable.refresh_from_db()
        assert meter.current_stock == 2
        assert cable.current_stock == 4
        # Provenance points at the kit LINE, but the unit is a component's.
        first = units.get(serial_number="M-1")
        assert first.provenance_purchase_order_item == line
        assert first.item == meter
        assert first.lot == "L9"
        # Every credited unit has a serial, so nothing is outstanding.
        line.refresh_from_db()
        assert services.serials_outstanding(line) == 0

    def test_the_component_serial_target_uses_the_live_serialized_flag(
        self, client, supplier, operator, db
    ):
        """Serialization is a property of the item TODAY, not of the box.

        The kit snapshot freezes what a receipt *credits* — quantities and
        component identities as ordered — because that is what physically
        arrives. Whether the system tracks a component serially is a different
        question: serials can only be recorded for an item it tracks serially
        now, so an item that became serialized after the order must still be
        offered.
        """
        part = make_item("Part", serialized=False, supplier=supplier)
        kit = InventoryItemFactory(
            name="Late Kit", is_kit=True, current_stock=0, minimum_stock=0, image=None
        )
        KitComponent.objects.create(kit=kit, component=part, quantity=1)
        ItemSupplier.objects.create(
            item=kit,
            supplier=supplier,
            supplier_sku="KIT-L",
            unit_cost=Decimal("5.00"),
            quantity_per_package=1,
            is_primary=True,
        )
        purchase_order = make_po(supplier, operator)
        line = add_line(purchase_order, kit, 1)
        # Ordered while the part was NOT serialized; it becomes so afterwards.
        assert line_of(worksheet(client, purchase_order).data, line)["serial_targets"] == []

        part.is_serialized = True
        part.save(update_fields=["is_serialized"])

        row = line_of(worksheet(client, purchase_order).data, line)
        assert [target["item"] for target in row["serial_targets"]] == [str(part.pk)]

    def test_naming_the_kit_itself_is_refused_and_says_why(self, client, kit_setup):
        """The corruption path, refused loudly rather than redirected quietly.

        Silently re-pointing the serial at a component would be a guess about
        which one, on a kit that has several.
        """
        purchase_order, line, kit, meter, cable = kit_setup

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

    def test_an_unlabelled_serial_on_a_multi_component_kit_is_refused(
        self, client, supplier, operator, db
    ):
        """With two serialized components there is no "the obvious one".

        Picking either would be inventing which physical part the operator was
        holding. Reachable only now that a kit may hold serialized components.
        """
        meter = make_item("Meter", serialized=True, supplier=supplier)
        probe = make_item("Probe", serialized=True, supplier=supplier)
        kit = InventoryItemFactory(
            name="Twin Kit", is_kit=True, current_stock=0, minimum_stock=0, image=None
        )
        KitComponent.objects.create(kit=kit, component=meter, quantity=1)
        KitComponent.objects.create(kit=kit, component=probe, quantity=1)
        ItemSupplier.objects.create(
            item=kit,
            supplier=supplier,
            supplier_sku="KIT-2",
            unit_cost=Decimal("50.00"),
            quantity_per_package=1,
            is_primary=True,
        )
        purchase_order = make_po(supplier, operator)
        line = add_line(purchase_order, kit, 1)

        response = receive(
            client,
            purchase_order,
            [
                {
                    "purchase_order_item": line.pk,
                    "quantity_received": 1,
                    "serials": [{"serial_number": "X-1"}],
                }
            ],
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "which one it belongs to" in response.data["error"]
        # Both candidates named, so the operator can fix it without guessing.
        assert "Meter" in response.data["error"]
        assert "Probe" in response.data["error"]
        assert SerializedComponent.objects.count() == 0

    def test_a_serial_for_the_wrong_component_of_the_right_kit_is_refused(self, client, kit_setup):
        """The cable is in the box, but it is not serialized.

        Accepting this would mint a serialized unit for an item nothing tracks
        serially — a different way to reach the same unusable row.
        """
        purchase_order, line, kit, meter, cable = kit_setup

        response = receive(
            client,
            purchase_order,
            [
                {
                    "purchase_order_item": line.pk,
                    "quantity_received": 1,
                    "serials": [{"item": str(cable.pk), "serial_number": "C-1"}],
                }
            ],
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "does not credit a serialized unit" in response.data["error"]
        assert SerializedComponent.objects.filter(item=cable).count() == 0

    def test_the_kit_rule_holds_on_an_over_receipt_too(self, client, kit_setup):
        """Three kits against an order for two: three meters, still no kit unit."""
        purchase_order, line, kit, meter, cable = kit_setup

        response = receive(
            client,
            purchase_order,
            [
                {
                    "purchase_order_item": line.pk,
                    "quantity_received": 3,
                    "serials": [
                        {"item": str(meter.pk), "serial_number": "M-1"},
                        {"item": str(meter.pk), "serial_number": "M-2"},
                        {"item": str(meter.pk), "serial_number": "M-3"},
                    ],
                }
            ],
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert SerializedComponent.objects.filter(item=meter).count() == 3
        assert SerializedComponent.objects.filter(item=kit).count() == 0
        kit.refresh_from_db()
        meter.refresh_from_db()
        cable.refresh_from_db()
        assert kit.current_stock == 0
        assert meter.current_stock == 3
        assert cable.current_stock == 6
        line.refresh_from_db()
        assert line.is_over_received


@pytest.mark.django_db
class TestSerialGapIsVisible:
    """What replaced the prohibition: an uncaptured serial is never silent.

    The old rule refused serialized kit components because "receiving the kit
    would credit stock without recording serial numbers". That gap was never
    unique to kits — ``mark-delivered`` has always credited an ordinary
    serialized line's stock and minted nothing — so the fix is to report it
    everywhere rather than to forbid one configuration.
    """

    def test_mark_delivered_on_a_serialized_line_reports_the_gap(self, client, supplier, operator):
        """The path that credits stock and records no serials at all.

        This is the exact hazard the prohibition named, on the ordinary line
        where it has always been reachable. It stays permitted — the goods did
        arrive — but it is now counted.
        """
        purchase_order = make_po(supplier, operator)
        meter = make_item("Meter", serialized=True, supplier=supplier)
        line = add_line(purchase_order, meter, 3)

        response = client.post(
            reverse("purchaseorder-mark-delivered", args=[purchase_order.pk]),
            {"delivery_date": date(2026, 8, 1).isoformat()},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        line.refresh_from_db()
        meter.refresh_from_db()
        # Stock credited, no serials — and the gap says exactly that.
        assert meter.current_stock == 3
        assert SerializedComponent.objects.filter(item=meter).count() == 0
        assert services.serials_outstanding(line) == 3
        assert response.data["serials_outstanding"] == 3

    def test_the_gap_closes_as_serials_are_captured(self, client, supplier, operator):
        purchase_order = make_po(supplier, operator)
        meter = make_item("Meter", serialized=True, supplier=supplier)
        line = add_line(purchase_order, meter, 3)

        receive(
            client,
            purchase_order,
            [
                {
                    "purchase_order_item": line.pk,
                    "quantity_received": 3,
                    "serials": [{"serial_number": "S-1"}, {"serial_number": "S-2"}],
                }
            ],
        )

        line.refresh_from_db()
        assert services.serials_outstanding(line) == 1
        row = line_of(worksheet(client, purchase_order).data, line)
        assert row["serials_outstanding"] == 1
        assert row["serial_gap"] == [
            {
                "item": str(meter.pk),
                "item_name": "Meter",
                "expected": 3,
                "recorded": 2,
                "outstanding": 1,
            }
        ]

    def test_the_gap_is_measured_per_component_on_a_kit_line(self, client, supplier, operator, db):
        """A single total could not say WHICH component is missing serials."""
        meter = make_item("Meter", serialized=True, supplier=supplier)
        probe = make_item("Probe", serialized=True, supplier=supplier)
        kit = InventoryItemFactory(
            name="Twin Kit", is_kit=True, current_stock=0, minimum_stock=0, image=None
        )
        KitComponent.objects.create(kit=kit, component=meter, quantity=1)
        KitComponent.objects.create(kit=kit, component=probe, quantity=2)
        ItemSupplier.objects.create(
            item=kit,
            supplier=supplier,
            supplier_sku="KIT-3",
            unit_cost=Decimal("50.00"),
            quantity_per_package=1,
            is_primary=True,
        )
        purchase_order = make_po(supplier, operator)
        line = add_line(purchase_order, kit, 1)

        # One kit: 1 meter, 2 probes. Only the meter gets a serial.
        receive(
            client,
            purchase_order,
            [
                {
                    "purchase_order_item": line.pk,
                    "quantity_received": 1,
                    "serials": [{"item": str(meter.pk), "serial_number": "M-1"}],
                }
            ],
        )

        line.refresh_from_db()
        gap = {row["item_name"]: row for row in services.serial_gap(line)}
        assert gap["Meter"]["outstanding"] == 0
        assert gap["Probe"]["outstanding"] == 2
        assert services.serials_outstanding(line) == 2

    def test_the_gap_counts_what_ARRIVED_not_what_was_ordered(self, client, supplier, operator):
        """Order 5, receive 2, capture none: two units owe serials, not five.

        Measuring against the ordered quantity would claim serials are missing
        for three units that are not on the shelf yet — outstanding work that
        nobody can do, on a figure that could never reach zero until the line
        was fully received. The distinction is invisible whenever a line is
        received in full, which is why it is asserted on a partial one.
        """
        purchase_order = make_po(supplier, operator)
        meter = make_item("Meter", serialized=True, supplier=supplier)
        line = add_line(purchase_order, meter, 5)

        receive(client, purchase_order, [{"purchase_order_item": line.pk, "quantity_received": 2}])

        line.refresh_from_db()
        assert line.quantity_ordered == 5
        assert line.quantity_received == 2
        assert services.serials_outstanding(line) == 2
        assert services.serial_gap(line)[0]["expected"] == 2

        # The rest arrives later; the gap grows with the stock, not the order.
        receive(client, purchase_order, [{"purchase_order_item": line.pk, "quantity_received": 3}])
        line.refresh_from_db()
        assert services.serials_outstanding(line) == 5

    def test_a_line_with_nothing_serialized_has_no_gap(self, client, supplier, operator):
        """Zero must mean "nothing owed", never "we did not look"."""
        purchase_order = make_po(supplier, operator)
        widget = make_item("Widget", serialized=False, supplier=supplier)
        line = add_line(purchase_order, widget, 5)
        receive(client, purchase_order, [{"purchase_order_item": line.pk, "quantity_received": 5}])

        line.refresh_from_db()
        assert services.serial_gap(line) == []
        assert services.serials_outstanding(line) == 0

    def test_the_order_rolls_the_gap_up(self, client, supplier, operator):
        purchase_order = make_po(supplier, operator)
        meter = make_item("Meter", serialized=True, supplier=supplier)
        probe = make_item("Probe", serialized=True, supplier=supplier)
        meter_line = add_line(purchase_order, meter, 2)
        probe_line = add_line(purchase_order, probe, 3)

        receive(
            client,
            purchase_order,
            [
                {"purchase_order_item": meter_line.pk, "quantity_received": 2},
                {"purchase_order_item": probe_line.pk, "quantity_received": 3},
            ],
        )

        payload = worksheet(client, purchase_order).data
        assert payload["serials_outstanding"] == 5


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
            "serial_gap",
            "serials_outstanding",
        }

    def test_the_documented_serial_gap_shape_is_the_real_one(self, client, supplier, operator):
        """The keys the doc's ``serial_gap`` example shows, exactly."""
        purchase_order = make_po(supplier, operator)
        meter = make_item("Meter", serialized=True, supplier=supplier)
        line = add_line(purchase_order, meter, 3)
        receive(
            client,
            purchase_order,
            [
                {
                    "purchase_order_item": line.pk,
                    "quantity_received": 3,
                    "serials": [{"serial_number": "S-1"}, {"serial_number": "S-2"}],
                }
            ],
        )

        row = line_of(worksheet(client, purchase_order).data, line)

        assert set(row["serial_gap"][0]) == {
            "item",
            "item_name",
            "expected",
            "recorded",
            "outstanding",
        }
        # The doc says `expected` comes from what was RECEIVED, not ordered.
        assert row["serial_gap"][0]["expected"] == 3
        assert row["serials_outstanding"] == 1

    def test_every_path_the_doc_says_can_leave_a_gap_actually_can(self, client, supplier, operator):
        """The doc's path table, asserted rather than asserted-about.

        A table claiming ``mark-delivered`` leaves a gap would be worse than no
        table if the path had since started capturing serials — a client would
        surface a warning that never fires.
        """
        # `receive` with no serials.
        first = make_po(supplier, operator)
        meter_a = make_item("MeterA", serialized=True, supplier=supplier)
        line_a = add_line(first, meter_a, 2)
        receive(client, first, [{"purchase_order_item": line_a.pk, "quantity_received": 2}])
        line_a.refresh_from_db()
        assert services.serials_outstanding(line_a) == 2

        # `mark-delivered`, which captures no serials at all.
        second = make_po(supplier, operator)
        meter_b = make_item("MeterB", serialized=True, supplier=supplier)
        line_b = add_line(second, meter_b, 2)
        client.post(
            reverse("purchaseorder-mark-delivered", args=[second.pk]),
            {"delivery_date": date(2026, 8, 1).isoformat()},
            format="json",
        )
        line_b.refresh_from_db()
        assert services.serials_outstanding(line_b) == 2

        # `receive` with full serials leaves none.
        third = make_po(supplier, operator)
        meter_c = make_item("MeterC", serialized=True, supplier=supplier)
        line_c = add_line(third, meter_c, 2)
        receive(
            client,
            third,
            [
                {
                    "purchase_order_item": line_c.pk,
                    "quantity_received": 2,
                    "serials": [{"serial_number": "C-1"}, {"serial_number": "C-2"}],
                }
            ],
        )
        line_c.refresh_from_db()
        assert services.serials_outstanding(line_c) == 0

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

        # Both orders are settled — receiving is finished with every line — but
        # only the one goods arrived at reads `received`. Nothing came in
        # against the other, so it stays `sent`: settlement is bookkeeping and
        # `received` is a claim about the world.
        stocked.refresh_from_db()
        written_off.refresh_from_db()
        assert stocked.is_settled == written_off.is_settled is True
        assert stocked.status == PurchaseOrder.Status.RECEIVED
        assert written_off.status == PurchaseOrder.Status.SENT
        assert stocked.has_receipt_variance is False
        assert written_off.has_receipt_variance is True


@pytest.mark.django_db
class TestOneAnswerToWhatIsOutstanding:
    """``mark-delivered`` receives what the order still owes — and only that.

    "Which lines does receiving still owe?" has one answer,
    :func:`services.outstanding_lines`, and every site that acts on those lines
    reads it from there. When ``mark-delivered`` asked the question with its own
    predicate instead, it stocked goods for two kinds of line that nothing is
    coming for: one whose shortfall had just been written off, and one that had
    been struck off the order altogether.
    """

    def test_mark_delivered_does_not_stock_a_closed_short_balance(self, client, supplier, operator):
        """8 of 10 arrived and the other 2 were written off — 2 must not appear.

        Stocking them would put units on the shelf that never existed and erase
        the shortfall the close-short record exists to preserve.
        """
        purchase_order = make_po(supplier, operator)
        short_item = make_item("Widget", stock=0, supplier=supplier)
        short_line = add_line(purchase_order, short_item, 10)
        other_item = make_item("Gasket", stock=0, supplier=supplier)
        other_line = add_line(purchase_order, other_item, 5)

        receive(
            client,
            purchase_order,
            [{"purchase_order_item": short_line.pk, "quantity_received": 8}],
        )
        client.post(
            reverse("purchaseorder-close-short", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": short_line.pk, "reason": "backorder cancelled"}]},
            format="json",
        )

        response = client.post(
            reverse("purchaseorder-mark-delivered", args=[purchase_order.pk]),
            {"delivery_date": date(2026, 8, 1).isoformat()},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        short_line.refresh_from_db()
        short_item.refresh_from_db()
        other_line.refresh_from_db()
        other_item.refresh_from_db()

        # The written-off balance stayed written off, on the line and on the shelf.
        assert short_line.quantity_received == 8
        assert short_item.current_stock == 8
        assert short_line.receipt_state == PurchaseOrderItem.ReceiptState.CLOSED_SHORT
        assert short_line.quantity_variance == -2
        # The line that really was outstanding is the one that got received.
        assert other_line.quantity_received == 5
        assert other_item.current_stock == 5

        purchase_order.refresh_from_db()
        assert purchase_order.status == PurchaseOrder.Status.RECEIVED
        # The shortfall is still chaseable after the order closed out.
        assert purchase_order.has_receipt_variance is True

    def test_mark_delivered_does_not_stock_a_voided_line(self, client, supplier, operator):
        """A struck-off line keeps its quantities, so a pending-based predicate stocks it."""
        purchase_order = make_po(supplier, operator)
        live_item = make_item("Widget", stock=0, supplier=supplier)
        live_line = add_line(purchase_order, live_item, 6)
        struck_item = make_item("Gasket", stock=0, supplier=supplier)
        struck_line = add_line(purchase_order, struck_item, 4)
        services.void_line_item(struck_line, operator, "ordered by mistake")

        response = client.post(
            reverse("purchaseorder-mark-delivered", args=[purchase_order.pk]),
            {"delivery_date": date(2026, 8, 1).isoformat()},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        live_line.refresh_from_db()
        live_item.refresh_from_db()
        struck_line.refresh_from_db()
        struck_item.refresh_from_db()

        assert live_line.quantity_received == 6
        assert live_item.current_stock == 6
        # Nothing arrives for a line nobody is expecting anything on.
        assert struck_line.quantity_received == 0
        assert struck_item.current_stock == 0
        assert not struck_line.deliveries.exists()


@pytest.mark.django_db
class TestReopeningACloseShort:
    """A close-short recorded in error is CORRECTED, never erased.

    The reopen puts the balance back on the order and is stamped beside the
    close-short it corrects — same standard, actor and timestamp and reason —
    so the record reads as a mistake and its correction. It is the action the
    ``receive`` endpoint names when it refuses a closed-short line.
    """

    def _closed_short_order(self, client, supplier, operator):
        purchase_order = make_po(supplier, operator)
        item = make_item("Widget", stock=0, supplier=supplier)
        line = add_line(purchase_order, item, 10)
        receive(client, purchase_order, [{"purchase_order_item": line.pk, "quantity_received": 8}])
        client.post(
            reverse("purchaseorder-close-short", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": line.pk, "reason": "backorder cancelled"}]},
            format="json",
        )
        line.refresh_from_db()
        purchase_order.refresh_from_db()
        assert purchase_order.status == PurchaseOrder.Status.RECEIVED
        return purchase_order, item, line

    def test_reopen_keeps_the_close_short_on_the_record_and_stamps_the_correction(
        self, client, supplier, operator
    ):
        purchase_order, _item, line = self._closed_short_order(client, supplier, operator)
        closed_at = line.closed_short_at

        response = client.post(
            reverse("purchaseorder-reopen-short", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": line.pk, "reason": "closed the wrong line"}]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        line.refresh_from_db()

        # The mistake, untouched.
        assert line.closed_short_at == closed_at
        assert line.closed_short_by == operator
        assert line.closed_short_reason == "backorder cancelled"
        # The correction, attributable to the same standard.
        assert line.reopened_at is not None
        assert line.reopened_at >= closed_at
        assert line.reopened_by == operator
        assert line.reopened_reason == "closed the wrong line"
        # And one derivation reads both stamps, so no reader special-cases this.
        assert line.is_closed_short is False
        assert line.was_reopened is True
        assert line.receipt_state == PurchaseOrderItem.ReceiptState.PARTIALLY_RECEIVED
        assert line.is_settled is False

    def test_reopen_moves_the_order_back_off_received(self, client, supplier, operator):
        purchase_order, _item, line = self._closed_short_order(client, supplier, operator)

        client.post(
            reverse("purchaseorder-reopen-short", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": line.pk, "reason": "closed the wrong line"}]},
            format="json",
        )

        purchase_order.refresh_from_db()
        assert purchase_order.status == PurchaseOrder.Status.PARTIALLY_RECEIVED
        assert purchase_order.is_settled is False
        assert purchase_order.outstanding_line_count == 1

    def test_a_reopened_line_can_be_received_against_again(self, client, supplier, operator):
        """The point of the correction: the balance is receivable, not stranded.

        A second, still-outstanding line keeps the order receivable, so the
        refusal comes from the line guard — the one whose message names the
        action — rather than from the order-status gate.
        """
        purchase_order = make_po(supplier, operator)
        item = make_item("Widget", stock=0, supplier=supplier)
        line = add_line(purchase_order, item, 10)
        add_line(purchase_order, make_item("Gasket", supplier=supplier), 5)
        receive(client, purchase_order, [{"purchase_order_item": line.pk, "quantity_received": 8}])
        client.post(
            reverse("purchaseorder-close-short", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": line.pk, "reason": "backorder cancelled"}]},
            format="json",
        )

        refused = receive(
            client, purchase_order, [{"purchase_order_item": line.pk, "quantity_received": 2}]
        )
        assert refused.status_code == status.HTTP_400_BAD_REQUEST
        # The refusal names an action that exists.
        assert "reopen-short" in refused.data["error"]

        client.post(
            reverse("purchaseorder-reopen-short", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": line.pk, "reason": "it shipped after all"}]},
            format="json",
        )
        accepted = receive(
            client, purchase_order, [{"purchase_order_item": line.pk, "quantity_received": 2}]
        )

        assert accepted.status_code == status.HTTP_200_OK, accepted.data
        line.refresh_from_db()
        item.refresh_from_db()

        assert line.quantity_received == 10
        assert item.current_stock == 10
        assert line.receipt_state == PurchaseOrderItem.ReceiptState.RECEIVED

    def test_the_close_short_and_the_reopen_are_two_attributable_audit_events(
        self, client, supplier, operator
    ):
        purchase_order, _item, line = self._closed_short_order(client, supplier, operator)

        client.post(
            reverse("purchaseorder-reopen-short", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": line.pk, "reason": "closed the wrong line"}]},
            format="json",
        )

        events = list(
            PurchaseOrderAuditEvent.objects.filter(purchase_order=purchase_order).order_by(
                "created_at"
            )
        )
        closures = [
            event
            for event in events
            if event.metadata.get("closed_short") and not event.metadata.get("reopened_short")
        ]
        reopens = [
            event
            for event in events
            if event.action == PurchaseOrderAuditEvent.Action.PO_LINE_REOPEN_SHORT
        ]

        assert len(closures) == 1
        assert len(reopens) == 1
        assert closures[0].actor == operator
        assert reopens[0].actor == operator
        # The correction names what it corrected, so the trail reads as a pair.
        entry = reopens[0].metadata["reopened_short"][0]
        assert entry["purchase_order_item"] == line.pk
        assert entry["reason"] == "closed the wrong line"
        assert entry["corrects_close_short_reason"] == "backorder cancelled"

    def test_reopening_a_line_that_is_not_closed_short_is_refused(self, client, supplier, operator):
        purchase_order = make_po(supplier, operator)
        line = add_line(purchase_order, make_item("Widget", supplier=supplier), 10)

        response = client.post(
            reverse("purchaseorder-reopen-short", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": line.pk, "reason": "oops"}]},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "not closed short" in response.data["error"]
        line.refresh_from_db()
        assert line.reopened_at is None


@pytest.mark.django_db
class TestRejectedReceiptWritesNothing:
    """The documented promise: "A rejected receipt writes nothing at all."

    The receipt and the closures it settles are one transaction. A close-short
    that fails after the receipt would otherwise leave the stock credited, the
    delivery created and the order advanced behind a 400 the operator would
    reasonably retry.
    """

    def test_a_failing_close_short_rolls_the_whole_receipt_back(self, client, supplier, operator):
        purchase_order = make_po(supplier, operator)
        item = make_item("Widget", stock=3, supplier=supplier)
        line = add_line(purchase_order, item, 10)

        # Everything outstanding arrives, so the close-short flag has nothing
        # left to write off by the time it runs — and raises.
        response = receive(
            client,
            purchase_order,
            [
                {
                    "purchase_order_item": line.pk,
                    "quantity_received": 10,
                    "close_short": True,
                    "close_short_reason": "the rest is not coming",
                }
            ],
            tracking_number="1Z999AA10123456784",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        line.refresh_from_db()
        item.refresh_from_db()
        purchase_order.refresh_from_db()

        assert purchase_order.deliveries.count() == 0
        assert item.current_stock == 3
        assert line.quantity_received == 0
        assert line.closed_short_at is None
        assert purchase_order.status == PurchaseOrder.Status.SENT
        assert (
            PurchaseOrderAuditEvent.objects.filter(
                purchase_order=purchase_order,
                action=PurchaseOrderAuditEvent.Action.PO_RECEIVE_ITEMS,
            ).count()
            == 0
        )

    def test_a_committed_receipt_always_records_its_audit_event(self, client, supplier, operator):
        """The audit event is inside the same transaction as the receipt."""
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
                    "close_short_reason": "backorder cancelled",
                }
            ],
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        events = PurchaseOrderAuditEvent.objects.filter(
            purchase_order=purchase_order,
            action=PurchaseOrderAuditEvent.Action.PO_RECEIVE_ITEMS,
        )
        assert events.count() == 1
        received = events.first().metadata["received_items"][0]
        assert received["purchase_order_item"] == line.pk
        assert received["quantity_received"] == 8


def scan_barcode(client, purchase_order, upc, quantity):
    """The older inline UPC receive path, driven as a real request."""
    return client.post(
        "/api/reorders/receipts/scan_barcode/",
        {
            "purchase_order_id": purchase_order.id,
            "scanned_upc": upc,
            "quantity_received": quantity,
        },
        format="json",
    )


def void_line(client, purchase_order, po_item, reason="ordered by mistake"):
    return client.post(
        reverse("purchaseorder-void-item", args=[purchase_order.pk, po_item.pk]),
        {"reason": reason},
        format="json",
    )


def settlement_consistent(purchase_order):
    """Re-read the order and assert its stored status says what its lines say.

    The invariant every settlement-changing path has to leave behind. An order
    whose lines are all settled but whose status still reads
    ``partially_received`` is unreceivable: both close-out actions refuse it for
    having nothing outstanding, and nothing else moves it.

    Read back through a fresh instance so what is checked is what was persisted,
    not what the request happened to leave in memory. Returns it, so a caller
    can go on to assert which of the two statuses it landed on.
    """
    fresh = PurchaseOrder.objects.get(pk=purchase_order.pk)
    if not fresh.has_received_anything:
        # Settlement alone never promotes an order nothing arrived against, so
        # for those the invariant is that receiving left the status alone.
        assert fresh.status in PurchaseOrder.RECEIVABLE_STATUSES
        return fresh
    expected = (
        PurchaseOrder.Status.RECEIVED
        if fresh.is_settled
        else PurchaseOrder.Status.PARTIALLY_RECEIVED
    )
    assert fresh.status == expected, (
        f"status {fresh.status!r} but is_settled={fresh.is_settled} "
        f"with {fresh.outstanding_line_count} outstanding line(s)"
    )
    return fresh


@pytest.mark.django_db
class TestSettlementDecidesStatusOnEveryPath:
    """One answer to "what status does this order's settlement imply?".

    Five paths can change whether a line is settled — receiving, closing short,
    reopening, voiding a line, and the older inline barcode scan. Each of them
    has to leave the stored status agreeing with the lines, or the order is
    stranded: every close-out action refuses an order with nothing outstanding,
    so a status that disagrees cannot be corrected through the API at all.
    """

    def test_receiving_the_last_outstanding_line_finishes_the_order(
        self, client, supplier, operator
    ):
        purchase_order = make_po(supplier, operator)
        first = add_line(purchase_order, make_item("Widget", supplier=supplier), 4)
        second = add_line(purchase_order, make_item("Gasket", supplier=supplier), 6)

        receive(client, purchase_order, [{"purchase_order_item": first.pk, "quantity_received": 4}])
        assert settlement_consistent(purchase_order).status == (
            PurchaseOrder.Status.PARTIALLY_RECEIVED
        )

        receive(
            client, purchase_order, [{"purchase_order_item": second.pk, "quantity_received": 6}]
        )
        assert settlement_consistent(purchase_order).status == PurchaseOrder.Status.RECEIVED

    def test_closing_and_reopening_move_the_status_both_ways(self, client, supplier, operator):
        purchase_order = make_po(supplier, operator)
        line = add_line(purchase_order, make_item("Widget", supplier=supplier), 10)
        receive(client, purchase_order, [{"purchase_order_item": line.pk, "quantity_received": 8}])
        assert settlement_consistent(purchase_order).status == (
            PurchaseOrder.Status.PARTIALLY_RECEIVED
        )

        client.post(
            reverse("purchaseorder-close-short", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": line.pk, "reason": "backorder cancelled"}]},
            format="json",
        )
        assert settlement_consistent(purchase_order).status == PurchaseOrder.Status.RECEIVED

        client.post(
            reverse("purchaseorder-reopen-short", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": line.pk, "reason": "closed the wrong line"}]},
            format="json",
        )
        assert settlement_consistent(purchase_order).status == (
            PurchaseOrder.Status.PARTIALLY_RECEIVED
        )

    def test_voiding_the_last_outstanding_line_finishes_the_order(self, client, supplier, operator):
        """Striking a line off settles it, so nothing is waiting on it any more.

        Voided after the other line had already landed, the order used to keep
        reading ``partially_received`` while its own payload said receiving was
        finished — and both close-out actions then refused it for having
        nothing outstanding, so no API call could move it.
        """
        purchase_order = make_po(supplier, operator)
        landed = add_line(purchase_order, make_item("Widget", supplier=supplier), 10)
        struck = add_line(purchase_order, make_item("Gasket", supplier=supplier), 5)

        receive(
            client, purchase_order, [{"purchase_order_item": landed.pk, "quantity_received": 10}]
        )
        assert settlement_consistent(purchase_order).status == (
            PurchaseOrder.Status.PARTIALLY_RECEIVED
        )

        response = void_line(client, purchase_order, struck)
        assert response.status_code == status.HTTP_200_OK, response.data

        closed_out = settlement_consistent(purchase_order)
        assert closed_out.status == PurchaseOrder.Status.RECEIVED
        assert closed_out.outstanding_line_count == 0

    def test_scanning_the_last_outstanding_line_finishes_a_short_closed_order(
        self, client, supplier, operator
    ):
        """The barcode path reads settlement the same way the others do.

        With one line closed short at 8 of 10, "did everything we ordered turn
        up?" is false for ever. Deciding the status from that question left the
        order at ``partially_received`` with nothing outstanding and no way out.
        """
        purchase_order = make_po(supplier, operator)
        short_item = make_item("Widget", stock=0, supplier=supplier)
        short_line = add_line(purchase_order, short_item, 10)
        scanned_item = make_item(
            "Gasket", stock=0, supplier=supplier, sku_barcodes={"package_upc": "0123456789012"}
        )
        scanned_line = add_line(purchase_order, scanned_item, 5)

        receive(
            client, purchase_order, [{"purchase_order_item": short_line.pk, "quantity_received": 8}]
        )
        client.post(
            reverse("purchaseorder-close-short", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": short_line.pk, "reason": "backorder cancelled"}]},
            format="json",
        )

        response = scan_barcode(client, purchase_order, "0123456789012", 5)
        assert response.status_code == status.HTTP_200_OK, response.data

        scanned_line.refresh_from_db()
        scanned_item.refresh_from_db()
        assert scanned_line.quantity_received == 5
        assert scanned_item.current_stock == 5

        closed_out = settlement_consistent(purchase_order)
        assert closed_out.status == PurchaseOrder.Status.RECEIVED
        # The shortfall survives the order closing out — that is the point of it.
        assert closed_out.has_receipt_variance is True
        assert response.data["order_status"] == PurchaseOrder.Status.RECEIVED

    def test_scanning_a_closed_short_line_is_refused(self, client, supplier, operator):
        """The written-off balance is not a receiving opportunity.

        Crediting it would walk the line back to ``received`` and erase the
        shortfall the close-short record exists to preserve.
        """
        purchase_order = make_po(supplier, operator)
        item = make_item(
            "Widget", stock=0, supplier=supplier, sku_barcodes={"package_upc": "0123456789012"}
        )
        line = add_line(purchase_order, item, 10)
        add_line(purchase_order, make_item("Gasket", supplier=supplier), 5)

        receive(client, purchase_order, [{"purchase_order_item": line.pk, "quantity_received": 8}])
        client.post(
            reverse("purchaseorder-close-short", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": line.pk, "reason": "backorder cancelled"}]},
            format="json",
        )

        response = scan_barcode(client, purchase_order, "0123456789012", 2)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "reopen-short" in response.data["error"]
        line.refresh_from_db()
        item.refresh_from_db()
        assert line.quantity_received == 8
        assert item.current_stock == 8
        assert line.receipt_state == PurchaseOrderItem.ReceiptState.CLOSED_SHORT

    def test_scanning_a_voided_line_is_refused(self, client, supplier, operator):
        purchase_order = make_po(supplier, operator)
        item = make_item(
            "Widget", stock=0, supplier=supplier, sku_barcodes={"package_upc": "0123456789012"}
        )
        line = add_line(purchase_order, item, 4)
        add_line(purchase_order, make_item("Gasket", supplier=supplier), 5)
        void_line(client, purchase_order, line)

        response = scan_barcode(client, purchase_order, "0123456789012", 4)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "voided" in response.data["error"]
        line.refresh_from_db()
        item.refresh_from_db()
        assert line.quantity_received == 0
        assert item.current_stock == 0


@pytest.mark.django_db
class TestLeadTimeIsLoggedOncePerLine:
    """A line becomes fully received once, so it is logged once.

    Supplier performance counts LeadTimeLog rows and averages their lead times.
    Now that an over-receipt is accepted rather than refused, a second box
    against a line that already landed would otherwise be counted as a second
    delivery of the same line.
    """

    def _sent_po(self, supplier, operator):
        purchase_order = make_po(supplier, operator)
        purchase_order.sent_at = timezone.now() - timedelta(days=5)
        purchase_order.save(update_fields=["sent_at"])
        return purchase_order

    def test_a_line_that_lands_in_full_is_logged_once(self, client, supplier, operator):
        purchase_order = self._sent_po(supplier, operator)
        line = add_line(purchase_order, make_item("Widget", supplier=supplier), 5)

        receive(client, purchase_order, [{"purchase_order_item": line.pk, "quantity_received": 5}])

        assert LeadTimeLog.objects.filter(item_supplier=line.item_supplier).count() == 1

    def test_an_over_receipt_after_the_line_landed_logs_nothing_more(
        self, client, supplier, operator
    ):
        purchase_order = self._sent_po(supplier, operator)
        line = add_line(purchase_order, make_item("Widget", supplier=supplier), 5)
        add_line(purchase_order, make_item("Gasket", supplier=supplier), 3)

        receive(client, purchase_order, [{"purchase_order_item": line.pk, "quantity_received": 5}])
        assert LeadTimeLog.objects.filter(item_supplier=line.item_supplier).count() == 1

        # A second box turns up for a line that already landed. It is recorded
        # as the over-receipt it is — and it is not a second delivery.
        response = receive(
            client, purchase_order, [{"purchase_order_item": line.pk, "quantity_received": 1}]
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        line.refresh_from_db()
        assert line.quantity_received == 6
        assert line.receipt_state == PurchaseOrderItem.ReceiptState.OVER_RECEIVED
        assert LeadTimeLog.objects.filter(item_supplier=line.item_supplier).count() == 1

    def test_one_request_that_over_receives_a_line_twice_logs_once(
        self, client, supplier, operator
    ):
        """Two entries for one line in a single receipt sum past the order."""
        purchase_order = self._sent_po(supplier, operator)
        line = add_line(purchase_order, make_item("Widget", supplier=supplier), 5)

        receive(
            client,
            purchase_order,
            [
                {"purchase_order_item": line.pk, "quantity_received": 5},
                {"purchase_order_item": line.pk, "quantity_received": 1},
            ],
        )

        line.refresh_from_db()
        assert line.quantity_received == 6
        assert LeadTimeLog.objects.filter(item_supplier=line.item_supplier).count() == 1


@pytest.mark.django_db
class TestReceivedMeansGoodsArrived:
    """``received`` is a claim about the world, not about bookkeeping.

    A line can settle three ways that are not deliveries — written off, struck
    off, or an order that had nothing on it — and settlement alone used to
    advance the order. An order reading "Fully Received" over a received
    quantity of zero is the screen stating a falsehood, so the promotion is
    gated on something having actually arrived.
    """

    def test_voiding_the_only_line_of_an_untouched_order_does_not_read_received(
        self, client, supplier, operator
    ):
        """Nothing arrived, so nothing was received — and the PO stays voidable.

        Promoting it to ``received`` also locked the operator out of voiding the
        order at all: ``void`` refuses a received PO and tells them to create a
        return for goods that never came.
        """
        purchase_order = make_po(supplier, operator)
        line = add_line(purchase_order, make_item("Widget", supplier=supplier), 5)

        response = void_line(client, purchase_order, line)
        assert response.status_code == status.HTTP_200_OK, response.data

        purchase_order.refresh_from_db()
        assert purchase_order.status == PurchaseOrder.Status.SENT
        assert purchase_order.total_received_quantity == 0

        # And the way out is still open.
        voided = client.post(
            reverse("purchaseorder-void", args=[purchase_order.pk]),
            {"reason": "nothing ever shipped"},
            format="json",
        )
        assert voided.status_code == status.HTTP_200_OK, voided.data
        purchase_order.refresh_from_db()
        assert purchase_order.status == PurchaseOrder.Status.VOIDED

    def test_voiding_one_of_two_untouched_lines_does_not_read_partially_received(
        self, client, supplier, operator
    ):
        purchase_order = make_po(supplier, operator)
        add_line(purchase_order, make_item("Widget", supplier=supplier), 5)
        struck = add_line(purchase_order, make_item("Gasket", supplier=supplier), 3)

        void_line(client, purchase_order, struck)

        purchase_order.refresh_from_db()
        assert purchase_order.status == PurchaseOrder.Status.SENT

    def test_closing_the_only_line_short_with_nothing_received_does_not_read_received(
        self, client, supplier, operator
    ):
        """The second trigger, reachable with no void anywhere in it.

        Close-short is allowed from ``sent`` and only needs an outstanding
        balance, so an order nobody has received anything against can settle
        every line this way.
        """
        purchase_order = make_po(supplier, operator)
        line = add_line(purchase_order, make_item("Widget", supplier=supplier), 5)

        response = client.post(
            reverse("purchaseorder-close-short", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": line.pk, "reason": "vendor never shipped"}]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.data

        purchase_order.refresh_from_db()
        line.refresh_from_db()
        assert purchase_order.status == PurchaseOrder.Status.SENT
        assert purchase_order.total_received_quantity == 0
        # The write-off itself is still recorded — only the promotion is refused.
        assert line.receipt_state == PurchaseOrderItem.ReceiptState.CLOSED_SHORT
        assert purchase_order.is_settled is True

    def test_mark_received_on_an_order_nothing_arrived_against_names_the_way_out(
        self, client, supplier, operator
    ):
        """Refusing without a way forward would just be the next trap."""
        purchase_order = make_po(supplier, operator)
        line = add_line(purchase_order, make_item("Widget", supplier=supplier), 5)
        client.post(
            reverse("purchaseorder-close-short", args=[purchase_order.pk]),
            {"items": [{"purchase_order_item": line.pk, "reason": "vendor never shipped"}]},
            format="json",
        )

        response = client.post(
            reverse("purchaseorder-mark-received", args=[purchase_order.pk]), {}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        error = response.data["error"]
        assert "Nothing was received against it" in error
        assert "void or cancel the order" in error

        # And that action really is available on this order.
        voided = client.post(
            reverse("purchaseorder-void", args=[purchase_order.pk]),
            {"reason": "vendor never shipped"},
            format="json",
        )
        assert voided.status_code == status.HTTP_200_OK, voided.data

    def test_an_order_that_took_something_in_still_closes_out(self, client, supplier, operator):
        """The gate must not make a genuinely completed order unclosable.

        Each of the three ways the last outstanding line can settle — received,
        closed short, voided — still finishes an order that has taken delivery.
        """
        for settle in ("receive", "close_short", "void"):
            purchase_order = make_po(supplier, operator)
            landed = add_line(purchase_order, make_item(f"Widget {settle}", supplier=supplier), 4)
            last = add_line(purchase_order, make_item(f"Gasket {settle}", supplier=supplier), 6)

            receive(
                client,
                purchase_order,
                [{"purchase_order_item": landed.pk, "quantity_received": 4}],
            )
            assert settlement_consistent(purchase_order).status == (
                PurchaseOrder.Status.PARTIALLY_RECEIVED
            )

            if settle == "receive":
                receive(
                    client,
                    purchase_order,
                    [{"purchase_order_item": last.pk, "quantity_received": 6}],
                )
            elif settle == "close_short":
                client.post(
                    reverse("purchaseorder-close-short", args=[purchase_order.pk]),
                    {"items": [{"purchase_order_item": last.pk, "reason": "not coming"}]},
                    format="json",
                )
            else:
                void_line(client, purchase_order, last)

            assert (
                settlement_consistent(purchase_order).status == PurchaseOrder.Status.RECEIVED
            ), f"an order that received goods failed to close out via {settle}"


@pytest.mark.django_db
class TestDeliveryCompletionReflectsTheWholeRequest:
    """ "Did this delivery finish the order off?" is answered after the request.

    A receipt can settle the last outstanding line by CLOSING IT SHORT rather
    than by filling it, and that closure is applied after the receipt itself.
    Answering before it left the order ``received`` and its delivery flagged
    incomplete — two records of the same event disagreeing.
    """

    def test_a_receipt_whose_close_short_settles_the_order_is_flagged_complete(
        self, client, supplier, operator
    ):
        purchase_order = make_po(supplier, operator)
        line = add_line(purchase_order, make_item("Widget", supplier=supplier), 10)

        response = receive(
            client,
            purchase_order,
            [
                {
                    "purchase_order_item": line.pk,
                    "quantity_received": 8,
                    "close_short": True,
                    "close_short_reason": "backorder cancelled",
                }
            ],
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        purchase_order.refresh_from_db()
        delivery = purchase_order.deliveries.get()

        assert purchase_order.status == PurchaseOrder.Status.RECEIVED
        assert delivery.is_complete is True

    def test_a_receipt_that_leaves_work_outstanding_is_not_flagged_complete(
        self, client, supplier, operator
    ):
        purchase_order = make_po(supplier, operator)
        line = add_line(purchase_order, make_item("Widget", supplier=supplier), 10)
        add_line(purchase_order, make_item("Gasket", supplier=supplier), 5)

        receive(
            client,
            purchase_order,
            [
                {
                    "purchase_order_item": line.pk,
                    "quantity_received": 8,
                    "close_short": True,
                    "close_short_reason": "backorder cancelled",
                }
            ],
        )

        purchase_order.refresh_from_db()
        assert purchase_order.deliveries.get().is_complete is False


@pytest.mark.django_db
class TestAnEmptyOrderClaimsNothing:
    """ "Did everything turn up?" is not vacuously yes.

    ``is_fully_received`` is an ``all()`` over the ACTIVE lines, and an order
    whose lines were every one struck off has none. Answering true there tells a
    report — and any integration reading the field — that goods arrived when the
    order took in nothing at all.
    """

    def test_an_order_with_every_line_voided_does_not_claim_the_goods_arrived(
        self, client, supplier, operator
    ):
        purchase_order = make_po(supplier, operator)
        first = add_line(purchase_order, make_item("Widget", supplier=supplier), 10)
        second = add_line(purchase_order, make_item("Gasket", supplier=supplier), 4)

        void_line(client, purchase_order, first)
        void_line(client, purchase_order, second)

        purchase_order.refresh_from_db()
        assert purchase_order.total_received_quantity == 0
        assert purchase_order.is_fully_received is False
        # Settlement is a different question and stays vacuously true: receiving
        # IS finished with every active line, of which there are none. The
        # status gate built on it is what keeps that from reading `received`.
        assert purchase_order.is_settled is True
        assert purchase_order.status == PurchaseOrder.Status.SENT

    def test_an_order_with_no_lines_at_all_does_not_claim_the_goods_arrived(
        self, client, supplier, operator
    ):
        purchase_order = make_po(supplier, operator)

        payload = worksheet(client, purchase_order).data

        assert payload["is_fully_received"] is False
        assert payload["is_settled"] is True
        assert payload["outstanding_line_count"] == 0

    def test_an_order_that_really_did_receive_everything_still_says_so(
        self, client, supplier, operator
    ):
        """The emptiness check must not swallow the honest true."""
        purchase_order = make_po(supplier, operator)
        line = add_line(purchase_order, make_item("Widget", supplier=supplier), 6)

        receive(client, purchase_order, [{"purchase_order_item": line.pk, "quantity_received": 6}])

        purchase_order.refresh_from_db()
        assert purchase_order.is_fully_received is True
        assert purchase_order.status == PurchaseOrder.Status.RECEIVED
