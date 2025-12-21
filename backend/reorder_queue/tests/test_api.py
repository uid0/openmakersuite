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

from inventory.tests.factories import InventoryItemFactory, ItemSupplierFactory, SupplierFactory
from reorder_queue.models import PurchaseOrder, PurchaseOrderItem
from reorder_queue.tests.factories import ReorderRequestFactory

User = get_user_model()


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

    def test_create_reorder_request_public(self, api_client):
        """Test anyone can create a reorder request."""
        item = InventoryItemFactory()

        url = reverse("reorderrequest-list")
        data = {
            "item": str(item.id),
            "quantity": 25,
            "requested_by": "Jane Doe",
            "request_notes": "We are running low",
            "priority": "high",
        }
        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert str(response.data["item"]) == str(item.id)
        assert response.data["quantity"] == 25
        assert response.data["status"] == "pending"

    def test_create_reorder_request_minimal(self, api_client):
        """Test creating request with minimal required fields."""
        item = InventoryItemFactory()

        url = reverse("reorderrequest-list")
        data = {"item": str(item.id), "quantity": 10}
        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_201_CREATED

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

    def test_generate_cart_links(self, authenticated_client):
        """Test generating shopping cart links."""
        client, user = authenticated_client

        supplier1 = SupplierFactory(supplier_type="amazon")
        supplier2 = SupplierFactory(supplier_type="grainger")

        item1 = InventoryItemFactory(
            supplier=supplier1,
            supplier_sku="AMZN-123",
            supplier_url="https://amazon.com/item1",
        )
        item2 = InventoryItemFactory(
            supplier=supplier2,
            supplier_sku="GRNG-456",
            supplier_url="https://grainger.com/item2",
        )

        ReorderRequestFactory(item=item1, status="approved")
        ReorderRequestFactory(item=item2, status="approved")

        url = reverse("reorderrequest-generate-cart-links")
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert "amazon" in response.data
        assert "grainger" in response.data
        assert len(response.data["amazon"]["items"]) == 1
        assert len(response.data["grainger"]["items"]) == 1


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
