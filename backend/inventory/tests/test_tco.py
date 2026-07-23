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
        status=WorkOrder.Status.COMPLETED,
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
        asset.status = Asset.Status.MAINTENANCE
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


def _closed_vendor_wo(
    asset,
    *,
    allocated_cost,
    closed_days_ago=10,
    status_value=None,
    share_pct=Decimal("100"),
):
    """Create a ThirdPartyWorkOrder bypassing the state machine and link it to
    ``asset`` with the given ``allocated_cost``. Used for TCO rollup tests."""
    from maintenance_orders.models import ThirdPartyWorkOrder, ThirdPartyWorkOrderAsset
    from vendors.models import Vendor

    vendor, _ = Vendor.objects.get_or_create(
        name="TCO Test Vendor",
        defaults={"vendor_kind": Vendor.KIND_HVAC},
    )
    wo = ThirdPartyWorkOrder.objects.create(
        title="Vendor work",
        vendor=vendor,
        asset=asset,
    )
    wo.status = status_value or ThirdPartyWorkOrder.STATUS_CLOSED
    if closed_days_ago is not None:
        wo.closed_at = timezone.now() - timedelta(days=closed_days_ago)
    wo.save(update_fields=["status", "closed_at"])
    return ThirdPartyWorkOrderAsset.objects.create(
        work_order=wo,
        asset=asset,
        share_pct=share_pct,
        allocated_cost=allocated_cost,
    )


