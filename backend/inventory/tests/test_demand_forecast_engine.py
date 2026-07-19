"""Tests for the demand-forecast engine (op-2).

Two pure stages, tested independently:

* :func:`build_daily_consumption_series` — DB-backed; combines usage + shrinkage
  into a zero-filled, leading-zero-trimmed daily series.
* :func:`forecast_item` — pure; turns a series into the reorder projection. The
  model call (:func:`_predict_future`) is the swap seam, so the reorder MATH is
  tested against a **mocked** model returning canned ``yhat`` / ``yhat_upper``
  (mirroring the "mock Prophet" approach in the bead — no real model trained),
  and the seasonal smoother is exercised separately + deterministically.
"""

from datetime import date, datetime, time, timedelta

from django.utils import timezone

import pytest

from inventory.models import StockReconciliation, UsageLog
from inventory.services import demand_forecast_engine as engine
from inventory.services.demand_forecast_engine import (
    METHOD_FALLBACK,
    METHOD_HOLTWINTERS,
    MODEL_VERSION,
    build_daily_consumption_series,
    forecast_item,
    horizon_days_for,
)
from inventory.tests.factories import InventoryItemFactory, UsageLogFactory
from reorder_queue.tests.factories import UserFactory


def _at(day: date) -> datetime:
    """Noon on ``day`` as a tz-aware datetime (TIME_ZONE=UTC -> stable TruncDate)."""
    return timezone.make_aware(datetime.combine(day, time(12, 0)))


def _log_usage(item, day: date, qty: int) -> None:
    """Create a UsageLog dated ``day`` (auto_now_add is bypassed via update())."""
    log = UsageLogFactory(item=item, quantity_used=qty)
    UsageLog.objects.filter(pk=log.pk).update(usage_date=_at(day))


def _reconcile(item, day: date, delta: int, reason: str, user) -> None:
    rec = StockReconciliation.objects.create(
        item=item,
        projected_count=100,
        actual_count=100 + delta,
        delta=delta,
        reason=reason,
        reconciled_by=user,
    )
    StockReconciliation.objects.filter(pk=rec.pk).update(reconciled_at=_at(day))


# --- build_daily_consumption_series ----------------------------------------

WINDOW_START = date(2026, 6, 1)
WINDOW_END = date(2026, 6, 30)


@pytest.mark.django_db
def test_series_usage_only_sums_and_zero_fills():
    item = InventoryItemFactory()
    _log_usage(item, date(2026, 6, 10), 3)
    _log_usage(item, date(2026, 6, 10), 2)  # same day -> summed to 5
    _log_usage(item, date(2026, 6, 12), 4)

    series = build_daily_consumption_series(item, start=WINDOW_START, end=WINDOW_END)
    by_day = dict(series)

    # Leading zeros (6/1..6/9) trimmed; series starts at first real usage.
    assert series[0] == (date(2026, 6, 10), 5.0)
    assert by_day[date(2026, 6, 11)] == 0.0  # gap zero-filled
    assert by_day[date(2026, 6, 12)] == 4.0
    # Trailing zeros are kept (recent no-demand is real signal).
    assert by_day[date(2026, 6, 30)] == 0.0
    assert len(series) == (WINDOW_END - date(2026, 6, 10)).days + 1


@pytest.mark.django_db
def test_series_includes_shrinkage_excludes_found_and_miscounted():
    item = InventoryItemFactory()
    user = UserFactory()

    _log_usage(item, date(2026, 6, 10), 2)
    _reconcile(item, date(2026, 6, 11), -3, StockReconciliation.ReasonCode.USED_WITHOUT_SCAN, user)
    _reconcile(item, date(2026, 6, 12), -1, StockReconciliation.ReasonCode.LOST, user)
    _reconcile(item, date(2026, 6, 13), -2, StockReconciliation.ReasonCode.DAMAGED, user)
    # Excluded: FOUND is a positive delta (stock in), MISCOUNTED is a correction.
    _reconcile(item, date(2026, 6, 14), 5, StockReconciliation.ReasonCode.FOUND, user)
    _reconcile(item, date(2026, 6, 15), -4, StockReconciliation.ReasonCode.MISCOUNTED, user)

    by_day = dict(build_daily_consumption_series(item, start=WINDOW_START, end=WINDOW_END))

    assert by_day[date(2026, 6, 10)] == 2.0  # usage
    assert by_day[date(2026, 6, 11)] == 3.0  # used-without-scan magnitude
    assert by_day[date(2026, 6, 12)] == 1.0  # lost
    assert by_day[date(2026, 6, 13)] == 2.0  # damaged
    assert by_day[date(2026, 6, 14)] == 0.0  # FOUND excluded
    assert by_day[date(2026, 6, 15)] == 0.0  # MISCOUNTED excluded


@pytest.mark.django_db
def test_series_usage_and_shrinkage_same_day_combine():
    item = InventoryItemFactory()
    user = UserFactory()
    _log_usage(item, date(2026, 6, 10), 2)
    _reconcile(item, date(2026, 6, 10), -3, StockReconciliation.ReasonCode.LOST, user)

    by_day = dict(build_daily_consumption_series(item, start=WINDOW_START, end=WINDOW_END))
    assert by_day[date(2026, 6, 10)] == 5.0  # 2 used + 3 lost


