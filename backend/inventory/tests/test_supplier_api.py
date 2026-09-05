"""
Comprehensive tests for Supplier API endpoints including new features.
"""

from decimal import Decimal

from django.urls import reverse

import pytest
from rest_framework import status

from inventory.models import Supplier
from inventory.tests.factories import InventoryItemFactory, ItemSupplierFactory, SupplierFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def supplier_reader(authenticated_client):
    """A signed-in client for the supplier catalogue.

    Every read here used the anonymous ``api_client`` until
    op-anonymous-read-posture closed ``SupplierViewSet`` — a supplier row is a
    vendor's identity, and the detail serializer nests their SKUs, prices and
    lead times. The reads themselves are unchanged; who may make them is not,
    and ``TestSupplierCatalogueRequiresAuth`` below pins the new boundary.
    """
    client, _user = authenticated_client
    return client


@pytest.mark.integration
class TestSupplierAPI:
    """Tests for Supplier API endpoints."""

    def test_list_suppliers(self, supplier_reader):
        """Test listing suppliers."""
        SupplierFactory.create_batch(3)
        url = reverse("supplier-list")
        response = supplier_reader.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 3
        assert len(response.data["results"]) == 3

    def test_list_suppliers_includes_computed_fields(self, supplier_reader):
        """Test that list includes computed fields."""
        supplier = SupplierFactory()
        # Create some items for this supplier
        item1 = InventoryItemFactory()
        item2 = InventoryItemFactory()
        ItemSupplierFactory(item=item1, supplier=supplier, is_active=True)
        ItemSupplierFactory(item=item2, supplier=supplier, is_active=True)
        ItemSupplierFactory(item=InventoryItemFactory(), supplier=supplier, is_active=False)

        url = reverse("supplier-list")
        response = supplier_reader.get(url)

        assert response.status_code == status.HTTP_200_OK
        supplier_data = next(s for s in response.data["results"] if s["id"] == supplier.id)
        assert supplier_data["item_count"] == 2  # Only active items
        assert "purchase_order_count" in supplier_data
        assert "total_spent" in supplier_data

    def test_filter_suppliers_by_type(self, supplier_reader):
        """Test filtering suppliers by type."""
        SupplierFactory(supplier_type=Supplier.SupplierType.LOCAL)
        SupplierFactory(supplier_type=Supplier.SupplierType.ONLINE)
        SupplierFactory(supplier_type=Supplier.SupplierType.NATIONAL)

        url = reverse("supplier-list")
        response = supplier_reader.get(url, {"supplier_type": Supplier.SupplierType.ONLINE})

        assert response.status_code == status.HTTP_200_OK
        assert all(
            s["supplier_type"] == Supplier.SupplierType.ONLINE for s in response.data["results"]
        )

    def test_search_suppliers(self, supplier_reader):
        """Test searching suppliers by name."""
        SupplierFactory(name="Acme Corporation")
        SupplierFactory(name="Beta Supplies")
        SupplierFactory(name="Gamma Tools", notes="Acme competitor")

        url = reverse("supplier-list")
        response = supplier_reader.get(url, {"search": "Acme"})

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 2  # Name match and notes match

    def test_create_supplier_requires_auth(self, api_client):
        """Test creating supplier requires authentication."""
        url = reverse("supplier-list")
        data = {"name": "New Supplier", "supplier_type": Supplier.SupplierType.ONLINE}
        response = api_client.post(url, data)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_create_supplier_authenticated(self, authenticated_client):
        """Test creating supplier when authenticated."""
        client, user = authenticated_client
        url = reverse("supplier-list")
        data = {
            "name": "New Supplier",
            "supplier_type": Supplier.SupplierType.ONLINE,
            "website": "https://example.com",
            "account_number": "ACC-123",
            "tax_free_paperwork_filed": True,
            "notes": "Test notes",
        }
        response = client.post(url, data)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "New Supplier"
        assert response.data["supplier_type"] == Supplier.SupplierType.ONLINE
        assert response.data["website"] == "https://example.com"
        assert response.data["account_number"] == "ACC-123"
        assert response.data["tax_free_paperwork_filed"] is True
        assert response.data["notes"] == "Test notes"

    def test_retrieve_supplier_detail(self, supplier_reader):
        """Test retrieving supplier with detail serializer."""
        supplier = SupplierFactory()
        item1 = InventoryItemFactory()
        item2 = InventoryItemFactory()
        ItemSupplierFactory(item=item1, supplier=supplier)
        ItemSupplierFactory(item=item2, supplier=supplier)

        url = reverse("supplier-detail", kwargs={"pk": supplier.pk})
        response = supplier_reader.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == supplier.name
        assert "items" in response.data
        assert len(response.data["items"]) == 2
        assert "purchase_orders" in response.data
        assert "lead_time_analytics" in response.data
        assert "price_trends" in response.data

    def test_update_supplier_requires_auth(self, api_client):
        """Test updating supplier requires authentication."""
        supplier = SupplierFactory()
        url = reverse("supplier-detail", kwargs={"pk": supplier.pk})
        data = {"name": "Updated Name"}
        response = api_client.patch(url, data)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_update_supplier_authenticated(self, authenticated_client):
        """Test updating supplier when authenticated."""
        client, user = authenticated_client
        supplier = SupplierFactory(name="Original Name")
        url = reverse("supplier-detail", kwargs={"pk": supplier.pk})
        data = {
            "name": "Updated Name",
            "tax_free_paperwork_filed": True,
        }
        response = client.patch(url, data)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Updated Name"
        assert response.data["tax_free_paperwork_filed"] is True

    def test_delete_supplier_requires_auth(self, api_client):
        """Test deleting supplier requires authentication."""
        supplier = SupplierFactory()
        url = reverse("supplier-detail", kwargs={"pk": supplier.pk})
        response = api_client.delete(url)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_delete_supplier_authenticated(self, authenticated_client):
        """Test deleting supplier when authenticated."""
        client, user = authenticated_client
        supplier = SupplierFactory()
        url = reverse("supplier-detail", kwargs={"pk": supplier.pk})
        response = client.delete(url)

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not Supplier.objects.filter(pk=supplier.pk).exists()

    def test_supplier_analytics_endpoint(self, supplier_reader):
        """Test supplier analytics endpoint."""
        supplier = SupplierFactory()
        url = reverse("supplier-analytics", kwargs={"pk": supplier.pk})
        response = supplier_reader.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert "lead_time_analytics" in response.data
        assert "price_trends" in response.data
        assert "order_statistics" in response.data

    def test_supplier_analytics_with_data(self, supplier_reader, authenticated_client):
        """Test supplier analytics with actual data."""
        supplier = SupplierFactory()
        item = InventoryItemFactory()
        item_supplier = ItemSupplierFactory(item=item, supplier=supplier)
        client, user = authenticated_client

        # Try to create lead time log if reorder_queue is available
        try:
            from datetime import timedelta

            from django.utils import timezone

            from reorder_queue.models import LeadTimeLog, PurchaseOrder

            # Create a purchase order
            po = PurchaseOrder.objects.create(
                supplier=supplier,
                status=PurchaseOrder.Status.RECEIVED,
                created_by=user,
                actual_total=Decimal("100.00"),
            )

            # Create lead time log
            LeadTimeLog.objects.create(
                item_supplier=item_supplier,
                purchase_order=po,
                order_date=timezone.now() - timedelta(days=20),
                expected_delivery_date=timezone.now().date() - timedelta(days=5),
                actual_delivery_date=timezone.now().date(),
                estimated_lead_time_days=15,
                actual_lead_time_days=20,
                variance_days=5,
                quantity_ordered=10,
                quantity_received=10,
            )
        except ImportError:
            pass  # reorder_queue not available in test environment

        url = reverse("supplier-analytics", kwargs={"pk": supplier.pk})
        response = supplier_reader.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert "lead_time_analytics" in response.data
        assert "price_trends" in response.data
        assert "order_statistics" in response.data

    def test_supplier_serializer_computed_fields(self, supplier_reader):
        """Test that serializer computes fields correctly."""
        supplier = SupplierFactory()

        # Create items with different active statuses
        item1 = InventoryItemFactory()
        item2 = InventoryItemFactory()
        item3 = InventoryItemFactory()
        ItemSupplierFactory(item=item1, supplier=supplier, is_active=True)
        ItemSupplierFactory(item=item2, supplier=supplier, is_active=True)
        ItemSupplierFactory(item=item3, supplier=supplier, is_active=False)

        url = reverse("supplier-list")
        response = supplier_reader.get(url)

        supplier_data = next(s for s in response.data["results"] if s["id"] == supplier.id)
        assert supplier_data["item_count"] == 2  # Only active items

    def test_supplier_detail_serializer_includes_items(self, supplier_reader):
        """Test that detail serializer includes related items."""
        supplier = SupplierFactory()
        item1 = InventoryItemFactory()
        item2 = InventoryItemFactory()
        ItemSupplierFactory(item=item1, supplier=supplier, supplier_sku="SKU1")
        ItemSupplierFactory(item=item2, supplier=supplier, supplier_sku="SKU2")

        url = reverse("supplier-detail", kwargs={"pk": supplier.pk})
        response = supplier_reader.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["items"]) == 2
        assert all("supplier_sku" in item for item in response.data["items"])
        assert any(item["supplier_sku"] == "SKU1" for item in response.data["items"])
        assert any(item["supplier_sku"] == "SKU2" for item in response.data["items"])

    def test_supplier_analytics_empty_lead_time_logs(self, supplier_reader):
        """Test analytics endpoint with no lead time logs."""
        supplier = SupplierFactory()
        item = InventoryItemFactory()
        ItemSupplierFactory(item=item, supplier=supplier)

        url = reverse("supplier-analytics", kwargs={"pk": supplier.pk})
        response = supplier_reader.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert "lead_time_analytics" in response.data
        # Should return empty stats when no logs exist
        lead_time = response.data["lead_time_analytics"]
        assert lead_time.get("total_orders") == 0 or "total_orders" not in lead_time

    def test_supplier_list_with_pagination(self, supplier_reader):
        """Test supplier list with pagination."""
        SupplierFactory.create_batch(25)
        url = reverse("supplier-list")
        response = supplier_reader.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert "count" in response.data
        assert "results" in response.data
        assert len(response.data["results"]) <= 25

    def test_supplier_update_partial(self, authenticated_client):
        """Test partial update of supplier."""
        client, user = authenticated_client
        supplier = SupplierFactory(name="Original", tax_free_paperwork_filed=False)
        url = reverse("supplier-detail", kwargs={"pk": supplier.pk})
        data = {"tax_free_paperwork_filed": True}
        response = client.patch(url, data)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["tax_free_paperwork_filed"] is True
        assert response.data["name"] == "Original"  # Name unchanged


