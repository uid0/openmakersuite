"""Tests for editing ``quantity_ordered`` on a PO line item (op-yh4h).

"We actually need 12, not 10" on an order that is already with the supplier.
The line's stored package count and the PO-level estimated total are both
derived from the quantity, so both have to move with it.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model

import pytest
from rest_framework.test import APIClient

from inventory.models import InventoryItem, ItemSupplier, Supplier
from inventory.tests.factories import CategoryFactory, LocationFactory
from reorder_queue.models import PurchaseOrder, PurchaseOrderItem

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_user():
    return User.objects.create_user(username="quartermaster", password="x", is_staff=True)


@pytest.fixture
def staff_client(staff_user):
    api = APIClient()
    api.force_authenticate(user=staff_user)
    return api


@pytest.fixture
def po_with_line(staff_user):
    """A sent PO with one 10-unit inventory line at $2.50 (case of 5)."""
    location = LocationFactory()
    category = CategoryFactory()
    item = InventoryItem.objects.create(
        name="M3 hex bolt",
        description="",
        category=category,
        location=location,
        current_stock=0,
        minimum_stock=5,
        reorder_quantity=10,
    )
    supplier = Supplier.objects.create(name="Acme")
    isup = ItemSupplier.objects.create(
        item=item,
        supplier=supplier,
        supplier_sku="ACME-M3-100",
        # Decimal, not str: ItemSupplier.save() derives package_cost as
        # unit_cost * quantity_per_package, and a str would repeat instead.
        unit_cost=Decimal("2.50"),
        quantity_per_package=5,
    )
    po = PurchaseOrder.objects.create(
        supplier=supplier,
        created_by=staff_user,
        status=PurchaseOrder.Status.SENT,
        estimated_total=Decimal("25.00"),
    )
    line = PurchaseOrderItem.objects.create(
        purchase_order=po,
        item_supplier=isup,
        quantity_ordered=10,
        unit_cost_ordered="2.50",
        order_in_packages=2,
    )
    return po, line


def patch_line(client, po, line, payload):
    return client.patch(
        f"/api/reorders/purchase-orders/{po.id}/items/{line.id}/",
        payload,
        format="json",
    )


class TestQuantityOrderedUpdate:
    def test_increase_quantity_applies_and_recomputes_totals(self, staff_client, po_with_line):
        po, line = po_with_line

        resp = patch_line(staff_client, po, line, {"quantity_ordered": 12})

        assert resp.status_code == 200, resp.content
        assert resp.json()["quantity_ordered"] == 12
        # estimated_cost is derived: 12 * 2.50
        assert Decimal(resp.json()["estimated_cost"]) == Decimal("30.00")

        line.refresh_from_db()
        assert line.quantity_ordered == 12
        # 12 units at 5 per package -> 3 packages (ceil), same math as create
        assert line.order_in_packages == 3

        po.refresh_from_db()
        assert po.estimated_total == Decimal("30.00")

    def test_decrease_quantity_applies_and_recomputes_totals(self, staff_client, po_with_line):
        po, line = po_with_line

        resp = patch_line(staff_client, po, line, {"quantity_ordered": 4})

        assert resp.status_code == 200, resp.content
        line.refresh_from_db()
        assert line.quantity_ordered == 4
        assert line.order_in_packages == 1

        po.refresh_from_db()
        assert po.estimated_total == Decimal("10.00")

    def test_string_quantity_accepted(self, staff_client, po_with_line):
        """Form-encoded / scantty clients send the quantity as a string."""
        po, line = po_with_line

        resp = patch_line(staff_client, po, line, {"quantity_ordered": "7"})

        assert resp.status_code == 200, resp.content
        line.refresh_from_db()
        assert line.quantity_ordered == 7

    def test_other_lines_left_alone_in_po_total(self, staff_client, po_with_line):
        po, line = po_with_line
        other = PurchaseOrderItem.objects.create(
            purchase_order=po,
            description="Shipping crate",
            quantity_ordered=1,
            unit_cost_ordered="15.00",
        )

        resp = patch_line(staff_client, po, line, {"quantity_ordered": 20})

        assert resp.status_code == 200, resp.content
        other.refresh_from_db()
        assert other.quantity_ordered == 1

        po.refresh_from_db()
        # 20 * 2.50 + 1 * 15.00
        assert po.estimated_total == Decimal("65.00")

    def test_freeform_line_quantity_edit_leaves_packages_zero(self, staff_client, po_with_line):
        po, _line = po_with_line
        freeform = PurchaseOrderItem.objects.create(
            purchase_order=po,
            description="Shipping crate",
            quantity_ordered=1,
            unit_cost_ordered="15.00",
        )

        resp = patch_line(staff_client, po, freeform, {"quantity_ordered": 3})

        assert resp.status_code == 200, resp.content
        freeform.refresh_from_db()
        assert freeform.quantity_ordered == 3
        assert freeform.order_in_packages == 0


class TestQuantityOrderedGuards:
    def test_below_quantity_received_rejected(self, staff_client, po_with_line):
        po, line = po_with_line
        line.quantity_received = 6
        line.save()

        resp = patch_line(staff_client, po, line, {"quantity_ordered": 5})

        assert resp.status_code == 400
        assert "already received" in resp.json()["error"]
        line.refresh_from_db()
        assert line.quantity_ordered == 10
        po.refresh_from_db()
        assert po.estimated_total == Decimal("25.00")

    def test_equal_to_quantity_received_allowed(self, staff_client, po_with_line):
        """Trimming an order down to exactly what showed up is the close-out case."""
        po, line = po_with_line
        line.quantity_received = 6
        line.save()

        resp = patch_line(staff_client, po, line, {"quantity_ordered": 6})

        assert resp.status_code == 200, resp.content
        line.refresh_from_db()
        assert line.quantity_ordered == 6
        assert line.is_fully_received is True

    @pytest.mark.parametrize("bad_quantity", [0, -3, "abc", 2.5, None, ""])
    def test_non_positive_integer_rejected(self, staff_client, po_with_line, bad_quantity):
        po, line = po_with_line

        resp = patch_line(staff_client, po, line, {"quantity_ordered": bad_quantity})

        assert resp.status_code == 400, resp.content
        line.refresh_from_db()
        assert line.quantity_ordered == 10

    def test_voided_line_rejected(self, staff_client, po_with_line):
        po, line = po_with_line
        line.is_voided = True
        line.save()

        resp = patch_line(staff_client, po, line, {"quantity_ordered": 12})

        assert resp.status_code == 400
        assert "voided" in resp.json()["error"]
        line.refresh_from_db()
        assert line.quantity_ordered == 10

    @pytest.mark.parametrize(
        "po_status",
        [
            PurchaseOrder.Status.RECEIVED,
            PurchaseOrder.Status.CANCELLED,
            PurchaseOrder.Status.VOIDED,
        ],
    )
    def test_closed_purchase_order_rejected(self, staff_client, po_with_line, po_status):
        po, line = po_with_line
        po.status = po_status
        po.save()

        resp = patch_line(staff_client, po, line, {"quantity_ordered": 12})

        assert resp.status_code == 400
        line.refresh_from_db()
        assert line.quantity_ordered == 10

    @pytest.mark.parametrize(
        "po_status",
        [
            PurchaseOrder.Status.DRAFT,
            PurchaseOrder.Status.SENT,
            PurchaseOrder.Status.CONFIRMED,
            PurchaseOrder.Status.PARTIALLY_RECEIVED,
        ],
    )
    def test_open_purchase_order_allowed(self, staff_client, po_with_line, po_status):
        po, line = po_with_line
        po.status = po_status
        po.save()

        resp = patch_line(staff_client, po, line, {"quantity_ordered": 12})

        assert resp.status_code == 200, resp.content
        line.refresh_from_db()
        assert line.quantity_ordered == 12

    def test_requires_authentication(self, po_with_line):
        po, line = po_with_line

        resp = patch_line(APIClient(), po, line, {"quantity_ordered": 12})

        assert resp.status_code in (401, 403)
        line.refresh_from_db()
        assert line.quantity_ordered == 10


class TestQuantityOrderedWithOtherFields:
    def test_line_cost_uses_the_new_quantity(self, staff_client, po_with_line):
        """A combined edit prices the line against what was just ordered."""
        po, line = po_with_line

        resp = patch_line(
            staff_client,
            po,
            line,
            {"quantity_ordered": 20, "line_cost": "50.00"},
        )

        assert resp.status_code == 200, resp.content
        line.refresh_from_db()
        assert line.quantity_ordered == 20
        assert line.unit_cost_actual == Decimal("2.5000")

    def test_existing_fields_still_update_without_quantity(self, staff_client, po_with_line):
        po, line = po_with_line

        resp = patch_line(
            staff_client,
            po,
            line,
            {
                "expected_shipment_date": "2026-08-03",
                "actual_shipment_date": "2026-08-05",
                "notes": "back-ordered",
                "unit_cost_actual": "3.25",
            },
        )

        assert resp.status_code == 200, resp.content
        line.refresh_from_db()
        assert line.expected_shipment_date == date(2026, 8, 3)
        assert line.actual_shipment_date == date(2026, 8, 5)
        assert line.notes == "back-ordered"
        assert line.unit_cost_actual == Decimal("3.2500")
        assert line.quantity_ordered == 10

    def test_notes_still_editable_on_a_voided_line(self, staff_client, po_with_line):
        """The voided guard is scoped to the quantity, not the whole action."""
        po, line = po_with_line
        line.is_voided = True
        line.save()

        resp = patch_line(staff_client, po, line, {"notes": "supplier discontinued"})

        assert resp.status_code == 200, resp.content
        line.refresh_from_db()
        assert line.notes == "supplier discontinued"

    def test_unchanged_quantity_is_a_noop(self, staff_client, po_with_line):
        po, line = po_with_line
        po.estimated_total = Decimal("999.00")
        po.save()

        resp = patch_line(staff_client, po, line, {"quantity_ordered": 10, "notes": "same"})

        assert resp.status_code == 200, resp.content
        line.refresh_from_db()
        assert line.quantity_ordered == 10
        assert line.notes == "same"
        po.refresh_from_db()
        # No quantity movement -> the stored total is left exactly as it was.
        assert po.estimated_total == Decimal("999.00")
