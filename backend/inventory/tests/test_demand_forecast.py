"""Tests for the demand-forecast storage + read side.

Covers the storage model (``DemandForecast``), the ``reorder_alerts_enabled``
opt-in field (read+write on the item API), and the read-only ``demand_forecast``
/ ``reorder_alerts`` report actions. Those actions read STORED rows and return
``[]`` until the forecasting task populates the table -- the empty state is part
of the contract and is asserted here. Urgency ordering is by ``days_until_due``
(the restock-interval signal); see ``test_demand_forecast_engine`` for how those
values are produced.
"""

from datetime import timedelta

from django.utils import timezone

import pytest
from rest_framework import status

from inventory.services.demand_forecast import (
    latest_demand_forecasts,
    reorder_alert_forecasts,
)
from inventory.tests.factories import DemandForecastFactory, InventoryItemFactory

DEMAND_FORECAST_URL = "/api/inventory/reports/inventory/demand_forecast/"
REORDER_ALERTS_URL = "/api/inventory/reports/inventory/reorder_alerts/"

# Full set of keys the DemandForecastSerializer must expose.
EXPECTED_ROW_KEYS = {
    "id",
    "item",
    "item_name",
    "sku",
    "category_name",
    # Count-level presentation (op-ev14) -- display only; the forecast numbers
    # below stay in base units.
    "count_mode",
    "count_unit",
    "on_hand_display",
    "generated_at",
    # Restock-interval signal.
    "avg_interval_days",
    "interval_samples",
    "last_restock_date",
    "predicted_next_reorder_date",
    "days_until_due",
    # Retired v1 quantity projection (still emitted, 0/null on current rows).
    "horizon_days",
    "predicted_daily_demand",
    "horizon_demand",
    "horizon_demand_upper",
    "available_at_generation",
    "days_until_stockout",
    "projected_stockout_date",
    "predictive_reorder_point",
    "needs_reorder",
    "lead_time_days",
    "safety_stock",
    "method",
    "model_version",
}


@pytest.mark.integration
@pytest.mark.django_db
class TestDemandForecastModel:
    """The storage model + latest-per-item selection."""

    def test_latest_returns_newest_row_for_item(self):
        item = InventoryItemFactory()
        now = timezone.now()
        DemandForecastFactory(
            item=item, generated_at=now - timedelta(days=3), predictive_reorder_point=10
        )
        newest = DemandForecastFactory(item=item, generated_at=now, predictive_reorder_point=99)

        # History is retained (one row per run) and get_latest_by picks the newest.
        assert item.demand_forecasts.count() == 2
        assert item.demand_forecasts.latest() == newest

    def test_service_resolves_latest_per_item(self):
        item_a = InventoryItemFactory()
        item_b = InventoryItemFactory()
        now = timezone.now()
        DemandForecastFactory(item=item_a, generated_at=now - timedelta(days=1))
        newest_a = DemandForecastFactory(item=item_a, generated_at=now)
        newest_b = DemandForecastFactory(item=item_b, generated_at=now)

        result = latest_demand_forecasts()

        # Exactly one row per item -- the newest of each.
        assert {f.pk for f in result} == {newest_a.pk, newest_b.pk}


@pytest.mark.integration
@pytest.mark.django_db
class TestReorderAlertsEnabledField:
    """The opt-in flag is read+write on the item API."""

    def test_defaults_off_and_patch_round_trips(self, authenticated_client):
        client, _ = authenticated_client
        item = InventoryItemFactory(reorder_alerts_enabled=False)
        url = f"/api/inventory/items/{item.id}/"

        get_resp = client.get(url)
        assert get_resp.status_code == status.HTTP_200_OK
        assert get_resp.data["reorder_alerts_enabled"] is False

        patch_resp = client.patch(url, {"reorder_alerts_enabled": True}, format="json")
        assert patch_resp.status_code == status.HTTP_200_OK
        assert patch_resp.data["reorder_alerts_enabled"] is True

        item.refresh_from_db()
        assert item.reorder_alerts_enabled is True


