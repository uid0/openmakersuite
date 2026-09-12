"""Every receivable-status gate agrees with ``PurchaseOrder.RECEIVABLE_STATUSES``.

``RECEIVABLE_STATUSES`` is the ONE definition of which orders can accept a
delivery. Three sites answered the same question from their own spelling of the
same three statuses instead of reading it:

* ``BarcodeReceiptSerializer.validate_purchase_order_id`` — the scan path;
* the ``pending_orders`` delivery worksheet;
* ``inventory.services.item_metrics.ON_ORDER_STATUSES`` — the QOO figure.

Nothing was wrong while the four spellings matched. The failure they set up is
the drift: add a status to the constant and the receive endpoints accept an
order the scan path refuses, the worksheet never lists, and QOO does not count
— with nothing anywhere reporting the contradiction. The operator at the
scanner, refused against a genuinely open order, would be the first to know.

:class:`TestGatesFollowTheConstant` is the drift guard. It drives EVERY member
of ``PurchaseOrder.Status`` — enumerated from the enum, never hand-listed —
through each gate and asserts the answer is exactly ``status in
RECEIVABLE_STATUSES``. Widen the constant and a gate that kept its own list
refuses the new status while the predicate accepts it, and that gate's test
fails.
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from inventory.models import ItemSupplier
from inventory.services.item_metrics import compute_item_metrics
from inventory.tests.factories import InventoryItemFactory, SupplierFactory
from reorder_queue.models import PurchaseOrder, PurchaseOrderItem
from reorder_queue.serializers import BarcodeReceiptSerializer

User = get_user_model()

#: Every status in the lifecycle, derived from the enum so a status added later
#: is driven through the gates without anyone remembering to list it here.
ALL_STATUSES = list(PurchaseOrder.Status)

ORDERED_QUANTITY = 7


def _expected(status_value):
    """The one answer every gate must give for ``status_value``."""
    return status_value in PurchaseOrder.RECEIVABLE_STATUSES


@pytest.fixture
def operator(db):
    return User.objects.create_user(
        username="receivable-parity-clerk", password="pw", is_staff=True, is_superuser=True
    )


@pytest.fixture
def client(operator):
    api = APIClient()
    api.force_authenticate(user=operator)
    return api


def _order_at(status_value, operator, *, upc="4006381333931"):
    """A one-line purchase order parked at ``status_value``, with its item.

    The status is written with ``update`` rather than ``save`` because adding a
    line re-derives the order's receipt status; the point here is to hold the
    order at each status, including ones the transitions would move it off.
    """
    supplier = SupplierFactory()
    item = InventoryItemFactory(current_stock=0, minimum_stock=0, image=None)
    ItemSupplier.objects.create(
        item=item,
        supplier=supplier,
        supplier_sku=f"SKU-{status_value}",
        package_upc=upc,
        unit_cost=Decimal("10.00"),
        quantity_per_package=1,
        is_primary=True,
    )
    purchase_order = PurchaseOrder.objects.create(
        supplier=supplier,
        status=PurchaseOrder.Status.DRAFT,
        order_date=timezone.now(),
        created_by=operator,
    )
    PurchaseOrderItem.objects.create(
        purchase_order=purchase_order,
        item_supplier=item.item_suppliers.first(),
        quantity_ordered=ORDERED_QUANTITY,
        unit_cost_ordered=Decimal("10.00"),
    )
    PurchaseOrder.objects.filter(pk=purchase_order.pk).update(status=status_value)
    purchase_order.refresh_from_db()
    assert purchase_order.status == status_value
    return purchase_order, item


class TestGatesFollowTheConstant:
    """Each gate's answer IS ``status in RECEIVABLE_STATUSES``, for every status."""

    @pytest.mark.django_db
    @pytest.mark.parametrize("status_value", ALL_STATUSES)
    def test_scan_path_accepts_exactly_the_receivable_statuses(self, status_value, operator):
        """The defect's own path: what the barcode scanner is allowed to receive against."""
        purchase_order, item = _order_at(status_value, operator)
        serializer = BarcodeReceiptSerializer(
            data={
                "purchase_order_id": purchase_order.pk,
                "scanned_upc": purchase_order.items.first().item_supplier.package_upc,
                "quantity_received": 1,
            }
        )
        accepted = serializer.is_valid()
        assert accepted is _expected(status_value), (
            f"scan path {'accepted' if accepted else 'refused'} a {status_value} order; "
            f"RECEIVABLE_STATUSES says {_expected(status_value)} — "
            f"errors={dict(serializer.errors)}"
        )
        if not accepted:
            assert "purchase_order_id" in serializer.errors

    @pytest.mark.django_db
    @pytest.mark.parametrize("status_value", ALL_STATUSES)
    def test_pending_orders_lists_exactly_the_receivable_statuses(
        self, status_value, operator, client
    ):
        """The delivery worksheet: an order the receive path accepts must be listed."""
        purchase_order, _ = _order_at(status_value, operator)
        response = client.get(reverse("orderdelivery-pending-orders"))
        assert response.status_code == 200, response.data
        listed = any(str(row["id"]) == str(purchase_order.pk) for row in response.data)
        assert listed is _expected(status_value), (
            f"pending_orders {'listed' if listed else 'omitted'} a {status_value} order; "
            f"RECEIVABLE_STATUSES says {_expected(status_value)}"
        )

    @pytest.mark.django_db
    @pytest.mark.parametrize("status_value", ALL_STATUSES)
    def test_quantity_on_order_counts_exactly_the_receivable_statuses(self, status_value, operator):
        """QOO: units committed on an order still in flight with the supplier."""
        _, item = _order_at(status_value, operator)
        on_order = compute_item_metrics(item)["quantity_on_order"]
        expected = ORDERED_QUANTITY if _expected(status_value) else 0
        assert on_order == expected, (
            f"quantity_on_order counted {on_order} units on a {status_value} order, "
            f"expected {expected} from RECEIVABLE_STATUSES"
        )
