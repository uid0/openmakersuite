"""Tests for the restock-interval demand-forecast engine.

Two pure stages, tested independently:

* :func:`build_restock_events` — DB-backed; the item's purchase-order dates,
  deduplicated per day and bounded by ``end``.
* :func:`forecast_item_by_interval` — pure; averages the gaps between those
  dates into a due date and the reorder flag. No model is trained and no
  randomness is involved, so every expectation below is exact.
"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.utils import timezone

import pytest

from inventory.models import ItemSupplier
from inventory.services.demand_forecast_engine import (
    METHOD_INSUFFICIENT_HISTORY,
    METHOD_RESTOCK_INTERVAL,
    MODEL_VERSION,
    build_restock_events,
    forecast_item_by_interval,
)
from inventory.tests.factories import InventoryItemFactory, ItemSupplierFactory
from reorder_queue.models import PurchaseOrder, PurchaseOrderItem
from reorder_queue.tests.factories import UserFactory


def _at(day: date) -> datetime:
    """Noon on ``day`` as a tz-aware datetime (TIME_ZONE=UTC -> stable TruncDate)."""
    return timezone.make_aware(datetime.combine(day, time(12, 0)))


def _restock(item, day, *, user, status=PurchaseOrder.Status.SENT, item_supplier=None):
    """Record a purchase of ``item`` on ``day`` as a one-line purchase order.

    ``PurchaseOrder.order_date`` is ``auto_now_add``, so it is backdated with an
    ``update()`` after creation (same trick the usage/reconciliation helpers used).
    ``item_supplier`` defaults to the item's own supplier link; pass one to buy
    the same item from a second supplier.
    """
    if item_supplier is None:
        item_supplier = ItemSupplier.objects.filter(item=item).first()
    po = PurchaseOrder.objects.create(
        supplier=item_supplier.supplier,
        status=status,
        created_by=user,
    )
    PurchaseOrderItem.objects.create(
        purchase_order=po,
        item_supplier=item_supplier,
        quantity_ordered=1,
        unit_cost_ordered=Decimal("1.0000"),
    )
    PurchaseOrder.objects.filter(pk=po.pk).update(order_date=_at(day))
    return po


# --- build_restock_events ---------------------------------------------------

WINDOW_END = date(2026, 6, 30)


@pytest.mark.django_db
def test_events_are_sorted_ascending():
    item = InventoryItemFactory()
    user = UserFactory()
    # Created out of order -- the engine, not the caller, owns the ordering.
    _restock(item, date(2026, 6, 20), user=user)
    _restock(item, date(2026, 6, 10), user=user)
    _restock(item, date(2026, 6, 1), user=user)

    assert build_restock_events(item, end=WINDOW_END) == [
        date(2026, 6, 1),
        date(2026, 6, 10),
        date(2026, 6, 20),
    ]


@pytest.mark.django_db
def test_events_separate_orders_on_the_same_day_are_one_event():
    item = InventoryItemFactory()
    user = UserFactory()
    _restock(item, date(2026, 6, 10), user=user)
    _restock(item, date(2026, 6, 10), user=user)  # second PO, same day

    assert build_restock_events(item, end=WINDOW_END) == [date(2026, 6, 10)]


@pytest.mark.django_db
def test_events_same_day_orders_from_two_suppliers_are_one_event():
    """One shopping trip split across two suppliers is still one restock."""
    item = InventoryItemFactory()
    user = UserFactory()
    second_source = ItemSupplierFactory(item=item, is_primary=False)

    _restock(item, date(2026, 6, 10), user=user)
    _restock(item, date(2026, 6, 10), user=user, item_supplier=second_source)

    assert build_restock_events(item, end=WINDOW_END) == [date(2026, 6, 10)]


@pytest.mark.django_db
def test_events_only_include_the_requested_item():
    item = InventoryItemFactory()
    other = InventoryItemFactory()
    user = UserFactory()
    _restock(item, date(2026, 6, 10), user=user)
    _restock(other, date(2026, 6, 11), user=user)

    assert build_restock_events(item, end=WINDOW_END) == [date(2026, 6, 10)]
    assert build_restock_events(other, end=WINDOW_END) == [date(2026, 6, 11)]


@pytest.mark.django_db
def test_events_exclude_orders_after_end():
    item = InventoryItemFactory()
    user = UserFactory()
    _restock(item, date(2026, 6, 30), user=user)  # on the boundary -> kept
    _restock(item, date(2026, 7, 1), user=user)  # after end -> dropped

    assert build_restock_events(item, end=WINDOW_END) == [date(2026, 6, 30)]


@pytest.mark.django_db
def test_events_exclude_cancelled_and_voided_orders():
    item = InventoryItemFactory()
    user = UserFactory()
    _restock(item, date(2026, 6, 5), user=user, status=PurchaseOrder.Status.RECEIVED)
    _restock(item, date(2026, 6, 12), user=user, status=PurchaseOrder.Status.CANCELLED)
    _restock(item, date(2026, 6, 19), user=user, status=PurchaseOrder.Status.VOIDED)
    _restock(item, date(2026, 6, 26), user=user, status=PurchaseOrder.Status.DRAFT)

    assert build_restock_events(item, end=WINDOW_END) == [date(2026, 6, 5), date(2026, 6, 26)]


@pytest.mark.django_db
def test_events_empty_when_never_purchased():
    item = InventoryItemFactory()
    assert build_restock_events(item, end=WINDOW_END) == []


# --- forecast_item_by_interval: insufficient history ------------------------


def test_forecast_no_events_is_insufficient_history():
    result = forecast_item_by_interval(None, [], now=_at(date(2026, 6, 15)), lead_time_days=5)

    assert result.method == METHOD_INSUFFICIENT_HISTORY
    assert result.model_version == ""
    assert result.avg_interval_days is None
    assert result.interval_samples == 0
    assert result.last_restock_date is None
    assert result.predicted_next_reorder_date is None
    assert result.days_until_due is None
    assert result.needs_reorder is False
    assert result.lead_time_days == 5


def test_forecast_single_event_is_insufficient_history_but_reports_last_restock():
    """One purchase is a known last-restock date, but describes no cadence."""
    result = forecast_item_by_interval(
        None, [date(2026, 6, 1)], now=_at(date(2026, 6, 15)), lead_time_days=5
    )

    assert result.method == METHOD_INSUFFICIENT_HISTORY
    assert result.interval_samples == 0  # 1 purchase = 0 gaps
    assert result.last_restock_date == date(2026, 6, 1)
    assert result.avg_interval_days is None
    assert result.predicted_next_reorder_date is None
    assert result.days_until_due is None
    assert result.needs_reorder is False  # never guess from a single purchase


# --- forecast_item_by_interval: the cadence --------------------------------


def test_forecast_averages_even_gaps():
    events = [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)]  # 31, 28 -> 29.5
    result = forecast_item_by_interval(None, events, now=_at(date(2026, 3, 1)), lead_time_days=0)

    assert result.method == METHOD_RESTOCK_INTERVAL
    assert result.model_version == MODEL_VERSION
    assert result.avg_interval_days == 29.5
    assert result.interval_samples == 2  # 3 purchases = 2 gaps
    assert result.last_restock_date == date(2026, 3, 1)
    # 29.5 rounds to 30 days rather than truncating to 29.
    assert result.predicted_next_reorder_date == date(2026, 3, 31)
    assert result.days_until_due == 30.0
    assert result.needs_reorder is False  # 30 days out, no lead time


def test_forecast_sorts_and_dedupes_defensively():
    """Unsorted/duplicated input must not change the cadence."""
    messy = [date(2026, 3, 1), date(2026, 1, 1), date(2026, 2, 1), date(2026, 1, 1)]
    tidy = [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)]
    now = _at(date(2026, 3, 10))

    assert forecast_item_by_interval(None, messy, now=now) == forecast_item_by_interval(
        None, tidy, now=now
    )


def test_forecast_flags_when_due_inside_the_lead_time():
    # Bought every 30 days, last bought 25 days ago -> due in 5 days.
    events = [date(2026, 6, 1), date(2026, 7, 1)]
    now = _at(date(2026, 7, 26))

    inside = forecast_item_by_interval(None, events, now=now, lead_time_days=7)
    assert inside.days_until_due == 5.0
    assert inside.needs_reorder is True  # 5 <= 7: order now and it lands in time

    outside = forecast_item_by_interval(None, events, now=now, lead_time_days=3)
    assert outside.days_until_due == 5.0
    assert outside.needs_reorder is False  # 5 > 3: still time to wait


def test_forecast_flags_when_overdue():
    events = [date(2026, 1, 1), date(2026, 2, 1)]  # avg 31 -> due 2026-03-04
    result = forecast_item_by_interval(
        None, events, now=_at(date(2026, 3, 20)), lead_time_days=None
    )

    assert result.predicted_next_reorder_date == date(2026, 3, 4)
    assert result.days_until_due == -16.0
    assert result.needs_reorder is True  # overdue clears any threshold


def test_forecast_without_lead_time_flags_only_once_due():
    events = [date(2026, 6, 1), date(2026, 7, 1)]  # due 2026-07-31

    on_the_day = forecast_item_by_interval(None, events, now=_at(date(2026, 7, 31)))
    assert on_the_day.days_until_due == 0.0
    assert on_the_day.needs_reorder is True  # None lead time -> threshold 0

    day_before = forecast_item_by_interval(None, events, now=_at(date(2026, 7, 30)))
    assert day_before.days_until_due == 1.0
    assert day_before.needs_reorder is False


def test_forecast_leaves_the_retired_quantity_fields_empty():
    events = [date(2026, 6, 1), date(2026, 7, 1)]
    result = forecast_item_by_interval(None, events, now=_at(date(2026, 7, 10)))

    assert result.horizon_days == 0
    assert result.predicted_daily_demand == 0.0
    assert result.horizon_demand == 0.0
    assert result.horizon_demand_upper == 0.0
    assert result.safety_stock == 0
    assert result.predictive_reorder_point == 0
    assert result.days_until_stockout is None
    assert result.projected_stockout_date is None


@pytest.mark.django_db
def test_forecast_snapshots_current_stock_but_stock_does_not_drive_the_flag():
    item = InventoryItemFactory(current_stock=42, average_lead_time=7)
    events = [date(2026, 6, 1), date(2026, 7, 1)]

    result = forecast_item_by_interval(item, events, now=_at(date(2026, 7, 26)), lead_time_days=7)

    assert result.available_at_generation == 42  # informational snapshot
    assert result.needs_reorder is True  # due in 5 days despite ample stock


def test_forecast_stores_a_rounded_lead_time_but_compares_the_exact_one():
    events = [date(2026, 6, 1), date(2026, 7, 1)]  # due 2026-07-31
    result = forecast_item_by_interval(None, events, now=_at(date(2026, 7, 25)), lead_time_days=5.6)

    assert result.days_until_due == 6.0
    assert result.lead_time_days == 6  # stored rounded (the column is an int)...
    assert result.needs_reorder is False  # ...but compared exact: 6 <= 5.6 is False


@pytest.mark.django_db
def test_forecast_end_to_end_from_real_purchase_orders():
    """The two stages compose: PO history in, due date out."""
    item = InventoryItemFactory(current_stock=3, average_lead_time=10)
    user = UserFactory()
    for day in (date(2026, 4, 1), date(2026, 5, 1), date(2026, 6, 1)):
        _restock(item, day, user=user)

    events = build_restock_events(item, end=date(2026, 6, 25))
    result = forecast_item_by_interval(item, events, now=_at(date(2026, 6, 25)), lead_time_days=10)

    assert result.interval_samples == 2
    assert result.avg_interval_days == 30.5  # 30 and 31 day gaps
    assert result.last_restock_date == date(2026, 6, 1)
    assert result.predicted_next_reorder_date == date(2026, 7, 1)
    assert result.days_until_due == 6.0
    assert result.needs_reorder is True  # 6 <= 10-day lead time


@pytest.mark.django_db
def test_forecast_end_to_end_for_a_never_purchased_item():
    item = InventoryItemFactory(current_stock=0)
    events = build_restock_events(item, end=date(2026, 6, 25))
    result = forecast_item_by_interval(item, events, now=_at(date(2026, 6, 25)))

    assert events == []
    assert result.method == METHOD_INSUFFICIENT_HISTORY
    assert result.needs_reorder is False  # zero stock is not a cadence
    assert result.available_at_generation == 0


def test_forecast_defaults_now_to_the_current_time():
    """Omitting ``now`` uses the clock, so the due date is measured from today."""
    today = timezone.now().date()
    events = [today - timedelta(days=60), today - timedelta(days=30)]

    result = forecast_item_by_interval(None, events)

    assert result.avg_interval_days == 30.0
    assert result.days_until_due == 0.0  # due today
    assert result.needs_reorder is True
