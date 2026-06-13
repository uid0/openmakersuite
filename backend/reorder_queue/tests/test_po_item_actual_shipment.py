"""Tests for the actual_shipment_date update path on PO line items.

The "mark as shipped" workflow in scantty + the web UI flips this field
when a supplier confirms shipment so the operator can see the gap
between expected_shipment_date and reality.
"""

from datetime import date

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
    return User.objects.create_user(username="warden", password="x", is_staff=True)


@pytest.fixture
def staff_client(staff_user):
    api = APIClient()
    api.force_authenticate(user=staff_user)
    return api


@pytest.fixture
def po_with_line(staff_user):
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
        item=item, supplier=supplier, supplier_sku="ACME-M3-100", unit_cost="0.05"
    )
    po = PurchaseOrder.objects.create(supplier=supplier, created_by=staff_user)
    line = PurchaseOrderItem.objects.create(
        purchase_order=po,
        item_supplier=isup,
        quantity_ordered=100,
        unit_cost_ordered="0.05",
    )
    return po, line


class TestActualShipmentDateUpdate:
    def test_sets_actual_shipment_date(self, staff_client, po_with_line):
        po, line = po_with_line
        assert line.actual_shipment_date is None

        resp = staff_client.patch(
            f"/api/reorders/purchase-orders/{po.id}/items/{line.id}/",
            {"actual_shipment_date": "2026-06-12"},
            format="json",
        )
        assert resp.status_code == 200, resp.content
        line.refresh_from_db()
        assert line.actual_shipment_date == date(2026, 6, 12)

    def test_empty_string_clears_actual_shipment_date(self, staff_client, po_with_line):
        po, line = po_with_line
        line.actual_shipment_date = date(2026, 6, 12)
        line.save()

        resp = staff_client.patch(
            f"/api/reorders/purchase-orders/{po.id}/items/{line.id}/",
            {"actual_shipment_date": ""},
            format="json",
        )
        assert resp.status_code == 200, resp.content
        line.refresh_from_db()
        assert line.actual_shipment_date is None

    def test_invalid_format_rejected(self, staff_client, po_with_line):
        po, line = po_with_line
        resp = staff_client.patch(
            f"/api/reorders/purchase-orders/{po.id}/items/{line.id}/",
            {"actual_shipment_date": "not-a-date"},
            format="json",
        )
        assert resp.status_code == 400
        line.refresh_from_db()
        assert line.actual_shipment_date is None

    def test_serializer_exposes_field(self, staff_client, po_with_line):
        po, line = po_with_line
        line.actual_shipment_date = date(2026, 6, 12)
        line.save()

        resp = staff_client.get(f"/api/reorders/purchase-orders/{po.id}/")
        assert resp.status_code == 200
        body = resp.json()
        items = body["items"]
        assert items[0]["actual_shipment_date"] == "2026-06-12"

    def test_updating_actual_does_not_clobber_expected(self, staff_client, po_with_line):
        po, line = po_with_line
        line.expected_shipment_date = date(2026, 6, 1)
        line.save()

        staff_client.patch(
            f"/api/reorders/purchase-orders/{po.id}/items/{line.id}/",
            {"actual_shipment_date": "2026-06-14"},
            format="json",
        )
        line.refresh_from_db()
        assert line.expected_shipment_date == date(2026, 6, 1)
        assert line.actual_shipment_date == date(2026, 6, 14)
