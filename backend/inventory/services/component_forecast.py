"""Consumption forecasting + low-stock detection for serialized components.

Serialized :class:`~inventory.models.InventoryItem` records track individual
physical units as :class:`~inventory.models.SerializedComponent` rows that move
through a lifecycle branched on ``serial_tracking_mode`` and log every
transition as a :class:`~inventory.models.ComponentUsageEvent`.

This module turns that usage history into a demand forecast:

* ``avg_daily_use`` — depletion rate over a trailing window, derived from
  ``ComponentUsageEvent`` and **branched on tracking mode**:

  - ``consumable`` units deplete when they are **consumed** (``consume``).
  - ``reusable`` units deplete **only** when they are **retired or disposed**
    (``retire`` / ``dispose``); the ``install`` <-> ``remove`` reuse cycle does
    **not** reduce stock.

* ``days_until_stockout = available / avg_daily_use`` — where ``available`` is
  the ready-to-install stock: physically-present (**not yet** depleted, branched
  on mode) **minus** the units currently installed in an asset. Each row also
  carries ``on_hand`` (present, installed included) and ``installed`` for
  display; ``available_stock`` is kept as a backward-compatible alias of
  ``on_hand``. Installing a unit lowers ``available`` (and can trip the reorder
  point) without lowering ``on_hand``; only a depleting transition lowers both.

* ``reorder_point = avg_daily_use * lead_time_days + safety_stock`` — the
  classic reorder trigger. ``lead_time_days`` reuses observed supplier
  performance from :class:`reorder_queue.models.LeadTimeLog` (falling back to
  the supplier's estimated ``average_lead_time``), and ``safety_stock`` reuses
  the item's existing ``minimum_stock`` buffer.

The output feeds the inventory + purchasing overview dashboards.
"""

from __future__ import annotations

import math
from datetime import timedelta
from typing import Any, Optional

from django.db.models import Avg, Count
from django.utils import timezone

from inventory.models import (
    ComponentUsageEvent,
    InventoryItem,
    ItemSupplier,
    SerializedComponent,
)

# Default trailing window used to estimate the depletion rate.
DEFAULT_WINDOW_DAYS = 90

# Statuses that mean a unit has permanently left the usable pool, per mode.
_DEPLETED_STATUSES = {
    InventoryItem.SERIAL_TRACKING_CONSUMABLE: {
        SerializedComponent.CONSUMED,
        SerializedComponent.DISPOSED,
    },
    InventoryItem.SERIAL_TRACKING_REUSABLE: {
        SerializedComponent.RETIRED,
        SerializedComponent.DISPOSED,
    },
}

# Lifecycle actions that count as a depletion for the forecast rate, per mode.
# NOTE: ``dispose`` is a legal action in *both* modes, but for consumables the
# depletion already happened at ``consume`` — counting ``dispose`` too would
# double-count a single unit, so consumables only count ``consume``.
_DEPLETING_ACTIONS = {
    InventoryItem.SERIAL_TRACKING_CONSUMABLE: [SerializedComponent.ACTION_CONSUME],
    InventoryItem.SERIAL_TRACKING_REUSABLE: [
        SerializedComponent.ACTION_RETIRE,
        SerializedComponent.ACTION_DISPOSE,
    ],
}


def _lead_time_days_by_item(items: list[InventoryItem]) -> dict[Any, Optional[float]]:
    """Resolve each item's lead time in days, batched to avoid N+1 queries.

    Prefers the mean of *observed* lead times recorded in
    ``reorder_queue.LeadTimeLog`` across the item's suppliers; falls back to the
    supplier's estimated ``average_lead_time`` (primary supplier preferred);
    maps to ``None`` when neither is available.
    """
    # Imported lazily so this module has no hard import-time dependency on the
    # reorder_queue app (mirrors how the rest of inventory references it).
    from reorder_queue.models import LeadTimeLog

    observed = {
        row["item_supplier__item_id"]: row["avg"]
        for row in (
            LeadTimeLog.objects.filter(item_supplier__item__in=items)
            .values("item_supplier__item_id")
            .annotate(avg=Avg("actual_lead_time_days"))
        )
    }

    # Estimated fallback: the primary supplier's average_lead_time (or any
    # supplier's if none is flagged primary), matching InventoryItem's own
    # ``average_lead_time`` resolution without a query per item.
    estimated: dict[Any, Any] = {}
    for row in (
        ItemSupplier.objects.filter(item__in=items)
        .order_by("item_id", "-is_primary")
        .values("item_id", "average_lead_time")
    ):
        estimated.setdefault(row["item_id"], row["average_lead_time"])

    resolved: dict[Any, Optional[float]] = {}
    for item in items:
        obs = observed.get(item.id)
        if obs is not None:
            resolved[item.id] = float(obs)
            continue
        est = estimated.get(item.id)
        resolved[item.id] = float(est) if est is not None else None
    return resolved


def _stock_split_by_item(items: list[InventoryItem]) -> dict[Any, dict[str, int]]:
    """Per-item ``on_hand`` / ``installed`` / ``available`` split (mode-aware).

    * ``on_hand`` — physically-present units = every unit not in a *depleted*
      status for the item's mode (consumable present = received/in_stock/
      installed; reusable present additionally counts ``removed``).
    * ``installed`` — units currently installed in an asset.
    * ``available`` — ready-to-install stock = ``on_hand`` minus ``installed``.
      An ``install`` moves a unit out of ``available`` while leaving ``on_hand``
      unchanged; only a depleting transition (consume; retire/dispose) lowers
      ``on_hand``.
    """
    counts: dict[Any, dict[str, int]] = {}
    rows = (
        SerializedComponent.objects.filter(item__in=items)
        .values("item_id", "status")
        .annotate(n=Count("id"))
    )
    for row in rows:
        counts.setdefault(row["item_id"], {})[row["status"]] = row["n"]

    split: dict[Any, dict[str, int]] = {}
    for item in items:
        depleted = _DEPLETED_STATUSES.get(item.serial_tracking_mode, set())
        per_status = counts.get(item.id, {})
        on_hand = sum(n for status, n in per_status.items() if status not in depleted)
        installed = per_status.get(SerializedComponent.INSTALLED, 0)
        split[item.id] = {
            "on_hand": on_hand,
            "installed": installed,
            "available": on_hand - installed,
        }
    return split


def stock_split_for_item(item: InventoryItem) -> dict[str, int]:
    """``on_hand`` / ``installed`` / ``available`` for a single serialized item.

    Thin convenience wrapper around :func:`_stock_split_by_item` for callers
    that hold one item (e.g. the item-detail serialized panel).
    """
    return _stock_split_by_item([item])[item.id]


def _depletion_counts(items: list[InventoryItem], window_start, now) -> dict[Any, int]:
    """Count distinct units depleted within the window per item (mode-aware).

    Deduplicating by unit keeps a reusable unit that is retired *and* disposed
    inside the same window from counting twice.
    """
    consumable_ids = [
        i.id for i in items if i.serial_tracking_mode == InventoryItem.SERIAL_TRACKING_CONSUMABLE
    ]
    reusable_ids = [
        i.id for i in items if i.serial_tracking_mode == InventoryItem.SERIAL_TRACKING_REUSABLE
    ]

    depleted: dict[Any, int] = {}
    for item_ids, mode in (
        (consumable_ids, InventoryItem.SERIAL_TRACKING_CONSUMABLE),
        (reusable_ids, InventoryItem.SERIAL_TRACKING_REUSABLE),
    ):
        if not item_ids:
            continue
        rows = (
            ComponentUsageEvent.objects.filter(
                component__item_id__in=item_ids,
                action__in=_DEPLETING_ACTIONS[mode],
                at__gte=window_start,
                at__lte=now,
            )
            .values("component__item_id")
            .annotate(n=Count("component", distinct=True))
        )
        for row in rows:
            depleted[row["component__item_id"]] = row["n"]
    return depleted


