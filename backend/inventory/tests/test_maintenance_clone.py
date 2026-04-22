"""
Tests for MaintenanceItemViewSet.clone action — clone a PM plan between assets.
"""

from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status

from inventory.models import MaintenanceItem, MaintenanceMaterial, MaintenanceTask
from inventory.tests.factories import AssetFactory

pytestmark = pytest.mark.django_db


def _build_source_item(asset):
    item = MaintenanceItem.objects.create(
        asset=asset,
        title="Monthly inspection",
        description="Check everything",
        instructions="Step-by-step",
        estimated_time_minutes=45,
        estimated_cost=Decimal("12.50"),
        interval_days=30,
        last_completed_at=timezone.now(),
        is_active=True,
    )
    MaintenanceTask.objects.create(
        maintenance_item=item, order=1, title="Disconnect power", is_required=True
    )
    MaintenanceTask.objects.create(
        maintenance_item=item, order=2, title="Inspect bearings", is_required=False
    )
    MaintenanceMaterial.objects.create(
        maintenance_item=item,
        name="Grease",
        quantity=Decimal("2.00"),
        unit="oz",
        estimated_cost_per_unit=Decimal("1.25"),
        notes="Lithium-based",
    )
    MaintenanceMaterial.objects.create(
        maintenance_item=item,
        name="Rag",
        quantity=Decimal("3.00"),
        unit="pieces",
        estimated_cost_per_unit=Decimal("0.50"),
    )
    return item


class TestMaintenanceItemClone:
    def test_clone_requires_auth(self, api_client):
        source_asset = AssetFactory()
        target_asset = AssetFactory()
        item = _build_source_item(source_asset)
        url = reverse("maintenanceitem-clone", args=[item.pk])
        response = api_client.post(url, {"target_asset_id": str(target_asset.pk)})
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_clone_happy_path(self, authenticated_client):
        client, _ = authenticated_client
        source_asset = AssetFactory()
        target_asset = AssetFactory()
        item = _build_source_item(source_asset)
        url = reverse("maintenanceitem-clone", args=[item.pk])

        response = client.post(url, {"target_asset_id": str(target_asset.pk)}, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        cloned_id = response.data["id"]
        assert cloned_id != str(item.pk)

        cloned = MaintenanceItem.objects.get(pk=cloned_id)
        assert cloned.asset_id == target_asset.pk
        assert cloned.title == item.title
        assert cloned.description == item.description
        assert cloned.instructions == item.instructions
        assert cloned.interval_days == item.interval_days
        assert cloned.estimated_time_minutes == item.estimated_time_minutes
        assert cloned.estimated_cost == item.estimated_cost
        assert cloned.is_active == item.is_active
        # last_completed_at must be null on the clone (fresh template)
        assert cloned.last_completed_at is None

        # Tasks cloned with preserved order
        cloned_tasks = list(cloned.tasks.order_by("order"))
        source_tasks = list(item.tasks.order_by("order"))
        assert len(cloned_tasks) == len(source_tasks) == 2
        for c, s in zip(cloned_tasks, source_tasks):
            assert c.order == s.order
            assert c.title == s.title
            assert c.description == s.description
            assert c.is_required == s.is_required
            assert c.pk != s.pk

        # Materials cloned
        cloned_materials = list(cloned.materials.order_by("name"))
        source_materials = list(item.materials.order_by("name"))
        assert len(cloned_materials) == len(source_materials) == 2
        for c, s in zip(cloned_materials, source_materials):
            assert c.name == s.name
            assert c.quantity == s.quantity
            assert c.unit == s.unit
            assert c.estimated_cost_per_unit == s.estimated_cost_per_unit
            assert c.notes == s.notes
            assert c.pk != s.pk

        # Source item untouched
        item.refresh_from_db()
        assert item.asset_id == source_asset.pk
        assert item.tasks.count() == 2
        assert item.materials.count() == 2

    def test_clone_missing_target_asset_id_returns_400(self, authenticated_client):
        client, _ = authenticated_client
        source_asset = AssetFactory()
        item = _build_source_item(source_asset)
        url = reverse("maintenanceitem-clone", args=[item.pk])

        response = client.post(url, {}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "target_asset_id" in response.data["detail"]

    def test_clone_nonexistent_target_returns_404(self, authenticated_client):
        client, _ = authenticated_client
        source_asset = AssetFactory()
        item = _build_source_item(source_asset)
        url = reverse("maintenanceitem-clone", args=[item.pk])

        bogus_id = "00000000-0000-0000-0000-000000000000"
        response = client.post(url, {"target_asset_id": bogus_id}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_clone_malformed_target_id_returns_404(self, authenticated_client):
        client, _ = authenticated_client
        source_asset = AssetFactory()
        item = _build_source_item(source_asset)
        url = reverse("maintenanceitem-clone", args=[item.pk])

        response = client.post(url, {"target_asset_id": "not-a-uuid"}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND
