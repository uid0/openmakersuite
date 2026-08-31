"""Demand-forecast *engine* for non-serialized inventory items.

This is the write side of the demand forecast: it turns purchase history into
the numbers a :class:`~inventory.models.DemandForecast` row holds, and the
nightly ``inventory.tasks.generate_demand_forecasts`` task persists one row per
item per run.

The model is **restock interval**, not usage rate: predict *when* to buy an
item again from the average time between the times it was actually bought.
Two pure, unit-testable stages:

1. :func:`build_restock_events` — the item's purchase-event dates, from
   :class:`~reorder_queue.models.PurchaseOrder` history.
2. :func:`forecast_item_by_interval` — averages the gaps between those dates
   and projects the next one, flagging the item when that date falls inside
   the supplier lead time.

Why intervals and not consumption
---------------------------------
v1 of this engine reconstructed a daily consumption series from ``UsageLog``
(plus ``StockReconciliation`` shrinkage) and projected it with a seasonal
smoother. On real data that model had nothing to stand on: **0 of 53**
non-serialized items had a single ``UsageLog`` row — nobody scans usage — so
the series was driven entirely by sporadic stock corrections. One item came out
at 1818 units/day with a reorder point of 40,233, off the back of a −19k
reconciliation.

Purchase history is the signal that *is* reliably recorded, because every
restock goes through a purchase order: 10" paper towel bought 6 times at a
~48-day cadence, coreless TP 4 times at ~78 days, kitchen paper towel 3 times
at ~29 days. Those are exactly the recurring consumables the alerts exist for,
and the cadence is what a stockroom actually acts on ("this is about due
again"), so the engine models that directly.

Signal choice: ``PurchaseOrder.order_date`` (when we ordered) rather than
receipt (``DeliveryItem.scanned_at`` / ``OrderDelivery.delivery_date``).
Ordering is the decision this forecast is trying to reproduce, and every PO has
an order date whereas receipts depend on scan discipline. Receipt-based
intervals remain a possible refinement if delivery data ever gets denser.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Iterable, List, Optional

from django.db.models.functions import TruncDate
from django.utils import timezone

if TYPE_CHECKING:  # pragma: no cover
    from inventory.models import InventoryItem

# --- tuning constants -------------------------------------------------------

# Fewer purchase events than this and there is no gap to average, so no cadence
# can be inferred. Two events = one gap = the minimum usable history.
MIN_RESTOCK_EVENTS = 2

# Method labels (mirror inventory.models.DemandForecast.Method).
METHOD_RESTOCK_INTERVAL = "restock_interval"
METHOD_INSUFFICIENT_HISTORY = "insufficient_history"
MODEL_VERSION = "interval-1"


@dataclass
class ForecastResult:
    """The per-item projection, ready to persist as a ``DemandForecast`` row.

    Field names line up 1:1 with the model so the task can splat this into
    ``DemandForecast.objects.create(**...)`` without a translation layer. The
    retired v1 quantity fields default to ``0``/``None`` here — the interval
    model does not predict quantities, and the columns are kept only so
    historical rows and existing API consumers stay readable.
    """

    method: str
    model_version: str
    avg_interval_days: Optional[float]
    interval_samples: int
    last_restock_date: Optional[date]
    predicted_next_reorder_date: Optional[date]
    days_until_due: Optional[float]
    needs_reorder: bool
    available_at_generation: int
    lead_time_days: Optional[int]

    # Retired v1 quantity projection — written as 0/NULL by this engine.
    horizon_days: int = 0
    predicted_daily_demand: float = 0.0
    horizon_demand: float = 0.0
    horizon_demand_upper: float = 0.0
    safety_stock: int = 0
    predictive_reorder_point: int = 0
    days_until_stockout: Optional[float] = None
    projected_stockout_date: Optional[date] = None


# --- stage 1: restock events ------------------------------------------------


def build_restock_events(item: "InventoryItem", *, end: date) -> List[date]:
    """The dates ``item`` was purchased, ascending, one per day.

    Walks the item's purchase-order line items
    (``PurchaseOrderItem.item_supplier.item``) and collects their order dates,
    deduplicated per day: what the cadence measures is *shopping trips*, not
    paperwork, so restocking the item twice on one day (a second order, or a
    top-up from a different supplier) counts once.

    Cancelled and voided purchase orders are excluded — an order that was never
    placed is not a restock, and counting it would stretch the measured gaps
    around it.

    Args:
        item: the inventory item.
        end: last calendar day to consider (inclusive); orders after it are
            ignored so a run can be reproduced as of a past date.

    Returns:
        Ascending, deduplicated ``date`` list — possibly empty.
    """
    # Imported lazily so this module has no import-time dependency on the
    # reorder_queue app (mirrors how the rest of inventory references it).
    from reorder_queue.models import PurchaseOrder, PurchaseOrderItem

    days = (
        PurchaseOrderItem.objects.filter(item_supplier__item=item)
        .exclude(
            purchase_order__status__in=(
                PurchaseOrder.Status.CANCELLED,
                PurchaseOrder.Status.VOIDED,
            )
        )
        .annotate(day=TruncDate("purchase_order__order_date"))
        .filter(day__lte=end)
        .values_list("day", flat=True)
        .distinct()
    )
    return sorted(day for day in days if day is not None)


# --- stage 2: forecast ------------------------------------------------------


def forecast_item_by_interval(
    item: Optional["InventoryItem"],
    events: Iterable[date],
    *,
    now=None,
    lead_time_days: Optional[float] = None,
    available: Optional[int] = None,
) -> ForecastResult:
    """Average the gaps between ``events`` and project the next purchase.

    With at least :data:`MIN_RESTOCK_EVENTS` events the cadence is the mean gap
    between consecutive purchase dates; the item is due one cadence after its
    last purchase, and is **flagged** once that due date is within the supplier
    lead time — order now and it lands about when it is needed. With fewer
    events there is no gap to average, so the row records
    ``insufficient_history`` and flags nothing (guessing a cadence from a single
    purchase would be noise, and a false alert costs more than a missing one).

    ``lead_time_days`` of ``None`` means no lead time is known, and the flag
    threshold falls back to the due date itself rather than to a fabricated
    zero-day wait — see the comment at the comparison for why that population
    is deliberately not flagged earlier.

    Args:
        item: the inventory item; only read for ``current_stock`` when
            ``available`` is not given, so pure tests may pass ``None``.
        events: purchase dates from :func:`build_restock_events` (re-sorted and
            deduplicated defensively).
        now: reference time for ``days_until_due`` (defaults to
            ``timezone.now``).
        lead_time_days: resolved supplier lead time; the flag threshold.
        available: on-hand stock snapshot (defaults to ``item.current_stock``).

    Returns:
        A :class:`ForecastResult` ready to persist.
    """
    now = now or timezone.now()
    if available is None:
        available = int(item.current_stock) if item is not None else 0
    else:
        available = int(available)
    stored_lead = int(round(lead_time_days)) if lead_time_days is not None else None

    ordered = sorted(set(events))
    # Gaps, not events: n purchases describe n-1 intervals.
    interval_samples = max(0, len(ordered) - 1)
    # A lone purchase is still the last known restock, so report it even though
    # no cadence can be derived from it.
    last_restock_date = ordered[-1] if ordered else None

    if len(ordered) < MIN_RESTOCK_EVENTS:
        return ForecastResult(
            method=METHOD_INSUFFICIENT_HISTORY,
            model_version="",
            avg_interval_days=None,
            interval_samples=interval_samples,
            last_restock_date=last_restock_date,
            predicted_next_reorder_date=None,
            days_until_due=None,
            needs_reorder=False,
            available_at_generation=available,
            lead_time_days=stored_lead,
        )

    gaps = [(later - earlier).days for earlier, later in zip(ordered, ordered[1:])]
    avg_interval_days = statistics.fmean(gaps)
    # Round to the nearest whole day rather than truncating: date arithmetic
    # drops the fractional part, which would bias every prediction early.
    predicted_next_reorder_date = last_restock_date + timedelta(days=round(avg_interval_days))
    days_until_due = float((predicted_next_reorder_date - now.date()).days)
    # The flag threshold. An UNKNOWN lead time is not a zero-day one (op-c1ke):
    # spelled ``is None`` rather than ``or 0`` so a genuine zero-day wait keeps
    # its own branch and cannot be silently re-collapsed by a later edit.
    #
    # With no lead time the only thing this engine can still say is WHEN the
    # item is due, so the threshold is 0 — flagged once it is due, never
    # earlier, and the row records ``lead_time_days: None`` so nobody reads a
    # horizon into it. That is deliberately not widened into "flag it anyway":
    # the entire population reaching this branch is items with no supplier link
    # at all (``average_lead_time`` is non-nullable with a default, so any link
    # supplies an estimate — a DISCONTINUED link included, which is why an item
    # whose last vendor died keeps its full lead-time threshold here). Flagging
    # the no-supplier population regardless of cadence would turn a data gap
    # into a permanent alert.
    flag_threshold = lead_time_days if lead_time_days is not None else 0.0
    needs_reorder = days_until_due <= flag_threshold

    return ForecastResult(
        method=METHOD_RESTOCK_INTERVAL,
        model_version=MODEL_VERSION,
        avg_interval_days=round(avg_interval_days, 4),
        interval_samples=interval_samples,
        last_restock_date=last_restock_date,
        predicted_next_reorder_date=predicted_next_reorder_date,
        days_until_due=days_until_due,
        needs_reorder=needs_reorder,
        available_at_generation=available,
        lead_time_days=stored_lead,
    )
