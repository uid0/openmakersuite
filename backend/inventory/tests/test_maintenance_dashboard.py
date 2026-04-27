"""
Tests for the Maintenance Dashboard endpoint (oms-95o).
"""

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

import pytest
from rest_framework import status

from inventory.models import MaintenanceItem, MaintenanceMaterial, WorkOrder, WorkOrderMaterialUsage
from inventory.tests.factories import AssetFactory

URL = "/api/inventory/maintenance/dashboard/"


def _completed_wo(maintenance_item, completed_at, *, created_at=None):
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
class TestMaintenanceDashboard:
    def test_requires_auth(self, api_client):
        response = api_client.get(URL)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_empty_state_returns_well_formed_shape(self, authenticated_client):
        client, _ = authenticated_client

        response = client.get(URL)
        assert response.status_code == status.HTTP_200_OK
        body = response.data
        assert body["scheduled_pm"] == []
        assert body["unscheduled"] == []
        assert set(body["costs"]["per_period"].keys()) == {
            "today",
            "this_week",
            "this_month",
            "this_year",
            "all_time",
        }
        for key, val in body["costs"]["per_period"].items():
            assert Decimal(val) == Decimal("0.00"), key
        assert body["costs"]["by_asset"] == []

    def test_scheduled_pm_lists_active_recurring_items(self, authenticated_client):
        client, _ = authenticated_client
        asset = AssetFactory(name="Lathe-A")
        item = MaintenanceItem.objects.create(
            asset=asset,
            title="Quarterly oil change",
            interval_days=90,
            estimated_cost=Decimal("75.00"),
            last_completed_at=timezone.now() - timedelta(days=30),
        )
        # Inactive items and one-time items should NOT appear in scheduled_pm.
        MaintenanceItem.objects.create(
            asset=asset,
            title="One-off seal repair",
            interval_days=None,
            estimated_cost=Decimal("0.00"),
        )
        MaintenanceItem.objects.create(
            asset=asset,
            title="Retired check",
            interval_days=30,
            is_active=False,
        )

        response = client.get(URL)
        assert response.status_code == status.HTTP_200_OK

        scheduled = response.data["scheduled_pm"]
        assert len(scheduled) == 1
        row = scheduled[0]
        assert row["maintenance_item_id"] == str(item.id)
        assert row["asset_name"] == "Lathe-A"
        assert row["interval_days"] == 90
        assert row["next_due"] is not None
        assert row["days_until"] == 60
        assert row["is_overdue"] is False

    def test_unscheduled_lists_open_work_orders_for_non_recurring_items(self, authenticated_client):
        client, _ = authenticated_client
        asset = AssetFactory(name="Mill-B")
        unscheduled_item = MaintenanceItem.objects.create(
            asset=asset,
            title="Belt replacement",
            interval_days=None,
        )
        scheduled_item = MaintenanceItem.objects.create(
            asset=asset,
            title="Lubrication",
            interval_days=14,
        )
        open_wo = WorkOrder.objects.create(
            maintenance_item=unscheduled_item,
            status=WorkOrder.STATUS_OPEN,
        )
        # A completed WO should NOT show as unscheduled.
        _completed_wo(unscheduled_item, completed_at=timezone.now())
        # An open WO on a scheduled item should NOT show in unscheduled.
        WorkOrder.objects.create(
            maintenance_item=scheduled_item,
            status=WorkOrder.STATUS_OPEN,
        )

        response = client.get(URL)
        assert response.status_code == status.HTTP_200_OK

        unscheduled = response.data["unscheduled"]
        assert len(unscheduled) == 1
        row = unscheduled[0]
        assert row["workorder_id"] == str(open_wo.id)
        assert row["asset_name"] == "Mill-B"
        assert row["problem"] == "Belt replacement"
        assert row["status"] == WorkOrder.STATUS_OPEN

    def test_cost_per_period_buckets_match_completed_at(self, authenticated_client):
        """today ⊆ this_week ⊆ this_month ⊆ this_year ⊆ all_time, anchored on
        completed_at. Scheduled cost = MaintenanceItem.estimated_cost; unscheduled
        cost = sum(used material qty * unit cost)."""
        client, _ = authenticated_client
        asset = AssetFactory(name="Press-C")

        scheduled_pm = MaintenanceItem.objects.create(
            asset=asset,
            title="Daily inspection",
            interval_days=1,
            estimated_cost=Decimal("10.00"),
        )
        unscheduled_repair = MaintenanceItem.objects.create(
            asset=asset,
            title="Belt swap",
            interval_days=None,
        )

        now = timezone.now()

        # Scheduled WO completed today contributes $10 to every bucket.
        _completed_wo(scheduled_pm, completed_at=now)

        # Unscheduled WO completed today contributes $50 (5 * $10).
        wo_today = _completed_wo(unscheduled_repair, completed_at=now)
        belt = MaintenanceMaterial.objects.create(
            maintenance_item=unscheduled_repair,
            name="Drive belt",
            quantity=Decimal("1.00"),
            estimated_cost_per_unit=Decimal("10.00"),
        )
        WorkOrderMaterialUsage.objects.create(
            work_order=wo_today,
            material=belt,
            material_name=belt.name,
            quantity_planned=Decimal("5.00"),
            was_used=True,
        )
        # Not-used materials must not contribute.
        WorkOrderMaterialUsage.objects.create(
            work_order=wo_today,
            material=belt,
            material_name=belt.name,
            quantity_planned=Decimal("100.00"),
            was_used=False,
        )

        # An older scheduled WO completed >1 year ago — only all_time should see it.
        _completed_wo(scheduled_pm, completed_at=now - timedelta(days=400))

        response = client.get(URL)
        assert response.status_code == status.HTTP_200_OK

        per_period = response.data["costs"]["per_period"]

        today_total = Decimal(per_period["today"])
        week_total = Decimal(per_period["this_week"])
        month_total = Decimal(per_period["this_month"])
        year_total = Decimal(per_period["this_year"])
        all_time = Decimal(per_period["all_time"])

        # Two WOs completed `now` total $10 + $50 = $60. The 400-day-old WO
        # only contributes to all_time.
        assert today_total == Decimal("60.00")
        assert week_total == Decimal("60.00")
        assert month_total == Decimal("60.00")
        assert year_total == Decimal("60.00")
        assert all_time == Decimal("70.00")

    def test_by_asset_rollup_uses_90_day_window(self, authenticated_client):
        client, _ = authenticated_client
        recent_asset = AssetFactory(name="Recent", asset_tag="A-RECENT")
        stale_asset = AssetFactory(name="Stale", asset_tag="A-STALE")
        recent_mi = MaintenanceItem.objects.create(
            asset=recent_asset,
            title="Quarterly",
            interval_days=90,
            estimated_cost=Decimal("100.00"),
        )
        stale_mi = MaintenanceItem.objects.create(
            asset=stale_asset,
            title="Annual",
            interval_days=365,
            estimated_cost=Decimal("999.00"),
        )

        now = timezone.now()
        _completed_wo(
            recent_mi,
            completed_at=now - timedelta(days=30),
            created_at=now - timedelta(days=31),
        )
        # Completed >90 days ago — must NOT appear in by_asset rollup.
        _completed_wo(
            stale_mi,
            completed_at=now - timedelta(days=200),
            created_at=now - timedelta(days=201),
        )

        response = client.get(URL)
        assert response.status_code == status.HTTP_200_OK

        by_asset = response.data["costs"]["by_asset"]
        assert len(by_asset) == 1
        row = by_asset[0]
        assert row["asset_id"] == str(recent_asset.id)
        assert Decimal(row["total_cost"]) == Decimal("100.00")
        assert row["days_in_maintenance_90d"] >= 1
