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
  classic reorder trigger. ``lead_time_days`` is **the wait at the supplier we
  would actually buy from** (op-3vqk): observed performance from
  :class:`reorder_queue.models.LeadTimeLog` against that one link, falling back
  to that link's estimated ``average_lead_time``. ``safety_stock`` reuses the
  item's existing ``minimum_stock`` buffer.

  ``lead_time_basis`` on every row says WHOSE wait the number describes, and it
  is three-valued because these are three different facts (see
  :func:`lead_times_for`):

  - ``orderable_supplier`` — the link :mod:`inventory.services.supplier_selection`
    picked. This is the only basis on which ``reorder_point`` is a horizon we
    can actually order against.
  - ``unorderable_supplier`` — every link is inactive or discontinued, so the
    number is read from ALL of them exactly as it was before this rule existed.
    Real information, about a vendor nobody can buy from. The row STAYS on the
    report with its lead component intact.
  - ``no_supplier`` — no link at all, so no lead time is on record.

  When NO lead time is known — which, because ``average_lead_time`` is a
  non-nullable column with a default, means only an item carrying no supplier
  link at all — that formula cannot be evaluated. The row then reports
  ``lead_time_days: null``, ``lead_time_known: false`` and a ``reorder_point``
  that is the safety stock ALONE: a lower bound, stated as one, rather than a
  horizon computed at a fabricated zero-day wait (op-c1ke).

The output feeds the inventory + purchasing overview dashboards.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Optional

from django.db.models import Avg, Count, Q
from django.utils import timezone

from inventory.models import (
    ComponentUsageEvent,
    InventoryItem,
    ItemSupplier,
    SerializedComponent,
)
from inventory.services.supplier_selection import NONE_ORDERABLE, select_suppliers_for

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


#: The lead time is the one recorded against the link
#: :mod:`inventory.services.supplier_selection` picked — the supplier we would
#: actually buy from. Only on this basis is ``reorder_point`` a horizon we can
#: order against.
LEAD_TIME_FROM_ORDERABLE = "orderable_supplier"

#: Every one of the item's links is inactive or discontinued. The number is
#: REAL — it is how long that vendor took — but it describes a supplier nobody
#: can buy from. Deliberately NOT the same as :data:`LEAD_TIME_UNKNOWN`: this
#: item has a wait on record and stays on the forecast with its lead component
#: intact.
LEAD_TIME_FROM_UNORDERABLE = "unorderable_supplier"

#: No lead time on record at all — the item carries no supplier link. A DATA
#: GAP, pointing at a different operator action ("add a supplier") than
#: :data:`LEAD_TIME_FROM_UNORDERABLE` ("find one that still carries it").
LEAD_TIME_UNKNOWN = "no_supplier"


@dataclass(frozen=True)
class LeadTime:
    """How long a replacement takes — and WHOSE wait that number describes.

    ``basis`` is one of :data:`LEAD_TIME_FROM_ORDERABLE`,
    :data:`LEAD_TIME_FROM_UNORDERABLE` or :data:`LEAD_TIME_UNKNOWN`. It is a
    separate field rather than an overload of ``days`` because a 30 we can order
    against and a 30 nobody can sell us are the same number and different facts;
    collapsing them is the recurring defect this module's history records.
    """

    days: Optional[float]
    basis: str

    @property
    def known(self) -> bool:
        """Is there a lead time on record at all? (Says nothing about whose.)"""
        return self.days is not None


#: The answer for an item :func:`lead_times_for` was never asked about.
_NO_LEAD_TIME = LeadTime(days=None, basis=LEAD_TIME_UNKNOWN)


