"""Tests for analytics aggregation service."""

from datetime import date, datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone

import pytest

from analytics.services.aggregation import (
    category_spend,
    top_users,
    utilization,
    value_summary,
    wo_volume,
)
from inventory.models import MaintenanceItem, WorkOrder
from inventory.tests.factories import AssetFactory, CategoryFactory
from maintenance_orders.models import ThirdPartyWorkOrder
from vendors.models import Vendor

User = get_user_model()
pytestmark = pytest.mark.django_db


def _aware(d: date) -> datetime:
    return timezone.make_aware(
        datetime.combine(d, datetime.min.time()), timezone.get_current_timezone()
    )


def _make_completed_wo(
    *,
    completed_at: datetime,
    estimated_external_cost: Decimal | None = Decimal("100.00"),
    estimated_internal_cost: Decimal = Decimal("25.00"),
    asset=None,
    assigned_to=None,
):
    asset = asset or AssetFactory()
    item = MaintenanceItem.objects.create(
        asset=asset,
        title="Routine",
        description="",
        interval_days=30,
        estimated_cost=estimated_internal_cost,
    )
    return WorkOrder.objects.create(
        maintenance_item=item,
        due_date=completed_at.date(),
        status=WorkOrder.Status.COMPLETED,
        completed_at=completed_at,
        estimated_external_cost=estimated_external_cost,
        assigned_to=assigned_to,
    )


def _make_closed_tpwo(
    *,
    closed_at: datetime,
    actual: Decimal = Decimal("400.00"),
    nte: Decimal = Decimal("500.00"),
    asset=None,
):
    asset = asset or AssetFactory()
    vendor = Vendor.objects.create(name=f"V-{asset.id}")
    return ThirdPartyWorkOrder.objects.create(
        title="Repair",
        asset=asset,
        vendor=vendor,
        status=ThirdPartyWorkOrder.STATUS_CLOSED,
        closed_at=closed_at,
        actual_invoice_total=actual,
        nte_amount=nte,
    )


class TestValueSummary:
    def test_empty_window(self):
        result = value_summary(date(2026, 1, 1), date(2026, 2, 1))
        assert result["internal_completed_count"] == 0
        assert result["external_closed_count"] == 0
        assert Decimal(result["internal_estimated_external_cost"]) == Decimal("0.00")
        assert Decimal(result["external_actual_cost"]) == Decimal("0.00")

    def test_internal_counterfactual_savings(self):
        _make_completed_wo(
            completed_at=_aware(date(2026, 4, 15)),
            estimated_external_cost=Decimal("300.00"),
            estimated_internal_cost=Decimal("50.00"),
        )
        result = value_summary(date(2026, 4, 1), date(2026, 5, 1))
        assert result["internal_completed_count"] == 1
        assert Decimal(result["internal_estimated_external_cost"]) == Decimal("300.00")
        assert Decimal(result["internal_estimated_internal_cost"]) == Decimal("50.00")
        assert Decimal(result["internal_net_value"]) == Decimal("250.00")

    def test_external_actual_spend(self):
        _make_closed_tpwo(
            closed_at=_aware(date(2026, 4, 20)),
            actual=Decimal("475.50"),
            nte=Decimal("500.00"),
        )
        result = value_summary(date(2026, 4, 1), date(2026, 5, 1))
        assert result["external_closed_count"] == 1
        assert Decimal(result["external_actual_cost"]) == Decimal("475.50")
        assert Decimal(result["external_estimated_cost"]) == Decimal("500.00")

    def test_total_value_subtracts_external_spend(self):
        _make_completed_wo(
            completed_at=_aware(date(2026, 4, 15)),
            estimated_external_cost=Decimal("300.00"),
            estimated_internal_cost=Decimal("50.00"),
        )
        _make_closed_tpwo(
            closed_at=_aware(date(2026, 4, 20)),
            actual=Decimal("100.00"),
        )
        result = value_summary(date(2026, 4, 1), date(2026, 5, 1))
        # internal_net_value = 250, external_actual = 100 -> total = 150
        assert Decimal(result["total_value_to_makerspace"]) == Decimal("150.00")

    def test_excludes_outside_window(self):
        _make_completed_wo(completed_at=_aware(date(2026, 3, 31)))
        _make_completed_wo(completed_at=_aware(date(2026, 5, 1)))
        result = value_summary(date(2026, 4, 1), date(2026, 5, 1))
        assert result["internal_completed_count"] == 0

    def test_excludes_non_completed(self):
        asset = AssetFactory()
        item = MaintenanceItem.objects.create(asset=asset, title="X", interval_days=30)
        WorkOrder.objects.create(
            maintenance_item=item,
            status=WorkOrder.Status.OPEN,
            completed_at=_aware(date(2026, 4, 15)),
            estimated_external_cost=Decimal("999.00"),
        )
        result = value_summary(date(2026, 4, 1), date(2026, 5, 1))
        assert result["internal_completed_count"] == 0
        assert Decimal(result["internal_estimated_external_cost"]) == Decimal("0.00")

    def test_null_estimated_external_cost_treated_as_zero(self):
        _make_completed_wo(
            completed_at=_aware(date(2026, 4, 15)),
            estimated_external_cost=None,
            estimated_internal_cost=Decimal("25.00"),
        )
        result = value_summary(date(2026, 4, 1), date(2026, 5, 1))
        assert Decimal(result["internal_estimated_external_cost"]) == Decimal("0.00")
        assert Decimal(result["internal_estimated_internal_cost"]) == Decimal("25.00")
        # net_value goes negative — internal cost without an external estimate.
        assert Decimal(result["internal_net_value"]) == Decimal("-25.00")


