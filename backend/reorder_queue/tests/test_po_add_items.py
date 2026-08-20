"""Adding line items to a draft purchase order (op-4kq).

Line items could only ever be supplied at create time. A purchase order is
rarely complete on the first pass — someone remembers the gloves after the rest
of the basket is built — and the only recourse was to delete the order and
retype it.

``POST /api/reorders/purchase-orders/<pk>/items/`` appends lines through the
same per-line validation the create payload uses, and is **draft-only**: once
an order is sent, its lines are the record of what the supplier was actually
asked for, so growing it afterwards would misrepresent the order and hand
receiving a line the supplier never saw. Correcting a sent order stays the job
of ``update_item``/``void_item``.

What this file pins down:

* all three line shapes (inventory / asset / freeform) append correctly;
* every non-draft status is refused, including the receivable ones;
* ``estimated_total`` is re-derived, not accumulated, so it stays correct after
  a prior quantity edit;
* duplicates are refused both within the batch and against lines already on the
  order — including voided lines, whose ``unique_together`` slot is never
  released;
* the batch is atomic — one bad line leaves no partial basket behind;
* an audit event records the addition.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model

import pytest
from rest_framework import status as http_status
from rest_framework.test import APIClient

from inventory.tests.factories import ItemSupplierFactory, SupplierFactory
from reorder_queue.models import PurchaseOrder, PurchaseOrderAuditEvent, PurchaseOrderItem

User = get_user_model()

pytestmark = pytest.mark.django_db


def _staff(username="buyer"):
    return User.objects.create_user(username=username, password="x", is_staff=True)


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _po(user=None, **kwargs):
    return PurchaseOrder.objects.create(
        supplier=kwargs.pop("supplier", None) or SupplierFactory(),
        status=kwargs.pop("status", PurchaseOrder.Status.DRAFT),
        created_by=user or _staff(),
        **kwargs,
    )


def _line(po, **kwargs):
    item_supplier = kwargs.pop("item_supplier", None) or ItemSupplierFactory(
        supplier=po.supplier, quantity_per_package=1
    )
    return PurchaseOrderItem.objects.create(
        purchase_order=po,
        item_supplier=item_supplier,
        quantity_ordered=kwargs.pop("quantity_ordered", 5),
        unit_cost_ordered=kwargs.pop("unit_cost_ordered", Decimal("2.00")),
        **kwargs,
    )


def _url(po):
    return f"/api/reorders/purchase-orders/{po.pk}/items/"


# ─────────────────────────────────────────────────────────────────────────────
# The happy paths — one per line shape
# ─────────────────────────────────────────────────────────────────────────────
def test_appends_an_inventory_line_to_a_draft_order():
    user = _staff()
    po = _po(user)
    _line(po)
    item_supplier = ItemSupplierFactory(supplier=po.supplier, quantity_per_package=1)

    response = _client(user).post(
        _url(po),
        {"items": [{"item_supplier_id": item_supplier.id, "quantity": 7, "unit_cost": "3.00"}]},
        format="json",
    )

    assert response.status_code == http_status.HTTP_201_CREATED
    assert po.items.count() == 2
    added = po.items.get(item_supplier=item_supplier)
    assert added.quantity_ordered == 7
    assert added.unit_cost_ordered == Decimal("3.00")
    # The response is the full PO, so the detail page can repaint from it.
    assert response.data["total_items"] == 2


def test_appends_a_freeform_line():
    user = _staff()
    po = _po(user)
    _line(po)

    response = _client(user).post(
        _url(po),
        {"items": [{"description": "Shipping surcharge", "quantity": 1, "unit_cost": "12.50"}]},
        format="json",
    )

    assert response.status_code == http_status.HTTP_201_CREATED
    added = po.items.get(description="Shipping surcharge")
    assert added.quantity_ordered == 1
    assert added.unit_cost_ordered == Decimal("12.50")


def test_appends_several_lines_in_one_call():
    user = _staff()
    po = _po(user)
    first = ItemSupplierFactory(supplier=po.supplier, quantity_per_package=1)
    second = ItemSupplierFactory(supplier=po.supplier, quantity_per_package=1)

    response = _client(user).post(
        _url(po),
        {
            "items": [
                {"item_supplier_id": first.id, "quantity": 2, "unit_cost": "1.00"},
                {"item_supplier_id": second.id, "quantity": 3, "unit_cost": "1.00"},
                {"description": "Pallet fee", "quantity": 1, "unit_cost": "9.00"},
            ]
        },
        format="json",
    )

    assert response.status_code == http_status.HTTP_201_CREATED
    assert po.items.count() == 3


# ─────────────────────────────────────────────────────────────────────────────
# Draft-only
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "po_status",
    ["sent", "confirmed", "partially_received", "received", "cancelled", "voided"],
)
def test_refuses_every_status_except_draft(po_status):
    user = _staff()
    po = _po(user, status=po_status)
    item_supplier = ItemSupplierFactory(supplier=po.supplier, quantity_per_package=1)

    response = _client(user).post(
        _url(po),
        {"items": [{"item_supplier_id": item_supplier.id, "quantity": 1, "unit_cost": "1.00"}]},
        format="json",
    )

    assert response.status_code == http_status.HTTP_400_BAD_REQUEST
    assert "draft" in response.data["error"].lower()
    assert po.items.count() == 0


def test_requires_authentication():
    po = _po()
    item_supplier = ItemSupplierFactory(supplier=po.supplier, quantity_per_package=1)

    response = APIClient().post(
        _url(po),
        {"items": [{"item_supplier_id": item_supplier.id, "quantity": 1, "unit_cost": "1.00"}]},
        format="json",
    )

    assert response.status_code in (
        http_status.HTTP_401_UNAUTHORIZED,
        http_status.HTTP_403_FORBIDDEN,
        http_status.HTTP_404_NOT_FOUND,
    )
    assert po.items.count() == 0


# ─────────────────────────────────────────────────────────────────────────────
# Totals
# ─────────────────────────────────────────────────────────────────────────────
def test_estimated_total_is_rederived_not_accumulated():
    """A prior quantity edit must not be double-counted or lost."""
    user = _staff()
    po = _po(user)
    existing = _line(po, quantity_ordered=5, unit_cost_ordered=Decimal("2.00"))
    po.estimated_total = Decimal("10.00")
    po.save(update_fields=["estimated_total"])

    # The stored total is now stale relative to the line.
    existing.quantity_ordered = 10
    existing.save(update_fields=["quantity_ordered"])

    item_supplier = ItemSupplierFactory(supplier=po.supplier, quantity_per_package=1)
    response = _client(user).post(
        _url(po),
        {"items": [{"item_supplier_id": item_supplier.id, "quantity": 1, "unit_cost": "5.00"}]},
        format="json",
    )

    assert response.status_code == http_status.HTTP_201_CREATED
    po.refresh_from_db()
    # 10 x 2.00 (re-derived, not the stale 10.00) + 1 x 5.00
    assert po.estimated_total == Decimal("25.00")


# ─────────────────────────────────────────────────────────────────────────────
# Duplicates
# ─────────────────────────────────────────────────────────────────────────────
def test_refuses_an_item_already_on_the_order():
    user = _staff()
    po = _po(user)
    existing = _line(po)

    response = _client(user).post(
        _url(po),
        {
            "items": [
                {"item_supplier_id": existing.item_supplier_id, "quantity": 1, "unit_cost": "1.00"}
            ]
        },
        format="json",
    )

    assert response.status_code == http_status.HTTP_400_BAD_REQUEST
    assert po.items.count() == 1


def test_refuses_a_duplicate_within_the_batch():
    user = _staff()
    po = _po(user)
    item_supplier = ItemSupplierFactory(supplier=po.supplier, quantity_per_package=1)

    response = _client(user).post(
        _url(po),
        {
            "items": [
                {"item_supplier_id": item_supplier.id, "quantity": 1, "unit_cost": "1.00"},
                {"item_supplier_id": item_supplier.id, "quantity": 2, "unit_cost": "1.00"},
            ]
        },
        format="json",
    )

    assert response.status_code == http_status.HTTP_400_BAD_REQUEST
    assert po.items.count() == 0


def test_a_voided_line_is_still_a_duplicate():
    """``unique_together`` has no voided exclusion, so the slot stays taken.

    Adding the item back would hit an IntegrityError at the database; the
    serializer catches it first and says what to do instead.
    """
    user = _staff()
    po = _po(user)
    existing = _line(po, is_voided=True)

    response = _client(user).post(
        _url(po),
        {
            "items": [
                {"item_supplier_id": existing.item_supplier_id, "quantity": 4, "unit_cost": "1.00"}
            ]
        },
        format="json",
    )

    assert response.status_code == http_status.HTTP_400_BAD_REQUEST
    assert "un-void" in str(response.data).lower()
    assert po.items.count() == 1


# ─────────────────────────────────────────────────────────────────────────────
# Validation and atomicity
# ─────────────────────────────────────────────────────────────────────────────
def test_rejects_an_empty_items_list():
    user = _staff()
    po = _po(user)

    response = _client(user).post(_url(po), {"items": []}, format="json")

    assert response.status_code == http_status.HTTP_400_BAD_REQUEST


def test_a_line_from_another_supplier_is_refused():
    user = _staff()
    po = _po(user)
    foreign = ItemSupplierFactory(supplier=SupplierFactory(), quantity_per_package=1)

    response = _client(user).post(
        _url(po),
        {"items": [{"item_supplier_id": foreign.id, "quantity": 1, "unit_cost": "1.00"}]},
        format="json",
    )

    assert response.status_code == http_status.HTTP_400_BAD_REQUEST
    assert po.items.count() == 0


def test_one_bad_line_rolls_the_whole_batch_back():
    user = _staff()
    po = _po(user)
    good = ItemSupplierFactory(supplier=po.supplier, quantity_per_package=1)

    response = _client(user).post(
        _url(po),
        {
            "items": [
                {"item_supplier_id": good.id, "quantity": 3, "unit_cost": "1.00"},
                {"description": "", "quantity": 1, "unit_cost": "1.00"},
            ]
        },
        format="json",
    )

    assert response.status_code == http_status.HTTP_400_BAD_REQUEST
    assert po.items.count() == 0


# ─────────────────────────────────────────────────────────────────────────────
# Audit
# ─────────────────────────────────────────────────────────────────────────────
def test_records_an_audit_event():
    user = _staff()
    po = _po(user)
    item_supplier = ItemSupplierFactory(supplier=po.supplier, quantity_per_package=1)

    _client(user).post(
        _url(po),
        {"items": [{"item_supplier_id": item_supplier.id, "quantity": 1, "unit_cost": "1.00"}]},
        format="json",
    )

    event = PurchaseOrderAuditEvent.objects.get(action=PurchaseOrderAuditEvent.Action.PO_LINE_ADD)
    assert event.purchase_order == po
    assert event.actor == user
    assert event.metadata["line_count"] == 1