@pytest.mark.integration
class TestAssetTcoVendorRollup:
    """ThirdPartyWorkOrderAsset.allocated_cost rolls into vendor_maintenance_cost
    and total_maintenance_cost_90d when the parent WO is closed in the window."""

    def test_vendor_only_asset_reports_vendor_cost(self, authenticated_client):
        client, _ = authenticated_client
        asset = AssetFactory(name="Vendor Only", asset_tag="A-V01")
        _closed_vendor_wo(asset, allocated_cost=Decimal("250.00"), closed_days_ago=5)

        response = client.get(URL)
        assert response.status_code == status.HTTP_200_OK

        row = {r["asset_id"]: r for r in response.data}[str(asset.id)]
        assert Decimal(row["preventive_maintenance_cost"]) == Decimal("0.00")
        assert Decimal(row["vendor_maintenance_cost"]) == Decimal("250.00")
        assert Decimal(row["total_maintenance_cost_90d"]) == Decimal("250.00")

    def test_blended_preventive_and_vendor_cost(self, authenticated_client):
        client, _ = authenticated_client
        asset = AssetFactory(name="Mixed", asset_tag="A-MIX")

        mi = MaintenanceItem.objects.create(
            asset=asset,
            title="Quarterly PM",
            interval_days=90,
            estimated_cost=Decimal("100.00"),
        )
        _create_completed_wo(mi, completed_at=timezone.now() - timedelta(days=7))
        _closed_vendor_wo(asset, allocated_cost=Decimal("300.00"), closed_days_ago=3)

        response = client.get(URL)
        assert response.status_code == status.HTTP_200_OK

        row = {r["asset_id"]: r for r in response.data}[str(asset.id)]
        assert Decimal(row["preventive_maintenance_cost"]) == Decimal("100.00")
        assert Decimal(row["vendor_maintenance_cost"]) == Decimal("300.00")
        assert Decimal(row["total_maintenance_cost_90d"]) == Decimal("400.00")

    def test_multi_asset_po_allocates_per_asset(self, authenticated_client):
        """One vendor WO covering two assets — each row gets its own allocated_cost,
        not the parent WO's full invoice."""
        from maintenance_orders.models import ThirdPartyWorkOrder, ThirdPartyWorkOrderAsset
        from vendors.models import Vendor

        client, _ = authenticated_client
        asset_a = AssetFactory(name="Split A", asset_tag="A-SPA")
        asset_b = AssetFactory(name="Split B", asset_tag="A-SPB")

        vendor, _ = Vendor.objects.get_or_create(
            name="Split Vendor", defaults={"vendor_kind": Vendor.KIND_HVAC}
        )
        wo = ThirdPartyWorkOrder.objects.create(
            title="Two-asset truck roll", vendor=vendor, asset=asset_a
        )
        wo.status = ThirdPartyWorkOrder.STATUS_CLOSED
        wo.closed_at = timezone.now() - timedelta(days=2)
        wo.save(update_fields=["status", "closed_at"])

        ThirdPartyWorkOrderAsset.objects.create(
            work_order=wo,
            asset=asset_a,
            share_pct=Decimal("60"),
            allocated_cost=Decimal("180.00"),
        )
        ThirdPartyWorkOrderAsset.objects.create(
            work_order=wo,
            asset=asset_b,
            share_pct=Decimal("40"),
            allocated_cost=Decimal("120.00"),
        )

        response = client.get(URL)
        assert response.status_code == status.HTTP_200_OK
        rows = {r["asset_id"]: r for r in response.data}
        assert Decimal(rows[str(asset_a.id)]["vendor_maintenance_cost"]) == Decimal("180.00")
        assert Decimal(rows[str(asset_b.id)]["vendor_maintenance_cost"]) == Decimal("120.00")

    def test_cancelled_vendor_wo_excluded(self, authenticated_client):
        from maintenance_orders.models import ThirdPartyWorkOrder

        client, _ = authenticated_client
        asset = AssetFactory(name="Cancelled Vendor", asset_tag="A-CXL")
        _closed_vendor_wo(
            asset,
            allocated_cost=Decimal("999.00"),
            closed_days_ago=5,
            status_value=ThirdPartyWorkOrder.STATUS_CANCELLED,
        )

        response = client.get(URL)
        assert response.status_code == status.HTTP_200_OK

        row = {r["asset_id"]: r for r in response.data}[str(asset.id)]
        assert Decimal(row["vendor_maintenance_cost"]) == Decimal("0.00")
        assert Decimal(row["total_maintenance_cost_90d"]) == Decimal("0.00")

    def test_vendor_wo_outside_window_excluded(self, authenticated_client):
        client, _ = authenticated_client
        asset = AssetFactory(name="Old Vendor", asset_tag="A-OLD")
        _closed_vendor_wo(asset, allocated_cost=Decimal("500.00"), closed_days_ago=120)

        response = client.get(URL)
        assert response.status_code == status.HTTP_200_OK

        row = {r["asset_id"]: r for r in response.data}[str(asset.id)]
        assert Decimal(row["vendor_maintenance_cost"]) == Decimal("0.00")

    def test_open_vendor_wo_excluded(self, authenticated_client):
        """A WO still in financial_review (not yet closed) is excluded — its
        closed_at is null even if allocated_cost has been materialized."""
        from maintenance_orders.models import ThirdPartyWorkOrder

        client, _ = authenticated_client
        asset = AssetFactory(name="Pending Vendor", asset_tag="A-PND")
        _closed_vendor_wo(
            asset,
            allocated_cost=Decimal("420.00"),
            closed_days_ago=None,
            status_value=ThirdPartyWorkOrder.STATUS_FINANCIAL_REVIEW,
        )

        response = client.get(URL)
        assert response.status_code == status.HTTP_200_OK

        row = {r["asset_id"]: r for r in response.data}[str(asset.id)]
        assert Decimal(row["vendor_maintenance_cost"]) == Decimal("0.00")


