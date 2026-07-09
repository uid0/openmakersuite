"""
Integration tests for dashboard API views.
"""

from django.contrib.auth import get_user_model

import pytest
from rest_framework import status

from dashboard.tests.factories import DashboardWidgetFactory
from inventory.tests.factories import InventoryItemFactory
from reorder_queue.tests.factories import ReorderRequestFactory

User = get_user_model()


@pytest.mark.integration
class TestDashboardWidgetViews:
    """Tests for dashboard widget API endpoints."""

    def test_get_user_widgets_requires_auth(self, api_client):
        """Test that getting widgets requires authentication."""
        response = api_client.get("/api/dashboard/widgets/")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_get_user_widgets_creates_defaults(self, authenticated_client):
        """Test that default widgets are created if user has none."""
        client, user = authenticated_client
        response = client.get("/api/dashboard/widgets/")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 5
        assert all(w["is_visible"] for w in data)

    def test_get_user_widgets_returns_existing(self, authenticated_client):
        """Test getting existing widgets."""
        client, user = authenticated_client
        widget = DashboardWidgetFactory(user=user, widget_type="low_stock")

        response = client.get("/api/dashboard/widgets/")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) >= 1
        widget_ids = [w["id"] for w in data]
        assert widget.id in widget_ids

    def test_save_user_widgets(self, authenticated_client):
        """Test saving widget layout."""
        client, user = authenticated_client
        widget = DashboardWidgetFactory(
            user=user, widget_type="low_stock", position_x=0, position_y=0
        )

        widgets_data = [
            {
                "id": widget.id,
                "widget_type": "low_stock",
                "position_x": 2,
                "position_y": 1,
                "width": 3,
                "height": 2,
                "is_visible": True,
                "order": 0,
                "settings": {},
            }
        ]

        response = client.post(
            "/api/dashboard/widgets/save/", {"widgets": widgets_data}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        widget.refresh_from_db()
        assert widget.position_x == 2
        assert widget.position_y == 1
        assert widget.width == 3
        assert widget.height == 2

    def test_save_user_widgets_requires_list(self, authenticated_client):
        """Test that widgets must be a list."""
        client, user = authenticated_client
        response = client.post(
            "/api/dashboard/widgets/save/", {"widgets": "not a list"}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
class TestDashboardWidgetDataViews:
    """Tests for dashboard widget data endpoints."""

    def test_get_low_stock_data_requires_auth(self, api_client):
        """Test that low stock data requires authentication."""
        response = api_client.get("/api/dashboard/widget-data/low-stock/")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_get_low_stock_data(self, authenticated_client):
        """Test getting low stock data."""
        client, user = authenticated_client
        # Create items with low stock
        item1 = InventoryItemFactory(current_stock=5, minimum_stock=10)
        item2 = InventoryItemFactory(current_stock=3, minimum_stock=10)
        # Create item with adequate stock
        InventoryItemFactory(current_stock=20, minimum_stock=10)

        response = client.get("/api/dashboard/widget-data/low-stock/")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["count"] >= 2
        assert len(data["items"]) >= 2
        item_names = [item["name"] for item in data["items"]]
        assert item1.name in item_names
        assert item2.name in item_names

    def test_get_pending_reorders_data(self, authenticated_client):
        """Test getting pending reorders data."""
        client, user = authenticated_client
        item = InventoryItemFactory()
        request = ReorderRequestFactory(item=item, status="pending")

        response = client.get("/api/dashboard/widget-data/pending-reorders/")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["count"] >= 1
        assert len(data["requests"]) >= 1
        request_ids = [r["id"] for r in data["requests"]]
        assert request.id in request_ids

    def test_get_asset_problems_data(self, authenticated_client):
        """Test getting asset problems data."""
        client, user = authenticated_client
        from inventory.models import AssetProblem
        from inventory.tests.factories import AssetFactory

        asset = AssetFactory()
        problem = AssetProblem.objects.create(
            asset=asset, status="reported", description="Test problem", reported_by="testuser"
        )

        response = client.get("/api/dashboard/widget-data/asset-problems/")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["count"] >= 1
        assert len(data["problems"]) >= 1
        problem_ids = [p["id"] for p in data["problems"]]
        assert str(problem.id) in problem_ids

    def test_get_qr_scans_data(self, authenticated_client):
        """Test getting QR scans data."""
        client, user = authenticated_client
        from datetime import timedelta

        from django.utils import timezone

        from inventory.tests.factories import AssetFactory

        # Create asset with recent scan
        asset = AssetFactory()
        asset.last_scanned_at = timezone.now() - timedelta(days=1)
        asset.save()

        response = client.get("/api/dashboard/widget-data/qr-scans/")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "total_scans" in data
        assert "asset_scans" in data
        assert "item_scans" in data
        assert "daily_scans" in data

    def test_get_deliveries_data(self, authenticated_client):
        """Deliveries widget returns 200 for a delivery that HAS items.

        Regression (prod 500 -> whole-dashboard crash): get_deliveries_data
        used prefetch_related("items__purchase_order_item__item"), but
        PurchaseOrderItem has no "item" relation. Django only validates that
        trailing segment once level-2 (the DeliveryItems) yields objects, so a
        delivery WITH items raised ValueError while a delivery without items did
        not. The previous version of this test created a delivery with no items
        and thus never traversed far enough to catch it.
        """
        client, user = authenticated_client
        from datetime import timedelta
        from decimal import Decimal

        from django.utils import timezone

        from inventory.tests.factories import SupplierFactory
        from reorder_queue.models import (
            DeliveryItem,
            OrderDelivery,
            PurchaseOrder,
            PurchaseOrderItem,
        )

        supplier = SupplierFactory()
        po = PurchaseOrder.objects.create(
            supplier=supplier, status=PurchaseOrder.SENT, created_by=user
        )
        # Freeform line item (no item_supplier / asset needed).
        po_item = PurchaseOrderItem.objects.create(
            purchase_order=po,
            description="Freeform widget",
            quantity_ordered=5,
            unit_cost_ordered=Decimal("1.00"),
        )
        delivery = OrderDelivery.objects.create(
            purchase_order=po,
            delivery_date=timezone.now() - timedelta(days=1),
            received_by=user,
        )
        DeliveryItem.objects.create(
            delivery=delivery,
            purchase_order_item=po_item,
            quantity_received=3,
        )

        response = client.get("/api/dashboard/widget-data/deliveries/")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["count"] == 1
        assert len(data["deliveries"]) == 1
        row = data["deliveries"][0]
        assert row["items_count"] == 1
        assert row["total_quantity"] == 3
        assert row["supplier_name"] == supplier.name