@pytest.mark.integration
@pytest.mark.django_db
class TestDemandForecastEndpoint:
    """GET /reports/inventory/demand_forecast/."""

    def test_requires_auth(self, api_client):
        assert api_client.get(DEMAND_FORECAST_URL).status_code == status.HTTP_401_UNAUTHORIZED

    def test_empty_when_no_rows(self, authenticated_client):
        client, _ = authenticated_client
        resp = client.get(DEMAND_FORECAST_URL)
        assert resp.status_code == status.HTTP_200_OK
        assert list(resp.data) == []

    def test_populated_shape_and_urgency_ordering(self, authenticated_client):
        client, _ = authenticated_client
        # Distinct items (SubFactory) so all four are eligible + latest.
        flagged_soon = DemandForecastFactory(needs_reorder=True, days_until_due=2.0)
        flagged_later = DemandForecastFactory(needs_reorder=True, days_until_due=9.0)
        flagged_null = DemandForecastFactory(needs_reorder=True, days_until_due=None)
        not_flagged = DemandForecastFactory(needs_reorder=False, days_until_due=1.0)

        resp = client.get(DEMAND_FORECAST_URL)
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data) == 4

        # Most-urgent first: reorder-flagged desc, then days_until_due asc
        # (nulls last). not_flagged sorts last despite being due soonest.
        order = [str(r["item"]) for r in resp.data]
        assert order == [
            str(flagged_soon.item_id),
            str(flagged_later.item_id),
            str(flagged_null.item_id),
            str(not_flagged.item_id),
        ]

        # Row shape mirrors the model + item_name/sku/category_name.
        top = resp.data[0]
        assert set(top.keys()) == EXPECTED_ROW_KEYS
        assert top["item_name"] == flagged_soon.item.name
        assert top["sku"] == flagged_soon.item.sku
        # Uncategorised items still emit category_name as null (not dropped).
        assert top["category_name"] is None

    def test_low_stock_only_filters_to_needs_reorder(self, authenticated_client):
        client, _ = authenticated_client
        flagged = DemandForecastFactory(needs_reorder=True, days_until_due=2.0)
        DemandForecastFactory(needs_reorder=False, days_until_due=1.0)

        resp = client.get(DEMAND_FORECAST_URL, {"low_stock_only": "1"})
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data) == 1
        assert str(resp.data[0]["item"]) == str(flagged.item_id)
        assert resp.data[0]["needs_reorder"] is True

    def test_excludes_serialized_retired_and_inactive_items(self, authenticated_client):
        client, _ = authenticated_client
        eligible = DemandForecastFactory()
        DemandForecastFactory(item=InventoryItemFactory(is_serialized=True))
        DemandForecastFactory(item=InventoryItemFactory(is_retired=True))
        DemandForecastFactory(item=InventoryItemFactory(is_active=False))

        resp = client.get(DEMAND_FORECAST_URL)
        assert {str(r["item"]) for r in resp.data} == {str(eligible.item_id)}

    def test_reflects_only_the_latest_row(self, authenticated_client):
        client, _ = authenticated_client
        item = InventoryItemFactory()
        now = timezone.now()
        DemandForecastFactory(
            item=item,
            generated_at=now - timedelta(days=1),
            needs_reorder=True,
            predictive_reorder_point=5,
        )
        DemandForecastFactory(
            item=item,
            generated_at=now,
            needs_reorder=False,
            predictive_reorder_point=99,
        )

        resp = client.get(DEMAND_FORECAST_URL)
        assert len(resp.data) == 1
        assert resp.data[0]["needs_reorder"] is False
        assert resp.data[0]["predictive_reorder_point"] == 99


@pytest.mark.integration
@pytest.mark.django_db
class TestReorderAlertsEndpoint:
    """GET /reports/inventory/reorder_alerts/ -- the notify set."""

    def test_requires_auth(self, api_client):
        assert api_client.get(REORDER_ALERTS_URL).status_code == status.HTTP_401_UNAUTHORIZED

    def test_empty_when_no_rows(self, authenticated_client):
        client, _ = authenticated_client
        resp = client.get(REORDER_ALERTS_URL)
        assert resp.status_code == status.HTTP_200_OK
        assert list(resp.data) == []

    def test_returns_only_flagged_and_due(self, authenticated_client):
        client, _ = authenticated_client
        # flagged + due -> included
        flagged_due = DemandForecastFactory(
            item=InventoryItemFactory(reorder_alerts_enabled=True),
            needs_reorder=True,
            days_until_due=3.0,
        )
        # flagged but NOT due -> excluded
        DemandForecastFactory(
            item=InventoryItemFactory(reorder_alerts_enabled=True),
            needs_reorder=False,
        )
        # due but NOT flagged -> excluded
        DemandForecastFactory(
            item=InventoryItemFactory(reorder_alerts_enabled=False),
            needs_reorder=True,
        )

        resp = client.get(REORDER_ALERTS_URL)
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data) == 1
        assert str(resp.data[0]["item"]) == str(flagged_due.item_id)

    def test_service_returns_only_flagged_and_due(self):
        flagged_due = DemandForecastFactory(
            item=InventoryItemFactory(reorder_alerts_enabled=True),
            needs_reorder=True,
        )
        DemandForecastFactory(
            item=InventoryItemFactory(reorder_alerts_enabled=True),
            needs_reorder=False,
        )
        DemandForecastFactory(
            item=InventoryItemFactory(reorder_alerts_enabled=False),
            needs_reorder=True,
        )

        result = reorder_alert_forecasts()
        assert {f.pk for f in result} == {flagged_due.pk}
