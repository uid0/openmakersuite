"""Purchasing + receiving through the item pack chain (op-ev14, phase 2b).

The item's own packaging chain and the supplier's case size are two different
things that meet on a purchase-order line, and this module pins the
reconciliation between them:

* ``quantity_ordered`` is always BASE units, but a caller may express it in the
  item's count unit with ``at_level`` on the line ("order 4 cases").
* ``order_in_packages`` prefers the **supplier's** case size — you buy what the
  vendor ships — and falls back to the **item's** outermost rung only when the
  supplier declares none. Before op-ev14 that fallback recorded the base-unit
  count, which said nothing.
* A receipt may likewise be reported in packs; the stock add stays base units.

An item in ``count_mode=each`` — every item that exists today — behaves exactly
as it did, which the first class pins.

The on-hand / usage / container half lives in
``inventory/tests/test_stock_transactions_at_level.py``.
"""

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

import pytest
from rest_framework import status
from rest_framework.reverse import reverse

from inventory.models import InventoryItem, PackagingLevel
from inventory.tests.factories import (
    InventoryItemFactory,
    ItemSupplierFactory,
    SupplierFactory,
)
from reorder_queue.models import PurchaseOrder, PurchaseOrderItem
from reorder_queue.services.purchase_orders import order_packages_for_line

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def _pack_item(mode=InventoryItem.CountMode.BY_LEVEL, case_size=12, **kwargs):
    """An item counted in whole cases of ``case_size`` base units ("bottles")."""
    kwargs.setdefault("image", None)
    kwargs.setdefault("base_unit", "bottle")
    item = InventoryItemFactory(**kwargs)
    case = PackagingLevel.objects.create(item=item, name="case", sort_order=0, base_units=case_size)
    PackagingLevel.objects.create(item=item, name="bottle", sort_order=1, base_units=1)
    item.count_mode = mode
    item.count_level = None if mode == InventoryItem.CountMode.EACH else case
    item.save(update_fields=["count_mode", "count_level"])
    return item


def _create_po(client, supplier, line):
    return client.post(
        reverse("purchaseorder-list"),
        {"supplier": supplier.id, "items": [line]},
        format="json",
    )


def _sent_po_with_line(user, item, *, quantity_ordered, quantity_per_package=1):
    """A SENT PO with one inventory line for ``item``."""
    supplier = SupplierFactory()
    item_supplier = ItemSupplierFactory(
        supplier=supplier,
        item=item,
        quantity_per_package=quantity_per_package,
        average_lead_time=10,
        # ItemSupplier.save() re-derives unit_cost from package_cost, so the
        # pinned cost only survives with package_cost cleared.
        unit_cost=Decimal("2.50"),
        package_cost=None,
    )
    purchase_order = PurchaseOrder.objects.create(
        supplier=supplier,
        status=PurchaseOrder.Status.SENT,
        created_by=user,
        sent_by=user,
        sent_at=timezone.now() - timedelta(days=7),
        estimated_total=Decimal("50.00"),
    )
    po_item = PurchaseOrderItem.objects.create(
        purchase_order=purchase_order,
        item_supplier=item_supplier,
        quantity_ordered=quantity_ordered,
        unit_cost_ordered=Decimal("2.50"),
        order_in_packages=quantity_ordered,
    )
    return purchase_order, po_item


