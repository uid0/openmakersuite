"""Tests for the AssetViewSet `maintenance-history` action."""

from datetime import date, datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.crypto import get_random_string

import pytest
from rest_framework.test import APIClient

from inventory.models import MaintenanceRecord
from inventory.tests.factories import AssetFactory
from maintenance_orders.models import ThirdPartyWorkOrder, ThirdPartyWorkOrderAsset
from vendors.models import Vendor

User = get_user_model()
pytestmark = pytest.mark.django_db


def _user(username, **flags):
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password=get_random_string(24),
        **flags,
    )


def _client(user=None):
    c = APIClient()
    if user is not None:
        c.force_authenticate(user=user)
    return c


def _history_url(asset_id, **params):
    base = f"/api/inventory/assets/{asset_id}/maintenance-history/"
    if not params:
        return base
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}"


@pytest.fixture
def asset():
    return AssetFactory()


@pytest.fixture
def vendor():
    return Vendor.objects.create(name="ACME HVAC", vendor_kind=Vendor.KIND_HVAC)


@pytest.fixture
def viewer():
    return _user("viewer")


class TestMaintenanceHistory:
    def test_anonymous_blocked(self, asset):
        resp = _client().get(_history_url(asset.id))
        assert resp.status_code in (401, 403)

    def test_empty_history(self, asset, viewer):
        resp = _client(viewer).get(_history_url(asset.id))
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 0
        assert body["results"] == []
        assert body["total_cost"] == "0"

    def test_returns_historical_records(self, asset, vendor, viewer):
        MaintenanceRecord.objects.create(
            asset=asset,
            title="Pump rebuild",
            description="...",
            completed_on=date(2024, 3, 14),
            vendor=vendor,
            cost=Decimal("450.00"),
        )
        resp = _client(viewer).get(_history_url(asset.id))
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        row = body["results"][0]
        assert row["source"] == "historical"
        assert row["title"] == "Pump rebuild"
        assert row["completed_on"] == "2024-03-14"
        assert row["performed_by"]["vendor"]["name"] == "ACME HVAC"
        assert row["cost"] == "450.00"
        assert row["detail_url"] is None

    def test_returns_closed_tpwo(self, asset, vendor, viewer):
        closed = timezone.make_aware(datetime(2023, 1, 5, 17, 0))
        wo = ThirdPartyWorkOrder.objects.create(
            title="Compressor swap",
            vendor=vendor,
            asset=asset,
            status=ThirdPartyWorkOrder.STATUS_CLOSED,
            actual_invoice_total=Decimal("2300.00"),
            closed_at=closed,
        )
        assert wo.opened_at is not None

        resp = _client(viewer).get(_history_url(asset.id))
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        row = body["results"][0]
        assert row["source"] == "workorder"
        assert row["title"] == "Compressor swap"
        assert row["completed_on"] == "2023-01-05"
        assert row["cost"] == "2300.00"
        assert row["detail_url"] == f"/work-orders/{wo.id}"

    def test_unified_ordering(self, asset, vendor, viewer):
        # Closed TPWO on 2023-01-05
        ThirdPartyWorkOrder.objects.create(
            title="TPWO",
            vendor=vendor,
            asset=asset,
            status=ThirdPartyWorkOrder.STATUS_CLOSED,
            actual_invoice_total=Decimal("1000.00"),
            closed_at=timezone.make_aware(datetime(2023, 1, 5, 17, 0)),
        )
        # Historical record on 2024-06-01 (newer)
        MaintenanceRecord.objects.create(
            asset=asset,
            title="HVAC tune",
            description="",
            completed_on=date(2024, 6, 1),
            vendor=vendor,
            cost=Decimal("500.00"),
        )
        resp = _client(viewer).get(_history_url(asset.id))
        assert resp.status_code == 200
        body = resp.json()
        titles = [r["title"] for r in body["results"]]
        assert titles == ["HVAC tune", "TPWO"]

    def test_date_range_filter_inclusive(self, asset, vendor, viewer):
        MaintenanceRecord.objects.create(
            asset=asset,
            title="In range",
            description="",
            completed_on=date(2023, 6, 15),
            vendor=vendor,
        )
        MaintenanceRecord.objects.create(
            asset=asset,
            title="Below range",
            description="",
            completed_on=date(2022, 12, 31),
            vendor=vendor,
        )
        MaintenanceRecord.objects.create(
            asset=asset,
            title="Above range",
            description="",
            completed_on=date(2024, 1, 1),
            vendor=vendor,
        )
        resp = _client(viewer).get(_history_url(asset.id, since="2023-01-01", until="2023-12-31"))
        body = resp.json()
        titles = {r["title"] for r in body["results"]}
        assert titles == {"In range"}

    def test_source_filter_historical(self, asset, vendor, viewer):
        MaintenanceRecord.objects.create(
            asset=asset,
            title="Historic",
            description="",
            completed_on=date(2024, 3, 14),
            vendor=vendor,
        )
        ThirdPartyWorkOrder.objects.create(
            title="TPWO",
            vendor=vendor,
            asset=asset,
            status=ThirdPartyWorkOrder.STATUS_CLOSED,
            actual_invoice_total=Decimal("100.00"),
            closed_at=timezone.make_aware(datetime(2024, 3, 14, 12, 0)),
        )
        resp = _client(viewer).get(_history_url(asset.id, source="historical"))
        body = resp.json()
        sources = {r["source"] for r in body["results"]}
        assert sources == {"historical"}

    def test_source_filter_workorder(self, asset, vendor, viewer):
        MaintenanceRecord.objects.create(
            asset=asset,
            title="Historic",
            description="",
            completed_on=date(2024, 3, 14),
            vendor=vendor,
        )
        ThirdPartyWorkOrder.objects.create(
            title="TPWO",
            vendor=vendor,
            asset=asset,
            status=ThirdPartyWorkOrder.STATUS_CLOSED,
            actual_invoice_total=Decimal("100.00"),
            closed_at=timezone.make_aware(datetime(2024, 3, 14, 12, 0)),
        )
        resp = _client(viewer).get(_history_url(asset.id, source="workorder"))
        body = resp.json()
        sources = {r["source"] for r in body["results"]}
        assert sources == {"workorder"}

    def test_invalid_date_returns_400(self, asset, viewer):
        resp = _client(viewer).get(_history_url(asset.id, since="not-a-date"))
        assert resp.status_code == 400

    def test_invalid_source_returns_400(self, asset, viewer):
        resp = _client(viewer).get(_history_url(asset.id, source="bogus"))
        assert resp.status_code == 400

    def test_multi_asset_tpwo_surfaces_on_linked_assets(self, asset, vendor, viewer):
        other_asset = AssetFactory()
        wo = ThirdPartyWorkOrder.objects.create(
            title="Multi-asset job",
            vendor=vendor,
            asset=other_asset,  # primary
            status=ThirdPartyWorkOrder.STATUS_CLOSED,
            actual_invoice_total=Decimal("200.00"),
            closed_at=timezone.make_aware(datetime(2024, 3, 14, 12, 0)),
        )
        ThirdPartyWorkOrderAsset.objects.create(
            work_order=wo,
            asset=asset,
            share_pct=Decimal("50.00"),
        )
        ThirdPartyWorkOrderAsset.objects.create(
            work_order=wo,
            asset=other_asset,
            share_pct=Decimal("50.00"),
        )
        resp = _client(viewer).get(_history_url(asset.id))
        assert resp.status_code == 200
        body = resp.json()
        titles = [r["title"] for r in body["results"]]
        assert "Multi-asset job" in titles

    def test_total_cost_rollup(self, asset, vendor, viewer):
        MaintenanceRecord.objects.create(
            asset=asset,
            title="A",
            description="",
            completed_on=date(2024, 3, 14),
            vendor=vendor,
            cost=Decimal("100.50"),
        )
        MaintenanceRecord.objects.create(
            asset=asset,
            title="B",
            description="",
            completed_on=date(2024, 4, 14),
            vendor=vendor,
            cost=Decimal("200.50"),
        )
        MaintenanceRecord.objects.create(
            asset=asset,
            title="No cost",
            description="",
            completed_on=date(2024, 5, 14),
            vendor=vendor,
            cost=None,
        )
        resp = _client(viewer).get(_history_url(asset.id))
        body = resp.json()
        assert body["count"] == 3
        assert Decimal(body["total_cost"]) == Decimal("301.00")

    def test_only_closed_tpwo_shows_up(self, asset, vendor, viewer):
        # in-progress should NOT appear
        ThirdPartyWorkOrder.objects.create(
            title="Open WO",
            vendor=vendor,
            asset=asset,
            status=ThirdPartyWorkOrder.STATUS_IN_PROGRESS,
            actual_invoice_total=Decimal("999.00"),
        )
        # closed should appear
        ThirdPartyWorkOrder.objects.create(
            title="Closed WO",
            vendor=vendor,
            asset=asset,
            status=ThirdPartyWorkOrder.STATUS_CLOSED,
            actual_invoice_total=Decimal("123.00"),
            closed_at=timezone.make_aware(datetime(2024, 3, 14, 12, 0)),
        )
        resp = _client(viewer).get(_history_url(asset.id))
        body = resp.json()
        titles = [r["title"] for r in body["results"]]
        assert titles == ["Closed WO"]