def build_component_forecast(
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    low_stock_only: bool = False,
    now=None,
) -> list[dict[str, Any]]:
    """Build the serialized-component consumption forecast / low-stock report.

    Args:
        window_days: Trailing window (in days) used to estimate the depletion
            rate. Clamped to a minimum of 1.
        low_stock_only: When ``True``, only rows whose ``available`` stock is at
            or below their ``reorder_point`` are returned.
        now: Reference "now" (defaults to :func:`django.utils.timezone.now`);
            injectable for deterministic tests.

    Returns:
        One row per active serialized item, sorted most-urgent first (soonest
        stockout, then items flagged for reorder).
    """
    now = now or timezone.now()
    window_days = max(1, int(window_days))
    window_start = now - timedelta(days=window_days)

    items = list(
        InventoryItem.objects.filter(
            is_serialized=True, is_active=True, is_retired=False
        ).select_related("category")
    )
    if not items:
        return []

    split_by_item = _stock_split_by_item(items)
    depleted_by_item = _depletion_counts(items, window_start, now)
    lead_time_by_item = _lead_time_days_by_item(items)

    rows: list[dict[str, Any]] = []
    for item in items:
        split = split_by_item.get(item.id, {"on_hand": 0, "installed": 0, "available": 0})
        on_hand = split["on_hand"]
        installed = split["installed"]
        # ``available`` (on-hand minus installed) is the ready-to-install stock
        # that actually backs future demand, so it — not on_hand — drives the
        # stockout / reorder math. An installed unit lowers ``available`` and
        # can therefore push an item over its reorder point.
        available = split["available"]
        units_depleted = depleted_by_item.get(item.id, 0)
        avg_daily_use = units_depleted / window_days

        if avg_daily_use > 0:
            days_until_stockout = round(available / avg_daily_use, 1)
            projected_stockout_date = (now + timedelta(days=available / avg_daily_use)).date()
        else:
            days_until_stockout = None
            projected_stockout_date = None

        lead_time_days = lead_time_by_item.get(item.id)
        safety_stock = item.minimum_stock or 0
        lead_component = avg_daily_use * (lead_time_days or 0)
        reorder_point = int(math.ceil(lead_component + safety_stock))
        needs_reorder = available <= reorder_point

        if low_stock_only and not needs_reorder:
            continue

        rows.append(
            {
                "item_id": str(item.id),
                "item_name": item.name,
                "sku": item.sku,
                "category_name": item.category.name if item.category else None,
                "serial_tracking_mode": item.serial_tracking_mode,
                # ``available`` / ``on_hand`` / ``installed`` are the serialized
                # split; ``available_stock`` is retained as a backward-compatible
                # alias of ``on_hand`` for existing consumers of this row.
                "available": available,
                "on_hand": on_hand,
                "installed": installed,
                "available_stock": on_hand,
                "current_stock": item.current_stock,
                "window_days": window_days,
                "units_depleted_in_window": units_depleted,
                "avg_daily_use": round(avg_daily_use, 4),
                "days_until_stockout": days_until_stockout,
                "projected_stockout_date": (
                    projected_stockout_date.isoformat() if projected_stockout_date else None
                ),
                "lead_time_days": round(lead_time_days, 1) if lead_time_days is not None else None,
                "safety_stock": safety_stock,
                "reorder_point": reorder_point,
                "needs_reorder": needs_reorder,
            }
        )

    # Most urgent first: soonest stockout (rows without a stockout date last),
    # then reorder-flagged items, then by name for stable ordering.
    rows.sort(
        key=lambda r: (
            r["days_until_stockout"] is None,
            r["days_until_stockout"] if r["days_until_stockout"] is not None else 0.0,
            not r["needs_reorder"],
            r["item_name"].lower(),
        )
    )
    return rows
