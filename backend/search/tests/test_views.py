"""
Tests for search views.
"""

from django.contrib.auth import get_user_model
from django.urls import reverse

import pytest
from rest_framework import status

from inventory.models import Asset, Supplier
from inventory.tests.factories import (
    CategoryFactory,
    InventoryItemFactory,
    LocationFactory,
    SupplierFactory,
)
from reorder_queue.models import PurchaseOrder

from ..models import RecentSearch

User = get_user_model()


@pytest.mark.integration
class TestGlobalSearchView:
    """Tests for global search endpoint."""

    def test_global_search_requires_authentication(self, api_client):
        """Anonymous callers cannot reach the cross-app search surface
        because it returns SKUs, asset serials, and supplier names."""
        url = reverse("global-search")
        response = api_client.get(url, {"q": "anything"})
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_global_search_empty_query(self, authenticated_client):
        """Test that empty query returns empty results."""
        client, _ = authenticated_client
        url = reverse("global-search")
        response = client.get(url, {"q": ""})
        assert response.status_code == status.HTTP_200_OK
        assert response.data["results"] == []

    def test_global_search_no_query(self, authenticated_client):
        """Test that missing query returns empty results."""
        client, _ = authenticated_client
        url = reverse("global-search")
        response = client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["results"] == []

    def test_global_search_inventory_item(self, authenticated_client, db):
        """Test searching for inventory items."""
        client, _ = authenticated_client
        category = CategoryFactory()
        location = LocationFactory()
        InventoryItemFactory(
            name="Test Inventory Item",
            description="A test inventory item",
            sku="TEST-001",
            category=category,
            location=location,
        )

        url = reverse("global-search")
        response = client.get(url, {"q": "Test Inventory"})
        assert response.status_code == status.HTTP_200_OK
        results = response.data["results"]
        assert len(results) > 0
        inventory_results = [r for r in results if r["type"] == "inventory"]
        assert len(inventory_results) > 0
        assert inventory_results[0]["title"] == "Test Inventory Item"

    def test_global_search_asset(self, authenticated_client, db):
        """Test searching for assets."""
        client, _ = authenticated_client
        category = CategoryFactory()
        location = LocationFactory()
        Asset.objects.create(
            name="Test Asset",
            description="A test asset",
            asset_tag="ASSET-001",
            category=category,
            location=location,
        )

        url = reverse("global-search")
        response = client.get(url, {"q": "Test Asset"})
        assert response.status_code == status.HTTP_200_OK
        results = response.data["results"]
        asset_results = [r for r in results if r["type"] == "asset"]
        assert len(asset_results) > 0
        assert asset_results[0]["title"] == "Test Asset"

    def test_global_search_supplier(self, authenticated_client, db):
        """Test searching for suppliers."""
        client, _ = authenticated_client
        SupplierFactory(name="Test Supplier", supplier_type=Supplier.ONLINE)

        url = reverse("global-search")
        response = client.get(url, {"q": "Test Supplier"})
        assert response.status_code == status.HTTP_200_OK
        results = response.data["results"]
        supplier_results = [r for r in results if r["type"] == "supplier"]
        assert len(supplier_results) > 0
        assert supplier_results[0]["title"] == "Test Supplier"

    def test_global_search_purchase_order(self, authenticated_client, db):
        """Test searching for purchase orders."""
        client, user = authenticated_client
        supplier = SupplierFactory()
        PurchaseOrder.objects.create(
            supplier=supplier,
            po_number="PO-001",
            created_by=user,
            notes="Test purchase order",
        )

        url = reverse("global-search")
        response = client.get(url, {"q": "PO-001"})
        assert response.status_code == status.HTTP_200_OK
        results = response.data["results"]
        po_results = [r for r in results if r["type"] == "purchase_order"]
        assert len(po_results) > 0

    def test_global_search_location(self, authenticated_client, db):
        """Test searching for locations."""
        client, _ = authenticated_client
        LocationFactory(name="Test Location")

        url = reverse("global-search")
        response = client.get(url, {"q": "Test Location"})
        assert response.status_code == status.HTTP_200_OK
        results = response.data["results"]
        location_results = [r for r in results if r["type"] == "location"]
        assert len(location_results) > 0
        assert location_results[0]["title"] == "Test Location"

    def test_global_search_limit(self, authenticated_client, db):
        """Test that limit parameter works."""
        client, _ = authenticated_client
        # Create multiple items
        for i in range(15):
            InventoryItemFactory(name=f"Item {i}", description="Test")

        url = reverse("global-search")
        response = client.get(url, {"q": "Item", "limit": 5})
        assert response.status_code == status.HTTP_200_OK
        results = response.data["results"]
        inventory_results = [r for r in results if r["type"] == "inventory"]
        assert len(inventory_results) <= 5

    def test_global_search_invalid_limit(self, authenticated_client):
        """Test that invalid limit defaults to 10."""
        client, _ = authenticated_client
        url = reverse("global-search")
        response = client.get(url, {"q": "Test", "limit": "invalid"})
        assert response.status_code == status.HTTP_200_OK
        # Should not raise an error