class TestEachItemsAreUntouched:
    """The regression guard for the ordering + receiving paths."""

    def test_order_in_packages_still_ceil_divides_the_supplier_case(self, authenticated_client):
        client, _ = authenticated_client
        item_supplier = ItemSupplierFactory(
            quantity_per_package=12,
            unit_cost=Decimal("2.50"),
            package_cost=None,
            item=InventoryItemFactory(),
        )

        response = _create_po(
            client,
            item_supplier.supplier,
            {"item_supplier_id": item_supplier.id, "quantity": 25},
        )

        assert response.status_code == status.HTTP_201_CREATED
        line = PurchaseOrder.objects.get(id=response.data["id"]).items.first()
        assert line.quantity_ordered == 25
        assert line.order_in_packages == 3  # ceil(25 / 12)

    def test_no_supplier_case_size_still_records_the_base_count(self, authenticated_client):
        """An ``each`` item with no chain has nothing else to count in."""
        client, _ = authenticated_client
        item_supplier = ItemSupplierFactory(
            quantity_per_package=1,
            unit_cost=Decimal("2.50"),
            package_cost=None,
            item=InventoryItemFactory(),
        )

        response = _create_po(
            client,
            item_supplier.supplier,
            {"item_supplier_id": item_supplier.id, "quantity": 7},
        )

        assert response.status_code == status.HTTP_201_CREATED
        line = PurchaseOrder.objects.get(id=response.data["id"]).items.first()
        assert (line.quantity_ordered, line.order_in_packages) == (7, 7)

    def test_at_level_on_an_each_item_is_rejected(self, authenticated_client):
        client, _ = authenticated_client
        item_supplier = ItemSupplierFactory(
            quantity_per_package=12,
            unit_cost=Decimal("2.50"),
            package_cost=None,
            item=InventoryItemFactory(),
        )

        response = _create_po(
            client,
            item_supplier.supplier,
            {"item_supplier_id": item_supplier.id, "quantity": 2, "at_level": True},
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not PurchaseOrder.objects.exists()

    def test_receive_without_the_flag_adds_base_units(self, authenticated_client):
        client, user = authenticated_client
        item = InventoryItemFactory(current_stock=10)
        purchase_order, po_item = _sent_po_with_line(user, item, quantity_ordered=5)

        response = client.post(
            reverse("purchaseorder-receive", kwargs={"pk": purchase_order.pk}),
            {"items": [{"purchase_order_item": po_item.id, "quantity_received": 2}]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        item.refresh_from_db()
        po_item.refresh_from_db()
        assert item.current_stock == 12
        assert po_item.quantity_received == 2


class TestOrderPackagesReconciliation:
    """Supplier case size wins; the item's outermost rung is the fallback."""

    def test_supplier_case_wins_over_the_item_chain(self):
        """A supplier who sells 6-packs of a case-counted item: 6 is the divisor."""
        item = _pack_item(case_size=12, current_stock=0)
        item_supplier = ItemSupplierFactory(
            item=item, quantity_per_package=6, unit_cost=Decimal("1.00"), package_cost=None
        )

        # 24 bottles = 4 supplier six-packs (and 2 of the item's own cases).
        assert order_packages_for_line(item_supplier, 24) == 4

    def test_item_chain_fills_in_when_the_supplier_declares_none(self):
        item = _pack_item(case_size=12, current_stock=0)
        item_supplier = ItemSupplierFactory(
            item=item, quantity_per_package=1, unit_cost=Decimal("1.00"), package_cost=None
        )

        assert order_packages_for_line(item_supplier, 24) == 2
        # Partial packs round up — the line still ships whole cases.
        assert order_packages_for_line(item_supplier, 25) == 3

    def test_item_chain_is_not_consulted_for_an_each_item(self):
        """An ``each`` item with a chain is still counted in base units."""
        item = _pack_item(mode=InventoryItem.CountMode.EACH, case_size=12, current_stock=0)
        item_supplier = ItemSupplierFactory(
            item=item, quantity_per_package=1, unit_cost=Decimal("1.00"), package_cost=None
        )

        assert order_packages_for_line(item_supplier, 24) == 24


class TestOrderAtCountLevel:
    """``at_level`` on a line: order in the item's count unit."""

    def test_pack_count_becomes_base_units(self, authenticated_client):
        client, _ = authenticated_client
        item = _pack_item(case_size=12, current_stock=0)
        item_supplier = ItemSupplierFactory(
            item=item, quantity_per_package=12, unit_cost=Decimal("2.00"), package_cost=None
        )

        response = _create_po(
            client,
            item_supplier.supplier,
            {"item_supplier_id": item_supplier.id, "quantity": 4, "at_level": True},
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        purchase_order = PurchaseOrder.objects.get(id=response.data["id"])
        line = purchase_order.items.first()
        assert line.quantity_ordered == 48  # 4 cases of 12
        assert line.order_in_packages == 4
        # The PO total is derived from the BASE quantity, not the pack count.
        assert purchase_order.estimated_total == Decimal("96.00")

    def test_pack_count_with_no_supplier_case_size(self, authenticated_client):
        """The item's own rung shapes both numbers when the vendor declares none."""
        client, _ = authenticated_client
        item = _pack_item(case_size=12, current_stock=0)
        item_supplier = ItemSupplierFactory(
            item=item, quantity_per_package=1, unit_cost=Decimal("2.00"), package_cost=None
        )

        response = _create_po(
            client,
            item_supplier.supplier,
            {"item_supplier_id": item_supplier.id, "quantity": 3, "at_level": True},
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        line = PurchaseOrder.objects.get(id=response.data["id"]).items.first()
        assert (line.quantity_ordered, line.order_in_packages) == (36, 3)

    def test_explicit_order_in_packages_still_wins(self, authenticated_client):
        """The frontend's whole-case path is untouched by the conversion."""
        client, _ = authenticated_client
        item = _pack_item(case_size=12, current_stock=0)
        item_supplier = ItemSupplierFactory(
            item=item, quantity_per_package=24, unit_cost=Decimal("2.00"), package_cost=None
        )

        response = _create_po(
            client,
            item_supplier.supplier,
            {
                "item_supplier_id": item_supplier.id,
                "quantity": 4,
                "at_level": True,
                "order_in_packages": 2,
            },
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        line = PurchaseOrder.objects.get(id=response.data["id"]).items.first()
        assert (line.quantity_ordered, line.order_in_packages) == (48, 2)

    def test_line_edit_at_level_converts_and_rerolls_the_total(self, authenticated_client):
        """ "Make it 3 cases" on an order already with the supplier."""
        client, user = authenticated_client
        item = _pack_item(case_size=12, current_stock=0)
        purchase_order, po_item = _sent_po_with_line(
            user, item, quantity_ordered=24, quantity_per_package=12
        )

        response = client.patch(
            f"/api/reorders/purchase-orders/{purchase_order.id}/items/{po_item.id}/",
            {"quantity_ordered": 3, "at_level": True},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        po_item.refresh_from_db()
        purchase_order.refresh_from_db()
        assert po_item.quantity_ordered == 36
        assert po_item.order_in_packages == 3
        assert purchase_order.estimated_total == Decimal("90.00")  # 36 * 2.50

    def test_line_edit_without_the_flag_is_unchanged(self, authenticated_client):
        client, user = authenticated_client
        item = _pack_item(case_size=12, current_stock=0)
        purchase_order, po_item = _sent_po_with_line(
            user, item, quantity_ordered=24, quantity_per_package=12
        )

        response = client.patch(
            f"/api/reorders/purchase-orders/{purchase_order.id}/items/{po_item.id}/",
            {"quantity_ordered": 36},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        po_item.refresh_from_db()
        assert po_item.quantity_ordered == 36


class TestReceiveAtCountLevel:
    """ "Three cases came in" — converted before the pending check and the add."""

    def _receive(self, client, purchase_order, payload):
        return client.post(
            reverse("purchaseorder-receive", kwargs={"pk": purchase_order.pk}),
            {"items": [payload]},
            format="json",
        )

    def test_pack_count_adds_the_right_base_units(self, authenticated_client):
        client, user = authenticated_client
        item = _pack_item(case_size=12, current_stock=6)
        purchase_order, po_item = _sent_po_with_line(
            user, item, quantity_ordered=48, quantity_per_package=12
        )

        response = self._receive(
            client,
            purchase_order,
            {"purchase_order_item": po_item.id, "quantity_received": 3, "at_level": True},
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        item.refresh_from_db()
        po_item.refresh_from_db()
        assert item.current_stock == 42  # 6 + 3 cases of 12
        assert po_item.quantity_received == 36
        assert purchase_order.deliveries.first().items.first().quantity_received == 36

    def test_full_pack_receipt_completes_the_line(self, authenticated_client):
        client, user = authenticated_client
        item = _pack_item(case_size=12, current_stock=0)
        purchase_order, po_item = _sent_po_with_line(
            user, item, quantity_ordered=36, quantity_per_package=12
        )

        response = self._receive(
            client,
            purchase_order,
            {"purchase_order_item": po_item.id, "quantity_received": 3, "at_level": True},
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        purchase_order.refresh_from_db()
        po_item.refresh_from_db()
        assert po_item.is_fully_received
        assert purchase_order.status == PurchaseOrder.Status.RECEIVED

    def test_over_receiving_is_measured_on_the_converted_quantity(self, authenticated_client):
        """4 cases against a 36-bottle order is 48 bottles, +12 — not 4.

        The pack conversion still happens before anything judges the figure
        (op-ev14); what changed is the verdict. An over-receipt is now recorded
        rather than refused, so the thing to prove is that the recorded
        quantity and the flagged variance are both in BASE units. A conversion
        applied after the variance was computed would report +12 as -32.
        """
        client, user = authenticated_client
        item = _pack_item(case_size=12, current_stock=0)
        purchase_order, po_item = _sent_po_with_line(
            user, item, quantity_ordered=36, quantity_per_package=12
        )

        response = self._receive(
            client,
            purchase_order,
            {"purchase_order_item": po_item.id, "quantity_received": 4, "at_level": True},
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        po_item.refresh_from_db()
        item.refresh_from_db()
        assert po_item.quantity_received == 48
        assert po_item.quantity_variance == 12
        assert po_item.is_over_received
        assert item.current_stock == 48

    def test_at_level_on_an_each_item_is_rejected(self, authenticated_client):
        client, user = authenticated_client
        item = InventoryItemFactory(current_stock=0)
        purchase_order, po_item = _sent_po_with_line(user, item, quantity_ordered=36)

        response = self._receive(
            client,
            purchase_order,
            {"purchase_order_item": po_item.id, "quantity_received": 3, "at_level": True},
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "not counted in packs" in response.data["error"]
        item.refresh_from_db()
        assert item.current_stock == 0

    def test_at_level_on_a_freeform_line_is_rejected(self, authenticated_client):
        """A line with no inventory item has no packaging chain to convert with."""
        client, user = authenticated_client
        item = InventoryItemFactory(current_stock=0)
        purchase_order, _po_item = _sent_po_with_line(user, item, quantity_ordered=5)
        freeform = PurchaseOrderItem.objects.create(
            purchase_order=purchase_order,
            description="Pallet fee",
            quantity_ordered=1,
            unit_cost_ordered=Decimal("15.00"),
        )

        response = self._receive(
            client,
            purchase_order,
            {"purchase_order_item": freeform.id, "quantity_received": 1, "at_level": True},
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "no inventory item" in response.data["error"]


class TestReorderDataPresentation:
    """The order pad reads at the item's counting granularity — display only."""

    URL = "/api/reorders/purchase-orders/reorder_data/"

    def test_pack_counted_item_carries_its_count_unit(self, authenticated_client):
        client, _ = authenticated_client
        # The supplier kwargs ride on the factory's own ItemSupplier — adding a
        # second one would list the item once per supplier on the pad.
        item = _pack_item(
            case_size=12,
            current_stock=12,
            minimum_stock=2,
            reorder_quantity=3,
            quantity_per_package=12,
            unit_cost=Decimal("2.00"),
        )

        response = client.get(self.URL)

        assert response.status_code == status.HTTP_200_OK
        rows = [
            row
            for supplier in response.data["suppliers"]
            for row in supplier["items"]
            if row["item_id"] == str(item.id)
        ]
        assert len(rows) == 1
        row = rows[0]
        assert row["count_unit"] == "case"
        assert row["on_hand_display"]["level_count"] == 1
        assert row["reorder_display"]["unit"] == "case"
        # Base units on the wire, cases for the label.
        assert row["suggested_quantity"] == 36
        assert row["suggested_quantity_at_unit"] == 3

    def test_each_item_reports_base_units(self, authenticated_client):
        client, _ = authenticated_client
        item = InventoryItemFactory(
            image=None,
            current_stock=2,
            minimum_stock=5,
            reorder_quantity=10,
            quantity_per_package=1,
            unit_cost=Decimal("2.00"),
        )

        response = client.get(self.URL)

        assert response.status_code == status.HTTP_200_OK
        rows = [
            row
            for supplier in response.data["suppliers"]
            for row in supplier["items"]
            if row["item_id"] == str(item.id)
        ]
        assert len(rows) == 1
        assert rows[0]["count_unit"] == "unit"
        assert rows[0]["suggested_quantity"] == rows[0]["suggested_quantity_at_unit"] == 10