def lead_times_for(items: list[InventoryItem]) -> dict[Any, LeadTime]:
    """Resolve each item's lead time AND its basis, batched to avoid N+1 queries.

    **The reorder point must be computed from the lead time of the supplier we
    would actually buy from** (op-3vqk). So the first question asked is the one
    :mod:`inventory.services.supplier_selection` owns — this does not re-derive
    it — and the answer branches on what that derivation returns:

    * A chosen link (orderable; the flagged-primary gate, else the score). The
      lead time is the mean of that ONE link's
      ``reorder_queue.LeadTimeLog`` rows, falling back to that link's estimated
      ``average_lead_time``. Basis :data:`LEAD_TIME_FROM_ORDERABLE`. Before this
      rule, both branches averaged across EVERY link: an item with a flagged
      30-day primary took its reorder point from a 7-day vendor it will never
      buy from, understating the trigger roughly fourfold, and then sat below
      its true reorder point unflagged.
    * :data:`~inventory.services.supplier_selection.NONE_ORDERABLE` — links
      exist, every one inactive or discontinued. The lead time is then read from
      ALL of them, **byte-identically to the pre-op-3vqk rule**: the observed
      mean across the item's links, else the flagged-primary-first
      ``average_lead_time``. Basis :data:`LEAD_TIME_FROM_UNORDERABLE`.
    * :data:`~inventory.services.supplier_selection.NO_SUPPLIERS` — no link, so
      no estimate exists to read. ``days`` is ``None``, basis
      :data:`LEAD_TIME_UNKNOWN`.

    **Why the second branch is not simply filtered away, which is the whole
    reason this function used to bypass the derivation.** ``average_lead_time``
    is non-nullable with a default, so ANY link supplies an estimate — a
    discontinued one included. Filtering to orderable links and stopping there
    would leave an item whose only vendor is dead with no lead time at all, its
    threshold collapsing to zero days, and it would silently leave the
    demand-forecast report AND the nightly digest. That is exactly what op-2rsp
    round 5 did and had to revert. The fix is a PREFERENCE, not a filter: prefer
    the supplier we can buy from, fall back to what the dead links still know,
    and say which of the two you did. Two behavioural tests in
    ``inventory/tests/test_alert_suppression.py`` pin the fallback —
    ``test_the_serialized_forecast_keeps_a_dead_vendors_lead_time`` and
    ``test_an_item_whose_only_supplier_died_reaches_the_report_and_the_digest``
    — and both fail if it is turned back into a filter. Read AGENTS.md's
    "The alert-suppression class" before changing anything here.

    Query budget: one for :func:`select_suppliers_for` (zero when the caller
    prefetched ``item_suppliers``), one ``LeadTimeLog`` aggregate covering both
    branches, and one ``ItemSupplier`` scan taken ONLY when some item has links
    but none orderable.
    """
    # Imported lazily so this module has no hard import-time dependency on the
    # reorder_queue app (mirrors how the rest of inventory references it).
    from reorder_queue.models import LeadTimeLog

    choices = select_suppliers_for(items)

    chosen_link_ids: list[Any] = []
    unorderable_item_ids: list[Any] = []
    for item in items:
        choice = choices.get(item.id)
        if choice is None:
            continue
        if choice.item_supplier is not None:
            chosen_link_ids.append(choice.item_supplier.id)
        elif choice.reason == NONE_ORDERABLE:
            unorderable_item_ids.append(item.id)

    # ONE observed-mean query serving both branches. Grouping by ITEM while
    # restricting the rows differently per branch is what lets them share it:
    # a chosen-link item contributes only that link's deliveries, an item with
    # nothing orderable contributes every link's — which is the pre-op-3vqk
    # expression, unchanged, for exactly the population that still needs it.
    predicates = []
    if chosen_link_ids:
        predicates.append(Q(item_supplier_id__in=chosen_link_ids))
    if unorderable_item_ids:
        predicates.append(Q(item_supplier__item_id__in=unorderable_item_ids))

    observed: dict[Any, Any] = {}
    if predicates:
        predicate = predicates[0]
        for extra in predicates[1:]:
            predicate |= extra
        observed = {
            row["item_supplier__item_id"]: row["avg"]
            for row in (
                LeadTimeLog.objects.filter(predicate)
                .values("item_supplier__item_id")
                .annotate(avg=Avg("actual_lead_time_days"))
            )
        }

    # Estimated fallback for the unbuyable population only: the flagged-primary
    # link's ``average_lead_time``, or any link's if none is flagged. Carried
    # over verbatim, ordering included, so an item whose every vendor died keeps
    # exactly the number it had. The chosen-link population needs no query at
    # all — the derivation already handed us the row.
    estimated_unorderable: dict[Any, Any] = {}
    if unorderable_item_ids:
        for row in (
            ItemSupplier.objects.filter(item_id__in=unorderable_item_ids)
            .order_by("item_id", "-is_primary")
            .values("item_id", "average_lead_time")
        ):
            estimated_unorderable.setdefault(row["item_id"], row["average_lead_time"])

    resolved: dict[Any, LeadTime] = {}
    for item in items:
        choice = choices.get(item.id)
        link = choice.item_supplier if choice is not None else None
        if link is not None:
            basis = LEAD_TIME_FROM_ORDERABLE
            estimate = link.average_lead_time
        elif choice is not None and choice.reason == NONE_ORDERABLE:
            basis = LEAD_TIME_FROM_UNORDERABLE
            estimate = estimated_unorderable.get(item.id)
        else:
            resolved[item.id] = _NO_LEAD_TIME
            continue

        observed_mean = observed.get(item.id)
        if observed_mean is not None:
            days: Optional[float] = float(observed_mean)
        elif estimate is not None:
            days = float(estimate)
        else:
            # Unreachable while ``average_lead_time`` is non-nullable with a
            # default: a link always supplies an estimate. Spelled out anyway so
            # that if the column ever becomes nullable the honest answer is "we
            # have a supplier and no number for it" — NOT the ``no_supplier``
            # basis, which is a different fact and a different operator action.
            days = None
        resolved[item.id] = LeadTime(days=days, basis=basis)
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
    lead_time_by_item = lead_times_for(items)

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

        lead_time = lead_time_by_item.get(item.id, _NO_LEAD_TIME)
        lead_time_days = lead_time.days
        safety_stock = item.minimum_stock or 0
        # An UNKNOWN lead time is not a zero-day one (op-c1ke). Spelled
        # ``is None`` rather than ``or 0`` so a genuine zero-day wait — a local
        # vendor you collect from the same afternoon — takes the arithmetic
        # branch instead of the "we were never told" one. The two produce the
        # same lead component today; they are different facts, and a guard
        # written with ``or`` cannot keep them apart.
        lead_time_known = lead_time.known
        lead_component = avg_daily_use * lead_time_days if lead_time_known else 0.0
        # With no lead time the reorder point is a LOWER BOUND — the operator's
        # own safety stock, with the lead component missing rather than
        # fabricated at zero. ``lead_time_known`` says so on the row so no
        # consumer reads the number as complete. The flag is deliberately NOT
        # widened here: the whole population reaching this branch is items with
        # no supplier link at all, and flagging that population regardless of
        # what a lead time would have said turns a DATA GAP into a permanent
        # alert — the failure mode AGENTS.md records from op-2rsp round 4. The
        # actionable fact for those items is the missing supplier, and
        # ``lead_time_days: null`` is what says it.
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
                "lead_time_days": round(lead_time_days, 1) if lead_time_known else None,
                # Whether ``reorder_point`` includes a lead component at all.
                # ``False`` means the number below is the safety stock alone —
                # a lower bound, not the classic reorder point this module's
                # docstring describes (op-c1ke).
                "lead_time_known": lead_time_known,
                # WHOSE wait ``lead_time_days`` describes (op-3vqk). An
                # ``unorderable_supplier`` row is NOT incomplete — the lead
                # component is there and the item is judged on it — but the
                # vendor behind the number cannot be bought from, which is a
                # different thing to tell an operator than either a live
                # supplier or a missing one. See :func:`lead_times_for`.
                "lead_time_basis": lead_time.basis,
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
