"""Demand-forecast *engine* for non-serialized inventory items (op-2).

This is the write side of the ML demand forecast. op-1 shipped the storage
model (:class:`inventory.models.DemandForecast`) and the read-only API; this
module turns raw consumption history into the numbers those rows hold, and the
nightly ``inventory.tasks.generate_demand_forecasts`` task persists one row per
item per run.

Two pure, unit-testable stages:

1. :func:`build_daily_consumption_series` — reconstructs a regular daily
   consumption series for one item from :class:`~inventory.models.UsageLog`
   **plus** shrinkage recorded in
   :class:`~inventory.models.StockReconciliation` (usage alone undercounts).
2. :func:`forecast_item` — projects that series over the reorder horizon and
   derives the predictive reorder point / safety stock / stockout date.

The projection is chosen by history length:

* **< ``MIN_HISTORY_DAYS``** — a trailing run-rate ("fallback"), mirroring
  :func:`inventory.services.component_forecast.build_component_forecast`'s
  ``avg_daily_use``.
* **otherwise** — an additive Holt-Winters seasonal smoother with a weekly
  period (:func:`_predict_future`), which is the single *swap seam* for the
  forecasting model.

Model choice — why not Prophet here
-----------------------------------
op-2 was specced as a Prophet engine, with statsmodels Holt-Winters named as
the sanctioned swap "if prophet fights Docker" (the interface is swappable by
design). Both fight *this* image: the backend runs on ``python:3.14-slim``,
where the only pandas with a cp314 wheel is the 3.0 line — and **both**
``prophet==1.1.7`` and ``statsmodels==0.14.5`` break on pandas 3.0 (prophet:
``crosstab`` reindex; statsmodels: ``deprecate_kwarg`` import). Pinning
``pandas<3`` fixes both but has no cp314 wheel (source-build on 3.14), and
prophet additionally needs a ``cmdstanpy`` pin and adds ~312 MB. So the shipped
advanced tier is an additive Holt-Winters implemented on the **standard
library** (``method="holtwinters"`` — the bead's sanctioned label): no pandas,
no Stan, cp314-clean, zero image bloat, and deterministic (so tests need not
mock it). Dropping in real Prophet/statsmodels later is a one-function change
in :func:`_predict_future` once they support pandas 3 (or the base image moves
back to 3.13); the downstream reorder math in :func:`forecast_item` is
model-agnostic.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, List, Optional, Sequence, Tuple

from django.db.models import Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from inventory.models import StockReconciliation, UsageLog

if TYPE_CHECKING:  # pragma: no cover
    from inventory.models import InventoryItem

# --- tuning constants -------------------------------------------------------

# Trailing window the nightly task feeds the builder (one year of history).
TRAILING_WINDOW_DAYS = 365

# Below this much *usable* history the seasonal smoother can't be trusted, so
# we fall back to a trailing run-rate. 60 days ~= 8 weekly cycles.
MIN_HISTORY_DAYS = 60

# Buffer added to the lead time to set the projection horizon: cover the
# reorder lead time plus a week of slack.
SAFETY_BUFFER_DAYS = 7

# Weekly seasonality — most maker-space consumption is day-of-week driven.
SEASONAL_PERIOD_DAYS = 7

# One-sided z for the upper prediction band that drives safety stock. 1.2816 is
# the ~90th percentile of the standard normal, i.e. the upper edge of an 80%
# central interval — matching Prophet's default ``interval_width=0.8`` so the
# safety-stock semantics are stable if the model is later swapped.
UNCERTAINTY_Z = 1.2816

# Holt-Winters additive smoothing coefficients. The trend term is intentionally
# omitted (flat level) because consumables don't trend unbounded — this mirrors
# the ``growth="flat"`` choice Prophet would have used here.
_HW_ALPHA = 0.3  # level
_HW_GAMMA = 0.3  # seasonal

# StockReconciliation reasons whose negative delta is *consumption* (not a
# counting correction or a positive "found"). FOUND / MISCOUNTED / positive
# deltas are excluded — they are stock movements, not demand.
SHRINKAGE_REASONS = (
    StockReconciliation.ReasonCode.USED_WITHOUT_SCAN,
    StockReconciliation.ReasonCode.LOST,
    StockReconciliation.ReasonCode.DAMAGED,
)

# Method labels (mirror inventory.models.DemandForecast.Method).
METHOD_HOLTWINTERS = "holtwinters"
METHOD_FALLBACK = "fallback"
MODEL_VERSION = "holtwinters-1"


DailyPoint = Tuple[date, float]


@dataclass
class ForecastResult:
    """The per-item projection, ready to persist as a ``DemandForecast`` row.

    Field names line up 1:1 with the model so the task can splat this into
    ``DemandForecast.objects.create(**...)`` without a translation layer.
    """

    method: str
    model_version: str
    horizon_days: int
    predicted_daily_demand: float
    horizon_demand: float
    horizon_demand_upper: float
    available_at_generation: int
    safety_stock: int
    predictive_reorder_point: int
    needs_reorder: bool
    days_until_stockout: Optional[float]
    projected_stockout_date: Optional[date]
    lead_time_days: Optional[int]


# --- stage 1: consumption series -------------------------------------------


def build_daily_consumption_series(
    item: "InventoryItem", *, start: date, end: date
) -> List[DailyPoint]:
    """Reconstruct a regular daily consumption series for ``item``.

    Consumption on a day is the sum of:

    * ``UsageLog.quantity_used`` logged that day, and
    * shrinkage — the magnitude of *negative* ``StockReconciliation.delta``
      rows whose reason is in :data:`SHRINKAGE_REASONS` (used-without-scan,
      lost, damaged). Positive deltas and FOUND/MISCOUNTED are stock movements,
      not demand, and are excluded.

    Every calendar day in ``[start, end]`` with no activity is **zero-filled**
    (the smoother needs a regular series), then the leading run of zeros before
    the item's first real usage is **trimmed** (don't train on days the item
    effectively didn't exist yet).

    Args:
        item: the inventory item.
        start: first calendar day to consider (inclusive).
        end: last calendar day to consider (inclusive).

    Returns:
        ``[(date, quantity), ...]`` in ascending date order, zero-filled and
        leading-zero-trimmed. Empty when the item had no consumption at all in
        the window.
    """
    if end < start:
        return []

    by_day: dict[date, float] = {}

    usage_rows = (
        UsageLog.objects.filter(item=item, usage_date__date__gte=start, usage_date__date__lte=end)
        .annotate(day=TruncDate("usage_date"))
        .values("day")
        .annotate(total=Sum("quantity_used"))
    )
    for row in usage_rows:
        if row["day"] is None:
            continue
        by_day[row["day"]] = by_day.get(row["day"], 0.0) + float(row["total"] or 0)

    shrink_rows = (
        StockReconciliation.objects.filter(
            item=item,
            delta__lt=0,
            reason__in=SHRINKAGE_REASONS,
            reconciled_at__date__gte=start,
            reconciled_at__date__lte=end,
        )
        .annotate(day=TruncDate("reconciled_at"))
        .values("day")
        .annotate(total=Sum("delta"))
    )
    for row in shrink_rows:
        if row["day"] is None:
            continue
        # total is negative (sum of negative deltas); its magnitude is demand.
        by_day[row["day"]] = by_day.get(row["day"], 0.0) + float(-(row["total"] or 0))

    # Zero-fill the whole calendar range.
    series: List[DailyPoint] = []
    day = start
    step = timedelta(days=1)
    while day <= end:
        series.append((day, by_day.get(day, 0.0)))
        day += step

    # Trim the leading zero-run (before the first day with real consumption).
    first = next((i for i, (_, qty) in enumerate(series) if qty > 0), None)
    if first is None:
        return []
    return series[first:]


# --- stage 2: forecast ------------------------------------------------------


def horizon_days_for(lead_time_days: Optional[float]) -> int:
    """Projection horizon = lead time + :data:`SAFETY_BUFFER_DAYS`, min 1 day."""
    lead = int(math.ceil(lead_time_days)) if lead_time_days and lead_time_days > 0 else 0
    return max(1, lead + SAFETY_BUFFER_DAYS)


def forecast_item(
    item: "InventoryItem",
    series: Sequence[DailyPoint],
    horizon_days: int,
    *,
    lead_time_days: Optional[float] = None,
    available: Optional[int] = None,
    now=None,
) -> ForecastResult:
    """Project ``series`` over ``horizon_days`` into a :class:`ForecastResult`.

    The model tier is picked by *usable* history length (``len(series)`` — the
    series is already leading-zero-trimmed): a seasonal Holt-Winters smoother
    when there are at least :data:`MIN_HISTORY_DAYS` days, otherwise a trailing
    run-rate fallback. Both produce a per-day ``yhat`` / ``yhat_upper`` pair;
    the reorder math below is identical either way:

    * ``safety_stock = ceil(horizon_demand_upper - horizon_demand)`` — the
      prediction-band width over the horizon, so a noisier item carries more
      buffer.
    * ``predictive_reorder_point = ceil(horizon_demand + safety_stock)``.
    * ``needs_reorder`` when available stock is at/below that point.
    * ``days_until_stockout = available / predicted_daily_demand`` (when demand
      is positive) and the matching ``projected_stockout_date``.

    Args:
        item: the item (used for ``current_stock`` when ``available`` is None).
        series: leading-zero-trimmed daily ``(date, qty)`` points.
        horizon_days: projection window; see :func:`horizon_days_for`.
        lead_time_days: resolved lead time, stored on the row for context.
        available: on-hand stock at generation (defaults to
            ``item.current_stock``).
        now: reference time for the stockout date (defaults to ``timezone.now``).

    Returns:
        A :class:`ForecastResult` ready to persist.
    """
    now = now or timezone.now()
    available = int(item.current_stock if available is None else available)
    horizon_days = max(1, int(horizon_days))

    quantities = [float(qty) for _, qty in series]

    if len(quantities) >= MIN_HISTORY_DAYS:
        method = METHOD_HOLTWINTERS
        model_version = MODEL_VERSION
        yhat, yhat_upper = _predict_future(series, horizon_days)
        predicted_daily_demand = _mean([max(v, 0.0) for v in yhat]) if yhat else 0.0
        horizon_demand = sum(max(v, 0.0) for v in yhat)
        horizon_demand_upper = sum(max(v, 0.0) for v in yhat_upper)
    else:
        method = METHOD_FALLBACK
        model_version = ""
        predicted_daily_demand, horizon_demand, horizon_demand_upper = _forecast_fallback(
            quantities, horizon_days
        )

    safety_stock = max(0, int(math.ceil(horizon_demand_upper - horizon_demand)))
    predictive_reorder_point = int(math.ceil(horizon_demand + safety_stock))
    needs_reorder = available <= predictive_reorder_point

    if predicted_daily_demand > 0:
        days_until_stockout = round(available / predicted_daily_demand, 1)
        projected_stockout_date = (now + timedelta(days=available / predicted_daily_demand)).date()
    else:
        days_until_stockout = None
        projected_stockout_date = None

    stored_lead = int(round(lead_time_days)) if lead_time_days is not None else None

    return ForecastResult(
        method=method,
        model_version=model_version,
        horizon_days=horizon_days,
        predicted_daily_demand=round(predicted_daily_demand, 4),
        horizon_demand=round(horizon_demand, 4),
        horizon_demand_upper=round(horizon_demand_upper, 4),
        available_at_generation=available,
        safety_stock=safety_stock,
        predictive_reorder_point=predictive_reorder_point,
        needs_reorder=needs_reorder,
        days_until_stockout=days_until_stockout,
        projected_stockout_date=projected_stockout_date,
        lead_time_days=stored_lead,
    )


def _forecast_fallback(
    quantities: Sequence[float], horizon_days: int
) -> Tuple[float, float, float]:
    """Trailing run-rate projection for thin history.

    Mean daily demand over the (short) series, with an upper band from the
    daily standard deviation grown over the horizon (``σ·√H``) — the same
    uncertainty→safety-stock idea as the seasonal path, without needing a
    model. Returns ``(predicted_daily_demand, horizon_demand,
    horizon_demand_upper)``.
    """
    if not quantities:
        return 0.0, 0.0, 0.0
    mean = _mean(quantities)
    std = statistics.pstdev(quantities) if len(quantities) >= 2 else 0.0
    horizon_demand = mean * horizon_days
    horizon_demand_upper = horizon_demand + UNCERTAINTY_Z * std * math.sqrt(horizon_days)
    return mean, horizon_demand, horizon_demand_upper


def _predict_future(
    series: Sequence[DailyPoint], horizon_days: int
) -> Tuple[List[float], List[float]]:
    """The forecasting-model **swap seam**: fit and project ``horizon_days`` ahead.

    Returns ``(yhat, yhat_upper)`` — two lists of length ``horizon_days`` — the
    per-day point forecast and its upper prediction band. This is the *only*
    function that knows the model. To adopt real Prophet or statsmodels later,
    replace this body (return the same pair) and flip
    :data:`METHOD_HOLTWINTERS` / :data:`MODEL_VERSION`; nothing else changes.

    Default: additive Holt-Winters seasonal exponential smoothing with a weekly
    period and no trend term (flat level — consumables don't trend unbounded).
    The upper band is the point forecast plus ``UNCERTAINTY_Z × σ`` where ``σ``
    is the in-sample one-step residual standard deviation. Pure standard
    library (no pandas/numpy), so it is deterministic and cp314-clean.
    """
    quantities = [float(qty) for _, qty in series]
    n = len(quantities)
    period = SEASONAL_PERIOD_DAYS

    # Not enough for a seasonal fit (shouldn't happen — caller gates at
    # MIN_HISTORY_DAYS >> 2*period — but stay safe): flat mean forecast.
    if n < 2 * period:
        mean = _mean(quantities) if quantities else 0.0
        std = statistics.pstdev(quantities) if n >= 2 else 0.0
        band = UNCERTAINTY_Z * std
        return [mean] * horizon_days, [mean + band] * horizon_days

    # Seasonal init: level = mean of first full season; seasonal deviations
    # from that level.
    level = _mean(quantities[:period])
    seasonal = [quantities[i] - level for i in range(period)]

    residuals: List[float] = []
    for t in range(period, n):
        season = seasonal[t - period]
        prediction = level + season  # one-step-ahead, flat level
        residuals.append(quantities[t] - prediction)
        # Update level and this position's seasonal component.
        new_level = _HW_ALPHA * (quantities[t] - season) + (1 - _HW_ALPHA) * level
        seasonal.append(_HW_GAMMA * (quantities[t] - new_level) + (1 - _HW_GAMMA) * season)
        level = new_level

    resid_std = statistics.pstdev(residuals) if len(residuals) >= 2 else 0.0
    band = UNCERTAINTY_Z * resid_std

    # Project: flat level + the continuing weekly seasonal pattern. seasonal now
    # has length n; its last ``period`` entries are the most recent cycle.
    yhat: List[float] = []
    yhat_upper: List[float] = []
    for h in range(horizon_days):
        season = seasonal[len(seasonal) - period + (h % period)]
        point = level + season
        yhat.append(point)
        yhat_upper.append(point + band)
    return yhat, yhat_upper


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0
