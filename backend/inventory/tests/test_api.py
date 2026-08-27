"""
API tests for inventory endpoints.
"""

from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status

from inventory.models import UsageLog
from inventory.tests.factories import (
    CategoryFactory,
    FixtureFactory,
    InventoryItemFactory,
    SupplierFactory,
    UsageLogFactory,
)

pytestmark = pytest.mark.django_db


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
        category = CategoryFactory()
        url = reverse("category-detail", kwargs={"pk": category.pk})
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == category.name


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

    def test_list_items_filter_low_stock_true(self, api_client):
        """low_stock=true returns only items at or under their minimum."""
        low = InventoryItemFactory(current_stock=5, minimum_stock=10)
        InventoryItemFactory(current_stock=50, minimum_stock=10)

        url = reverse("inventoryitem-list")
        response = api_client.get(url, {"low_stock": "true"})

        assert response.status_code == status.HTTP_200_OK
        ids = [r["id"] for r in response.data["results"]]
        assert ids == [str(low.id)]

    def test_list_items_filter_low_stock_false(self, api_client):
        """low_stock=false returns only items above their minimum."""
        InventoryItemFactory(current_stock=5, minimum_stock=10)
        in_stock = InventoryItemFactory(current_stock=50, minimum_stock=10)

        url = reverse("inventoryitem-list")
        response = api_client.get(url, {"low_stock": "false"})

        assert response.status_code == status.HTTP_200_OK
        ids = [r["id"] for r in response.data["results"]]
        assert ids == [str(in_stock.id)]

    def test_list_items_ordering(self, api_client):
        """The ordering param sorts by the requested allow-listed field."""
        InventoryItemFactory(name="Banana")
        InventoryItemFactory(name="Apple")
        InventoryItemFactory(name="Cherry")

        url = reverse("inventoryitem-list")

        asc = api_client.get(url, {"ordering": "name"})
        assert [r["name"] for r in asc.data["results"]] == ["Apple", "Banana", "Cherry"]

        desc = api_client.get(url, {"ordering": "-name"})
        assert [r["name"] for r in desc.data["results"]] == ["Cherry", "Banana", "Apple"]

    def test_list_items_invalid_ordering_falls_back_to_name(self, api_client):
        """An ordering value outside the allow-list is ignored (defaults to name)."""
        InventoryItemFactory(name="Banana")
        InventoryItemFactory(name="Apple")

        url = reverse("inventoryitem-list")
        response = api_client.get(url, {"ordering": "current_stock); DROP TABLE"})

        assert response.status_code == status.HTTP_200_OK
        assert [r["name"] for r in response.data["results"]] == ["Apple", "Banana"]

    def test_retrieve_item(self, api_client):
        """Test retrieving a single item with details."""
        item = InventoryItemFactory()
        url = reverse("inventoryitem-detail", kwargs={"pk": str(item.id)})
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == item.name
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

    def test_low_stock_endpoint_excludes_retired(self, api_client):
        """The low_stock action never lists a retired item, even below minimum."""
        low_item = InventoryItemFactory(current_stock=5, minimum_stock=10)
        InventoryItemFactory(current_stock=2, minimum_stock=10, is_retired=True)

        url = reverse("inventoryitem-low-stock")
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert [r["id"] for r in response.data] == [str(low_item.id)]

    def test_low_stock_filter_excludes_retired(self, api_client):
        """?low_stock=true excludes retired items even when they are under min."""
        low_item = InventoryItemFactory(current_stock=5, minimum_stock=10)
        InventoryItemFactory(current_stock=2, minimum_stock=10, is_retired=True)

        url = reverse("inventoryitem-list")
        response = api_client.get(url, {"low_stock": "true"})

        assert response.status_code == status.HTTP_200_OK
        assert [r["id"] for r in response.data["results"]] == [str(low_item.id)]

    def test_list_hides_retired_item_at_zero_stock_by_default(self, api_client):
        """A retired item is auto-hidden from the default list once stock hits 0."""
        visible = InventoryItemFactory(current_stock=5, minimum_stock=10)
        InventoryItemFactory(current_stock=0, minimum_stock=10, is_retired=True)

        url = reverse("inventoryitem-list")
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert [r["id"] for r in response.data["results"]] == [str(visible.id)]

    def test_list_shows_retired_empty_item_with_include_retired(self, api_client):
        """include_retired=true reveals retired-and-empty items."""
        visible = InventoryItemFactory(current_stock=5, minimum_stock=10)
        retired_empty = InventoryItemFactory(current_stock=0, minimum_stock=10, is_retired=True)

        url = reverse("inventoryitem-list")
        response = api_client.get(url, {"include_retired": "true"})

        assert response.status_code == status.HTTP_200_OK
        ids = {r["id"] for r in response.data["results"]}
        assert ids == {str(visible.id), str(retired_empty.id)}

    def test_list_always_shows_retired_item_with_stock(self, api_client):
        """A retired item with stock remaining always stays listed (draw-down)."""
        retired_with_stock = InventoryItemFactory(
            current_stock=7, minimum_stock=10, is_retired=True
        )

        url = reverse("inventoryitem-list")
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert [r["id"] for r in response.data["results"]] == [str(retired_with_stock.id)]

    def test_retire_requires_auth(self, api_client):
        """Retiring an item requires authentication."""
        item = InventoryItemFactory(is_retired=False)
        url = reverse("inventoryitem-retire", kwargs={"pk": str(item.id)})
        response = api_client.post(url)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        item.refresh_from_db()
        assert item.is_retired is False

    def test_unretire_requires_auth(self, api_client):
        """Un-retiring an item requires authentication."""
        item = InventoryItemFactory(is_retired=True, retired_at=timezone.now())
        url = reverse("inventoryitem-unretire", kwargs={"pk": str(item.id)})
        response = api_client.post(url)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        item.refresh_from_db()
        assert item.is_retired is True

    def test_retire_action_sets_flag_and_stamp(self, authenticated_client):
        """The retire action flags the item and stamps retired_at."""
        client, _ = authenticated_client
        item = InventoryItemFactory(is_retired=False)

        url = reverse("inventoryitem-retire", kwargs={"pk": str(item.id)})
        response = client.post(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_retired"] is True
        assert response.data["retired_at"] is not None
        item.refresh_from_db()
        assert item.is_retired is True
        assert item.retired_at is not None

    def test_unretire_action_clears_flag_and_stamp(self, authenticated_client):
        """The unretire action clears the flag and the retired_at stamp."""
        client, _ = authenticated_client
        # Stock is pinned, not left to the factory's random 0..100: a retired
        # item with no stock left is auto-hidden from the default queryset the
        # detail route resolves through (see the include_retired chokepoint in
        # InventoryItemViewSet.get_queryset), so a random 0 turned this into a
        # 404 roughly one run in a hundred. What is under test here is the
        # action, not the visibility rule, which has its own tests above.
        item = InventoryItemFactory(is_retired=True, retired_at=timezone.now(), current_stock=5)

        url = reverse("inventoryitem-unretire", kwargs={"pk": str(item.id)})
        response = client.post(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_retired"] is False
        assert response.data["retired_at"] is None
        item.refresh_from_db()
        assert item.is_retired is False
        assert item.retired_at is None

    def test_retire_is_idempotent_and_preserves_stamp(self, authenticated_client):
        """Retiring an already-retired item is a no-op that keeps retired_at."""
        client, _ = authenticated_client
        original = timezone.now() - timedelta(days=3)
        # Stock pinned for the same reason as the unretire test above: retired
        # and empty is hidden from the queryset the detail route resolves
        # through, and the factory's stock is otherwise random.
        item = InventoryItemFactory(is_retired=True, retired_at=original, current_stock=5)

        url = reverse("inventoryitem-retire", kwargs={"pk": str(item.id)})
        response = client.post(url)

        assert response.status_code == status.HTTP_200_OK
        item.refresh_from_db()
        assert item.is_retired is True
        # The original stamp is preserved (not re-stamped).
        assert item.retired_at == original

    def test_unretire_on_active_item_is_noop(self, authenticated_client):
        """Un-retiring an item that is not retired is a harmless no-op."""
        client, _ = authenticated_client
        item = InventoryItemFactory(is_retired=False)

        url = reverse("inventoryitem-unretire", kwargs={"pk": str(item.id)})
        response = client.post(url)

        assert response.status_code == status.HTTP_200_OK
        item.refresh_from_db()
        assert item.is_retired is False
        assert item.retired_at is None

    def test_is_retired_writable_via_patch_retired_at_read_only(self, authenticated_client):
        """is_retired round-trips through PATCH; retired_at stays read-only."""
        client, _ = authenticated_client
        item = InventoryItemFactory(is_retired=False)

        url = reverse("inventoryitem-detail", kwargs={"pk": str(item.id)})
        stamp = "2020-01-01T00:00:00Z"
        response = client.patch(url, {"is_retired": True, "retired_at": stamp}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_retired"] is True
        item.refresh_from_db()
        assert item.is_retired is True
        # retired_at is read-only on the serializer: the client value is ignored.
        assert item.retired_at is None

    def test_generate_qr_endpoint(self, authenticated_client, mocker):
        """Test QR code generation endpoint."""
        client, user = authenticated_client
        item = InventoryItemFactory()

        # Mock the QR code service (it's imported inside the method)
        mock_service = mocker.patch("inventory.services.qr_code_service.QRCodeService")
        mock_instance = mock_service.return_value
        mock_instance.generate_for_item.return_value = item

        url = reverse("inventoryitem-generate-qr", kwargs={"pk": str(item.id)})
        response = client.post(url)

        assert response.status_code == status.HTTP_200_OK
        mock_instance.generate_for_item.assert_called_once()

    def test_download_card_endpoint(self, api_client, mocker):
        """Test PDF card download endpoint."""
        item = InventoryItemFactory()

        # Mock PDF generation (the actual method used in the view)
        mock_renderer = mocker.patch("index_cards.services.IndexCardRenderer")
        mock_instance = mock_renderer.return_value
        mock_instance.render_preview.return_value = b"fake pdf content"

        url = reverse("inventoryitem-download_card", kwargs={"pk": str(item.id)})
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response["Content-Type"] == "application/pdf"
        mock_renderer.assert_called_once_with(blank_cards=False)
        mock_instance.render_preview.assert_called_once_with(item, blank_card=False)

    def test_fixture_download_card_endpoint(self, api_client, mocker):
        """Test fixture card download endpoint."""
        fixture = FixtureFactory()

        mock_renderer = mocker.patch("index_cards.services.FixtureCardRenderer")
        mock_instance = mock_renderer.return_value
        mock_instance.render_preview.return_value = b"fixture pdf"

        url = reverse("fixture-download_card", kwargs={"pk": str(fixture.id)})
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response["Content-Type"] == "application/pdf"
        mock_renderer.assert_called_once_with()
        mock_instance.render_preview.assert_called_once_with(fixture)

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