class TestWoVolume:
    def test_buckets_by_month(self):
        _make_completed_wo(completed_at=_aware(date(2026, 1, 10)))
        _make_completed_wo(completed_at=_aware(date(2026, 1, 25)))
        _make_completed_wo(completed_at=_aware(date(2026, 2, 5)))
        result = wo_volume(date(2026, 1, 1), date(2026, 4, 1), bucket="month")
        assert len(result) == 2
        assert result[0]["period"] == "2026-01-01"
        assert result[0]["count"] == 2
        assert result[1]["period"] == "2026-02-01"
        assert result[1]["count"] == 1

    def test_buckets_by_quarter(self):
        _make_completed_wo(completed_at=_aware(date(2026, 1, 10)))
        _make_completed_wo(completed_at=_aware(date(2026, 4, 10)))
        result = wo_volume(date(2026, 1, 1), date(2026, 7, 1), bucket="quarter")
        assert len(result) == 2

    def test_invalid_bucket_raises(self):
        with pytest.raises(ValueError):
            wo_volume(date(2026, 1, 1), date(2026, 2, 1), bucket="week")


class TestTopUsers:
    def test_orders_by_count_desc(self):
        alice = User.objects.create_user(username="alice", first_name="Al", last_name="Ice")
        bob = User.objects.create_user(username="bob")
        for _ in range(3):
            _make_completed_wo(completed_at=_aware(date(2026, 4, 10)), assigned_to=alice)
        _make_completed_wo(completed_at=_aware(date(2026, 4, 11)), assigned_to=bob)
        result = top_users(date(2026, 4, 1), date(2026, 5, 1))
        assert len(result) == 2
        assert result[0]["username"] == "alice"
        assert result[0]["completed_wo_count"] == 3
        assert result[0]["display_name"] == "Al Ice"
        assert result[1]["username"] == "bob"
        # Falls back to username when no first/last name set.
        assert result[1]["display_name"] == "bob"

    def test_excludes_unassigned(self):
        _make_completed_wo(completed_at=_aware(date(2026, 4, 10)), assigned_to=None)
        result = top_users(date(2026, 4, 1), date(2026, 5, 1))
        assert result == []

    def test_respects_limit(self):
        for i in range(5):
            user = User.objects.create_user(username=f"u{i}")
            _make_completed_wo(completed_at=_aware(date(2026, 4, 10)), assigned_to=user)
        result = top_users(date(2026, 4, 1), date(2026, 5, 1), limit=3)
        assert len(result) == 3


class TestUtilization:
    def test_includes_only_assets_with_completed_wos(self):
        active_asset = AssetFactory()
        AssetFactory()  # quiet asset, no WO
        _make_completed_wo(completed_at=_aware(date(2026, 4, 10)), asset=active_asset)
        result = utilization(date(2026, 4, 1), date(2026, 5, 1))
        assert len(result) == 1
        assert result[0]["asset_id"] == str(active_asset.id)
        assert result[0]["completed_wo_count"] == 1

    def test_returns_hours_used(self):
        asset = AssetFactory(hours_used=42)
        _make_completed_wo(completed_at=_aware(date(2026, 4, 10)), asset=asset)
        result = utilization(date(2026, 4, 1), date(2026, 5, 1))
        assert result[0]["hours_used"] == 42


class TestCategorySpend:
    def test_groups_by_category(self):
        cat_a = CategoryFactory(name="3D Printers")
        cat_b = CategoryFactory(name="CNC")
        asset_a = AssetFactory(category=cat_a)
        asset_b = AssetFactory(category=cat_b)
        _make_completed_wo(
            completed_at=_aware(date(2026, 4, 10)),
            asset=asset_a,
            estimated_external_cost=Decimal("200.00"),
            estimated_internal_cost=Decimal("30.00"),
        )
        _make_closed_tpwo(
            closed_at=_aware(date(2026, 4, 15)),
            asset=asset_b,
            actual=Decimal("800.00"),
        )
        result = category_spend(date(2026, 4, 1), date(2026, 5, 1))
        assert len(result) == 2
        # Largest external_actual first
        cnc = next(r for r in result if r["category_name"] == "CNC")
        printers = next(r for r in result if r["category_name"] == "3D Printers")
        assert Decimal(cnc["external_actual"]) == Decimal("800.00")
        assert Decimal(printers["external_estimated"]) == Decimal("200.00")
        assert Decimal(printers["internal_estimated"]) == Decimal("30.00")

    def test_buckets_uncategorized_assets(self):
        asset = AssetFactory(category=None)
        _make_completed_wo(
            completed_at=_aware(date(2026, 4, 10)),
            asset=asset,
            estimated_external_cost=Decimal("50.00"),
        )
        result = category_spend(date(2026, 4, 1), date(2026, 5, 1))
        assert len(result) == 1
        assert result[0]["category_id"] is None
        assert result[0]["category_name"] is None