@pytest.mark.integration
class TestAssetTcoActualMaterialCost(object):
    """op-srrv (B5) — tco prices internal work from the captured material
    actuals where they exist. This is internal analytics, so it is *not* gated
    on ``Asset.is_cost_recoverable`` the way the cost-recovery statement is."""

    @staticmethod
    def _priced_usage(work_order, *, name, quantity_used, unit_cost):
        return WorkOrderMaterialUsage.objects.create(
            work_order=work_order,
            material_name=name,
            is_ad_hoc=True,
            quantity_planned=quantity_used,
            quantity_used=quantity_used,
            was_used=True,
            unit_cost=unit_cost,
        )

    def test_actual_cost_replaces_scheduled_estimate(self, authenticated_client):
        """A scheduled PM whose job recorded real material spend reports that
        spend instead of the template estimate."""
        client, _ = authenticated_client
        asset = AssetFactory(name="Priced RTU", asset_tag="A-ACT1")
        mi = MaintenanceItem.objects.create(
            asset=asset,
            title="Quarterly filter",
            interval_days=90,
            estimated_cost=Decimal("75.00"),
        )
        wo = _create_completed_wo(mi, completed_at=timezone.now() - timedelta(days=5))
        self._priced_usage(
            wo, name="Filter", quantity_used=Decimal("2.00"), unit_cost=Decimal("61.25")
        )

        response = client.get(URL)
        row = {r["asset_id"]: r for r in response.data}[str(asset.id)]
        assert Decimal(row["scheduled_maintenance_cost"]) == Decimal("122.50")
        assert Decimal(row["unscheduled_maintenance_cost"]) == Decimal("0.00")
        assert Decimal(row["tco"]) == Decimal("122.50")

    def test_actual_cost_replaces_unscheduled_material_estimate(self, authenticated_client):
        client, _ = authenticated_client
        asset = AssetFactory(name="Priced Lathe", asset_tag="A-ACT2")
        mi = MaintenanceItem.objects.create(
            asset=asset,
            title="Belt replacement",
            interval_days=None,
            estimated_cost=Decimal("0.00"),
        )
        wo = _create_completed_wo(mi, completed_at=timezone.now() - timedelta(days=5))
        belt = MaintenanceMaterial.objects.create(
            maintenance_item=mi,
            name="Drive belt",
            quantity=Decimal("1.00"),
            estimated_cost_per_unit=Decimal("42.50"),
        )
        # Same line, now with what it really cost.
        WorkOrderMaterialUsage.objects.create(
            work_order=wo,
            material=belt,
            material_name=belt.name,
            quantity_planned=Decimal("2.00"),
            quantity_used=Decimal("2.00"),
            was_used=True,
            unit_cost=Decimal("50.00"),
        )

        response = client.get(URL)
        row = {r["asset_id"]: r for r in response.data}[str(asset.id)]
        assert Decimal(row["unscheduled_maintenance_cost"]) == Decimal("100.00")

    def test_unpriced_work_order_keeps_the_estimate(self, authenticated_client):
        """No regression: with no unit_cost anywhere the old numbers stand."""
        client, _ = authenticated_client
        asset = AssetFactory(name="Unpriced", asset_tag="A-ACT3")
        mi = MaintenanceItem.objects.create(
            asset=asset,
            title="Quarterly PM",
            interval_days=90,
            estimated_cost=Decimal("75.00"),
        )
        _create_completed_wo(mi, completed_at=timezone.now() - timedelta(days=5))

        response = client.get(URL)
        row = {r["asset_id"]: r for r in response.data}[str(asset.id)]
        assert Decimal(row["scheduled_maintenance_cost"]) == Decimal("75.00")

    def test_not_gated_on_cost_recoverable_flag(self, authenticated_client):
        """Two identical assets, one flagged recoverable — tco reports the same
        cost for both."""
        client, _ = authenticated_client
        flagged = AssetFactory(name="TCO Flagged", asset_tag="A-ACT4", is_cost_recoverable=True)
        plain = AssetFactory(name="TCO Plain", asset_tag="A-ACT5")
        for asset in (flagged, plain):
            mi = MaintenanceItem.objects.create(
                asset=asset, title="Repair", interval_days=None, estimated_cost=Decimal("0.00")
            )
            wo = _create_completed_wo(mi, completed_at=timezone.now() - timedelta(days=5))
            self._priced_usage(
                wo, name="Part", quantity_used=Decimal("1.00"), unit_cost=Decimal("40.00")
            )

        rows = {r["asset_id"]: r for r in client.get(URL).data}
        assert Decimal(rows[str(flagged.id)]["tco"]) == Decimal("40.00")
        assert Decimal(rows[str(plain.id)]["tco"]) == Decimal("40.00")
