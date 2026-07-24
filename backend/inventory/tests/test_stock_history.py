"""Tests for the weekly stock-snapshot task + the ``stock_history`` endpoint (op-izy5).

Covers:
* ``snapshot_stock_levels`` writes one ``StockLevelSnapshot`` per tracked item,
  keyed on the week-start date, and a re-run within the same week is idempotent
  (it refreshes the existing row's count rather than appending a duplicate).
* Retired / inactive items are skipped.
* ``GET /api/inventory/items/<id>/stock_history/`` returns the weekly series,
  both event overlays (reorder requests + cycle counts), the reorder-point /
  desired thresholds, and current stock.
* The endpoint is authentication-gated.
"""

from datetime import timedelta

from django.utils import timezone

import pytest
from rest_framework import status
from rest_framework.reverse import reverse

from inventory.models import StockLevelSnapshot, StockReconciliation
from inventory.tasks import snapshot_stock_levels
from inventory.tests.factories import InventoryItemFactory
from reorder_queue.models import ReorderRequest

pytestmark = pytest.mark.django_db


def _week_start(today=None):
    """Monday of ``today``'s week — the key the task snapshots against."""
    today = today or timezone.now().date()
    return today - timedelta(days=today.weekday())


class TestSnapshotStockLevelsTask:
    def test_creates_one_snapshot_per_tracked_item(self):
        item_a = InventoryItemFactory(current_stock=12, is_active=True)
        item_b = InventoryItemFactory(current_stock=3, is_active=True)
        # Excluded from the snapshot set:
        InventoryItemFactory(current_stock=99, is_active=False)
        InventoryItemFactory(current_stock=99, is_active=True, is_retired=True)

        snapshot_stock_levels()

        week = _week_start()
        assert StockLevelSnapshot.objects.count() == 2

        snap_a = StockLevelSnapshot.objects.get(item=item_a)
        assert snap_a.count == 12
        assert snap_a.snapshot_date == week
        assert StockLevelSnapshot.objects.get(item=item_b).count == 3

    def test_idempotent_and_refreshes_count_within_the_week(self):
        item = InventoryItemFactory(current_stock=10)

        snapshot_stock_levels()

        # Stock moves, task runs again the same week: the (item, week) row is
        # updated in place, not duplicated.
        item.current_stock = 42
        item.save(update_fields=["current_stock"])
        snapshot_stock_levels()

        snaps = StockLevelSnapshot.objects.filter(item=item)
        assert snaps.count() == 1
        snap = snaps.get()
        assert snap.count == 42
        assert snap.snapshot_date == _week_start()

    def test_skips_retired_and_inactive_items(self):
        InventoryItemFactory(current_stock=5, is_active=False)
        InventoryItemFactory(current_stock=5, is_active=True, is_retired=True)

        snapshot_stock_levels()

        assert StockLevelSnapshot.objects.count() == 0


class TestStockHistoryEndpoint:
    def _url(self, item):
        return reverse("inventoryitem-stock-history", kwargs={"pk": str(item.id)})

    def test_requires_authentication(self, api_client):
        item = InventoryItemFactory()
        response = api_client.get(self._url(item))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_returns_series_events_and_thresholds(self, authenticated_client):
        client, user = authenticated_client
        item = InventoryItemFactory(
            current_stock=7,
            minimum_stock=5,
            reorder_quantity=20,
        )
        week = _week_start()
        # Two weekly snapshots, created out of order to prove the response is
        # chronological regardless of insertion order.
        StockLevelSnapshot.objects.create(item=item, snapshot_date=week, count=7)
        StockLevelSnapshot.objects.create(
            item=item, snapshot_date=week - timedelta(days=7), count=9
        )
        # One reorder request (event marker) and one cycle count (carries count).
        ReorderRequest.objects.create(item=item, quantity=20)
        StockReconciliation.objects.create(
            item=item,
            projected_count=8,
            actual_count=7,
            delta=-1,
            reason=StockReconciliation.ReasonCode.MISCOUNTED,
            reconciled_by=user,
        )

        response = client.get(self._url(item))

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["series"] == [
            {"date": (week - timedelta(days=7)).isoformat(), "count": 9},
            {"date": week.isoformat(), "count": 7},
        ]
        assert len(data["reorder_events"]) == 1
        assert set(data["reorder_events"][0]) == {"date"}
        assert len(data["cycle_counts"]) == 1
        assert set(data["cycle_counts"][0]) == {"date", "count"}
        assert data["cycle_counts"][0]["count"] == 8
        assert data["thresholds"] == {"reorder_point": 5, "desired": 25}
        assert data["current_stock"] == 7

    def test_returns_empty_overlays_and_thresholds_for_a_bare_item(self, authenticated_client):
        client, _user = authenticated_client
        item = InventoryItemFactory(current_stock=4, minimum_stock=2, reorder_quantity=6)

        response = client.get(self._url(item))

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["series"] == []
        assert data["reorder_events"] == []
        assert data["cycle_counts"] == []
        assert data["thresholds"] == {"reorder_point": 2, "desired": 8}
        assert data["current_stock"] == 4
