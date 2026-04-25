"""
Tests for the Asset Total Cost of Ownership report endpoint.
"""

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

import pytest
from rest_framework import status

from inventory.models import (
    Asset,
    MaintenanceItem,
    MaintenanceMaterial,
    WorkOrder,
    WorkOrderMaterialUsage,
)
from inventory.tests.factories import AssetFactory

URL = "/api/inventory/reports/assets/tco/"


def _create_completed_wo(maintenance_item, completed_at, *, created_at=None):
    wo = WorkOrder.objects.create(
        maintenance_item=maintenance_item,
        status=WorkOrder.STATUS_COMPLETED,
        completed_at=completed_at,
    )
    if created_at is not None:
        WorkOrder.objects.filter(pk=wo.pk).update(created_at=created_at)
        wo.refresh_from_db()
    return wo


@pytest.mark.integration
class TestAssetTcoReport:
    """Tests for AssetReportViewSet.tco endpoint."""

    def test_requires_auth(self, api_client):
        response = api_client.get(URL)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_asset_with_no_maintenance_returns_zero_tco(self, authenticated_client):
        client, _ = authenticated_client
        asset = AssetFactory(name="Quiet Lathe", asset_tag="A-001")

        response = client.get(URL)
        assert response.status_code == status.HTTP_200_OK

        rows = {row["asset_id"]: row for row in response.data}
        row = rows[str(asset.id)]
        assert row["maintenance_days_last_90"] == 0
        assert Decimal(row["scheduled_maintenance_cost"]) == Decimal("0.00")
        assert Decimal(row["unscheduled_maintenance_cost"]) == Decimal("0.00")
        assert Decimal(row["repair_cost"]) == Decimal("0.00")
        assert Decimal(row["tco"]) == Decimal("0.00")

    def test_tco_includes_scheduled_and_unscheduled(self, authenticated_client):
        """Scheduled PM cost comes from MaintenanceItem.estimated_cost; unscheduled
        cost is computed from used materials on the WorkOrder."""
        client, _ = authenticated_client
        asset = AssetFactory(name="Heavy Mill", asset_tag="A-100")

        scheduled_pm = MaintenanceItem.objects.create(
            asset=asset,
            title="Quarterly oil change",
            interval_days=90,
            estimated_cost=Decimal("75.00"),
        )
        unscheduled_repair = MaintenanceItem.objects.create(
            asset=asset,
            title="Belt replacement",
            interval_days=None,
            estimated_cost=Decimal("0.00"),
        )

        now = timezone.now()
        _create_completed_wo(scheduled_pm, completed_at=now - timedelta(days=10))

        wo_unscheduled = _create_completed_wo(
            unscheduled_repair, completed_at=now - timedelta(days=5)
        )
        belt_material = MaintenanceMaterial.objects.create(
            maintenance_item=unscheduled_repair,
            name="Drive belt",
            quantity=Decimal("1.00"),
            estimated_cost_per_unit=Decimal("42.50"),
        )
        WorkOrderMaterialUsage.objects.create(
            work_order=wo_unscheduled,
            material=belt_material,
            material_name=belt_material.name,
            quantity_planned=Decimal("2.00"),
            was_used=True,
        )
        unused_material = MaintenanceMaterial.objects.create(
            maintenance_item=unscheduled_repair,
            name="Spare bolt",
            quantity=Decimal("4.00"),
            estimated_cost_per_unit=Decimal("1.00"),
        )
        WorkOrderMaterialUsage.objects.create(
            work_order=wo_unscheduled,
            material=unused_material,
            material_name=unused_material.name,
            quantity_planned=Decimal("4.00"),
            was_used=False,
        )

        response = client.get(URL)
        assert response.status_code == status.HTTP_200_OK

        rows = {row["asset_id"]: row for row in response.data}
        row = rows[str(asset.id)]

        assert Decimal(row["scheduled_maintenance_cost"]) == Decimal("75.00")
        assert Decimal(row["unscheduled_maintenance_cost"]) == Decimal("85.00")
        assert Decimal(row["repair_cost"]) == Decimal("0.00")
        assert Decimal(row["tco"]) == Decimal("160.00")

    def test_maintenance_days_counted_correctly_on_90d_boundary(self, authenticated_client):
        """Work orders that completed >90 days ago are excluded; recent overlap
        contributes distinct calendar days; current MAINTENANCE status adds today.
        """
        client, _ = authenticated_client
        asset = AssetFactory(name="Window Asset", asset_tag="A-200")

        mi = MaintenanceItem.objects.create(
            asset=asset,
            title="Inspection",
            interval_days=30,
            estimated_cost=Decimal("10.00"),
        )

        now = timezone.now()

        # Outside the window — should be ignored entirely.
        _create_completed_wo(
            mi,
            completed_at=now - timedelta(days=120),
            created_at=now - timedelta(days=125),
        )

        # Spans 4 calendar days inside the window (created 10d ago, completed 7d ago).
        _create_completed_wo(
            mi,
            completed_at=now - timedelta(days=7),
            created_at=now - timedelta(days=10),
        )

        # Currently in MAINTENANCE — adds today.
        asset.status = Asset.MAINTENANCE
        asset.save(update_fields=["status"])

        response = client.get(URL)
        assert response.status_code == status.HTTP_200_OK

        rows = {row["asset_id"]: row for row in response.data}
        row = rows[str(asset.id)]

        # 4 days from the in-window WO + today (status=maintenance) = 5
        assert row["maintenance_days_last_90"] == 5
        # Only the in-window completion contributes scheduled cost.
        assert Decimal(row["scheduled_maintenance_cost"]) == Decimal("10.00")

    def test_rows_sorted_by_tco_descending(self, authenticated_client):
        client, _ = authenticated_client
        cheap = AssetFactory(name="Cheap", asset_tag="A-CHP")
        pricey = AssetFactory(name="Pricey", asset_tag="A-PRC")

        for asset, cost in ((cheap, Decimal("5.00")), (pricey, Decimal("500.00"))):
            mi = MaintenanceItem.objects.create(
                asset=asset,
                title="Daily check",
                interval_days=1,
                estimated_cost=cost,
            )
            _create_completed_wo(mi, completed_at=timezone.now() - timedelta(days=1))

        response = client.get(URL)
        assert response.status_code == status.HTTP_200_OK

        ids = [row["asset_id"] for row in response.data]
        assert ids.index(str(pricey.id)) < ids.index(str(cheap.id))
