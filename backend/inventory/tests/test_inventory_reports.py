"""
Tests for inventory report endpoints.
"""

from decimal import Decimal

import pytest
from rest_framework import status

from inventory.tests.factories import CategoryFactory, InventoryItemFactory, LocationFactory


@pytest.mark.integration
class TestInventoryReportViewSet:
    """Tests for InventoryReportViewSet endpoints."""

    def test_stock_by_category_requires_auth(self, api_client):
        """Test that stock_by_category requires authentication."""
        url = "/api/inventory/reports/inventory/stock_by_category/"
        response = api_client.get(url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_stock_by_category_with_categorized_items(self, authenticated_client):
        """Test stock_by_category returns data for items with categories."""
        client, user = authenticated_client
        category1 = CategoryFactory(name="Electronics")
        category2 = CategoryFactory(name="Tools")

        # Create items with categories
        InventoryItemFactory(
            category=category1,
            current_stock=10,
            unit_cost=Decimal("5.00"),
            minimum_stock=5,
            is_active=True,
        )
        InventoryItemFactory(
            category=category1,
            current_stock=20,
            unit_cost=Decimal("10.00"),
            minimum_stock=10,
            is_active=True,
        )
        InventoryItemFactory(
            category=category2,
            current_stock=5,
            unit_cost=Decimal("15.00"),
            minimum_stock=3,
            is_active=True,
        )

        url = "/api/inventory/reports/inventory/stock_by_category/"
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

        # Find categories in response
        cat1_data = next((d for d in response.data if d["category_name"] == "Electronics"), None)
        cat2_data = next((d for d in response.data if d["category_name"] == "Tools"), None)

        assert cat1_data is not None
        assert cat1_data["total_items"] == 2
        assert cat1_data["total_stock"] == 30
        assert cat1_data["total_value"] == 250.0  # (10 * 5) + (20 * 10)
        assert cat1_data["low_stock_count"] == 0  # Both items above minimum

        assert cat2_data is not None
        assert cat2_data["total_items"] == 1
        assert cat2_data["total_stock"] == 5
        assert cat2_data["total_value"] == 75.0  # 5 * 15

    def test_stock_by_category_with_uncategorized_items(self, authenticated_client):
        """Test stock_by_category includes items without categories."""
        client, user = authenticated_client
        category = CategoryFactory(name="Electronics")

        # Create items with and without categories
        InventoryItemFactory(
            category=category,
            current_stock=10,
            unit_cost=Decimal("5.00"),
            is_active=True,
        )
        InventoryItemFactory(
            category=None,
            current_stock=20,
            unit_cost=Decimal("10.00"),
            is_active=True,
        )
        InventoryItemFactory(
            category=None,
            current_stock=5,
            unit_cost=Decimal("15.00"),
            is_active=True,
        )

        url = "/api/inventory/reports/inventory/stock_by_category/"
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

        # Find categories in response
        cat_data = next((d for d in response.data if d["category_name"] == "Electronics"), None)
        uncat_data = next((d for d in response.data if d["category_name"] == "Uncategorized"), None)

        assert cat_data is not None
        assert cat_data["total_items"] == 1
        assert cat_data["total_stock"] == 10
        assert cat_data["total_value"] == 50.0

        assert uncat_data is not None
        assert uncat_data["category_id"] is None
        assert uncat_data["total_items"] == 2
        assert uncat_data["total_stock"] == 25
        assert uncat_data["total_value"] == 275.0  # (20 * 10) + (5 * 15)

    def test_stock_by_category_low_stock_count(self, authenticated_client):
        """Test stock_by_category correctly counts low stock items."""
        client, user = authenticated_client
        category = CategoryFactory(name="Electronics")

        # Create items with various stock levels
        InventoryItemFactory(
            category=category,
            current_stock=10,
            minimum_stock=5,
            is_active=True,
        )  # Above minimum
        InventoryItemFactory(
            category=category,
            current_stock=3,
            minimum_stock=5,
            is_active=True,
        )  # Below minimum
        InventoryItemFactory(
            category=category,
            current_stock=5,
            minimum_stock=5,
            is_active=True,
        )  # At minimum (should count as low)

        url = "/api/inventory/reports/inventory/stock_by_category/"
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]["low_stock_count"] == 2

    def test_stock_by_category_excludes_inactive_items(self, authenticated_client):
        """Test stock_by_category excludes inactive items."""
        client, user = authenticated_client
        category = CategoryFactory(name="Electronics")

        InventoryItemFactory(category=category, current_stock=10, is_active=True)
        InventoryItemFactory(category=category, current_stock=20, is_active=False)

        url = "/api/inventory/reports/inventory/stock_by_category/"
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]["total_items"] == 1
        assert response.data[0]["total_stock"] == 10

    def test_value_by_location_requires_auth(self, api_client):
        """Test that value_by_location requires authentication."""
        url = "/api/inventory/reports/inventory/value_by_location/"
        response = api_client.get(url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_value_by_location_with_located_items(self, authenticated_client):
        """Test value_by_location returns data for items with locations."""
        client, user = authenticated_client
        location1 = LocationFactory(name="Warehouse A")
        location2 = LocationFactory(name="Warehouse B")

        # Create items with locations
        InventoryItemFactory(
            location=location1,
            current_stock=10,
            unit_cost=Decimal("5.00"),
            is_active=True,
        )
        InventoryItemFactory(
            location=location1,
            current_stock=20,
            unit_cost=Decimal("10.00"),
            is_active=True,
        )
        InventoryItemFactory(
            location=location2,
            current_stock=5,
            unit_cost=Decimal("15.00"),
            is_active=True,
        )

        url = "/api/inventory/reports/inventory/value_by_location/"
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

        # Find locations in response (ordered by total_value descending)
        loc1_data = next((d for d in response.data if d["location_name"] == "Warehouse A"), None)
        loc2_data = next((d for d in response.data if d["location_name"] == "Warehouse B"), None)

        assert loc1_data is not None
        assert loc1_data["total_items"] == 2
        assert loc1_data["total_stock"] == 30
        assert loc1_data["total_value"] == 250.0  # (10 * 5) + (20 * 10)

        assert loc2_data is not None
        assert loc2_data["total_items"] == 1
        assert loc2_data["total_stock"] == 5
        assert loc2_data["total_value"] == 75.0  # 5 * 15

        # Verify ordering (highest value first)
        assert response.data[0]["total_value"] >= response.data[1]["total_value"]

    def test_value_by_location_with_unlocated_items(self, authenticated_client):
        """Test value_by_location includes items without locations."""
        client, user = authenticated_client
        location = LocationFactory(name="Warehouse A")

        # Create items with and without locations
        InventoryItemFactory(
            location=location,
            current_stock=10,
            unit_cost=Decimal("5.00"),
            is_active=True,
        )
        InventoryItemFactory(
            location=None,
            current_stock=20,
            unit_cost=Decimal("10.00"),
            is_active=True,
        )
        InventoryItemFactory(
            location=None,
            current_stock=5,
            unit_cost=Decimal("15.00"),
            is_active=True,
        )

        url = "/api/inventory/reports/inventory/value_by_location/"
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

        # Find locations in response
        loc_data = next((d for d in response.data if d["location_name"] == "Warehouse A"), None)
        noloc_data = next((d for d in response.data if d["location_name"] == "No Location"), None)

        assert loc_data is not None
        assert loc_data["total_items"] == 1
        assert loc_data["total_stock"] == 10
        assert loc_data["total_value"] == 50.0

        assert noloc_data is not None
        assert noloc_data["location_id"] is None
        assert noloc_data["total_items"] == 2
        assert noloc_data["total_stock"] == 25
        assert noloc_data["total_value"] == 275.0  # (20 * 10) + (5 * 15)

    def test_value_by_location_excludes_inactive_items(self, authenticated_client):
        """Test value_by_location excludes inactive items."""
        client, user = authenticated_client
        location = LocationFactory(name="Warehouse A")

        InventoryItemFactory(location=location, current_stock=10, is_active=True)
        InventoryItemFactory(location=location, current_stock=20, is_active=False)

        url = "/api/inventory/reports/inventory/value_by_location/"
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]["total_items"] == 1
        assert response.data[0]["total_stock"] == 10

    def test_value_by_location_empty_result(self, authenticated_client):
        """Test value_by_location returns empty list when no active items exist."""
        client, user = authenticated_client

        # Create only inactive items
        InventoryItemFactory(is_active=False)

        url = "/api/inventory/reports/inventory/value_by_location/"
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data == []

    def test_stock_by_category_empty_result(self, authenticated_client):
        """Test stock_by_category returns empty list when no active items exist."""
        client, user = authenticated_client

        # Create only inactive items
        InventoryItemFactory(is_active=False)

        url = "/api/inventory/reports/inventory/stock_by_category/"
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data == []

    def test_stock_by_category_with_null_unit_cost(self, authenticated_client):
        """Test stock_by_category handles items with null unit_cost."""
        client, user = authenticated_client
        category = CategoryFactory(name="Electronics")

        InventoryItemFactory(
            category=category,
            current_stock=10,
            unit_cost=None,
            is_active=True,
        )
        InventoryItemFactory(
            category=category,
            current_stock=20,
            unit_cost=Decimal("10.00"),
            is_active=True,
        )

        url = "/api/inventory/reports/inventory/stock_by_category/"
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        # total_value should only include items with unit_cost
        assert response.data[0]["total_value"] == 200.0  # 20 * 10

    def test_value_by_location_with_null_unit_cost(self, authenticated_client):
        """Test value_by_location handles items with null unit_cost."""
        client, user = authenticated_client
        location = LocationFactory(name="Warehouse A")

        InventoryItemFactory(
            location=location,
            current_stock=10,
            unit_cost=None,
            is_active=True,
        )
        InventoryItemFactory(
            location=location,
            current_stock=20,
            unit_cost=Decimal("10.00"),
            is_active=True,
        )

        url = "/api/inventory/reports/inventory/value_by_location/"
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        # total_value should only include items with unit_cost
        assert response.data[0]["total_value"] == 200.0  # 20 * 10
