"""Tests for the nightly ``generate_demand_forecasts`` task (op-2).

Covers: rows are written for non-serialized eligible items only, per-item
failures are isolated, and the reorder-alert digest fires for opted-in + due
items only (not flagged-not-due, not due-not-flagged) and is deduped per day.
"""

from django.contrib.auth import get_user_model

import pytest

from inventory.models import DemandForecast
from inventory.services import demand_forecast_engine as engine
from inventory.tasks import REORDER_DIGEST_KIND, generate_demand_forecasts
from inventory.tests.factories import InventoryItemFactory, UsageLogFactory
from notifications.models import Notification

pytestmark = pytest.mark.django_db

User = get_user_model()


def _use(item, qty=5):
    """Give ``item`` recent consumption so its forecast has positive demand."""
    UsageLogFactory(item=item, quantity_used=qty)  # usage_date auto-set to now


def _admin(username="admin"):
    return User.objects.create_user(username=username, password="pw", is_staff=True, is_active=True)


def test_task_creates_rows_for_non_serialized_eligible_items_only():
    eligible = InventoryItemFactory(current_stock=10)
    _use(eligible)
    serialized = InventoryItemFactory(is_serialized=True, current_stock=10)
    _use(serialized)
    retired = InventoryItemFactory(is_retired=True, current_stock=10)
    _use(retired)
    inactive = InventoryItemFactory(is_active=False, current_stock=10)
    _use(inactive)

    generate_demand_forecasts()

    assert DemandForecast.objects.filter(item=eligible).count() == 1
    assert not DemandForecast.objects.filter(item=serialized).exists()
    assert not DemandForecast.objects.filter(item=retired).exists()
    assert not DemandForecast.objects.filter(item=inactive).exists()


def test_task_digest_fires_for_flagged_and_due_only():
    admin = _admin()
    non_admin = User.objects.create_user(username="member", password="pw")

    due_flagged = InventoryItemFactory(name="Alpha", current_stock=0, reorder_alerts_enabled=True)
    _use(due_flagged)
    flagged_not_due = InventoryItemFactory(
        name="Bravo", current_stock=100000, reorder_alerts_enabled=True
    )
    _use(flagged_not_due)
    due_not_flagged = InventoryItemFactory(
        name="Charlie", current_stock=0, reorder_alerts_enabled=False
    )
    _use(due_not_flagged)

    generate_demand_forecasts()

    # needs_reorder was computed as expected for each item.
    assert DemandForecast.objects.get(item=due_flagged).needs_reorder is True
    assert DemandForecast.objects.get(item=flagged_not_due).needs_reorder is False
    assert DemandForecast.objects.get(item=due_not_flagged).needs_reorder is True

    # Exactly one digest, to the admin, listing only the flagged+due item.
    digests = Notification.objects.filter(metadata__kind=REORDER_DIGEST_KIND)
    assert digests.filter(user=admin).count() == 1
    assert not Notification.objects.filter(user=non_admin).exists()

    digest = digests.get(user=admin)
    assert digest.type == "warning"
    assert digest.metadata["item_count"] == 1
    assert digest.metadata["item_ids"] == [str(due_flagged.id)]
    assert "Alpha" in digest.message
    assert "Bravo" not in digest.message
    assert "Charlie" not in digest.message


def test_task_digest_not_emitted_when_nothing_due():
    admin = _admin()
    ok_item = InventoryItemFactory(current_stock=100000, reorder_alerts_enabled=True)
    _use(ok_item)

    generate_demand_forecasts()

    assert not Notification.objects.filter(user=admin, metadata__kind=REORDER_DIGEST_KIND).exists()


def test_task_digest_deduped_within_same_day():
    admin = _admin()
    item = InventoryItemFactory(current_stock=0, reorder_alerts_enabled=True)
    _use(item)

    generate_demand_forecasts()
    generate_demand_forecasts()  # same day -> no second digest

    assert Notification.objects.filter(user=admin, metadata__kind=REORDER_DIGEST_KIND).count() == 1
    # ...but forecast history still accrues one row per run.
    assert DemandForecast.objects.filter(item=item).count() == 2


def test_task_isolates_per_item_failure(mocker):
    good = InventoryItemFactory(current_stock=5)
    _use(good)
    bad = InventoryItemFactory(current_stock=5)
    _use(bad)

    real_builder = engine.build_daily_consumption_series

    def flaky(item, **kwargs):
        if item.id == bad.id:
            raise ValueError("boom")
        return real_builder(item, **kwargs)

    # The task does `from .services.demand_forecast_engine import
    # build_daily_consumption_series` at call time, so patching the engine
    # attribute reaches it.
    mocker.patch.object(engine, "build_daily_consumption_series", side_effect=flaky)

    result = generate_demand_forecasts()

    assert DemandForecast.objects.filter(item=good).exists()
    assert not DemandForecast.objects.filter(item=bad).exists()
    assert "1 created" in result
    assert "1 failed" in result
