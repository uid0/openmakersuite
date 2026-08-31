"""Tests for the nightly ``generate_demand_forecasts`` task.

Covers: interval rows are written for non-serialized eligible items only,
per-item failures are isolated, and the reorder-alert digest fires for opted-in
items that are due within their lead time (not not-due, not un-opted-in) and is
deduped per day.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone

import pytest

from inventory.models import DemandForecast, ItemSupplier
from inventory.services import demand_forecast_engine as engine
from inventory.tasks import REORDER_DIGEST_KIND, generate_demand_forecasts
from inventory.tests.factories import InventoryItemFactory
from notifications.models import Notification
from reorder_queue.models import PurchaseOrder, PurchaseOrderItem

pytestmark = pytest.mark.django_db

User = get_user_model()

# Every item below pins its lead time so ``needs_reorder`` is deterministic --
# InventoryItemFactory otherwise rolls a random 1..30 day average_lead_time.
LEAD_DAYS = 7


def _buyer():
    """A user to own the purchase orders (created once per call, cheap enough)."""
    return User.objects.create_user(username=f"buyer-{User.objects.count()}", password="pw")


def _bought_on(item, days_ago, *, user):
    """Record a purchase of ``item`` ``days_ago`` days back."""
    item_supplier = ItemSupplier.objects.filter(item=item).first()
    po = PurchaseOrder.objects.create(
        supplier=item_supplier.supplier,
        status=PurchaseOrder.Status.SENT,
        created_by=user,
    )
    PurchaseOrderItem.objects.create(
        purchase_order=po,
        item_supplier=item_supplier,
        quantity_ordered=1,
        unit_cost_ordered=Decimal("1.0000"),
    )
    PurchaseOrder.objects.filter(pk=po.pk).update(
        order_date=timezone.now() - timedelta(days=days_ago)
    )


def _due_item(user, **kwargs):
    """An item bought every 30 days, last bought 30 days ago -> due today."""
    item = InventoryItemFactory(average_lead_time=LEAD_DAYS, **kwargs)
    _bought_on(item, 60, user=user)
    _bought_on(item, 30, user=user)
    return item


def _not_due_item(user, **kwargs):
    """An item bought every 200 days, last bought today -> due far out."""
    item = InventoryItemFactory(average_lead_time=LEAD_DAYS, **kwargs)
    _bought_on(item, 200, user=user)
    _bought_on(item, 0, user=user)
    return item


def _admin(username="admin"):
    return User.objects.create_user(username=username, password="pw", is_staff=True, is_active=True)


def test_task_creates_rows_for_non_serialized_eligible_items_only():
    user = _buyer()
    eligible = _due_item(user)
    serialized = _due_item(user, is_serialized=True)
    retired = _due_item(user, is_retired=True)
    inactive = _due_item(user, is_active=False)

    generate_demand_forecasts()

    assert DemandForecast.objects.filter(item=eligible).count() == 1
    assert not DemandForecast.objects.filter(item=serialized).exists()
    assert not DemandForecast.objects.filter(item=retired).exists()
    assert not DemandForecast.objects.filter(item=inactive).exists()


def test_task_persists_the_interval_fields():
    user = _buyer()
    item = _due_item(user)
    today = timezone.now().date()

    generate_demand_forecasts()

    row = DemandForecast.objects.get(item=item)
    assert row.method == DemandForecast.Method.RESTOCK_INTERVAL
    assert row.model_version == "interval-1"
    assert row.avg_interval_days == 30.0
    assert row.interval_samples == 1
    assert row.last_restock_date == today - timedelta(days=30)
    assert row.predicted_next_reorder_date == today
    assert row.days_until_due == 0.0
    assert row.lead_time_days == LEAD_DAYS
    assert row.needs_reorder is True
    # The retired v1 quantity projection is stored empty.
    assert row.horizon_days == 0
    assert row.predicted_daily_demand == 0.0
    assert row.predictive_reorder_point == 0
    assert row.days_until_stockout is None
    assert row.projected_stockout_date is None


def test_task_records_insufficient_history_for_never_purchased_items():
    item = InventoryItemFactory(average_lead_time=LEAD_DAYS, current_stock=0)

    generate_demand_forecasts()

    row = DemandForecast.objects.get(item=item)
    assert row.method == DemandForecast.Method.INSUFFICIENT_HISTORY
    assert row.avg_interval_days is None
    assert row.interval_samples == 0
    assert row.predicted_next_reorder_date is None
    assert row.needs_reorder is False


def test_task_digest_fires_for_flagged_and_due_only():
    admin = _admin()
    non_admin = User.objects.create_user(username="member", password="pw")
    user = _buyer()

    due_flagged = _due_item(user, name="Alpha", reorder_alerts_enabled=True)
    flagged_not_due = _not_due_item(user, name="Bravo", reorder_alerts_enabled=True)
    due_not_flagged = _due_item(user, name="Charlie", reorder_alerts_enabled=False)

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


def test_task_digest_describes_when_each_item_is_due():
    admin = _admin()
    user = _buyer()
    _due_item(user, name="Alpha", reorder_alerts_enabled=True)

    generate_demand_forecasts()

    digest = Notification.objects.get(user=admin, metadata__kind=REORDER_DIGEST_KIND)
    assert "Alpha (due today)" in digest.message


def test_task_digest_not_emitted_when_nothing_due():
    admin = _admin()
    user = _buyer()
    _not_due_item(user, reorder_alerts_enabled=True)

    generate_demand_forecasts()

    assert not Notification.objects.filter(user=admin, metadata__kind=REORDER_DIGEST_KIND).exists()


def test_task_digest_not_emitted_for_items_without_purchase_history():
    """An opted-in item nobody has ever bought must not raise an alert."""
    admin = _admin()
    InventoryItemFactory(average_lead_time=LEAD_DAYS, current_stock=0, reorder_alerts_enabled=True)

    generate_demand_forecasts()

    assert not Notification.objects.filter(user=admin, metadata__kind=REORDER_DIGEST_KIND).exists()


def test_task_digest_deduped_within_same_day():
    admin = _admin()
    user = _buyer()
    item = _due_item(user, reorder_alerts_enabled=True)

    generate_demand_forecasts()
    generate_demand_forecasts()  # same day -> no second digest

    assert Notification.objects.filter(user=admin, metadata__kind=REORDER_DIGEST_KIND).count() == 1
    # ...but forecast history still accrues one row per run.
    assert DemandForecast.objects.filter(item=item).count() == 2


def test_task_isolates_per_item_failure(mocker):
    user = _buyer()
    good = _due_item(user)
    bad = _due_item(user)

    real_builder = engine.build_restock_events

    def flaky(item, **kwargs):
        if item.id == bad.id:
            raise ValueError("boom")
        return real_builder(item, **kwargs)

    # The task does `from .services.demand_forecast_engine import
    # build_restock_events` at call time, so patching the engine attribute
    # reaches it.
    mocker.patch.object(engine, "build_restock_events", side_effect=flaky)

    result = generate_demand_forecasts()

    assert DemandForecast.objects.filter(item=good).exists()
    assert not DemandForecast.objects.filter(item=bad).exists()
    assert "1 created" in result
    assert "1 failed" in result
