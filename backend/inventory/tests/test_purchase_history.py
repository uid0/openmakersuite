"""Tests for the ``purchase_history`` endpoint (op-96uo).

``GET /api/inventory/items/<id>/purchase_history/`` exposes an item's order +
delivery provenance for the item-detail screen. Covers:

* ``order_costs`` — every PO line for the item, oldest order first, carrying
  the unit cost that order was placed at.
* ``deliveries`` — one row per delivery of the item, so a partially-shipped
  order surfaces all of its tracking numbers.
* An item that was never ordered gets empty lists and a 200.
* Asset-only and freeform PO lines (no inventory item) are excluded from both
  lists, including when they were received in the same delivery.
* The endpoint is authentication-gated.
"""

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

import pytest
from rest_framework import status
from rest_framework.reverse import reverse

from inventory.tests.factories import AssetFactory, InventoryItemFactory, ItemSupplierFactory
from reorder_queue.models import DeliveryItem, OrderDelivery, PurchaseOrder, PurchaseOrderItem

pytestmark = pytest.mark.django_db


def _url(item):
    return reverse("inventoryitem-purchase-history", kwargs={"pk": str(item.id)})


def _order(user, supplier, po_number, *, ordered_at=None, **kwargs):
    """A ``PurchaseOrder``, optionally backdated.

    ``order_date`` is ``auto_now_add``, so a test that needs orders spread over
    time has to rewrite it after the insert.
    """
    order = PurchaseOrder.objects.create(
        supplier=supplier, created_by=user, po_number=po_number, **kwargs
    )
    if ordered_at is not None:
        PurchaseOrder.objects.filter(pk=order.pk).update(order_date=ordered_at)
        order.refresh_from_db()
    return order


class TestPurchaseHistoryOrderCosts:
    def test_returns_every_order_chronologically_with_its_own_unit_cost(self, authenticated_client):
        client, user = authenticated_client
        item_supplier = ItemSupplierFactory()
        item = item_supplier.item
        now = timezone.now()

        # Created newest-first to prove the response is ordered by order_date,
        # not by insertion order.
        newer = _order(
            user,
            item_supplier.supplier,
            "PO-TEST-0002",
            ordered_at=now - timedelta(days=5),
            status=PurchaseOrder.Status.RECEIVED,
        )
        older = _order(
            user,
            item_supplier.supplier,
            "PO-TEST-0001",
            ordered_at=now - timedelta(days=40),
            status=PurchaseOrder.Status.SENT,
        )
        PurchaseOrderItem.objects.create(
            purchase_order=newer,
            item_supplier=item_supplier,
            quantity_ordered=10,
            unit_cost_ordered=Decimal("4.5000"),
            unit_cost_actual=Decimal("4.7500"),
        )
        PurchaseOrderItem.objects.create(
            purchase_order=older,
            item_supplier=item_supplier,
            quantity_ordered=25,
            unit_cost_ordered=Decimal("3.2500"),
        )

        response = client.get(_url(item))

        assert response.status_code == status.HTTP_200_OK
        order_costs = response.json()["order_costs"]
        assert [row["po_number"] for row in order_costs] == ["PO-TEST-0001", "PO-TEST-0002"]
        assert order_costs[0] == {
            "purchase_order": older.pk,
            "po_number": "PO-TEST-0001",
            "order_date": order_costs[0]["order_date"],
            "status": "sent",
            "quantity_ordered": 25,
            "unit_cost_ordered": "3.2500",
            "unit_cost_actual": None,
        }
        assert order_costs[1]["unit_cost_ordered"] == "4.5000"
        assert order_costs[1]["unit_cost_actual"] == "4.7500"
        assert order_costs[1]["status"] == "received"
        assert order_costs[1]["quantity_ordered"] == 10
        # order_date round-trips as the backdated timestamp, not "now".
        assert order_costs[0]["order_date"].startswith(
            (now - timedelta(days=40)).date().isoformat()
        )

    def test_excludes_orders_for_other_items(self, authenticated_client):
        client, user = authenticated_client
        mine = ItemSupplierFactory()
        theirs = ItemSupplierFactory()
        order = _order(user, mine.supplier, "PO-TEST-0010")
        PurchaseOrderItem.objects.create(
            purchase_order=order,
            item_supplier=mine,
            quantity_ordered=1,
            unit_cost_ordered=Decimal("1.0000"),
        )
        other_order = _order(user, theirs.supplier, "PO-TEST-0011")
        PurchaseOrderItem.objects.create(
            purchase_order=other_order,
            item_supplier=theirs,
            quantity_ordered=1,
            unit_cost_ordered=Decimal("9.0000"),
        )

        response = client.get(_url(mine.item))

        assert response.status_code == status.HTTP_200_OK
        order_costs = response.json()["order_costs"]
        assert [row["po_number"] for row in order_costs] == ["PO-TEST-0010"]


