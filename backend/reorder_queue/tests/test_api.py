"""
API tests for reorder queue endpoints.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from inventory.models import Supplier
from inventory.tests.factories import InventoryItemFactory, ItemSupplierFactory, SupplierFactory
from reorder_queue.models import (
    LeadTimeLog,
    PurchaseOrder,
    PurchaseOrderAuditEvent,
    PurchaseOrderItem,
)
from reorder_queue.tests.factories import ReorderRequestFactory

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.mark.integration
class TestReorderRequestAPI:
    """Tests for ReorderRequest API endpoints."""

    def test_list_reorder_requests(self, authenticated_client):
        """Test listing reorder requests."""
        client, user = authenticated_client
        ReorderRequestFactory.create_batch(3)

        url = reverse("reorderrequest-list")
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 3

    def test_create_reorder_request_authenticated(self, authenticated_client):
        """Authenticated users can create a reorder request."""
        client, _user = authenticated_client
        item = InventoryItemFactory()

        url = reverse("reorderrequest-list")
        data = {
            "item": str(item.id),
            "quantity": 25,
            "requested_by": "Jane Doe",
            "request_notes": "We are running low",
            "priority": "high",
        }
        response = client.post(url, data, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert str(response.data["item"]) == str(item.id)
        assert response.data["quantity"] == 25
        assert response.data["status"] == "pending"

    def test_create_reorder_request_minimal(self, authenticated_client):
        """Test creating request with minimal required fields."""
        client, _user = authenticated_client
        item = InventoryItemFactory()

        url = reverse("reorderrequest-list")
        data = {"item": str(item.id), "quantity": 10}
        response = client.post(url, data, format="json")

        assert response.status_code == status.HTTP_201_CREATED

    def test_create_reorder_request_anonymous_allowed(self, api_client):
        """The QR-scan reorder flow must work without a login. Anonymous
        callers may submit a reorder; the limited create serializer
        guarantees they can only set safe fields and the response leaks
        no admin metadata."""
        item = InventoryItemFactory()

        url = reverse("reorderrequest-list")
        data = {
            "item": str(item.id),
            "quantity": 10,
            "requested_by": "Anonymous",
            "request_notes": "Auto-submitted via QR scan",
        }
        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        # Limited response shape — no admin / invoice / supplier-URL leakage.
        assert "admin_notes" not in response.data
        assert "invoice_url" not in response.data
        assert "supplier_url" not in response.data
        assert "actual_cost" not in response.data
        assert response.data["quantity"] == 10
        assert response.data["requested_by"] == "Anonymous"

    def test_create_reorder_request_anonymous_cannot_set_admin_fields(self, api_client):
        """Even if an anonymous caller posts admin fields, the limited
        create serializer drops them on the floor — defense in depth."""
        item = InventoryItemFactory()

        url = reverse("reorderrequest-list")
        data = {
            "item": str(item.id),
            "quantity": 5,
            "admin_notes": "should be ignored",
            "actual_cost": "999.99",
            "supplier_url": "https://evil.example.com/poison",
        }
        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        from reorder_queue.models import ReorderRequest

        row = ReorderRequest.objects.get(id=response.data["id"])
        assert row.admin_notes == ""
        assert row.actual_cost is None
        assert row.supplier_url == ""

    def test_list_reorder_requests_anonymous_denied(self, api_client):
        """Anonymous users cannot list reorder requests (gh #327)."""
        ReorderRequestFactory.create_batch(2)

        url = reverse("reorderrequest-list")
        response = api_client.get(url)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_retrieve_reorder_request_anonymous_denied(self, api_client):
        """Anonymous users cannot retrieve a reorder request (gh #327)."""
        request_obj = ReorderRequestFactory()

        url = reverse("reorderrequest-detail", kwargs={"pk": request_obj.pk})
        response = api_client.get(url)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_pending_reorder_requests_anonymous_denied(self, api_client):
        """Anonymous users cannot view the pending queue (gh #327)."""
        ReorderRequestFactory(status="pending")

        url = reverse("reorderrequest-pending")
        response = api_client.get(url)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.parametrize(
        "action_name,method,detail",
        [
            ("by-supplier", "get", False),
            ("sig-pending", "get", False),
            ("generate-cart-links", "get", False),
            ("approve", "post", True),
            ("mark-ordered", "post", True),
            ("mark-received", "post", True),
            ("cancel", "post", True),
        ],
    )
    def test_admin_actions_anonymous_denied(self, api_client, action_name, method, detail):
        """All admin reorder actions reject anonymous callers (gh #327)."""
        if detail:
            request_obj = ReorderRequestFactory(status="pending")
            url = reverse(f"reorderrequest-{action_name}", kwargs={"pk": request_obj.pk})
        else:
            url = reverse(f"reorderrequest-{action_name}")

        response = getattr(api_client, method)(url, {}, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_retrieve_reorder_request(self, authenticated_client):
        """Test retrieving a single reorder request."""
        client, user = authenticated_client
        request_obj = ReorderRequestFactory()

        url = reverse("reorderrequest-detail", kwargs={"pk": request_obj.pk})
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["id"] == request_obj.id
        assert "item_details" in response.data

    def test_update_requires_auth(self, api_client):
        """Test updating request requires authentication."""
        request_obj = ReorderRequestFactory()

        url = reverse("reorderrequest-detail", kwargs={"pk": request_obj.pk})
        data = {"status": "approved"}
        response = api_client.patch(url, data, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_pending_requests_endpoint(self, authenticated_client):
        """Test getting pending requests only."""
        client, user = authenticated_client

        # Create requests with different statuses
        ReorderRequestFactory(status="pending")
        ReorderRequestFactory(status="pending")
        ReorderRequestFactory(status="approved")
        ReorderRequestFactory(status="cancelled")

        url = reverse("reorderrequest-pending")
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        # Endpoint returns list directly, not paginated
        assert len(response.data) == 2
        for req in response.data:
            assert req["status"] == "pending"

    def test_by_supplier_endpoint(self, authenticated_client):
        """Test grouping requests by supplier."""
        client, user = authenticated_client

        supplier1 = SupplierFactory(supplier_type="online")
        supplier2 = SupplierFactory(supplier_type="national")

        item1 = InventoryItemFactory(supplier=supplier1)
        item2 = InventoryItemFactory(supplier=supplier1)
        item3 = InventoryItemFactory(supplier=supplier2)

        ReorderRequestFactory(item=item1, status="pending")
        ReorderRequestFactory(item=item2, status="pending")
        ReorderRequestFactory(item=item3, status="pending")

        url = reverse("reorderrequest-by-supplier")
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

        # Check grouping
        supplier_names = [group["supplier"] for group in response.data]
        assert supplier1.name in supplier_names
        assert supplier2.name in supplier_names

    def test_approve_request(self, authenticated_client):
        """Test approving a reorder request."""
        client, user = authenticated_client
        request_obj = ReorderRequestFactory(status="pending")

        url = reverse("reorderrequest-approve", kwargs={"pk": request_obj.pk})
        data = {"admin_notes": "Approved for ordering"}
        response = client.post(url, data, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "approved"
        assert response.data["reviewed_by"] == user.id
        assert response.data["admin_notes"] == "Approved for ordering"

        # Verify in database
        request_obj.refresh_from_db()
        assert request_obj.status == "approved"
        assert request_obj.reviewed_by == user
        assert request_obj.reviewed_at is not None

    def test_mark_ordered(self, authenticated_client):
        """Test marking a request as ordered."""
        client, user = authenticated_client
        request_obj = ReorderRequestFactory(status="approved")

        url = reverse("reorderrequest-mark-ordered", kwargs={"pk": request_obj.pk})
        data = {
            "order_number": "ORD-12345",
            "estimated_delivery": (timezone.now() + timedelta(days=7)).date().isoformat(),
            "actual_cost": "125.50",
        }
        response = client.post(url, data, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "ordered"
        assert response.data["order_number"] == "ORD-12345"

        # Verify in database
        request_obj.refresh_from_db()
        assert request_obj.status == "ordered"
        assert request_obj.ordered_at is not None
        assert request_obj.order_number == "ORD-12345"

    def test_mark_ordered_one_click_without_body(self, authenticated_client):
        """Marking ordered needs no order number — a bare POST is enough.

        The order/PO number belongs to the Purchase Order domain, so the
        operator is not forced to type one at mark-ordered time.
        """
        client, user = authenticated_client
        request_obj = ReorderRequestFactory(status="approved")

        url = reverse("reorderrequest-mark-ordered", kwargs={"pk": request_obj.pk})
        response = client.post(url, {}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "ordered"

        request_obj.refresh_from_db()
        assert request_obj.status == "ordered"
        assert request_obj.ordered_at is not None
        # No number was typed; it stays blank for the PO to carry later.
        assert request_obj.order_number == ""

    def test_mark_ordered_preserves_po_fields_when_omitted(self, authenticated_client):
        """A bare mark-ordered must not wipe values a PO already populated."""
        client, user = authenticated_client
        existing_delivery = (timezone.now() + timedelta(days=5)).date()
        request_obj = ReorderRequestFactory(
            status="approved",
            order_number="PO-2026-0007",
            estimated_delivery=existing_delivery,
        )

        url = reverse("reorderrequest-mark-ordered", kwargs={"pk": request_obj.pk})
        response = client.post(url, {}, format="json")

        assert response.status_code == status.HTTP_200_OK

        request_obj.refresh_from_db()
        assert request_obj.status == "ordered"
        # Omitted fields are left untouched, not blanked.
        assert request_obj.order_number == "PO-2026-0007"
        assert request_obj.estimated_delivery == existing_delivery

    def test_public_transparency_endpoint_returns_ledger(self, api_client):
        """Ensure the transparency endpoint is publicly accessible and returns ledger data."""
        ordered_time = timezone.now()
        delivery_date = ordered_time.date()
        request_obj = ReorderRequestFactory(
            status="received",
            ordered_at=ordered_time,
            actual_delivery=delivery_date,
            actual_cost="150.25",
            order_number="ORD-LEDGER-1",
            invoice_number="INV-LEDGER-1",
            public_notes="Delivered to logistics bay",
        )

        url = reverse("analytics-transparency")
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        data = response.data

        assert data["summary"]["total_orders_with_financial_data"] == 1
        assert data["summary"]["total_amount_spent"] == float(request_obj.actual_cost)

        ledger_entry = data["ledger"][0]
        assert ledger_entry["item_name"] == request_obj.item.name
        assert ledger_entry["quantity"] == request_obj.quantity
        assert ledger_entry["ordered_at"] is not None
        assert ledger_entry["delivered_at"] == delivery_date.isoformat()
        assert ledger_entry["actual_cost"] == float(request_obj.actual_cost)

        order_entry = data["orders"][0]
        assert order_entry["supplier_name"] == request_obj.item.supplier.name
        assert order_entry["invoice_number"] == "INV-LEDGER-1"

    def test_logistics_dashboard_public_endpoint(self, api_client):
        """The logistics dashboard data should be accessible without authentication."""
        urgent_request = ReorderRequestFactory(
            status="pending", priority="urgent", quantity=7, request_notes="Needs ASAP"
        )
        approved_request = ReorderRequestFactory(status="approved", quantity=3)  # noqa: F841

        user = User.objects.create_user(username="logistics-user", password="pass12345")
        item_supplier = urgent_request.item.primary_item_supplier

        purchase_order = PurchaseOrder.objects.create(
            po_number="PO-LOG-001",
            supplier=item_supplier.supplier,
            status=PurchaseOrder.SENT,
            created_by=user,
            sent_at=timezone.now(),
            expected_delivery_date=timezone.now().date() + timedelta(days=5),
            estimated_total=Decimal("250.00"),
        )

        PurchaseOrderItem.objects.create(
            purchase_order=purchase_order,
            item_supplier=item_supplier,
            quantity_ordered=10,
            quantity_received=2,
            unit_cost_ordered=Decimal("12.50"),
            order_in_packages=0,  # Default value for existing items
        )

        url = reverse("analytics-logistics-dashboard")
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        data = response.data

        # Check that our created requests are included
        # At least our 2 requests (pending + approved)
        assert data["open_item_requests"] >= 2
        assert isinstance(data["open_item_requests"], int)

        # Check other required fields exist
        assert "open_locations_with_problems" in data
        assert isinstance(data["open_locations_with_problems"], int)

        assert "assets_overdue_maintenance" in data
        assert isinstance(data["assets_overdue_maintenance"], int)

        assert "qr_scans_total" in data
        assert isinstance(data["qr_scans_total"], int)

        assert "qr_scans_by_day" in data
        assert isinstance(data["qr_scans_by_day"], list)
        assert len(data["qr_scans_by_day"]) == 7  # Should have 7 days of data

        assert "last_updated" in data
        assert isinstance(data["last_updated"], str)

    def test_mark_received(self, authenticated_client):
        """Test marking a request as received and updating inventory."""
        client, user = authenticated_client
        item = InventoryItemFactory(current_stock=10)
        request_obj = ReorderRequestFactory(item=item, quantity=50, status="ordered")

        url = reverse("reorderrequest-mark-received", kwargs={"pk": request_obj.pk})
        response = client.post(url, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "received"

        # Verify inventory was updated
        item.refresh_from_db()
        assert item.current_stock == 60  # 10 + 50

        # Verify in database
        request_obj.refresh_from_db()
        assert request_obj.status == "received"
        assert request_obj.actual_delivery is not None

    def test_cancel_request(self, authenticated_client):
        """Test cancelling a reorder request."""
        client, user = authenticated_client
        request_obj = ReorderRequestFactory(status="pending")

        url = reverse("reorderrequest-cancel", kwargs={"pk": request_obj.pk})
        data = {"admin_notes": "Duplicate request"}
        response = client.post(url, data, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "cancelled"

        # Verify in database
        request_obj.refresh_from_db()
        assert request_obj.status == "cancelled"
        assert request_obj.reviewed_by == user
        assert request_obj.admin_notes == "Duplicate request"

    def test_generate_cart_links_groups_live_queue_by_supplier(self, authenticated_client):
        """generate_cart_links groups the live reorder queue (pending +
        approved) by supplier into per-supplier part#,qty order pads.

        Rebuilt from the dead supplier_type-branching code (op-ls52): the
        Supplier model only produces local/online/national types, so the old
        amazon/grainger/hdsupply branches never matched and it returned {}.
        """
        client, user = authenticated_client

        acme = SupplierFactory(name="Acme Tools")
        globex = SupplierFactory(name="Globex Supply")

        item1 = InventoryItemFactory(supplier=acme, supplier_sku="ACME-123")
        item2 = InventoryItemFactory(supplier=globex, supplier_sku="GLBX-456")

        # An approved and a pending request both count as "live".
        ReorderRequestFactory(item=item1, status="approved", quantity=3)
        ReorderRequestFactory(item=item2, status="pending", quantity=5)

        url = reverse("reorderrequest-generate-cart-links")
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert set(response.data.keys()) == {"Acme Tools", "Globex Supply"}

        acme_pad = response.data["Acme Tools"]
        assert acme_pad["supplier"] == "Acme Tools"
        assert acme_pad["line_count"] == 1
        assert acme_pad["csv"] == "part#,qty\nACME-123,3\n"
        assert acme_pad["text"] == "ACME-123\t3"

        globex_pad = response.data["Globex Supply"]
        assert globex_pad["line_count"] == 1
        assert globex_pad["csv"] == "part#,qty\nGLBX-456,5\n"
        assert globex_pad["text"] == "GLBX-456\t5"

    def test_generate_cart_links_excludes_non_live_requests(self, authenticated_client):
        """Only pending/approved requests are live; ordered/received/cancelled
        have left the queue and must not appear in the order pad."""
        client, user = authenticated_client

        supplier = SupplierFactory(name="Acme Tools")
        live_item = InventoryItemFactory(supplier=supplier, supplier_sku="LIVE-1")
        done_item = InventoryItemFactory(supplier=supplier, supplier_sku="DONE-1")

        ReorderRequestFactory(item=live_item, status="approved", quantity=2)
        ReorderRequestFactory(item=done_item, status="ordered", quantity=9)
        ReorderRequestFactory(item=done_item, status="received", quantity=9)
        ReorderRequestFactory(item=done_item, status="cancelled", quantity=9)

        url = reverse("reorderrequest-generate-cart-links")
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert list(response.data.keys()) == ["Acme Tools"]
        pad = response.data["Acme Tools"]
        assert pad["line_count"] == 1
        assert "LIVE-1,2" in pad["csv"]
        assert "DONE-1" not in pad["csv"]


@pytest.mark.integration
class TestPurchaseOrderExportOrder:
    """Order-pad export action: GET purchase-orders/<id>/export-order/ (op-ls52).

    Turns a PO's lines into a vendor-ready part#,qty list an operator can paste
    or upload into any distributor bulk order pad, built from each line's
    ItemSupplier.supplier_sku with zero schema change.
    """

    def _po(self, user, supplier=None, po_number="PO-2026-0009"):
        supplier = supplier or SupplierFactory(name="Distributor Co")
        purchase_order = PurchaseOrder.objects.create(
            po_number=po_number,
            supplier=supplier,
            status=PurchaseOrder.DRAFT,
            created_by=user,
        )
        return purchase_order, supplier

    def _add_line(self, purchase_order, supplier, *, name, sku, qty, voided=False):
        item = InventoryItemFactory(name=name, supplier=supplier, supplier_sku=sku)
        return PurchaseOrderItem.objects.create(
            purchase_order=purchase_order,
            item_supplier=item.primary_item_supplier,
            quantity_ordered=qty,
            unit_cost_ordered=Decimal("1.00"),
            is_voided=voided,
        )

    def test_export_order_emits_part_qty_csv_and_text(self, authenticated_client):
        client, user = authenticated_client
        purchase_order, supplier = self._po(user)
        self._add_line(purchase_order, supplier, name="Alpha Widget", sku="W-1", qty=2)
        self._add_line(purchase_order, supplier, name="Beta Gadget", sku="G-2", qty=5)

        url = reverse("purchaseorder-export-order", kwargs={"pk": purchase_order.pk})
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["line_count"] == 2
        assert response.data["missing_sku"] == []
        assert response.data["supplier"] == "Distributor Co"
        assert response.data["filename"] == "PO-2026-0009-order.csv"

        csv_lines = response.data["csv"].splitlines()
        assert csv_lines[0] == "part#,qty"
        assert "W-1,2" in csv_lines
        assert "G-2,5" in csv_lines

        text_lines = response.data["text"].splitlines()
        assert "W-1\t2" in text_lines
        assert "G-2\t5" in text_lines
        assert "part#" not in response.data["text"]  # copy block has no header

    def test_export_order_skips_voided_lines(self, authenticated_client):
        client, user = authenticated_client
        purchase_order, supplier = self._po(user)
        self._add_line(purchase_order, supplier, name="Kept", sku="KEEP-1", qty=1)
        self._add_line(purchase_order, supplier, name="Dropped", sku="VOID-1", qty=9, voided=True)

        url = reverse("purchaseorder-export-order", kwargs={"pk": purchase_order.pk})
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["line_count"] == 1
        assert "KEEP-1,1" in response.data["csv"]
        assert "VOID-1" not in response.data["csv"]
        assert "VOID-1" not in response.data["text"]
        # A voided line is out of scope entirely — not even a missing_sku note.
        assert response.data["missing_sku"] == []

    def test_export_order_lists_missing_sku_without_dropping_them(self, authenticated_client):
        client, user = authenticated_client
        purchase_order, supplier = self._po(user)
        self._add_line(purchase_order, supplier, name="Has SKU", sku="OK-1", qty=4)
        self._add_line(purchase_order, supplier, name="No SKU Item", sku="", qty=7)

        url = reverse("purchaseorder-export-order", kwargs={"pk": purchase_order.pk})
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        # Blank-sku line is surfaced, not silently dropped...
        assert response.data["missing_sku"] == ["No SKU Item"]
        # ...and it stays out of the machine-readable pad (a blank part# is junk).
        assert response.data["line_count"] == 1
        assert "OK-1,4" in response.data["csv"]
        assert response.data["text"] == "OK-1\t4"

    def test_export_order_requires_authentication(self, api_client, authenticated_client):
        _, user = authenticated_client
        purchase_order, supplier = self._po(user)
        self._add_line(purchase_order, supplier, name="Widget", sku="W-1", qty=1)

        url = reverse("purchaseorder-export-order", kwargs={"pk": purchase_order.pk})
        response = api_client.get(url)  # anonymous — no credentials

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_generic_adapter_reports_adapter_and_empty_invalid_sku(self, authenticated_client):
        # A supplier with the default (none) adapter keeps the #855 generic pad
        # but now reports adapter + invalid_sku in the unified shape (op-svpq).
        client, user = authenticated_client
        supplier = SupplierFactory(name="Generic Co", ordering_adapter=Supplier.ADAPTER_NONE)
        purchase_order, _ = self._po(user, supplier=supplier)
        self._add_line(purchase_order, supplier, name="Widget", sku="W-1", qty=2)

        url = reverse("purchaseorder-export-order", kwargs={"pk": purchase_order.pk})
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["adapter"] == "none"
        assert response.data["invalid_sku"] == []
        assert "cart_urls" not in response.data
        assert response.data["csv"].splitlines()[0] == "part#,qty"


@pytest.mark.integration
class TestPurchaseOrderExportOrderAdapters:
    """Adapter-aware order-pad export (op-svpq): the PO supplier's
    ``ordering_adapter`` selects the emitted artifact — an Amazon add-to-cart
    URL, an HD Supply Part#,Qty CSV, or the generic part#,qty pad."""

    def _po(self, user, supplier):
        purchase_order = PurchaseOrder.objects.create(
            po_number="PO-2026-0100",
            supplier=supplier,
            status=PurchaseOrder.DRAFT,
            created_by=user,
        )
        return purchase_order

    def _add_line(self, purchase_order, supplier, *, name, sku, qty):
        item = InventoryItemFactory(name=name, supplier=supplier, supplier_sku=sku)
        return PurchaseOrderItem.objects.create(
            purchase_order=purchase_order,
            item_supplier=item.primary_item_supplier,
            quantity_ordered=qty,
            unit_cost_ordered=Decimal("1.00"),
        )

    def test_amazon_adapter_builds_add_to_cart_url(self, authenticated_client):
        client, user = authenticated_client
        supplier = SupplierFactory(name="Amazon", ordering_adapter=Supplier.ADAPTER_AMAZON)
        purchase_order = self._po(user, supplier)
        self._add_line(purchase_order, supplier, name="Widget", sku="B07X1234YZ", qty=2)
        self._add_line(purchase_order, supplier, name="Gadget", sku="B00ABCDE12", qty=3)

        url = reverse("purchaseorder-export-order", kwargs={"pk": purchase_order.pk})
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["adapter"] == "amazon"
        assert response.data["line_count"] == 2
        assert response.data["invalid_sku"] == []
        assert len(response.data["cart_urls"]) == 1

        # Parse the query so the ASIN↔Quantity pairing is asserted independently
        # of the DB row order (items.all() order is not guaranteed).
        from urllib.parse import parse_qs, urlparse

        cart_url = response.data["cart_urls"][0]
        assert cart_url.startswith("https://www.amazon.com/gp/aws/cart/add.html?")
        params = parse_qs(urlparse(cart_url).query)
        carted = {
            params[key][0]: params[f"Quantity.{key.split('.')[1]}"][0]
            for key in params
            if key.startswith("ASIN.")
        }
        assert carted == {"B07X1234YZ": "2", "B00ABCDE12": "3"}
        # URL adapter has no CSV/filename.
        assert "csv" not in response.data
        assert "filename" not in response.data

    def test_amazon_adapter_flags_non_asin_sku(self, authenticated_client):
        client, user = authenticated_client
        supplier = SupplierFactory(name="Amazon", ordering_adapter=Supplier.ADAPTER_AMAZON)
        purchase_order = self._po(user, supplier)
        self._add_line(purchase_order, supplier, name="Good", sku="B07X1234YZ", qty=1)
        self._add_line(purchase_order, supplier, name="Mistyped", sku="not-an-asin", qty=9)

        url = reverse("purchaseorder-export-order", kwargs={"pk": purchase_order.pk})
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        # The bad ASIN is surfaced, never silently added to a cart.
        assert response.data["invalid_sku"] == ["Mistyped"]
        assert response.data["line_count"] == 1
        assert "not-an-asin" not in response.data["cart_urls"][0]

    def test_hdsupply_adapter_builds_part_number_csv(self, authenticated_client):
        client, user = authenticated_client
        supplier = SupplierFactory(name="HD Supply", ordering_adapter=Supplier.ADAPTER_HDSUPPLY)
        purchase_order = self._po(user, supplier)
        self._add_line(purchase_order, supplier, name="Fastener", sku="12345", qty=4)

        url = reverse("purchaseorder-export-order", kwargs={"pk": purchase_order.pk})
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["adapter"] == "hdsupply"
        # HD Supply Saved-List header + numeric part rows.
        csv_lines = response.data["csv"].splitlines()
        assert csv_lines[0] == "Part Number,Quantity"
        assert "12345,4" in csv_lines
        # Quick Order paste block (tab-separated, no header).
        assert response.data["text"] == "12345\t4"
        assert response.data["filename"] == "PO-2026-0100-order.csv"
        assert "cart_urls" not in response.data

    def test_hdsupply_adapter_flags_non_numeric_part(self, authenticated_client):
        client, user = authenticated_client
        supplier = SupplierFactory(name="HD Supply", ordering_adapter=Supplier.ADAPTER_HDSUPPLY)
        purchase_order = self._po(user, supplier)
        self._add_line(purchase_order, supplier, name="Numeric", sku="9001", qty=1)
        self._add_line(purchase_order, supplier, name="Alpha", sku="ABC-1", qty=2)

        url = reverse("purchaseorder-export-order", kwargs={"pk": purchase_order.pk})
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["invalid_sku"] == ["Alpha"]
        assert response.data["line_count"] == 1
        assert "ABC-1" not in response.data["csv"]


@pytest.mark.integration
class TestPurchaseOrderAPI:
    """Tests for PurchaseOrder API endpoints."""

    def test_create_purchase_order_with_inventory_item_calculates_order_in_packages(
        self, authenticated_client
    ):
        """Test that order_in_packages is calculated correctly when creating a purchase order."""
        client, user = authenticated_client

        # Create supplier and item with quantity_per_package = 12
        supplier = SupplierFactory()
        item_supplier = ItemSupplierFactory(
            supplier=supplier, quantity_per_package=12, unit_cost=Decimal("2.50")
        )

        # Create purchase order with quantity_ordered = 25
        # Expected: order_in_packages = ceil(25 / 12) = 3
        url = reverse("purchaseorder-list")
        data = {
            "supplier": supplier.id,
            "items": [
                {
                    "item_supplier_id": item_supplier.id,
                    "quantity": 25,
                }
            ],
        }
        response = client.post(url, data, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["po_number"] is not None

        # Verify the purchase order item was created with correct order_in_packages
        po_id = response.data["id"]
        purchase_order = PurchaseOrder.objects.get(id=po_id)
        po_item = purchase_order.items.first()

        assert po_item is not None
        assert po_item.quantity_ordered == 25
        assert po_item.order_in_packages == 3  # ceil(25 / 12) = 3

    def test_create_purchase_order_with_exact_package_quantity(self, authenticated_client):
        """Test order_in_packages calculation when quantity is exactly divisible by quantity_per_package."""
        client, user = authenticated_client

        supplier = SupplierFactory()
        item_supplier = ItemSupplierFactory(
            supplier=supplier, quantity_per_package=10, unit_cost=Decimal("5.00")
        )

        # Create purchase order with quantity_ordered = 30 (exactly 3 packages)
        url = reverse("purchaseorder-list")
        data = {
            "supplier": supplier.id,
            "items": [
                {
                    "item_supplier_id": item_supplier.id,
                    "quantity": 30,
                }
            ],
        }
        response = client.post(url, data, format="json")

        assert response.status_code == status.HTTP_201_CREATED

        po_id = response.data["id"]
        purchase_order = PurchaseOrder.objects.get(id=po_id)
        po_item = purchase_order.items.first()

        assert po_item.order_in_packages == 3  # 30 / 10 = 3 exactly

    def test_create_purchase_order_honors_explicit_order_in_packages(self, authenticated_client):
        """Frontend can send order_in_packages directly when ordering whole cases.

        When the user enters '6 cases' for a QPP=24 item, the frontend submits
        quantity=144 and order_in_packages=6. The backend must record exactly 6,
        not recompute via ceil(144/24).
        """
        client, _ = authenticated_client

        supplier = SupplierFactory()
        item_supplier = ItemSupplierFactory(
            supplier=supplier, quantity_per_package=24, unit_cost=Decimal("1.25")
        )

        url = reverse("purchaseorder-list")
        data = {
            "supplier": supplier.id,
            "items": [
                {
                    "item_supplier_id": item_supplier.id,
                    "quantity": 144,
                    "order_in_packages": 6,
                }
            ],
        }
        response = client.post(url, data, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        po_item = PurchaseOrder.objects.get(id=response.data["id"]).items.first()
        assert po_item.quantity_ordered == 144
        assert po_item.order_in_packages == 6

    def test_create_purchase_order_with_asset_sets_order_in_packages_to_zero(
        self, authenticated_client
    ):
        """Test that assets have order_in_packages set to 0."""
        client, user = authenticated_client

        from inventory.tests.factories import AssetFactory

        supplier = SupplierFactory()
        asset = AssetFactory(manufacturer=supplier)

        url = reverse("purchaseorder-list")
        data = {
            "supplier": supplier.id,
            "items": [
                {
                    "asset_id": str(asset.id),
                    "quantity": 2,
                    "unit_cost": 100.00,
                }
            ],
        }
        response = client.post(url, data, format="json")

        assert response.status_code == status.HTTP_201_CREATED

        po_id = response.data["id"]
        purchase_order = PurchaseOrder.objects.get(id=po_id)
        po_item = purchase_order.items.first()

        assert po_item.order_in_packages == 0  # Assets don't have package information

    def test_create_purchase_order_with_freeform_item_sets_order_in_packages_to_zero(
        self, authenticated_client
    ):
        """Test that freeform items have order_in_packages set to 0."""
        client, user = authenticated_client

        supplier = SupplierFactory()

        url = reverse("purchaseorder-list")
        data = {
            "supplier": supplier.id,
            "items": [
                {
                    "description": "Custom item",
                    "quantity": 5,
                    "unit_cost": 25.00,
                }
            ],
        }
        response = client.post(url, data, format="json")

        assert response.status_code == status.HTTP_201_CREATED

        po_id = response.data["id"]
        purchase_order = PurchaseOrder.objects.get(id=po_id)
        po_item = purchase_order.items.first()

        assert po_item.order_in_packages == 0  # Freeform items don't have package information

    def test_freeform_purchase_order_item_exposes_description_in_response(
        self, authenticated_client
    ):
        """Freeform line items must serialize their description and item_type='freeform'.

        Regression for oms-74q: freeform items were rendering as 'Unknown item' because
        the API response/frontend fallback didn't surface the description.
        """
        client, _user = authenticated_client

        supplier = SupplierFactory()

        url = reverse("purchaseorder-list")
        data = {
            "supplier": supplier.id,
            "items": [
                {
                    "description": "Custom widget XL",
                    "quantity": 3,
                    "unit_cost": 17.50,
                }
            ],
        }
        response = client.post(url, data, format="json")
        assert response.status_code == status.HTTP_201_CREATED

        po_id = response.data["id"]
        detail_url = reverse("purchaseorder-detail", args=[po_id])
        detail = client.get(detail_url)
        assert detail.status_code == status.HTTP_200_OK

        items = detail.data["items"]
        assert len(items) == 1
        line = items[0]
        assert line["description"] == "Custom widget XL"
        assert line["item_type"] == "freeform"
        assert line["item_supplier"] is None
        assert line["asset"] is None

    def test_create_purchase_order_with_quantity_per_package_one(self, authenticated_client):
        """Test order_in_packages when quantity_per_package is 1."""
        client, user = authenticated_client

        supplier = SupplierFactory()
        item_supplier = ItemSupplierFactory(
            supplier=supplier, quantity_per_package=1, unit_cost=Decimal("1.00")
        )

        url = reverse("purchaseorder-list")
        data = {
            "supplier": supplier.id,
            "items": [
                {
                    "item_supplier_id": item_supplier.id,
                    "quantity": 7,
                }
            ],
        }
        response = client.post(url, data, format="json")

        assert response.status_code == status.HTTP_201_CREATED

        po_id = response.data["id"]
        purchase_order = PurchaseOrder.objects.get(id=po_id)
        po_item = purchase_order.items.first()

        assert po_item.order_in_packages == 7  # 7 / 1 = 7

    def test_create_po_rejects_duplicate_item_supplier_returns_400(self, authenticated_client):
        """Duplicate item_supplier_id in one PO must be rejected with 400, not 500."""
        client, _ = authenticated_client

        supplier = SupplierFactory()
        item_supplier = ItemSupplierFactory(
            supplier=supplier, quantity_per_package=1, unit_cost=Decimal("2.00")
        )

        url = reverse("purchaseorder-list")
        data = {
            "supplier": supplier.id,
            "items": [
                {"item_supplier_id": item_supplier.id, "quantity": 3},
                {"item_supplier_id": item_supplier.id, "quantity": 5},
            ],
        }
        response = client.post(url, data, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert (
            "more than once" in str(response.data).lower()
            or "combine" in str(response.data).lower()
        )
        assert PurchaseOrder.objects.count() == 0
        assert PurchaseOrderItem.objects.count() == 0

    def test_create_po_rejects_duplicate_asset_returns_400(self, authenticated_client):
        """Duplicate asset_id in one PO must be rejected with 400, not 500."""
        from inventory.tests.factories import AssetFactory

        client, _ = authenticated_client

        supplier = SupplierFactory()
        asset = AssetFactory(manufacturer=supplier)

        url = reverse("purchaseorder-list")
        data = {
            "supplier": supplier.id,
            "items": [
                {"asset_id": str(asset.id), "quantity": 1, "unit_cost": 100.00},
                {"asset_id": str(asset.id), "quantity": 2, "unit_cost": 100.00},
            ],
        }
        response = client.post(url, data, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert (
            "more than once" in str(response.data).lower()
            or "combine" in str(response.data).lower()
        )
        assert PurchaseOrder.objects.count() == 0
        assert PurchaseOrderItem.objects.count() == 0

    def test_create_po_rejects_non_numeric_item_supplier_id_returns_400(self, authenticated_client):
        """Non-integer item_supplier_id must produce 400, not 500."""
        client, _ = authenticated_client

        supplier = SupplierFactory()

        url = reverse("purchaseorder-list")
        data = {
            "supplier": supplier.id,
            "items": [
                {"item_supplier_id": "abc", "quantity": 1},
            ],
        }
        response = client.post(url, data, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "abc" in str(response.data) or "does not exist" in str(response.data).lower()
        assert PurchaseOrder.objects.count() == 0

    def test_create_po_rejects_malformed_asset_uuid_returns_400(self, authenticated_client):
        """Malformed asset_id UUID must produce 400, not 500."""
        client, _ = authenticated_client

        supplier = SupplierFactory()

        url = reverse("purchaseorder-list")
        data = {
            "supplier": supplier.id,
            "items": [
                {"asset_id": "not-a-uuid", "quantity": 1, "unit_cost": 50.00},
            ],
        }
        response = client.post(url, data, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "invalid" in str(response.data).lower() or "not-a-uuid" in str(response.data)
        assert PurchaseOrder.objects.count() == 0

    def test_create_po_concurrent_posts_produce_distinct_po_numbers(self, authenticated_client):
        """Two racing auto-generations must not collide into duplicate po_numbers."""
        from unittest.mock import patch

        client, user = authenticated_client

        supplier = SupplierFactory()
        item_supplier = ItemSupplierFactory(
            supplier=supplier, quantity_per_package=1, unit_cost=Decimal("1.00")
        )

        # Create first PO normally so it holds the current auto-generated number.
        first = PurchaseOrder.objects.create(created_by=user, supplier=supplier)
        assert first.po_number is not None

        # Simulate the race: second PO's first auto-generation pass collides with
        # first.po_number. The save() retry loop must recover and pick a fresh one.
        real_auto_generate = PurchaseOrder.auto_generate_po_number
        call_count = {"n": 0}

        def fake_auto_generate(self):
            call_count["n"] += 1
            if call_count["n"] == 1:
                self.po_number = first.po_number
                return self.po_number
            return real_auto_generate(self)

        with patch.object(PurchaseOrder, "auto_generate_po_number", fake_auto_generate):
            second = PurchaseOrder.objects.create(created_by=user, supplier=supplier)

        assert call_count["n"] >= 2, "save() should have retried after the collision"
        assert second.po_number is not None
        assert second.po_number != first.po_number
        assert PurchaseOrder.objects.filter(po_number=first.po_number).count() == 1
        assert PurchaseOrder.objects.filter(po_number=second.po_number).count() == 1

        # End-to-end: create two POs back-to-back through the API; numbers must differ.
        url = reverse("purchaseorder-list")
        data = {
            "supplier": supplier.id,
            "items": [{"item_supplier_id": item_supplier.id, "quantity": 1}],
        }
        r1 = client.post(url, data, format="json")
        r2 = client.post(url, data, format="json")
        assert r1.status_code == status.HTTP_201_CREATED
        assert r2.status_code == status.HTTP_201_CREATED
        assert r1.data["po_number"] != r2.data["po_number"]

    def test_create_po_inventory_plus_freeform_with_float_unit_cost_returns_201(
        self, authenticated_client
    ):
        """Inventory line (Decimal total) + freeform line with float unit_cost must not 500."""
        client, _ = authenticated_client

        supplier = SupplierFactory()
        item_supplier = ItemSupplierFactory(supplier=supplier, quantity_per_package=1)
        # ItemSupplier.save() recomputes unit_cost from package_cost / quantity_per_package,
        # so pin both to get a deterministic unit_cost.
        item_supplier.package_cost = Decimal("2.00")
        item_supplier.quantity_per_package = 1
        item_supplier.save()

        url = reverse("purchaseorder-list")
        data = {
            "supplier": supplier.id,
            "items": [
                {"item_supplier_id": item_supplier.id, "quantity": 3},
                {"description": "Custom part", "quantity": 2, "unit_cost": 12.50},
            ],
        }
        response = client.post(url, data, format="json")

        assert response.status_code == status.HTTP_201_CREATED, response.data
        po = PurchaseOrder.objects.get(id=response.data["id"])
        # 3 * 2.00 + 2 * 12.50 = 6.00 + 25.00 = 31.00
        assert po.estimated_total == Decimal("31.00")
        assert po.items.count() == 2

    def test_create_po_inventory_plus_asset_with_float_unit_cost_returns_201(
        self, authenticated_client
    ):
        """Inventory line (Decimal total) + asset line with float unit_cost must not 400/500."""
        from inventory.tests.factories import AssetFactory

        client, _ = authenticated_client

        supplier = SupplierFactory()
        item_supplier = ItemSupplierFactory(supplier=supplier, quantity_per_package=1)
        item_supplier.package_cost = Decimal("4.00")
        item_supplier.quantity_per_package = 1
        item_supplier.save()
        asset = AssetFactory(manufacturer=supplier)

        url = reverse("purchaseorder-list")
        data = {
            "supplier": supplier.id,
            "items": [
                {"item_supplier_id": item_supplier.id, "quantity": 2},
                {"asset_id": str(asset.id), "quantity": 1, "unit_cost": 99.99},
            ],
        }
        response = client.post(url, data, format="json")

        assert response.status_code == status.HTTP_201_CREATED, response.data
        # Regression guard: the asset branch must not mask the arithmetic TypeError
        # as an "Invalid asset id" 400.
        assert "Invalid asset id" not in str(response.data)
        po = PurchaseOrder.objects.get(id=response.data["id"])
        # 2 * 4.00 + 1 * 99.99 = 8.00 + 99.99 = 107.99
        assert po.estimated_total == Decimal("107.99")
        assert po.items.count() == 2

    def test_create_po_freeform_plus_inventory_with_float_unit_cost_returns_201(
        self, authenticated_client
    ):
        """Ordering sanity: freeform first, then inventory — still must not 500."""
        client, _ = authenticated_client

        supplier = SupplierFactory()
        item_supplier = ItemSupplierFactory(supplier=supplier, quantity_per_package=1)
        item_supplier.package_cost = Decimal("3.00")
        item_supplier.quantity_per_package = 1
        item_supplier.save()

        url = reverse("purchaseorder-list")
        data = {
            "supplier": supplier.id,
            "items": [
                {"description": "Custom part", "quantity": 2, "unit_cost": 12.50},
                {"item_supplier_id": item_supplier.id, "quantity": 4},
            ],
        }
        response = client.post(url, data, format="json")

        assert response.status_code == status.HTTP_201_CREATED, response.data
        po = PurchaseOrder.objects.get(id=response.data["id"])
        # 2 * 12.50 + 4 * 3.00 = 25.00 + 12.00 = 37.00
        assert po.estimated_total == Decimal("37.00")
        assert po.items.count() == 2


@pytest.mark.integration
class TestPurchaseOrderMarkDelivered:
    """Tests for manual PurchaseOrder mark-delivered flow (AC-1, AC-2, AC-5)."""

    def _create_sent_po(self, user, *, sent_at=None, quantity_ordered=5):
        """Create a SENT purchase order with one item_supplier line."""
        supplier = SupplierFactory()
        item_supplier = ItemSupplierFactory(
            supplier=supplier, quantity_per_package=1, average_lead_time=10
        )
        purchase_order = PurchaseOrder.objects.create(
            po_number=f"PO-MD-{item_supplier.id}",
            supplier=supplier,
            status=PurchaseOrder.SENT,
            created_by=user,
            sent_by=user,
            sent_at=sent_at or (timezone.now() - timedelta(days=7)),
            estimated_total=Decimal("50.00"),
        )
        po_item = PurchaseOrderItem.objects.create(
            purchase_order=purchase_order,
            item_supplier=item_supplier,
            quantity_ordered=quantity_ordered,
            quantity_received=0,
            unit_cost_ordered=Decimal("10.00"),
            order_in_packages=quantity_ordered,
        )
        return purchase_order, po_item, item_supplier

    def test_mark_delivered_creates_order_delivery(self, authenticated_client):
        """AC-1: POST mark-delivered creates an OrderDelivery and returns 200."""
        client, user = authenticated_client
        purchase_order, po_item, _ = self._create_sent_po(user)

        url = reverse("purchaseorder-mark-delivered", kwargs={"pk": purchase_order.pk})
        delivery_date = (timezone.now() - timedelta(days=1)).date()
        response = client.post(
            url,
            {
                "delivery_date": delivery_date.isoformat(),
                "tracking_number": "1ZABC",
                "carrier": "UPS",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

        purchase_order.refresh_from_db()
        po_item.refresh_from_db()
        assert purchase_order.status == PurchaseOrder.RECEIVED
        assert po_item.quantity_received == po_item.quantity_ordered
        assert purchase_order.deliveries.count() == 1

        delivery = purchase_order.deliveries.first()
        assert delivery.delivery_date.date() == delivery_date
        assert delivery.tracking_number == "1ZABC"
        assert delivery.carrier == "UPS"
        assert delivery.received_by == user
        assert delivery.items.count() == 1

    def test_mark_delivered_populates_lead_time_analysis(self, authenticated_client):
        """AC-2 + AC-5: Lead Time Analysis returns the (supplier, item) row."""
        client, user = authenticated_client
        sent_at = timezone.now() - timedelta(days=10)
        purchase_order, po_item, item_supplier = self._create_sent_po(user, sent_at=sent_at)

        delivery_date = sent_at.date() + timedelta(days=5)
        mark_url = reverse("purchaseorder-mark-delivered", kwargs={"pk": purchase_order.pk})
        resp = client.post(
            mark_url,
            {"delivery_date": delivery_date.isoformat()},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK

        report_url = reverse("purchasing-reports-lead-time-analysis")
        report_resp = client.get(
            report_url,
            {
                "start_date": (delivery_date - timedelta(days=1)).isoformat(),
                "end_date": (delivery_date + timedelta(days=1)).isoformat(),
            },
        )
        assert report_resp.status_code == status.HTTP_200_OK

        rows = [
            row
            for row in report_resp.data
            if row["supplier_id"] == item_supplier.supplier.id
            and row["item_name"] == item_supplier.item.name
        ]
        assert len(rows) == 1
        row = rows[0]
        assert row["total_orders"] == 1
        # 5 calendar days between sent_at and delivery_date; business-day count
        # ranges from 3 to 5 depending on weekday alignment. Just assert we got
        # a positive lead time (i.e. the row wasn't empty/zero-filled).
        assert row["avg_actual_lead_time"] > 0

    def test_mark_delivered_missing_delivery_date_returns_400(self, authenticated_client):
        """AC-5: missing delivery_date returns 400."""
        client, user = authenticated_client
        purchase_order, _, _ = self._create_sent_po(user)

        url = reverse("purchaseorder-mark-delivered", kwargs={"pk": purchase_order.pk})
        response = client.post(url, {}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "delivery_date" in response.data["error"]["details"]

    def test_mark_delivered_requires_authentication(self, api_client):
        """AC-1: unauthenticated request must be rejected."""
        user = User.objects.create_user(username="po-owner", password="pw12345678")
        supplier = SupplierFactory()
        purchase_order = PurchaseOrder.objects.create(
            po_number="PO-MD-AUTH",
            supplier=supplier,
            status=PurchaseOrder.SENT,
            created_by=user,
            sent_at=timezone.now(),
        )

        url = reverse("purchaseorder-mark-delivered", kwargs={"pk": purchase_order.pk})
        response = api_client.post(
            url,
            {"delivery_date": timezone.now().date().isoformat()},
            format="json",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_mark_delivered_rejects_non_sendable_status(self, authenticated_client):
        """Draft/cancelled/received orders cannot be re-delivered."""
        client, user = authenticated_client
        purchase_order, _, _ = self._create_sent_po(user)
        purchase_order.status = PurchaseOrder.DRAFT
        purchase_order.save()

        url = reverse("purchaseorder-mark-delivered", kwargs={"pk": purchase_order.pk})
        response = client.post(
            url,
            {"delivery_date": timezone.now().date().isoformat()},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
class TestPurchaseOrderReceive:
    """Tests for the per-line-item PO receive endpoint (POST .../receive/)."""

    def _create_po(self, user, *, lines=None, status_=PurchaseOrder.SENT):
        """Create a PO in ``status_`` with one item_supplier line per spec.

        ``lines`` is a list of dicts with optional ``quantity_ordered`` (default
        5), ``quantity_received`` (default 0) and ``current_stock`` (default 0)
        keys. Defaults to a single 5-unit line. Returns ``(po, [po_items])``.
        """
        if lines is None:
            lines = [{}]
        supplier = SupplierFactory()
        purchase_order = PurchaseOrder.objects.create(
            supplier=supplier,
            status=status_,
            created_by=user,
            sent_by=user,
            sent_at=timezone.now() - timedelta(days=7),
            estimated_total=Decimal("50.00"),
        )
        po_items = []
        for spec in lines:
            quantity_ordered = spec.get("quantity_ordered", 5)
            item_supplier = ItemSupplierFactory(
                supplier=supplier,
                quantity_per_package=1,
                average_lead_time=10,
                item=InventoryItemFactory(current_stock=spec.get("current_stock", 0)),
            )
            po_item = PurchaseOrderItem.objects.create(
                purchase_order=purchase_order,
                item_supplier=item_supplier,
                quantity_ordered=quantity_ordered,
                quantity_received=spec.get("quantity_received", 0),
                unit_cost_ordered=Decimal("10.00"),
                order_in_packages=quantity_ordered,
            )
            po_items.append(po_item)
        return purchase_order, po_items

    @staticmethod
    def _url(purchase_order):
        return reverse("purchaseorder-receive", kwargs={"pk": purchase_order.pk})

    def test_receive_partial_updates_status_stock_and_delivery(self, authenticated_client):
        """Partial receive advances the PO to PARTIALLY_RECEIVED and bumps stock."""
        client, user = authenticated_client
        purchase_order, (po_item,) = self._create_po(
            user, lines=[{"quantity_ordered": 5, "current_stock": 10}]
        )

        response = client.post(
            self._url(purchase_order),
            {"items": [{"purchase_order_item": po_item.id, "quantity_received": 2}]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        purchase_order.refresh_from_db()
        po_item.refresh_from_db()
        po_item.item.refresh_from_db()

        assert purchase_order.status == PurchaseOrder.PARTIALLY_RECEIVED
        assert po_item.quantity_received == 2
        assert po_item.item.current_stock == 12  # 10 + 2

        assert purchase_order.deliveries.count() == 1
        delivery = purchase_order.deliveries.first()
        assert delivery.received_by == user
        assert delivery.is_complete is False
        assert delivery.items.count() == 1
        assert delivery.items.first().quantity_received == 2
        # Not fully received yet -> no lead-time row.
        assert LeadTimeLog.objects.filter(purchase_order=purchase_order).count() == 0

    def test_receive_full_marks_received_and_complete(self, authenticated_client):
        """Receiving the full pending quantity advances the PO to RECEIVED."""
        client, user = authenticated_client
        purchase_order, (po_item,) = self._create_po(
            user, lines=[{"quantity_ordered": 5, "current_stock": 10}]
        )

        response = client.post(
            self._url(purchase_order),
            {"items": [{"purchase_order_item": po_item.id, "quantity_received": 5}]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        purchase_order.refresh_from_db()
        po_item.refresh_from_db()
        po_item.item.refresh_from_db()

        assert purchase_order.status == PurchaseOrder.RECEIVED
        assert po_item.quantity_received == 5
        assert po_item.item.current_stock == 15  # 10 + 5

        delivery = purchase_order.deliveries.first()
        assert delivery.is_complete is True
        # Fully received -> a lead-time row is recorded for the analysis report.
        assert LeadTimeLog.objects.filter(purchase_order=purchase_order).count() == 1

    def test_receive_multiple_lines_single_delivery(self, authenticated_client):
        """Several lines in one request produce one delivery with one item each."""
        client, user = authenticated_client
        purchase_order, (line_a, line_b) = self._create_po(
            user,
            lines=[
                {"quantity_ordered": 5, "current_stock": 1},
                {"quantity_ordered": 4, "current_stock": 2},
            ],
        )

        response = client.post(
            self._url(purchase_order),
            {
                "items": [
                    {"purchase_order_item": line_a.id, "quantity_received": 3},
                    {"purchase_order_item": line_b.id, "quantity_received": 4},
                ]
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        purchase_order.refresh_from_db()
        line_a.refresh_from_db()
        line_b.refresh_from_db()
        line_a.item.refresh_from_db()
        line_b.item.refresh_from_db()

        # line_a partial (3/5), line_b full (4/4) -> PO is partially received.
        assert purchase_order.status == PurchaseOrder.PARTIALLY_RECEIVED
        assert line_a.quantity_received == 3
        assert line_b.quantity_received == 4
        assert line_a.item.current_stock == 4  # 1 + 3
        assert line_b.item.current_stock == 6  # 2 + 4

        assert purchase_order.deliveries.count() == 1
        assert purchase_order.deliveries.first().items.count() == 2

    def test_receive_from_partially_received_advances_to_received(self, authenticated_client):
        """A second receipt off a PARTIALLY_RECEIVED PO can complete it."""
        client, user = authenticated_client
        purchase_order, (po_item,) = self._create_po(
            user,
            status_=PurchaseOrder.PARTIALLY_RECEIVED,
            lines=[{"quantity_ordered": 5, "quantity_received": 2, "current_stock": 7}],
        )

        response = client.post(
            self._url(purchase_order),
            {"items": [{"purchase_order_item": po_item.id, "quantity_received": 3}]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        purchase_order.refresh_from_db()
        po_item.refresh_from_db()
        po_item.item.refresh_from_db()

        assert purchase_order.status == PurchaseOrder.RECEIVED
        assert po_item.quantity_received == 5
        assert po_item.item.current_stock == 10  # 7 + 3

    def test_receive_defaults_delivery_date_to_now(self, authenticated_client):
        """Omitting delivery_date dates the delivery to now."""
        client, user = authenticated_client
        purchase_order, (po_item,) = self._create_po(user)

        response = client.post(
            self._url(purchase_order),
            {"items": [{"purchase_order_item": po_item.id, "quantity_received": 1}]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        delivery = purchase_order.deliveries.first()
        assert delivery.delivery_date.date() == timezone.now().date()

    def test_receive_honors_explicit_delivery_date(self, authenticated_client):
        """An explicit delivery_date is recorded on the delivery."""
        client, user = authenticated_client
        purchase_order, (po_item,) = self._create_po(user)
        delivery_date = (timezone.now() - timedelta(days=1)).date()

        response = client.post(
            self._url(purchase_order),
            {
                "items": [{"purchase_order_item": po_item.id, "quantity_received": 1}],
                "delivery_date": delivery_date.isoformat(),
                "tracking_number": "1ZRCV",
                "carrier": "UPS",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        delivery = purchase_order.deliveries.first()
        assert delivery.delivery_date.date() == delivery_date
        assert delivery.tracking_number == "1ZRCV"
        assert delivery.carrier == "UPS"

    def test_receive_records_audit_event(self, authenticated_client):
        """A successful receive writes a po_receive_items audit row."""
        client, user = authenticated_client
        purchase_order, (po_item,) = self._create_po(user)

        response = client.post(
            self._url(purchase_order),
            {"items": [{"purchase_order_item": po_item.id, "quantity_received": 1}]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        event = PurchaseOrderAuditEvent.objects.filter(
            purchase_order=purchase_order,
            action=PurchaseOrderAuditEvent.ACTION_PO_RECEIVE_ITEMS,
        ).first()
        assert event is not None
        assert event.actor == user
        assert event.metadata["received_items"] == [
            {"purchase_order_item": po_item.id, "quantity_received": 1}
        ]

    def test_receive_over_pending_returns_400(self, authenticated_client):
        """Receiving more than pending is rejected with no side effects."""
        client, user = authenticated_client
        purchase_order, (po_item,) = self._create_po(
            user, lines=[{"quantity_ordered": 5, "current_stock": 10}]
        )

        response = client.post(
            self._url(purchase_order),
            {"items": [{"purchase_order_item": po_item.id, "quantity_received": 6}]},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "exceeds pending" in response.data["error"]
        purchase_order.refresh_from_db()
        po_item.refresh_from_db()
        po_item.item.refresh_from_db()
        assert po_item.quantity_received == 0
        assert po_item.item.current_stock == 10
        assert purchase_order.deliveries.count() == 0

    def test_receive_duplicate_line_cannot_over_receive(self, authenticated_client):
        """The same line referenced twice cannot collectively exceed pending."""
        client, user = authenticated_client
        purchase_order, (po_item,) = self._create_po(
            user, lines=[{"quantity_ordered": 5, "current_stock": 0}]
        )

        response = client.post(
            self._url(purchase_order),
            {
                "items": [
                    {"purchase_order_item": po_item.id, "quantity_received": 3},
                    {"purchase_order_item": po_item.id, "quantity_received": 3},
                ]
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "exceeds pending" in response.data["error"]
        po_item.refresh_from_db()
        assert po_item.quantity_received == 0
        assert purchase_order.deliveries.count() == 0

    def test_receive_item_from_other_po_returns_400(self, authenticated_client):
        """A line belonging to another PO is rejected."""
        client, user = authenticated_client
        purchase_order, _ = self._create_po(user)
        other_po, (other_item,) = self._create_po(user)

        response = client.post(
            self._url(purchase_order),
            {"items": [{"purchase_order_item": other_item.id, "quantity_received": 1}]},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "does not belong" in response.data["error"]
        assert purchase_order.deliveries.count() == 0
        other_item.refresh_from_db()
        assert other_item.quantity_received == 0

    def test_receive_voided_item_returns_400(self, authenticated_client):
        """A voided line cannot be received."""
        client, user = authenticated_client
        purchase_order, (po_item,) = self._create_po(user)
        po_item.is_voided = True
        po_item.save()

        response = client.post(
            self._url(purchase_order),
            {"items": [{"purchase_order_item": po_item.id, "quantity_received": 1}]},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "voided" in response.data["error"]
        assert purchase_order.deliveries.count() == 0

    def test_receive_rejects_non_receivable_status(self, authenticated_client):
        """Draft/received/cancelled orders cannot receive items."""
        client, user = authenticated_client
        purchase_order, (po_item,) = self._create_po(user, status_=PurchaseOrder.DRAFT)

        response = client.post(
            self._url(purchase_order),
            {"items": [{"purchase_order_item": po_item.id, "quantity_received": 1}]},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert purchase_order.deliveries.count() == 0

    def test_receive_empty_items_returns_400(self, authenticated_client):
        """An empty items list is rejected by the serializer."""
        client, user = authenticated_client
        purchase_order, _ = self._create_po(user)

        response = client.post(
            self._url(purchase_order),
            {"items": []},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "items" in str(response.data)
        assert purchase_order.deliveries.count() == 0

    def test_receive_missing_items_returns_400(self, authenticated_client):
        """A missing items key is rejected by the serializer."""
        client, user = authenticated_client
        purchase_order, _ = self._create_po(user)

        response = client.post(self._url(purchase_order), {}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "items" in str(response.data)

    def test_receive_zero_quantity_returns_400(self, authenticated_client):
        """A quantity below 1 is rejected by the serializer."""
        client, user = authenticated_client
        purchase_order, (po_item,) = self._create_po(user)

        response = client.post(
            self._url(purchase_order),
            {"items": [{"purchase_order_item": po_item.id, "quantity_received": 0}]},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert purchase_order.deliveries.count() == 0

    def test_receive_requires_authentication(self, api_client, db):
        """Unauthenticated requests are rejected."""
        user = User.objects.create_user(username="po-rcv-owner", password="pw12345678")
        purchase_order, (po_item,) = self._create_po(user)

        response = api_client.post(
            self._url(purchase_order),
            {"items": [{"purchase_order_item": po_item.id, "quantity_received": 1}]},
            format="json",
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert purchase_order.deliveries.count() == 0


@pytest.mark.integration
class TestPurchaseOrderVoid:
    """Tests for PurchaseOrder void action (oms-rrx)."""

    def _make_po_with_items(self, user, *, status_=PurchaseOrder.SENT, item_count=2):
        supplier = SupplierFactory()
        purchase_order = PurchaseOrder.objects.create(
            supplier=supplier,
            status=status_,
            created_by=user,
            estimated_total=Decimal("100.00"),
        )
        items = []
        for _ in range(item_count):
            item_supplier = ItemSupplierFactory(supplier=supplier)
            items.append(
                PurchaseOrderItem.objects.create(
                    purchase_order=purchase_order,
                    item_supplier=item_supplier,
                    quantity_ordered=5,
                    unit_cost_ordered=Decimal("10.00"),
                )
            )
        return purchase_order, items

    def _staff_client(self):
        client = APIClient()
        user = User.objects.create_user(
            username="po-void-staff",
            email="staff@example.com",
            password="pw12345678",
            is_staff=True,
        )
        client.force_authenticate(user=user)
        return client, user

    def test_void_changes_status_and_records_metadata(self):
        """AC-2: voiding sets status=voided and records voided_at/voided_by/void_reason."""
        client, user = self._staff_client()
        purchase_order, _ = self._make_po_with_items(user)

        url = reverse("purchaseorder-void", kwargs={"pk": purchase_order.pk})
        response = client.post(url, {"reason": "supplier rejected all lines"}, format="json")

        assert response.status_code == status.HTTP_200_OK
        purchase_order.refresh_from_db()
        assert purchase_order.status == PurchaseOrder.VOIDED
        assert purchase_order.voided_at is not None
        assert purchase_order.voided_by == user
        assert purchase_order.void_reason == "supplier rejected all lines"

    def test_void_cascades_to_unvoided_line_items(self):
        """AC-2: voiding cascades is_voided=True to non-voided line items."""
        client, user = self._staff_client()
        purchase_order, items = self._make_po_with_items(user, item_count=3)
        # Pre-void one item to confirm we don't overwrite its existing void_reason
        already_voided = items[0]
        already_voided.is_voided = True
        already_voided.void_reason = "previously voided"
        already_voided.save()

        url = reverse("purchaseorder-void", kwargs={"pk": purchase_order.pk})
        response = client.post(url, {"reason": "orphaned"}, format="json")

        assert response.status_code == status.HTTP_200_OK
        for line_item in items:
            line_item.refresh_from_db()
            assert line_item.is_voided is True

        # Cascaded items get the cascade reason; pre-voided keeps its own reason
        already_voided.refresh_from_db()
        assert already_voided.void_reason == "previously voided"
        for line_item in items[1:]:
            line_item.refresh_from_db()
            assert line_item.void_reason == "PO voided"
            assert line_item.voided_by == user
            assert line_item.voided_at is not None

    def test_cannot_void_received_po(self):
        """AC-3: voiding a received PO returns 400."""
        client, user = self._staff_client()
        purchase_order, _ = self._make_po_with_items(user, status_=PurchaseOrder.RECEIVED)

        url = reverse("purchaseorder-void", kwargs={"pk": purchase_order.pk})
        response = client.post(url, {"reason": "oops"}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        purchase_order.refresh_from_db()
        assert purchase_order.status == PurchaseOrder.RECEIVED

    def test_cannot_double_void(self):
        """AC-2: voiding an already-voided PO returns 400."""
        client, user = self._staff_client()
        purchase_order, _ = self._make_po_with_items(user)

        url = reverse("purchaseorder-void", kwargs={"pk": purchase_order.pk})
        first = client.post(url, {"reason": "first"}, format="json")
        assert first.status_code == status.HTTP_200_OK

        second = client.post(url, {"reason": "second"}, format="json")
        assert second.status_code == status.HTTP_400_BAD_REQUEST

    def test_non_staff_cannot_void(self, authenticated_client):
        """AC-4: non-staff/non-COO users cannot void POs."""
        client, user = authenticated_client
        purchase_order, _ = self._make_po_with_items(user)

        url = reverse("purchaseorder-void", kwargs={"pk": purchase_order.pk})
        response = client.post(url, {"reason": "nope"}, format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        purchase_order.refresh_from_db()
        assert purchase_order.status != PurchaseOrder.VOIDED

    def test_coo_group_member_can_void(self):
        """COO group members may void POs without is_staff."""
        from django.contrib.auth.models import Group

        coo_group, _ = Group.objects.get_or_create(name="COO")
        user = User.objects.create_user(
            username="po-void-coo",
            email="coo@example.com",
            password="pw12345678",
        )
        user.groups.add(coo_group)
        client = APIClient()
        client.force_authenticate(user=user)

        purchase_order, _ = self._make_po_with_items(user)
        url = reverse("purchaseorder-void", kwargs={"pk": purchase_order.pk})
        response = client.post(url, {"reason": "coo"}, format="json")

        assert response.status_code == status.HTTP_200_OK
        purchase_order.refresh_from_db()
        assert purchase_order.status == PurchaseOrder.VOIDED


@pytest.mark.integration
class TestPurchaseOrderMetadataAndAttachments:
    """Tests for editable metadata fields and file attachments on POs (oms-aq2)."""

    def _make_po(self, user):
        supplier = SupplierFactory()
        purchase_order = PurchaseOrder.objects.create(
            supplier=supplier,
            status=PurchaseOrder.SENT,
            created_by=user,
        )
        return purchase_order

    def _file(self, name="sales-order.pdf", content=b"PDF-1.4 stub"):
        from django.core.files.uploadedfile import SimpleUploadedFile

        return SimpleUploadedFile(name=name, content=content, content_type="application/pdf")

    def test_patch_updates_supplier_and_sales_order_numbers(self, authenticated_client):
        """AC-1: PATCH allows setting supplier_order_number, sales_order_number, expected_delivery_date."""
        client, user = authenticated_client
        purchase_order = self._make_po(user)

        url = reverse("purchaseorder-detail", kwargs={"pk": purchase_order.pk})
        payload = {
            "supplier_order_number": "SUP-12345",
            "sales_order_number": "SO-99",
            "expected_delivery_date": "2026-05-15",
        }
        response = client.patch(url, payload, format="json")

        assert response.status_code == status.HTTP_200_OK
        purchase_order.refresh_from_db()
        assert purchase_order.supplier_order_number == "SUP-12345"
        assert purchase_order.sales_order_number == "SO-99"
        assert str(purchase_order.expected_delivery_date) == "2026-05-15"

    def test_po_detail_includes_metadata_fields(self, api_client, authenticated_client):
        """AC-1/AC-4: PO detail response surfaces the new metadata fields."""
        _, user = authenticated_client
        purchase_order = self._make_po(user)
        purchase_order.supplier_order_number = "SUP-7"
        purchase_order.sales_order_number = "SO-7"
        purchase_order.save()

        url = reverse("purchaseorder-detail", kwargs={"pk": purchase_order.pk})
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["supplier_order_number"] == "SUP-7"
        assert response.data["sales_order_number"] == "SO-7"
        assert response.data["attachments"] == []

    def test_upload_attachment_creates_record(self, authenticated_client):
        """AC-3: authenticated users may upload an attachment to a PO."""
        client, user = authenticated_client
        purchase_order = self._make_po(user)

        url = reverse("purchaseorder-upload-attachment", kwargs={"pk": purchase_order.pk})
        response = client.post(
            url,
            {"file": self._file(), "description": "Sales order from supplier"},
            format="multipart",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["description"] == "Sales order from supplier"
        assert response.data["uploaded_by"] == user.id
        assert response.data["file_url"]
        assert purchase_order.attachments.count() == 1

    def test_upload_attachment_requires_authentication(self, api_client, authenticated_client):
        """AC-3: anonymous uploads are rejected."""
        _, user = authenticated_client
        purchase_order = self._make_po(user)

        url = reverse("purchaseorder-upload-attachment", kwargs={"pk": purchase_order.pk})
        response = api_client.post(url, {"file": self._file()}, format="multipart")

        assert response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)
        assert purchase_order.attachments.count() == 0

    def test_attachments_appear_in_po_detail(self, authenticated_client):
        """AC-4: PO detail nests attachments with download URLs."""
        client, user = authenticated_client
        purchase_order = self._make_po(user)

        upload_url = reverse("purchaseorder-upload-attachment", kwargs={"pk": purchase_order.pk})
        client.post(upload_url, {"file": self._file(), "description": "doc"}, format="multipart")

        detail_url = reverse("purchaseorder-detail", kwargs={"pk": purchase_order.pk})
        response = client.get(detail_url)

        assert response.status_code == status.HTTP_200_OK
        attachments = response.data["attachments"]
        assert len(attachments) == 1
        assert attachments[0]["description"] == "doc"
        assert attachments[0]["file_url"]
        assert attachments[0]["uploaded_by_name"]

    def test_delete_attachment_requires_staff(self, authenticated_client):
        """AC-6: non-staff users cannot delete attachments."""
        client, user = authenticated_client
        purchase_order = self._make_po(user)

        upload_url = reverse("purchaseorder-upload-attachment", kwargs={"pk": purchase_order.pk})
        upload_response = client.post(upload_url, {"file": self._file()}, format="multipart")
        attachment_id = upload_response.data["id"]

        delete_url = reverse(
            "purchaseorder-destroy-attachment",
            kwargs={"pk": purchase_order.pk, "attachment_id": attachment_id},
        )
        response = client.delete(delete_url)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert purchase_order.attachments.count() == 1

    def test_delete_attachment_succeeds_for_staff(self, admin_user):
        """AC-6: staff users can delete attachments."""
        client = APIClient()
        client.force_authenticate(user=admin_user)
        purchase_order = self._make_po(admin_user)

        upload_url = reverse("purchaseorder-upload-attachment", kwargs={"pk": purchase_order.pk})
        upload_response = client.post(upload_url, {"file": self._file()}, format="multipart")
        attachment_id = upload_response.data["id"]

        delete_url = reverse(
            "purchaseorder-destroy-attachment",
            kwargs={"pk": purchase_order.pk, "attachment_id": attachment_id},
        )
        response = client.delete(delete_url)

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert purchase_order.attachments.count() == 0


@pytest.mark.integration
class TestPurchaseOrderListVoidHandling:
    """Tests for oms-a8o: zero-out and red-out voided POs in the list endpoint.

    The /reorders/purchase-orders/ list must hide POs whose line items are all
    voided, and the displayed estimated total must subtract voided line items.
    Detail retrieval is unaffected — deep links to fully-voided POs still work.
    """

    def _make_po_with_items(self, user, *, item_count=2, unit_cost=None):
        if unit_cost is None:
            unit_cost = Decimal("10.00")
        supplier = SupplierFactory()
        purchase_order = PurchaseOrder.objects.create(
            supplier=supplier,
            status=PurchaseOrder.SENT,
            created_by=user,
        )
        items = []
        for _ in range(item_count):
            item_supplier = ItemSupplierFactory(supplier=supplier)
            items.append(
                PurchaseOrderItem.objects.create(
                    purchase_order=purchase_order,
                    item_supplier=item_supplier,
                    quantity_ordered=5,
                    unit_cost_ordered=unit_cost,
                )
            )
        purchase_order.calculate_estimated_total()
        purchase_order.save()
        return purchase_order, items

    def _void_item(self, item, user):
        item.is_voided = True
        item.voided_at = timezone.now()
        item.voided_by = user
        item.void_reason = "discontinued"
        item.save()

    def test_list_excludes_po_with_all_voided_items(self, authenticated_client):
        """A PO whose every line item is voided must not appear in the list."""
        client, user = authenticated_client
        all_voided_po, items = self._make_po_with_items(user, item_count=2)
        for item in items:
            self._void_item(item, user)

        # A second PO with at least one active line should still appear.
        active_po, active_items = self._make_po_with_items(user, item_count=2)
        self._void_item(active_items[0], user)

        url = reverse("purchaseorder-list")
        response = client.get(url)
        assert response.status_code == status.HTTP_200_OK

        listed_ids = {row["id"] for row in response.data["results"]}
        assert all_voided_po.id not in listed_ids
        assert active_po.id in listed_ids

    def test_retrieve_still_returns_fully_voided_po(self, authenticated_client):
        """Hiding from the list must not 404 on detail retrieval."""
        client, user = authenticated_client
        purchase_order, items = self._make_po_with_items(user)
        for item in items:
            self._void_item(item, user)

        url = reverse("purchaseorder-detail", kwargs={"pk": purchase_order.pk})
        response = client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["id"] == purchase_order.id

    def test_estimated_total_subtracts_voided_line_items(self, authenticated_client):
        """Voided lines are subtracted from the displayed estimated total."""
        client, user = authenticated_client
        # 2 items × 5 qty × $10 = $100 stored estimated_total
        purchase_order, items = self._make_po_with_items(user, item_count=2)
        assert purchase_order.estimated_total == Decimal("100.00")

        # Void one line: 5 × $10 = $50 worth of items removed.
        self._void_item(items[0], user)

        url = reverse("purchaseorder-detail", kwargs={"pk": purchase_order.pk})
        response = client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert Decimal(response.data["estimated_total"]) == Decimal("50.00")

    def test_total_items_and_quantity_exclude_voided(self, authenticated_client):
        """Counts shown in the list must not include voided lines."""
        client, user = authenticated_client
        purchase_order, items = self._make_po_with_items(user, item_count=3)
        self._void_item(items[0], user)

        url = reverse("purchaseorder-detail", kwargs={"pk": purchase_order.pk})
        response = client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["total_items"] == 2
        assert response.data["total_quantity"] == 10  # 2 lines × 5 qty


@pytest.mark.integration
class TestPurchaseOrderAutoTransitionToSent:
    """DRAFT -> SENT auto-transition when a sales order number is attached (oms-qdxss).

    A draft PO that receives a non-blank sales_order_number is treated as
    submitted to the supplier: status flips to SENT, sent_by/sent_at are
    stamped, and a ``po_send`` audit event is recorded — the same effect as the
    explicit ``send_to_supplier`` action. The trigger is one-way and fires only
    on the empty -> non-empty edge.
    """

    def _draft_po(self, user, **kwargs):
        return PurchaseOrder.objects.create(
            supplier=SupplierFactory(),
            status=PurchaseOrder.DRAFT,
            created_by=user,
            **kwargs,
        )

    def _detail_url(self, po):
        return reverse("purchaseorder-detail", kwargs={"pk": po.pk})

    def _po_send_events(self, po=None):
        qs = PurchaseOrderAuditEvent.objects.filter(action=PurchaseOrderAuditEvent.ACTION_PO_SEND)
        return qs.filter(purchase_order=po) if po is not None else qs

    def test_attach_sales_order_number_to_draft_transitions_to_sent(self, authenticated_client):
        """Attaching a sales order number to a DRAFT PO sends it to the supplier."""
        client, user = authenticated_client
        po = self._draft_po(user)

        response = client.patch(
            self._detail_url(po), {"sales_order_number": "SO-123"}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        po.refresh_from_db()
        assert po.status == PurchaseOrder.SENT
        assert po.sales_order_number == "SO-123"
        assert po.sent_at is not None
        assert po.sent_by == user

    def test_attach_sales_order_number_records_po_send_audit_event(self, authenticated_client):
        """The auto-transition records the same po_send audit event as send_to_supplier."""
        client, user = authenticated_client
        po = self._draft_po(user)

        client.patch(self._detail_url(po), {"sales_order_number": "SO-123"}, format="json")

        event = self._po_send_events(po).get()
        assert event.actor == user
        assert event.purchase_order == po
        assert event.metadata["po_number"] == po.po_number

    def test_already_sent_po_edit_does_not_restamp(self, authenticated_client):
        """Editing the sales order number on an already-SENT PO must not re-stamp it."""
        client, user = authenticated_client
        sender = User.objects.create_user(username="sender", email="s@example.com", password="x")
        original_sent_at = timezone.now() - timedelta(days=3)
        po = PurchaseOrder.objects.create(
            supplier=SupplierFactory(),
            status=PurchaseOrder.SENT,
            created_by=user,
            sent_by=sender,
            sent_at=original_sent_at,
            sales_order_number="SO-OLD",
        )

        response = client.patch(
            self._detail_url(po), {"sales_order_number": "SO-NEW"}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        po.refresh_from_db()
        assert po.status == PurchaseOrder.SENT
        assert po.sales_order_number == "SO-NEW"
        # sent_by / sent_at untouched, and no new po_send event recorded.
        assert po.sent_by == sender
        assert po.sent_at == original_sent_at
        assert not self._po_send_events(po).exists()

    @pytest.mark.parametrize("blank_value", ["", "   ", "\t"])
    def test_attach_blank_sales_order_number_does_not_transition(
        self, authenticated_client, blank_value
    ):
        """Empty or whitespace-only sales order numbers are not a trigger."""
        client, user = authenticated_client
        po = self._draft_po(user)

        response = client.patch(
            self._detail_url(po), {"sales_order_number": blank_value}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        po.refresh_from_db()
        assert po.status == PurchaseOrder.DRAFT
        assert po.sent_at is None
        assert not self._po_send_events(po).exists()

    def test_editing_other_fields_on_draft_does_not_transition(self, authenticated_client):
        """Editing unrelated fields on a DRAFT PO must not change its status."""
        client, user = authenticated_client
        po = self._draft_po(user)

        response = client.patch(self._detail_url(po), {"notes": "rush order"}, format="json")

        assert response.status_code == status.HTTP_200_OK, response.data
        po.refresh_from_db()
        assert po.status == PurchaseOrder.DRAFT
        assert po.notes == "rush order"
        assert po.sent_at is None
        assert not self._po_send_events(po).exists()

    def test_clearing_sales_order_number_on_sent_po_stays_sent(self, authenticated_client):
        """Clearing the sales order number is one-way: a SENT PO is not un-sent."""
        client, user = authenticated_client
        po = PurchaseOrder.objects.create(
            supplier=SupplierFactory(),
            status=PurchaseOrder.SENT,
            created_by=user,
            sent_by=user,
            sent_at=timezone.now(),
            sales_order_number="SO-123",
        )

        response = client.patch(self._detail_url(po), {"sales_order_number": ""}, format="json")

        assert response.status_code == status.HTTP_200_OK, response.data
        po.refresh_from_db()
        assert po.status == PurchaseOrder.SENT
        assert po.sales_order_number == ""

    def test_create_with_sales_order_number_transitions_to_sent(self, authenticated_client):
        """Creating a PO with a sales order number sends it to the supplier immediately."""
        client, user = authenticated_client
        supplier = SupplierFactory()
        item_supplier = ItemSupplierFactory(supplier=supplier, quantity_per_package=1)

        response = client.post(
            reverse("purchaseorder-list"),
            {
                "supplier": supplier.id,
                "sales_order_number": "SO-555",
                "items": [
                    {"item_supplier_id": item_supplier.id, "quantity": 2, "unit_cost": "4.00"}
                ],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert response.data["status"] == PurchaseOrder.SENT
        assert response.data["sales_order_number"] == "SO-555"

        po = PurchaseOrder.objects.get(id=response.data["id"])
        assert po.status == PurchaseOrder.SENT
        assert po.sent_at is not None
        assert po.sent_by == user
        # Both the create and the auto-send are audited.
        assert self._po_send_events(po).count() == 1

    def test_create_without_sales_order_number_stays_draft(self, authenticated_client):
        """A normal create (no sales order number) stays DRAFT and is not auto-sent."""
        client, user = authenticated_client
        supplier = SupplierFactory()
        item_supplier = ItemSupplierFactory(supplier=supplier, quantity_per_package=1)

        response = client.post(
            reverse("purchaseorder-list"),
            {
                "supplier": supplier.id,
                "items": [
                    {"item_supplier_id": item_supplier.id, "quantity": 2, "unit_cost": "4.00"}
                ],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert response.data["status"] == PurchaseOrder.DRAFT
        po = PurchaseOrder.objects.get(id=response.data["id"])
        assert po.sent_at is None
        assert not self._po_send_events(po).exists()

    def test_send_to_supplier_action_records_po_send_audit_event(self, authenticated_client):
        """The explicit send_to_supplier action shares the SENT transition + audit event."""
        client, user = authenticated_client
        po = self._draft_po(user)

        response = client.post(
            reverse("purchaseorder-send-to-supplier", kwargs={"pk": po.pk}), {}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        po.refresh_from_db()
        assert po.status == PurchaseOrder.SENT
        assert po.sent_by == user
        assert po.sent_at is not None
        event = self._po_send_events(po).get()
        assert event.purchase_order == po
        assert event.actor == user
