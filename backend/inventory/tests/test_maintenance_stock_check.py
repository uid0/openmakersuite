"""
Tests for MaintenanceMaterial ↔ InventoryItem linkage and the
check_material_stock DRF action on MaintenanceItemViewSet.
"""

from decimal import Decimal

from django.urls import reverse

import pytest
from rest_framework import status

from inventory.models import MaintenanceItem, MaintenanceMaterial
from inventory.serializers import MaintenanceMaterialSerializer
from inventory.tests.factories import AssetFactory, InventoryItemFactory

pytestmark = pytest.mark.django_db


def _make_item_with_material(*, inventory_item=None):
    asset = AssetFactory()
    item = MaintenanceItem.objects.create(
        asset=asset,
        title="Monthly inspection",
        description="Standard monthly checklist",
        interval_days=30,
    )
    material = MaintenanceMaterial.objects.create(
        maintenance_item=item,
        name=(inventory_item.name if inventory_item else "Generic material"),
        quantity=Decimal("1.00"),
        inventory_item=inventory_item,
    )
    return item, material


class TestMaintenanceMaterialInventoryItemFK:
    """AC #1: nullable FK to InventoryItem."""

    def test_fk_is_nullable(self):
        item, material = _make_item_with_material()
        assert material.inventory_item is None

    def test_fk_can_be_set(self):
        inv = InventoryItemFactory(current_stock=10, minimum_stock=2)
        item, material = _make_item_with_material(inventory_item=inv)
        material.refresh_from_db()
        assert material.inventory_item_id == inv.id

    def test_inventory_item_delete_sets_null(self):
        inv = InventoryItemFactory()
        item, material = _make_item_with_material(inventory_item=inv)
        inv.delete()
        material.refresh_from_db()
        assert material.inventory_item is None


class TestMaintenanceMaterialSerializerInventoryDetail:
    """AC #2: serializer exposes inventory_item_detail when FK is set."""

    def test_detail_is_null_when_fk_unset(self):
        _, material = _make_item_with_material()
        data = MaintenanceMaterialSerializer(material).data
        assert data["inventory_item"] is None
        assert data["inventory_item_detail"] is None

    def test_detail_populated_when_fk_set(self):
        inv = InventoryItemFactory(
            name="Widget",
            current_stock=5,
            minimum_stock=10,
            reorder_quantity=20,
        )
        _, material = _make_item_with_material(inventory_item=inv)
        data = MaintenanceMaterialSerializer(material).data
        detail = data["inventory_item_detail"]
        assert detail["id"] == str(inv.id)
        assert detail["name"] == "Widget"
        assert detail["current_stock"] == 5
        assert detail["minimum_stock"] == 10
        assert detail["reorder_quantity"] == 20


@pytest.mark.integration
class TestCheckMaterialStockAction:
    """AC #3: GET /api/inventory/maintenance-items/<id>/check_material_stock/."""

    def _url(self, item):
        return reverse("maintenanceitem-check-material-stock", args=[item.id])

    def test_no_linked_items_returns_empty(self, api_client):
        item, _ = _make_item_with_material()
        response = api_client.get(self._url(item))
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {"low_stock_alerts": []}

    def test_item_above_minimum_returns_no_alert(self, api_client):
        inv = InventoryItemFactory(current_stock=50, minimum_stock=10)
        item, _ = _make_item_with_material(inventory_item=inv)
        response = api_client.get(self._url(item))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["low_stock_alerts"] == []

    def test_item_below_minimum_returns_alert(self, api_client):
        inv = InventoryItemFactory(
            name="Filter",
            current_stock=1,
            minimum_stock=5,
            reorder_quantity=10,
        )
        item, material = _make_item_with_material(inventory_item=inv)
        response = api_client.get(self._url(item))
        assert response.status_code == status.HTTP_200_OK
        alerts = response.data["low_stock_alerts"]
        assert len(alerts) == 1
        alert = alerts[0]
        assert alert["material_id"] == str(material.id)
        assert alert["item_id"] == str(inv.id)
        assert alert["name"] == "Filter"
        assert alert["current"] == 1
        assert alert["minimum"] == 5
        assert alert["reorder_qty"] == 10

    def test_item_at_minimum_returns_an_alert(self, api_client):
        """At the minimum IS low, here as everywhere else.

        This action used to be the outlier: a raw ``current_stock >=
        minimum_stock`` skip meant an item sitting exactly on its reorder point
        raised no maintenance warning while ``needs_reorder`` — the badge, the
        low-stock action, the serializer field — all called it low. The
        predicate now routes through that chokepoint, so the boundary agrees.
        """
        inv = InventoryItemFactory(current_stock=5, minimum_stock=5)
        assert inv.needs_reorder
        item, _ = _make_item_with_material(inventory_item=inv)
        response = api_client.get(self._url(item))
        alerts = response.data["low_stock_alerts"]
        assert len(alerts) == 1
        assert alerts[0]["item_id"] == str(inv.id)

    def test_item_one_unit_above_minimum_returns_no_alert(self, api_client):
        """The tight control for the boundary above: one unit clear is not low."""
        inv = InventoryItemFactory(current_stock=6, minimum_stock=5)
        item, _ = _make_item_with_material(inventory_item=inv)
        response = api_client.get(self._url(item))
        assert response.data["low_stock_alerts"] == []

    def test_mixed_materials_only_low_stock_reported(self, api_client):
        asset = AssetFactory()
        item = MaintenanceItem.objects.create(
            asset=asset,
            title="Mixed",
            description="",
            interval_days=30,
        )
        healthy = InventoryItemFactory(current_stock=50, minimum_stock=10)
        low = InventoryItemFactory(current_stock=1, minimum_stock=5, name="LowOne")
        MaintenanceMaterial.objects.create(
            maintenance_item=item, name="healthy", quantity=Decimal("1.00"), inventory_item=healthy
        )
        MaintenanceMaterial.objects.create(
            maintenance_item=item, name="low", quantity=Decimal("1.00"), inventory_item=low
        )
        MaintenanceMaterial.objects.create(
            maintenance_item=item, name="unlinked", quantity=Decimal("1.00")
        )

        response = api_client.get(self._url(item))
        alerts = response.data["low_stock_alerts"]
        assert len(alerts) == 1
        assert alerts[0]["name"] == "LowOne"