class TestPurchaseHistoryDeliveries:
    def test_one_order_many_tracking_numbers(self, authenticated_client):
        """A partially-shipped order returns one row per shipment."""
        client, user = authenticated_client
        item_supplier = ItemSupplierFactory()
        order = _order(user, item_supplier.supplier, "PO-TEST-0020")
        line = PurchaseOrderItem.objects.create(
            purchase_order=order,
            item_supplier=item_supplier,
            quantity_ordered=30,
            unit_cost_ordered=Decimal("2.0000"),
        )
        now = timezone.now()
        first = OrderDelivery.objects.create(
            purchase_order=order,
            received_by=user,
            delivery_date=now - timedelta(days=3),
            tracking_number="1Z-FIRST",
            carrier="UPS",
            receipt_notes="Partial shipment, 20 of 30.",
            is_complete=False,
        )
        second = OrderDelivery.objects.create(
            purchase_order=order,
            received_by=user,
            delivery_date=now - timedelta(days=1),
            tracking_number="1Z-SECOND",
            carrier="FedEx",
            receipt_notes="Balance of the order.",
            is_complete=True,
        )
        DeliveryItem.objects.create(delivery=second, purchase_order_item=line, quantity_received=10)
        DeliveryItem.objects.create(delivery=first, purchase_order_item=line, quantity_received=20)

        response = client.get(_url(item_supplier.item))

        assert response.status_code == status.HTTP_200_OK
        deliveries = response.json()["deliveries"]
        assert [row["tracking_number"] for row in deliveries] == ["1Z-FIRST", "1Z-SECOND"]
        assert deliveries[0] == {
            "purchase_order": order.pk,
            "po_number": "PO-TEST-0020",
            "delivery_date": deliveries[0]["delivery_date"],
            "tracking_number": "1Z-FIRST",
            "carrier": "UPS",
            "quantity_received": 20,
            "receipt_notes": "Partial shipment, 20 of 30.",
            "is_complete": False,
        }
        assert deliveries[1]["carrier"] == "FedEx"
        assert deliveries[1]["quantity_received"] == 10
        assert deliveries[1]["is_complete"] is True

    def test_excludes_asset_only_and_freeform_lines(self, authenticated_client):
        """Lines with no inventory item must not leak into either list.

        They ride the same PO and the same delivery as the item's own line, so
        this is the shape that would break a join done through the delivery
        rather than through ``item_supplier``.
        """
        client, user = authenticated_client
        item_supplier = ItemSupplierFactory()
        order = _order(user, item_supplier.supplier, "PO-TEST-0030")
        inventory_line = PurchaseOrderItem.objects.create(
            purchase_order=order,
            item_supplier=item_supplier,
            quantity_ordered=4,
            unit_cost_ordered=Decimal("6.0000"),
        )
        asset_line = PurchaseOrderItem.objects.create(
            purchase_order=order,
            asset=AssetFactory(manufacturer=item_supplier.supplier),
            quantity_ordered=1,
            unit_cost_ordered=Decimal("1299.0000"),
        )
        freeform_line = PurchaseOrderItem.objects.create(
            purchase_order=order,
            description="Shipping and handling",
            quantity_ordered=1,
            unit_cost_ordered=Decimal("35.0000"),
        )
        delivery = OrderDelivery.objects.create(
            purchase_order=order, received_by=user, tracking_number="1Z-MIXED"
        )
        for line, quantity in ((inventory_line, 4), (asset_line, 1), (freeform_line, 1)):
            DeliveryItem.objects.create(
                delivery=delivery, purchase_order_item=line, quantity_received=quantity
            )

        response = client.get(_url(item_supplier.item))

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert [row["unit_cost_ordered"] for row in data["order_costs"]] == ["6.0000"]
        assert [row["quantity_received"] for row in data["deliveries"]] == [4]


class TestPurchaseHistoryEmptyAndPermissions:
    def test_never_ordered_item_returns_empty_lists(self, authenticated_client):
        client, _user = authenticated_client
        item = InventoryItemFactory()

        response = client.get(_url(item))

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"order_costs": [], "deliveries": []}

    def test_requires_authentication(self, api_client):
        item = InventoryItemFactory()

        response = api_client.get(_url(item))

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
