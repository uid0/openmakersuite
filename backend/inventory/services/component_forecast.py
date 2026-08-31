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
  classic reorder trigger. ``lead_time_days`` describes the supplier the item
  would actually be bought through — observed performance from
  :class:`reorder_queue.models.LeadTimeLog` for THAT supplier, falling back to
  THAT supplier's estimated ``average_lead_time`` — and ``safety_stock`` reuses
  the item's existing ``minimum_stock`` buffer. An unknown lead time yields
  ``reorder_point: null``, never a point computed at a zero-day wait. An item
  whose every supplier link is dead is flagged ``needs_reorder`` outright,
  because it has no horizon at all and is the hardest thing to buy;
  ``no_orderable_supplier`` says so on the row so the flag is actionable. An
  item that simply never had a supplier recorded is NOT flagged — that is a
  data gap, not an unbuyable item.

The output feeds the inventory + purchasing overview dashboards.
"""

from __future__ import annotations

import math
from datetime import timedelta
from typing import Any, Optional

from django.db.models import Avg, Count
from django.utils import timezone

from inventory.models import ComponentUsageEvent, InventoryItem, SerializedComponent
from inventory.services.supplier_selection import (
    NONE_ORDERABLE,
    primary_suppliers_for,
    select_suppliers_for,
)

# Default trailing window used to estimate the depletion rate.
DEFAULT_WINDOW_DAYS = 90

# Statuses that mean a unit has permanently left the usable pool, per mode.
_DEPLETED_STATUSES = {
    InventoryItem.SerialTrackingMode.CONSUMABLE: {
        SerializedComponent.Status.CONSUMED,
        SerializedComponent.Status.DISPOSED,
    },
    InventoryItem.SerialTrackingMode.REUSABLE: {
        SerializedComponent.Status.RETIRED,
        SerializedComponent.Status.DISPOSED,
    },
}

# Lifecycle actions that count as a depletion for the forecast rate, per mode.
# NOTE: ``dispose`` is a legal action in *both* modes, but for consumables the
# depletion already happened at ``consume`` — counting ``dispose`` too would
# double-count a single unit, so consumables only count ``consume``.
_DEPLETING_ACTIONS = {
    InventoryItem.SerialTrackingMode.CONSUMABLE: [SerializedComponent.Action.CONSUME],
    InventoryItem.SerialTrackingMode.REUSABLE: [
        SerializedComponent.Action.RETIRE,
        SerializedComponent.Action.DISPOSE,
    ],
}


def _lead_time_days_by_item(
    items: list[InventoryItem], selected: Optional[dict[Any, Any]] = None
) -> dict[Any, Optional[float]]:
    """Resolve each item's lead time in days, batched to avoid N+1 queries.

    Both branches describe ONE supplier: the one the item would actually be
    bought through, resolved by the shared ``supplier_selection`` derivation.
    Prefers the mean of that supplier's *observed* deliveries recorded in
    ``reorder_queue.LeadTimeLog``; falls back to that same supplier's estimated
    ``average_lead_time``; maps to ``None`` when the item has no supplier it can
    be ordered from at all.

    Scoping matters both ways. Averaging history across every link forecast an
    item on a vendor it will not be bought from — an operator's flagged primary
    quoting 30 days, with history only against a faster second link, produced a
    reorder point roughly four times too low, i.e. running out while the numbers
    looked fine. Including dead links did the same with a vendor who no longer
    sells the item (op-2rsp).

    ``selected`` is the already-resolved ``{item_id: ItemSupplier | None}`` map
    when the caller holds one — :func:`build_component_forecast` does, because
    it also needs the REASON there is no supplier — and is resolved here
    otherwise.
    """
    # Imported lazily so this module has no hard import-time dependency on the
    # reorder_queue app (mirrors how the rest of inventory references it).
    from reorder_queue.models import LeadTimeLog

    # The one supplier per item, from the shared derivation: orderable, the
    # operator's flagged primary if they set one, else the best-scoring
    # candidate. One query per page. Everything below is scoped to it.
    if selected is None:
        selected = primary_suppliers_for(items)
    selected_link_ids = [link.id for link in selected.values() if link is not None]

    observed: dict[Any, Any] = {}
    if selected_link_ids:
        observed = {
            row["item_supplier__item_id"]: row["avg"]
            for row in (
                LeadTimeLog.objects.filter(item_supplier_id__in=selected_link_ids)
                .values("item_supplier__item_id")
                .annotate(avg=Avg("actual_lead_time_days"))
            )
        }

    estimated: dict[Any, Any] = {
        item_id: link.average_lead_time if link else None for item_id, link in selected.items()
    }

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
        installed = per_status.get(SerializedComponent.Status.INSTALLED, 0)
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
        i.id for i in items if i.serial_tracking_mode == InventoryItem.SerialTrackingMode.CONSUMABLE
    ]
    reusable_ids = [
        i.id for i in items if i.serial_tracking_mode == InventoryItem.SerialTrackingMode.REUSABLE
    ]

    depleted: dict[Any, int] = {}
    for item_ids, mode in (
        (consumable_ids, InventoryItem.SerialTrackingMode.CONSUMABLE),
        (reusable_ids, InventoryItem.SerialTrackingMode.REUSABLE),
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
        low_stock_only: When ``True``, only rows flagged ``needs_reorder`` are
            returned — stock at or below the reorder point, OR no supplier the
            item can be ordered from at all.
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
    # Resolved once and used for two different questions: which supplier sets
    # the lead time, and whether there IS one. Those are different facts.
    choice_by_item = select_suppliers_for(items)
    lead_time_by_item = _lead_time_days_by_item(
        items, {item_id: choice.item_supplier for item_id, choice in choice_by_item.items()}
    )

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

        safety_stock = item.minimum_stock or 0
        lead_time_days = lead_time_by_item.get(item.id)
        # An unknown lead time yields no reorder point at all. ``or 0`` here
        # read "we cannot tell you how long it takes" as a confident zero-day
        # wait — the most optimistic assumption available — and printed the
        # resulting number beside a flag it contradicted.
        if lead_time_days is None:
            reorder_point = None
        else:
            reorder_point = int(math.ceil(avg_daily_use * lead_time_days + safety_stock))

        # An item nobody can order needs attention UNCONDITIONALLY: it has no
        # horizon to be measured against, and it is the hardest thing in the
        # building to buy. Only NONE_ORDERABLE — every link dead — counts.
        # NO_SUPPLIERS is a data-completeness gap, not an unbuyable item;
        # flagging that whole population permanently regardless of stock would
        # flood the low-stock surface with false alarms, and a surface people
        # learn to ignore suppresses alerts as surely as a missing one. Those
        # are different facts and RULE 4 exists to keep them apart (op-2rsp).
        choice = choice_by_item.get(item.id)
        no_orderable_supplier = choice is not None and choice.reason == NONE_ORDERABLE
        if no_orderable_supplier:
            needs_reorder = True
        elif reorder_point is None:
            needs_reorder = False
        else:
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
                # Additive: says WHY a row can be flagged while its stock sits
                # above its reorder point, so the flag is actionable rather
                # than mysterious. The remedy is a supplier, not a purchase.
                "no_orderable_supplier": no_orderable_supplier,
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
