"""
API tests for inventory endpoints.
"""

from django.urls import reverse

import pytest
from rest_framework import status

from inventory.models import InventoryItem, UsageLog
from inventory.tests.factories import (
    CategoryFactory,
    InventoryItemFactory,
    SupplierFactory,
    UsageLogFactory,
)


@pytest.mark.integration
class TestSupplierAPI:
    """Tests for Supplier API endpoints."""

    def test_list_suppliers(self, api_client):
        """Test listing suppliers."""
        SupplierFactory.create_batch(3)
        url = reverse("supplier-list")
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 3
        assert len(response.data["results"]) == 3

    def test_create_supplier_requires_auth(self, api_client):
        """Test creating supplier requires authentication."""
        url = reverse("supplier-list")
        data = {"name": "New Supplier", "supplier_type": "amazon"}
        response = api_client.post(url, data)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_create_supplier_authenticated(self, authenticated_client):
        """Test creating supplier when authenticated."""
        client, user = authenticated_client
        url = reverse("supplier-list")
        data = {
            "name": "New Supplier",
            "supplier_type": "online",  # Using new supplier type choices
            "website": "https://example.com",
        }
        response = client.post(url, data)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "New Supplier"


@pytest.mark.integration
class TestCategoryAPI:
    """Tests for Category API endpoints."""

    def test_list_categories(self, api_client):
        """Test listing categories."""
        CategoryFactory.create_batch(5)
        url = reverse("category-list")
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 5
        assert len(response.data["results"]) == 5

    def test_retrieve_category(self, api_client):
        """Test retrieving a single category."""
        category = CategoryFactory(name="Test Category")
        url = reverse("category-detail", kwargs={"pk": category.pk})
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Test Category"


@pytest.mark.integration
class TestInventoryItemAPI:
    """Tests for InventoryItem API endpoints."""

    def test_list_items(self, api_client):
        """Test listing inventory items."""
        InventoryItemFactory.create_batch(3)
        url = reverse("inventoryitem-list")
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 3

    def test_retrieve_item(self, api_client):
        """Test retrieving a single item with details."""
        item = InventoryItemFactory(name="Test Item")
        url = reverse("inventoryitem-detail", kwargs={"pk": str(item.id)})
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Test Item"
        assert "supplier_details" in response.data
        assert "category_details" in response.data

    def test_create_item_requires_auth(self, api_client):
        """Test creating item requires authentication."""
        url = reverse("inventoryitem-list")
        data = {"name": "New Item", "reorder_quantity": 10}
        response = api_client.post(url, data)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_create_item_authenticated(self, authenticated_client):
        """Test creating item when authenticated."""
        client, user = authenticated_client
        supplier = SupplierFactory()
        category = CategoryFactory()

        url = reverse("inventoryitem-list")
        data = {
            "name": "New Widget",
            "description": "Test description",
            "sku": "TEST-001",
            "location": "Shelf A",
            "reorder_quantity": 25,
            "current_stock": 50,
            "minimum_stock": 10,
            "supplier": supplier.id,
            "category": category.id,
            "unit_cost": "15.99",
            "average_lead_time": 7,
        }
        response = client.post(url, data, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "New Widget"
        assert response.data["sku"] == "TEST-001"

    def test_low_stock_endpoint(self, api_client):
        """Test low stock items endpoint."""
        # Create items with different stock levels
        InventoryItemFactory(current_stock=50, minimum_stock=10)  # Not low
        low_item = InventoryItemFactory(current_stock=5, minimum_stock=10)  # Low
        InventoryItemFactory(current_stock=30, minimum_stock=10)  # Not low

        url = reverse("inventoryitem-low-stock")
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]["id"] == str(low_item.id)

    def test_generate_qr_endpoint(self, authenticated_client, mocker):
        """Test QR code generation endpoint."""
        client, user = authenticated_client
        item = InventoryItemFactory()

        # Mock the Celery task
        mock_task = mocker.patch("inventory.views.generate_qr_code.delay")

        url = reverse("inventoryitem-generate-qr", kwargs={"pk": str(item.id)})
        response = client.post(url)

        assert response.status_code == status.HTTP_200_OK
        mock_task.assert_called_once_with(str(item.id))

    def test_download_card_endpoint(self, api_client, mocker):
        """Test PDF card download endpoint."""
        item = InventoryItemFactory()

        # Mock PDF generation
        mock_pdf = mocker.patch("inventory.utils.pdf_generator.generate_item_card")
        from io import BytesIO

        mock_pdf.return_value = BytesIO(b"fake pdf content")

        url = reverse("inventoryitem-download-card", kwargs={"pk": str(item.id)})
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response["Content-Type"] == "application/pdf"
        mock_pdf.assert_called_once()

    def test_log_usage_endpoint(self, authenticated_client):
        """Test logging item usage."""
        client, user = authenticated_client
        item = InventoryItemFactory(current_stock=50)

        url = reverse("inventoryitem-log-usage", kwargs={"pk": str(item.id)})
        data = {"quantity": 5, "notes": "Used for project X"}
        response = client.post(url, data, format="json")

        assert response.status_code == status.HTTP_200_OK

        # Verify stock was updated
        item.refresh_from_db()
        assert item.current_stock == 45

        # Verify usage log was created
        assert UsageLog.objects.filter(item=item).count() == 1
        log = UsageLog.objects.get(item=item)
        assert log.quantity_used == 5
        assert log.notes == "Used for project X"

    def test_log_usage_insufficient_stock(self, authenticated_client):
        """Test logging usage when stock is insufficient."""
        client, user = authenticated_client
        item = InventoryItemFactory(current_stock=3)

        url = reverse("inventoryitem-log-usage", kwargs={"pk": str(item.id)})
        data = {"quantity": 5}
        response = client.post(url, data, format="json")

        # Should still create log, but not reduce stock below 0
        assert response.status_code == status.HTTP_200_OK
        item.refresh_from_db()
        assert item.current_stock == 3  # Stock unchanged


@pytest.mark.integration
class TestUsageLogAPI:
    """Tests for UsageLog API endpoints."""

    def test_list_usage_logs(self, authenticated_client):
        """Test listing usage logs."""
        client, user = authenticated_client
        UsageLogFactory.create_batch(3)

        url = reverse("usagelog-list")
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 3

    def test_filter_by_item(self, authenticated_client):
        """Test filtering usage logs by item."""
        client, user = authenticated_client
        item = InventoryItemFactory()
        UsageLogFactory.create_batch(2, item=item)
        UsageLogFactory()  # Different item

        url = reverse("usagelog-list")
        response = client.get(url, {"item_id": str(item.id)})

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 2
