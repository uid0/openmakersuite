"""
Tests for inventory report endpoints.
"""

from decimal import Decimal

import pytest
from rest_framework import status

from inventory.models import ItemSupplier
from inventory.tests.factories import (
    CategoryFactory,
    InventoryItemFactory,
    LocationFactory,
    SupplierFactory,
)


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

    def test_stock_by_category_low_stock_count_excludes_retired(self, authenticated_client):
        """Retired items stay counted as inventory but never as low-stock."""
        client, user = authenticated_client
        category = CategoryFactory(name="Electronics")

        InventoryItemFactory(
            category=category, current_stock=3, minimum_stock=5, is_active=True
        )  # low -> counts
        InventoryItemFactory(
            category=category,
            current_stock=1,
            minimum_stock=5,
            is_active=True,
            is_retired=True,
        )  # low but retired -> excluded from low_stock_count

        url = "/api/inventory/reports/inventory/stock_by_category/"
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        # Retired item remains a visible inventory item, but is not reorder-flagged.
        assert response.data[0]["total_items"] == 2
        assert response.data[0]["low_stock_count"] == 1

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

    def test_multi_supplier_average_cost(self, authenticated_client):
        """Total value uses the arithmetic mean of active-supplier unit_costs."""
        client, user = authenticated_client
        category = CategoryFactory(name="Electronics")

        item = InventoryItemFactory(
            category=category,
            current_stock=4,
            unit_cost=Decimal("10.00"),  # creates primary ItemSupplier at 10
            is_active=True,
        )
        # Add a second active, non-primary supplier at 20 → avg = 15
        ItemSupplier.objects.create(
            item=item,
            supplier=SupplierFactory(),
            supplier_sku="SUP-EXTRA-1",
            unit_cost=Decimal("20.00"),
            quantity_per_package=1,
            is_primary=False,
            is_active=True,
        )

        url = "/api/inventory/reports/inventory/stock_by_category/"
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        # 4 units * avg(10, 20) = 4 * 15 = 60
        assert response.data[0]["total_value"] == 60.0

    def test_no_suppliers_shows_zero(self, authenticated_client):
        """Items with no ItemSupplier rows contribute $0 to total_value."""
        client, user = authenticated_client
        category = CategoryFactory(name="Electronics")

        item = InventoryItemFactory(
            category=category,
            current_stock=10,
            unit_cost=Decimal("10.00"),
            is_active=True,
        )
        # Remove the auto-created primary supplier to simulate a zero-supplier item
        ItemSupplier.objects.filter(item=item).delete()

        url = "/api/inventory/reports/inventory/stock_by_category/"
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]["total_value"] == 0.0

    def test_inactive_supplier_excluded_from_average(self, authenticated_client):
        """Inactive ItemSuppliers are excluded from the average cost."""
        client, user = authenticated_client
        category = CategoryFactory(name="Electronics")

        item = InventoryItemFactory(
            category=category,
            current_stock=3,
            unit_cost=Decimal("10.00"),  # active primary @ 10
            is_active=True,
        )
        ItemSupplier.objects.create(
            item=item,
            supplier=SupplierFactory(),
            supplier_sku="SUP-EXTRA-2",
            unit_cost=Decimal("1000.00"),
            quantity_per_package=1,
            is_primary=False,
            is_active=False,  # inactive → excluded
        )

        url = "/api/inventory/reports/inventory/stock_by_category/"
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        # Only the active supplier at 10 counts: 3 * 10 = 30
        assert response.data[0]["total_value"] == 30.0

    def test_null_unit_cost_supplier_excluded_from_average(self, authenticated_client):
        """Suppliers with null unit_cost are excluded from the average."""
        client, user = authenticated_client
        category = CategoryFactory(name="Electronics")

        item = InventoryItemFactory(
            category=category,
            current_stock=5,
            unit_cost=Decimal("10.00"),  # active primary @ 10
            is_active=True,
        )
        ItemSupplier.objects.create(
            item=item,
            supplier=SupplierFactory(),
            supplier_sku="SUP-EXTRA-3",
            unit_cost=None,
            quantity_per_package=1,
            is_primary=False,
            is_active=True,
        )

        url = "/api/inventory/reports/inventory/stock_by_category/"
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        # Only the non-null supplier counts: 5 * 10 = 50
        assert response.data[0]["total_value"] == 50.0

    def test_average_unit_cost_model_method(self, db):
        """InventoryItem.average_unit_cost returns arithmetic mean, or None when empty."""
        item = InventoryItemFactory(unit_cost=Decimal("10.00"), current_stock=2)
        ItemSupplier.objects.create(
            item=item,
            supplier=SupplierFactory(),
            supplier_sku="SUP-MODEL-1",
            unit_cost=Decimal("20.00"),
            quantity_per_package=1,
            is_primary=False,
            is_active=True,
        )
        # Add an inactive + a null-cost supplier to ensure both are excluded
        ItemSupplier.objects.create(
            item=item,
            supplier=SupplierFactory(),
            supplier_sku="SUP-MODEL-2",
            unit_cost=Decimal("999.00"),
            quantity_per_package=1,
            is_primary=False,
            is_active=False,
        )
        ItemSupplier.objects.create(
            item=item,
            supplier=SupplierFactory(),
            supplier_sku="SUP-MODEL-3",
            unit_cost=None,
            quantity_per_package=1,
            is_primary=False,
            is_active=True,
        )

        assert item.average_unit_cost() == Decimal("15.00")
        assert item.average_total_value() == Decimal("30.00")

        empty_item = InventoryItemFactory(unit_cost=Decimal("5.00"), current_stock=7)
        ItemSupplier.objects.filter(item=empty_item).delete()
        assert empty_item.average_unit_cost() is None
        # ``None``, not ``Decimal("0")``: with no supplier recording a price the
        # stock cannot be valued, and saying "$0.00" is a claim about money
        # nobody made (op-9m2v).
        assert empty_item.average_total_value() is None

    def test_value_by_location_multi_supplier_average(self, authenticated_client):
        """value_by_location uses the same average-cost logic as stock_by_category."""
        client, user = authenticated_client
        location = LocationFactory(name="Warehouse A")

        item = InventoryItemFactory(
            location=location,
            current_stock=2,
            unit_cost=Decimal("10.00"),
            is_active=True,
        )
        ItemSupplier.objects.create(
            item=item,
            supplier=SupplierFactory(),
            supplier_sku="SUP-LOC-1",
            unit_cost=Decimal("20.00"),
            quantity_per_package=1,
            is_primary=False,
            is_active=True,
        )

        url = "/api/inventory/reports/inventory/value_by_location/"
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        # 2 units * avg(10, 20) = 30
        assert response.data[0]["total_value"] == 30.0

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
