"""Receiving a purchase order retires the reorder request behind it (op-hjz3).

Somebody scans a shelf label, a reorder request appears, and the request is the
thing they watch. Sending the PO already moves that request to *ordered*
(:func:`services.update_reorder_requests_from_po`); receiving the goods used to
leave it sitting in the queue until an admin separately hit its
``mark_received`` action. Fully receiving the line now closes it.

What this file pins down:

* full receipt of a line closes the item's active request — status, delivery
  date, and an audit note saying which PO closed it;
* **stock still moves exactly once**: the receipt posts it, the auto-close is
  bookkeeping only (``mark_received`` increments stock because it has no
  receipt behind it; doing both would double-count the delivery);
* a partial receipt leaves the request open — the parts are not all here yet;
* every receive path does it: ``receive``, ``mark-delivered`` and the
  barcode scan (which is a separate inline path, not routed through
  :func:`services.receive_delivery`);
* nothing else is disturbed: asset-only lines, items with no request, and
  already-received or cancelled requests.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from inventory.tests.factories import AssetFactory, ItemSupplierFactory, SupplierFactory
from reorder_queue import services
from reorder_queue.models import PurchaseOrder, PurchaseOrderItem, ReorderRequest

User = get_user_model()

pytestmark = pytest.mark.django_db


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _staff():
    return User.objects.create_user(username="storekeeper", password="x", is_staff=True)


def _staff_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _sent_po_with_line(user, *, qty=10, stock=0):
    """A SENT PO carrying one inventory line, and the item it restocks."""
    supplier = SupplierFactory()
    po = PurchaseOrder.objects.create(
        supplier=supplier,
        status=PurchaseOrder.Status.SENT,
        created_by=user,
        sent_at=timezone.now(),
    )
    # quantity_per_package=1 pins the derived unit cost (the ItemSupplier
    # save() derivation) so the line costs exactly what this asks for.
    item_supplier = ItemSupplierFactory(
        supplier=supplier,
        unit_cost=Decimal("2.00"),
        quantity_per_package=1,
        average_lead_time=5,
    )
    item = item_supplier.item
    item.current_stock = stock
    item.save()
    line = PurchaseOrderItem.objects.create(
        purchase_order=po,
        item_supplier=item_supplier,
        quantity_ordered=qty,
        unit_cost_ordered=Decimal("2.00"),
    )
    return po, line, item


def _request_for(item, *, status_=ReorderRequest.Status.ORDERED, quantity=7):
    return ReorderRequest.objects.create(
        item=item,
        quantity=quantity,
        status=status_,
        requested_by="member",
    )


def _receive(client, po, line, quantity):
    """Drive the per-line ``receive`` action."""
    return client.post(
        f"/api/reorders/purchase-orders/{po.id}/receive/",
        {"items": [{"purchase_order_item": line.id, "quantity_received": quantity}]},
        format="json",
    )


# ─────────────────────────────────────────────────────────────────────────────
# The close itself — and the stock it must not touch
# ─────────────────────────────────────────────────────────────────────────────
def test_full_receipt_closes_the_request_and_moves_stock_exactly_once():
    """The headline: parts land, the request is Received, stock counts once."""
    user = _staff()
    client = _staff_client(user)
    po, line, item = _sent_po_with_line(user, qty=10, stock=3)
    request = _request_for(item, quantity=7)

    response = _receive(client, po, line, 10)
    assert response.status_code == status.HTTP_200_OK

    request.refresh_from_db()
    item.refresh_from_db()
    assert request.status == ReorderRequest.Status.RECEIVED
    assert request.actual_delivery == timezone.now().date()
    # 3 + 10 received. NOT 3 + 10 + the request's own 7: the auto-close is
    # bookkeeping, the receipt already posted the stock.
    assert item.current_stock == 13


def test_close_records_which_purchase_order_closed_it():
    """An auto-closed request says so, so nobody hunts for who received it."""
    user = _staff()
    client = _staff_client(user)
    po, line, item = _sent_po_with_line(user, qty=4)
    request = _request_for(item)
    request.admin_notes = "Rush order."
    request.save(update_fields=["admin_notes"])

    _receive(client, po, line, 4)

    request.refresh_from_db()
    assert "Rush order." in request.admin_notes
    assert f"Auto-received via PO {po.po_number}" in request.admin_notes


def test_partial_receipt_leaves_the_request_open():
    """Half a delivery is not a delivery — the request stays in the queue."""
    user = _staff()
    client = _staff_client(user)
    po, line, item = _sent_po_with_line(user, qty=10)
    request = _request_for(item)

    response = _receive(client, po, line, 4)
    assert response.status_code == status.HTTP_200_OK

    request.refresh_from_db()
    item.refresh_from_db()
    assert request.status == ReorderRequest.Status.ORDERED
    assert request.actual_delivery is None
    assert item.current_stock == 4


def test_the_rest_of_a_partial_receipt_closes_the_request():
    """4 then 6 of 10: the line completes, so the request completes with it."""
    user = _staff()
    client = _staff_client(user)
    po, line, item = _sent_po_with_line(user, qty=10)
    request = _request_for(item)

    _receive(client, po, line, 4)
    _receive(client, po, line, 6)

    request.refresh_from_db()
    item.refresh_from_db()
    assert request.status == ReorderRequest.Status.RECEIVED
    assert item.current_stock == 10


def test_a_pending_request_is_closed_too():
    """Not every request gets formally ordered first; the parts still arrived."""
    user = _staff()
    client = _staff_client(user)
    po, line, item = _sent_po_with_line(user, qty=2)
    request = _request_for(item, status_=ReorderRequest.Status.PENDING)

    _receive(client, po, line, 2)

    request.refresh_from_db()
    assert request.status == ReorderRequest.Status.RECEIVED


# ─────────────────────────────────────────────────────────────────────────────
# Every receive path does it
# ─────────────────────────────────────────────────────────────────────────────
def test_mark_delivered_closes_the_request():
    """The whole-PO path receives every pending quantity — same close."""
    user = _staff()
    client = _staff_client(user)
    po, line, item = _sent_po_with_line(user, qty=5)
    request = _request_for(item)

    response = client.post(
        f"/api/reorders/purchase-orders/{po.id}/mark-delivered/",
        {"delivery_date": timezone.now().date().isoformat()},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK

    request.refresh_from_db()
    item.refresh_from_db()
    assert request.status == ReorderRequest.Status.RECEIVED
    assert request.actual_delivery == timezone.now().date()
    assert item.current_stock == 5


def test_scan_barcode_closes_the_request():
    """Receiving by scanning the carton is still receiving.

    ``OrderReceiptViewSet.scan_barcode`` is a separate *inline* receive path
    that does not call :func:`services.receive_delivery` (see the note on that
    action), so it shares the close explicitly — exactly as it already shares
    the lead-time log.
    """
    user = _staff()
    client = _staff_client(user)
    po, line, item = _sent_po_with_line(user, qty=6)
    request = _request_for(item)

    response = client.post(
        "/api/reorders/receipts/scan_barcode/",
        {
            "purchase_order_id": po.id,
            "scanned_upc": line.item_supplier.package_upc,
            "quantity_received": 6,
        },
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK

    request.refresh_from_db()
    item.refresh_from_db()
    assert request.status == ReorderRequest.Status.RECEIVED
    assert item.current_stock == 6


# ─────────────────────────────────────────────────────────────────────────────
# Everything it must leave alone
# ─────────────────────────────────────────────────────────────────────────────
def test_receipt_for_an_item_with_no_request_is_a_no_op():
    """Most receiving has no reorder request behind it at all."""
    user = _staff()
    client = _staff_client(user)
    po, line, item = _sent_po_with_line(user, qty=3)

    response = _receive(client, po, line, 3)

    assert response.status_code == status.HTTP_200_OK
    item.refresh_from_db()
    assert item.current_stock == 3
    assert not ReorderRequest.objects.exists()


def test_asset_only_line_receives_without_a_crash():
    """An asset line has no inventory item to match a request against."""
    user = _staff()
    client = _staff_client(user)
    po, _line, _item = _sent_po_with_line(user, qty=1)
    asset_line = PurchaseOrderItem.objects.create(
        purchase_order=po,
        asset=AssetFactory(),
        quantity_ordered=1,
        unit_cost_ordered=Decimal("50.00"),
    )

    response = _receive(client, po, asset_line, 1)

    assert response.status_code == status.HTTP_200_OK
    asset_line.refresh_from_db()
    assert asset_line.is_fully_received


def test_already_received_request_is_untouched():
    """A closed request is history — a later receipt does not restamp it."""
    user = _staff()
    client = _staff_client(user)
    po, line, item = _sent_po_with_line(user, qty=2)
    old_date = timezone.now().date() - timedelta(days=30)
    request = _request_for(item, status_=ReorderRequest.Status.RECEIVED)
    request.actual_delivery = old_date
    request.save(update_fields=["actual_delivery"])

    _receive(client, po, line, 2)

    request.refresh_from_db()
    assert request.status == ReorderRequest.Status.RECEIVED
    assert request.actual_delivery == old_date
    assert "Auto-received" not in request.admin_notes


def test_cancelled_request_is_untouched():
    """Cancelled means somebody decided against it; receiving stock is not a veto."""
    user = _staff()
    client = _staff_client(user)
    po, line, item = _sent_po_with_line(user, qty=2)
    request = _request_for(item, status_=ReorderRequest.Status.CANCELLED)

    _receive(client, po, line, 2)

    request.refresh_from_db()
    assert request.status == ReorderRequest.Status.CANCELLED
    assert request.actual_delivery is None


def test_all_active_requests_for_the_item_are_closed():
    """One receipt, every open request for the item.

    ``update_reorder_requests_from_po`` marks *every* active request for an item
    as ordered when the PO goes out, so several can be open at once. Receiving
    the line closes all of them — the delivery satisfies the item's outstanding
    demand, so nothing is left dangling in the queue.
    """
    user = _staff()
    client = _staff_client(user)
    po, line, item = _sent_po_with_line(user, qty=2)
    older = _request_for(item)
    newer = _request_for(item)

    _receive(client, po, line, 2)

    older.refresh_from_db()
    newer.refresh_from_db()
    assert newer.status == ReorderRequest.Status.RECEIVED
    assert older.status == ReorderRequest.Status.RECEIVED


def test_closing_twice_is_idempotent():
    """Re-driving the same close finds nothing active and writes nothing."""
    user = _staff()
    po, line, item = _sent_po_with_line(user, qty=2)
    request = _request_for(item)

    first = services.close_linked_reorder_request(line, timezone.now())
    second = services.close_linked_reorder_request(line, timezone.now())

    request.refresh_from_db()
    assert first == [request]
    assert second == []
    assert request.admin_notes.count("Auto-received") == 1
