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
        assert "delivery_date" in response.data

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