@pytest.mark.django_db
def test_series_empty_when_no_consumption():
    item = InventoryItemFactory()
    assert build_daily_consumption_series(item, start=WINDOW_START, end=WINDOW_END) == []


# --- horizon_days_for -------------------------------------------------------


def test_horizon_days_for():
    assert horizon_days_for(None) == 7  # no lead -> just the buffer
    assert horizon_days_for(0) == 7
    assert horizon_days_for(5) == 12
    assert horizon_days_for(7.0) == 14
    assert horizon_days_for(10.2) == 18  # ceil(10.2)=11, +7


# --- forecast_item: fallback (thin history) --------------------------------


def test_forecast_fallback_flat_series():
    series = [(date(2026, 1, 1) + timedelta(days=i), 2.0) for i in range(30)]  # <60
    result = forecast_item(None, series, 10, lead_time_days=3, available=5)

    assert result.method == METHOD_FALLBACK
    assert result.model_version == ""
    assert result.predicted_daily_demand == 2.0
    assert result.horizon_demand == 20.0
    assert result.horizon_demand_upper == 20.0  # zero variance -> no band
    assert result.safety_stock == 0
    assert result.predictive_reorder_point == 20
    assert result.needs_reorder is True  # 5 <= 20
    assert result.days_until_stockout == 2.5  # 5 / 2.0
    assert result.lead_time_days == 3


def test_forecast_fallback_variance_drives_safety_stock():
    # Alternating 0/4 -> mean 2.0, pstdev 2.0. horizon 9 -> band = z*2*sqrt(9).
    series = [(date(2026, 1, 1) + timedelta(days=i), 0.0 if i % 2 == 0 else 4.0) for i in range(30)]
    result = forecast_item(None, series, 9, available=1000)

    assert result.method == METHOD_FALLBACK
    assert result.horizon_demand == 18.0
    # upper = 18 + 1.2816*2*3 = 25.6896 -> safety ceil(7.6896) = 8
    assert result.safety_stock == 8
    assert result.predictive_reorder_point == 26
    assert result.needs_reorder is False  # 1000 > 26


# --- forecast_item: reorder math from a MOCKED model -----------------------


def test_forecast_math_from_mocked_model(mocker):
    """The reorder math computed from canned model output (no real model)."""
    series = [(date(2026, 1, 1) + timedelta(days=i), 1.0) for i in range(60)]  # >=60
    mocker.patch.object(engine, "_predict_future", return_value=([2.0] * 10, [3.0] * 10))
    now = _at(date(2026, 6, 15))

    result = forecast_item(None, series, 10, lead_time_days=3, available=25, now=now)

    assert result.method == METHOD_HOLTWINTERS
    assert result.model_version == MODEL_VERSION
    assert result.predicted_daily_demand == 2.0
    assert result.horizon_demand == 20.0  # sum(yhat)
    assert result.horizon_demand_upper == 30.0  # sum(yhat_upper)
    assert result.safety_stock == 10  # ceil(30 - 20)
    assert result.predictive_reorder_point == 30  # ceil(20 + 10)
    assert result.available_at_generation == 25
    assert result.needs_reorder is True  # 25 <= 30
    assert result.days_until_stockout == 12.5  # 25 / 2.0
    assert result.projected_stockout_date == (now + timedelta(days=12.5)).date()


def test_forecast_math_clips_negative_predictions(mocker):
    """yhat/yhat_upper are clipped at 0 before summing (demand can't be < 0)."""
    series = [(date(2026, 1, 1) + timedelta(days=i), 1.0) for i in range(60)]
    mocker.patch.object(engine, "_predict_future", return_value=([4.0, -2.0, 4.0], [5.0, 1.0, 5.0]))
    result = forecast_item(None, series, 3, available=100)

    assert result.horizon_demand == 8.0  # 4 + 0 + 4 (the -2 clipped)
    assert result.predicted_daily_demand == round(8 / 3, 4)
    assert result.horizon_demand_upper == 11.0  # 5 + 1 + 5
    assert result.safety_stock == 3  # ceil(11 - 8)


# --- _predict_future: the seasonal smoother (deterministic) ----------------


def _weekly_series(n_days: int, spike: float = 10.0, base: float = 1.0):
    return [
        (date(2026, 1, 1) + timedelta(days=i), spike if i % 7 == 0 else base) for i in range(n_days)
    ]


def test_predict_future_recovers_weekly_seasonality():
    yhat, yhat_upper = engine._predict_future(_weekly_series(63), horizon_days=14)

    assert len(yhat) == 14
    assert len(yhat_upper) == 14
    assert all(u >= y for y, u in zip(yhat, yhat_upper))  # band never below point
    # The weekly spike is reflected: at least one day well above the low days.
    assert max(yhat) > 4.0
    assert min(yhat) < 3.0


def test_forecast_item_seasonal_path_end_to_end():
    series = _weekly_series(63)  # >=60 -> Holt-Winters
    now = _at(date(2026, 6, 15))
    result = forecast_item(None, series, 14, lead_time_days=7, available=1000, now=now)

    assert result.method == METHOD_HOLTWINTERS
    assert result.model_version == MODEL_VERSION
    # Mean weekly demand ~ (10 + 6) / 7 ~= 2.29 -> point demand lands near it.
    assert 1.5 <= result.predicted_daily_demand <= 3.5
    assert result.horizon_demand > 0
    assert result.needs_reorder is False  # available 1000 is ample
    assert result.days_until_stockout is not None