@pytest.mark.integration
class TestRecentSearchView:
    """Tests for recent search endpoints."""

    def test_get_recent_searches_requires_authentication(self, api_client):
        """Test that getting recent searches requires authentication."""
        url = reverse("recent-searches")
        response = api_client.get(url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_get_recent_searches_empty(self, authenticated_client):
        """Test getting recent searches when none exist."""
        client, user = authenticated_client
        url = reverse("recent-searches")
        response = client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["results"] == []

    def test_get_recent_searches_with_data(self, authenticated_client, db):
        """Test getting recent searches with existing data."""
        client, user = authenticated_client
        RecentSearch.objects.create(
            user=user,
            query="test query",
            result_type="inventory",
            result_id="123",
            result_title="Test Item",
        )
        url = reverse("recent-searches")
        response = client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1
        assert response.data["results"][0]["query"] == "test query"

    def test_save_recent_search_requires_authentication(self, api_client):
        """Test that saving recent search requires authentication."""
        url = reverse("save-recent-search")
        response = api_client.post(
            url,
            {
                "query": "test",
                "result_type": "inventory",
                "result_id": "123",
                "result_title": "Test",
            },
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_save_recent_search(self, authenticated_client, db):
        """Test saving a recent search."""
        client, user = authenticated_client
        url = reverse("save-recent-search")
        response = client.post(
            url,
            {
                "query": "test query",
                "result_type": "inventory",
                "result_id": "123",
                "result_title": "Test Item",
            },
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["query"] == "test query"
        assert RecentSearch.objects.filter(user=user).count() == 1

    def test_save_recent_search_limits_to_50(self, authenticated_client, db):
        """Test that saving recent searches limits to 50 per user."""
        client, user = authenticated_client
        # Create 55 recent searches
        for i in range(55):
            RecentSearch.objects.create(
                user=user,
                query=f"query {i}",
                result_type="inventory",
                result_id=str(i),
                result_title=f"Item {i}",
            )

        # Save one more
        url = reverse("save-recent-search")
        response = client.post(
            url,
            {
                "query": "new query",
                "result_type": "inventory",
                "result_id": "999",
                "result_title": "New Item",
            },
        )
        assert response.status_code == status.HTTP_201_CREATED
        # Should have only 50 searches now
        assert RecentSearch.objects.filter(user=user).count() == 50

    def test_get_recent_searches_limit(self, authenticated_client, db):
        """Test that limit parameter works for recent searches."""
        client, user = authenticated_client
        # Create 25 recent searches
        for i in range(25):
            RecentSearch.objects.create(
                user=user,
                query=f"query {i}",
                result_type="inventory",
                result_id=str(i),
                result_title=f"Item {i}",
            )

        url = reverse("recent-searches")
        response = client.get(url, {"limit": 10})
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) <= 10

    def test_get_recent_searches_invalid_limit(self, authenticated_client):
        """Test that invalid limit defaults to 20."""
        client, user = authenticated_client
        url = reverse("recent-searches")
        response = client.get(url, {"limit": "invalid"})
        assert response.status_code == status.HTTP_200_OK
        # Should not raise an error
