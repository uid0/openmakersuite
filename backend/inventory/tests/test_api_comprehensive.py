"""
Comprehensive API tests for inventory management.

This module tests all API endpoints with various scenarios including:
- CRUD operations
- Authentication and permissions
- Pagination and filtering
- File uploads and downloads
- Related object navigation
"""

import json
from io import BytesIO
from unittest.mock import patch

import pytest
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from inventory.tests.factories import CategoryFactory, InventoryItemFactory, SupplierFactory


class InventoryAPIComprehensiveTest(APITestCase):
    """Comprehensive tests for inventory API endpoints."""

    def setUp(self):
        """Set up test data."""
        self.category = CategoryFactory()
        self.supplier = SupplierFactory()
        self.item = InventoryItemFactory(category=self.category, supplier=self.supplier)

    def test_inventory_item_list(self):
        """Test inventory item list endpoint."""
        url = reverse("inventoryitem-list")
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)

        # Check that clickable links are present in DRF interface
        self.assertIn("id", response.data["results"][0])
        self.assertIn("category", response.data["results"][0])
        self.assertEqual(response.data["results"][0]["category"], self.category.id)

    def test_inventory_item_detail(self):
        """Test inventory item detail endpoint."""
        url = reverse("inventoryitem-detail", kwargs={"pk": self.item.id})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], str(self.item.id))
        self.assertEqual(response.data["name"], self.item.name)

    def test_inventory_item_create(self):
        """Test inventory item creation."""
        url = reverse("inventoryitem-list")
        data = {
            "name": "Test Item",
            "description": "Test Description",
            "sku": "TEST-001",
            "category": self.category.id,
            "location": "Test Location",
            "supplier": self.supplier.id,
            "reorder_quantity": 10,
            "current_stock": 100,
            "minimum_stock": 20,
        }

        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["name"], "Test Item")

    def test_inventory_item_update(self):
        """Test inventory item update."""
        url = reverse("inventoryitem-detail", kwargs={"pk": self.item.id})
        data = {"name": "Updated Item Name"}

        response = self.client.patch(url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["name"], "Updated Item Name")

    def test_inventory_item_delete(self):
        """Test inventory item deletion."""
        url = reverse("inventoryitem-detail", kwargs={"pk": self.item.id})
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        # Verify item is deleted
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_category_list_and_detail(self):
        """Test category list and detail endpoints."""
        # Test list
        url = reverse("category-list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)

        # Test detail
        url = reverse("category-detail", kwargs={"pk": self.category.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], self.category.id)

    def test_supplier_list_and_detail(self):
        """Test supplier list and detail endpoints."""
        # Test list
        url = reverse("supplier-list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Test detail
        url = reverse("supplier-detail", kwargs={"pk": self.supplier.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_pagination(self):
        """Test API pagination."""
        # Create multiple items
        for i in range(5):
            InventoryItemFactory(category=self.category)

        url = reverse("inventoryitem-list")
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("results", response.data)
        self.assertIn("count", response.data)
        self.assertEqual(response.data["count"], 6)  # 5 new + 1 existing

    def test_filtering_by_category(self):
        """Test filtering items by category."""
        other_category = CategoryFactory()
        other_item = InventoryItemFactory(category=other_category)

        url = reverse("inventoryitem-list")
        response = self.client.get(url, {"category": self.category.id})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["id"], str(self.item.id))

    def test_search_functionality(self):
        """Test search functionality."""
        url = reverse("inventoryitem-list")
        response = self.client.get(url, {"search": self.item.name[:5]})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)
