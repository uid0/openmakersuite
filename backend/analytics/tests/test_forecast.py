"""Tests for the maintenance forecast service."""

from datetime import datetime, timedelta

from django.utils import timezone

import pytest

from analytics.services.forecast import maintenance_forecast
from inventory.models import MaintenanceItem, WorkOrder
from inventory.tests.factories import AssetFactory

pytestmark = pytest.mark.django_db


def _complete_wo(asset, completed_at: datetime):
    item = MaintenanceItem.objects.create(asset=asset, title="Service", interval_days=30)
    return WorkOrder.objects.create(
        maintenance_item=item,
        status=WorkOrder.STATUS_COMPLETED,
        completed_at=completed_at,
        due_date=completed_at.date(),
    )


class TestMaintenanceForecast:
    def test_no_thresholds_means_no_entry(self):
        AssetFactory(maintenance_interval_hours=None, maintenance_interval_days=None)
        assert maintenance_forecast() == []

    def test_below_thresholds_means_no_entry(self):
        asset = AssetFactory(
            hours_used=10,
            maintenance_interval_hours=100,
            maintenance_interval_days=30,
        )
        _complete_wo(asset, timezone.now() - timedelta(days=5))
        assert maintenance_forecast() == []

    def test_hours_threshold_triggers(self):
        asset = AssetFactory(
            hours_used=150,
            maintenance_interval_hours=100,
            maintenance_interval_days=None,
        )
        _complete_wo(asset, timezone.now() - timedelta(days=5))
        result = maintenance_forecast()
        assert len(result) == 1
        assert result[0]["asset_id"] == str(asset.id)
        assert result[0]["due_reason"] == "hours"

    def test_days_threshold_triggers(self):
        asset = AssetFactory(
            hours_used=10,
            maintenance_interval_hours=None,
            maintenance_interval_days=14,
        )
        _complete_wo(asset, timezone.now() - timedelta(days=20))
        result = maintenance_forecast()
        assert len(result) == 1
        assert result[0]["due_reason"] == "days"
        assert result[0]["days_since_last_wo"] == 20

    def test_both_triggers_when_both_cross(self):
        asset = AssetFactory(
            hours_used=200,
            maintenance_interval_hours=100,
            maintenance_interval_days=14,
        )
        _complete_wo(asset, timezone.now() - timedelta(days=30))
        result = maintenance_forecast()
        assert result[0]["due_reason"] == "both"

    def test_never_serviced_with_days_threshold(self):
        asset = AssetFactory(
            hours_used=0,
            maintenance_interval_hours=None,
            maintenance_interval_days=30,
        )
        # No completed WO at all -> flagged immediately.
        result = maintenance_forecast()
        assert len(result) == 1
        assert result[0]["asset_id"] == str(asset.id)
        assert result[0]["last_completed_wo_at"] is None

    def test_excludes_retired_assets(self):
        AssetFactory(
            status="retired",
            hours_used=999,
            maintenance_interval_hours=100,
        )
        assert maintenance_forecast() == []

    def test_sort_order(self):
        a_both = AssetFactory(
            name="aa-both",
            hours_used=200,
            maintenance_interval_hours=100,
            maintenance_interval_days=14,
        )
        _complete_wo(a_both, timezone.now() - timedelta(days=30))
        a_days = AssetFactory(
            name="bb-days",
            hours_used=0,
            maintenance_interval_hours=None,
            maintenance_interval_days=14,
        )
        _complete_wo(a_days, timezone.now() - timedelta(days=20))
        a_hours = AssetFactory(
            name="cc-hours",
            hours_used=200,
            maintenance_interval_hours=100,
            maintenance_interval_days=None,
        )
        _complete_wo(a_hours, timezone.now() - timedelta(days=2))

        result = maintenance_forecast()
        assert [r["due_reason"] for r in result] == ["both", "days", "hours"]