@pytest.mark.integration
class TestSupplierCatalogueRequiresAuth:
    """The boundary the tests above used to sit on the wrong side of.

    ``SupplierViewSet`` served ``list``, ``retrieve`` and ``analytics`` to
    anonymous callers under ``IsAuthenticatedOrReadOnly``. The captain's
    decision — "Vendor names should not be public, same with Vendor Pricing" —
    closes them (op-anonymous-read-posture). Asserted here as well as in
    ``config/tests/test_anonymous_vendor_exposure.py`` so a change to this
    viewset fails beside its own tests.
    """

    def test_an_anonymous_caller_cannot_list_suppliers(self, api_client):
        SupplierFactory(name="ZZQQ Anonymous Probe Vendor")
        response = api_client.get(reverse("supplier-list"))

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
        assert b"ZZQQ Anonymous Probe Vendor" not in response.content

    def test_an_anonymous_caller_cannot_read_one_supplier(self, api_client):
        supplier = SupplierFactory(name="ZZQQ Anonymous Probe Vendor")
        response = api_client.get(reverse("supplier-detail", kwargs={"pk": supplier.pk}))

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
        assert b"ZZQQ Anonymous Probe Vendor" not in response.content

    def test_an_anonymous_caller_cannot_read_supplier_analytics(self, api_client):
        supplier = SupplierFactory()
        response = api_client.get(f"/api/inventory/suppliers/{supplier.pk}/analytics/")

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
