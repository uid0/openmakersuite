"""The single derivation of "which supplier do we buy this item from?" (#882, op-2rsp).

Historically ``InventoryItem`` exposed its "primary supplier" through a cluster
of model properties (``supplier``, ``supplier_sku``, ``unit_cost``,
``average_lead_time`` …) that each ran a hidden
``item_suppliers.filter(is_primary=True).first()`` query. Reading them across a
page of items was therefore an N+1, and the selection logic was buried in the
model rather than named anywhere. #882 named it here.

op-2rsp made it the ONLY answer. The rule is three things in strict order:

    1. **Eligibility.** Only ORDERABLE links are candidates — ``is_active`` and
       not ``is_discontinued``.
    2. **The gate.** If an operator flagged one of those primary, it wins
       outright and scoring never runs.
    3. **The score.** Otherwise :func:`score_candidate` ranks the candidates on
       cost and lead time, and the best scoring one wins.

**Orderability is not a tiebreak, it is a precondition.** An inactive or
discontinued link is a supplier nobody can buy from; handing one to a purchase
order, an order pad, or a lead-time estimate presents an unbuyable option as the
one to buy. The item-detail page already dims those rows so they do not read as
actionable — this module is why that dimming is not contradicted one screen
later. ``mark_discontinued`` deliberately does not clear ``is_primary``, so even
a FLAGGED primary can be unorderable and gets skipped like any other.

**A flagged primary is a GATE, not a term in the sum.** It does not earn points
that a cheap enough rival could outbid — that would make an operator's explicit
choice merely expensive rather than binding. It short-circuits the ranking
entirely. The scoring therefore carries no primary-supplier term at all: under
the gate, no scored candidate is ever flagged.

**A refusal is a result, not a blank.** ``select_supplier`` distinguishes "this
item has no suppliers at all" from "it has suppliers but none you can buy from",
because those need different words in front of an operator and different actions
from them. Callers that only need the row keep using
:func:`primary_item_supplier`, which is that result with the reason dropped.

**History, because it bears on trusting this.** The scoring below came from
``reorder_queue.views.PurchaseOrderViewSet._find_best_supplier``, which was a
second, rival answer to this same question — it filtered orderability but ranked
by weighted score where every other surface ranked by price. It also raised
``TypeError`` on ``Decimal * float`` for any candidate priced below 150% of the
item's average, so it had **never once completed** in production; the one test
that touched it set ``unit_cost=None`` to route around the crash. Adopting it
here (the captain's decision) therefore turns on a rule that has never run, and
changes which supplier the whole system chooses for every item with no flagged
primary. That is a real behaviour change on member-facing surfaces, and on
``/metrics/``, which ScanTTY reads.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Iterable, List, Optional

from inventory.models import ItemSupplier

#: No supplier link exists for this item at all — nobody has said where it
#: comes from. The operator's action is to add one.
NO_SUPPLIERS = "no_suppliers"

#: Supplier links exist, but every one is inactive or discontinued. The item is
#: described but not buyable. The operator's action is to reactivate a link or
#: add a supplier that still carries it. Distinct from :data:`NO_SUPPLIERS` on
#: purpose: "we were never told" and "we were told, and the answer is no" are
#: different facts and must not collapse into the same silence.
NONE_ORDERABLE = "none_orderable"

#: An operator flagged this link primary and it is orderable, so it won the GATE
#: and scoring never ran.
BASIS_FLAGGED_PRIMARY = "flagged_primary"

#: Nothing orderable was flagged primary, so :func:`score_candidate` ranked the
#: orderable candidates and this one scored highest.
BASIS_BEST_SCORED = "best_scored"

# ── Scoring weights ──────────────────────────────────────────────────────────
#
# Carried over UNCHANGED from ``reorder_queue.views``'s ``_find_best_supplier``,
# which is where this scoring lived while it was one of two rival rules. The
# captain authorised repairing the scoring and adopting it everywhere, NOT
# retuning it — what cost or lead time is worth is a separate product question.
# Two things about these weights are worth a reader's attention; both are
# reported rather than changed:
#
# * ``COST_TOLERANCE`` makes the cost term a CLIFF, not a curve. A candidate
#   priced at or above 150% of the item's average orderable price scores 0 on
#   cost, and everything past that point is equally bad. Below average the
#   factor is NOT clamped at 1 either, so a candidate far cheaper than its peers
#   can contribute more than the nominal 40%.
# * ``PERFORMANCE_FACTOR * PERFORMANCE_WEIGHT`` is 0.01 — a constant added to
#   every candidate alike, so it cannot affect an ordering. The comment it
#   carried called it a "10% weight"; it is 1%, and inert either way until the
#   ``LeadTimeLog``-driven version it is a placeholder for exists.
COST_WEIGHT = Decimal("0.4")
LEAD_TIME_WEIGHT = Decimal("0.3")
PERFORMANCE_WEIGHT = Decimal("0.1")
PERFORMANCE_FACTOR = Decimal("0.1")

#: Percentage points above the item's average orderable unit cost at which the
#: cost term reaches zero.
COST_TOLERANCE = Decimal("50")

#: Lead time (days) at which the lead-time term reaches zero.
MAX_REASONABLE_LEAD_DAYS = Decimal("30")


@dataclass(frozen=True)
class SupplierChoice:
    """Which supplier to buy an item from, and why that one — or why none.

    Exactly one of ``item_supplier`` / ``reason`` is set. ``basis`` says which
    rule produced a choice, so a caller can tell an operator "you flagged this"
    apart from "the system picked this for you"; ``flagged_primary_unorderable``
    marks the case where an operator DID flag one and it was skipped because it
    cannot be bought from, which reads to them as their choice being ignored
    unless it is said out loud.
    """

    item_supplier: Optional[ItemSupplier] = None
    basis: Optional[str] = None
    reason: Optional[str] = None
    flagged_primary_unorderable: bool = False

    def __bool__(self) -> bool:
        return self.item_supplier is not None


def _orderable(link: ItemSupplier) -> bool:
    """Can anything actually be bought through this link right now?"""
    return link.is_active and not link.is_discontinued


def average_orderable_unit_cost(candidates: List[ItemSupplier]) -> Optional[Decimal]:
    """Mean ``unit_cost`` across ``candidates`` that have one, or ``None``.

    The yardstick the cost term measures each candidate against. Computed in
    Python from rows the caller already holds rather than with a ``Avg()``
    aggregate, because the aggregate cost one query PER ITEM — inside the
    scoring loop, at that — and would bypass the ``item_suppliers`` prefetch
    every read path relies on. Rows with no price are excluded from the mean,
    exactly as SQL ``AVG`` skips NULLs.
    """
    costs = [link.unit_cost for link in candidates if link.unit_cost is not None]
    if not costs:
        return None
    return sum(costs) / Decimal(len(costs))


def score_candidate(link: ItemSupplier, average_unit_cost: Optional[Decimal]) -> Decimal:
    """Score one ORDERABLE, UNFLAGGED candidate. Higher is better.

    Decimal throughout. The original raised ``TypeError`` on
    ``Decimal * float`` for any candidate priced below 150% of the item's
    average — which is nearly all of them, and always so for a single-supplier
    item — so this scoring had never once completed in production. Money is
    Decimal; mixing in binary floats was the bug, and casting to ``float`` would
    have been the wrong repair.

    A flagged primary is NOT scored. It is a gate: an orderable link an operator
    flagged wins outright in :func:`_choose` and never reaches here, so the
    operator's explicit choice is binding rather than merely worth some number
    of points that a cheap enough rival could outbid. The scoring accordingly
    has no primary-supplier term — under the gate it would be unreachable.
    """
    score = Decimal(0)

    # Cost (nominal 40%) — cheaper than the item's average orderable price is
    # better. See COST_TOLERANCE on the cliff and the missing upper clamp.
    if link.unit_cost and average_unit_cost:
        relative = (link.unit_cost / average_unit_cost - 1) * 100
        cost_factor = max(Decimal(0), COST_TOLERANCE - relative) / COST_TOLERANCE
        score += cost_factor * COST_WEIGHT

    # Lead time (30%) — sooner is better, flat zero at/after 30 days.
    if link.average_lead_time:
        lead_time_factor = max(
            Decimal(0),
            (MAX_REASONABLE_LEAD_DAYS - Decimal(link.average_lead_time)) / MAX_REASONABLE_LEAD_DAYS,
        )
        score += lead_time_factor * LEAD_TIME_WEIGHT

    # Historical performance — a placeholder constant pending LeadTimeLog-driven
    # scoring. Identical for every candidate, so it shifts no ordering.
    score += PERFORMANCE_FACTOR * PERFORMANCE_WEIGHT

    return score


def _best_scored(candidates: List[ItemSupplier]) -> ItemSupplier:
    """Highest-scoring candidate; a tie goes to the FIRST one in ``candidates``.

    ``candidates`` arrives in ``Meta.ordering`` and ``max`` returns the first
    maximal element, so the answer is a pure function of that order.

    In practice the only reachable tie is between rows identical on price AND
    lead time: the cost yardstick is the mean of the candidates themselves, so
    two of them cannot both sit past the 150% cliff, and any third cheap enough
    to drag the mean down would outscore them. Such rows are interchangeable —
    "the cheaper one" is not a meaningful tie-break between them — and which one
    wins is whichever the database returned first. Stable within a query, and
    deliberately not claimed to be more than that.
    """
    average = average_orderable_unit_cost(candidates)
    return max(candidates, key=lambda link: score_candidate(link, average))


def _choose(links: List[ItemSupplier]) -> SupplierChoice:
    """Resolve one item's ``ItemSupplier`` rows, already in ``Meta.ordering``.

    Two steps, in this order, and the order is the whole point:

    1. **The gate.** An orderable link flagged primary wins outright. Scoring
       never runs. An operator's explicit choice is not a term in a sum that
       something else can outbid.
    2. **The score.** Only when nothing orderable is flagged does
       :func:`score_candidate` rank what is left.

    Filtering happens HERE, in Python, rather than as a ``.filter()`` on the
    queryset: the callers that matter serialise a whole page and rely on the
    ``item_suppliers`` prefetch cache, and a fresh ``.filter()`` would bypass
    that cache and reintroduce the per-row query #882 removed.
    """
    if not links:
        return SupplierChoice(reason=NO_SUPPLIERS)

    flagged_exists = any(link.is_primary for link in links)
    candidates = [link for link in links if _orderable(link)]
    if not candidates:
        return SupplierChoice(reason=NONE_ORDERABLE, flagged_primary_unorderable=flagged_exists)

    # 1. The gate. ``enforce_single_primary`` keeps at most one flagged per item,
    #    and the rows arrive primary-first, so this is the first candidate if any.
    for link in candidates:
        if link.is_primary:
            return SupplierChoice(item_supplier=link, basis=BASIS_FLAGGED_PRIMARY)

    # 2. The score, over candidates none of which is flagged.
    return SupplierChoice(
        item_supplier=_best_scored(candidates),
        basis=BASIS_BEST_SCORED,
        flagged_primary_unorderable=flagged_exists,
    )


def _links_for(item) -> List[ItemSupplier]:
    """This item's supplier rows in ``Meta.ordering``, riding a prefetch if set."""
    prefetched = getattr(item, "_prefetched_objects_cache", None) or {}
    if "item_suppliers" in prefetched:
        return list(item.item_suppliers.all())
    return list(item.item_suppliers.select_related("supplier").all())


def select_supplier(item) -> SupplierChoice:
    """Return the :class:`SupplierChoice` for ``item`` — the row AND the reason.

    When ``item`` was loaded with ``prefetch_related("item_suppliers")`` this
    reads the prefetch cache and costs ZERO queries; otherwise it runs a single
    query, pulling ``supplier`` in the same round-trip so a downstream
    ``.supplier`` access does not add another.
    """
    return _choose(_links_for(item))


def select_suppliers_for(items: Iterable) -> Dict:
    """Return ``{item_id: SupplierChoice}`` for ``items`` in ONE query.

    ``items`` is an iterable of :class:`~inventory.models.InventoryItem`
    instances (typically a single paginated page). Every item id is present in
    the result — a :class:`SupplierChoice` carrying :data:`NO_SUPPLIERS` where
    the item has no links — so callers can index the map directly.

    Every candidate row for the page is pulled, not just the orderable ones:
    "none orderable" can only be told apart from "no suppliers" by seeing the
    rows that were rejected. The batch is one ``select_related("supplier")``
    query ordered by ``item`` then the selection ordering, so each item's rows
    arrive together and in the same order :func:`select_supplier` sees them.
    """
    items = list(items)
    grouped: Dict = {item.id: [] for item in items}
    if not items:
        return {}

    for link in (
        ItemSupplier.objects.filter(item_id__in=list(grouped))
        .select_related("supplier")
        .order_by("item", "-is_primary", "unit_cost")
    ):
        grouped[link.item_id].append(link)
    return {item_id: _choose(links) for item_id, links in grouped.items()}


def primary_item_supplier(item) -> Optional[ItemSupplier]:
    """The orderable :class:`ItemSupplier` to buy ``item`` from, or ``None``.

    The row half of :func:`select_supplier`, for the many callers that only need
    somewhere to read a SKU or a cost from. ``None`` here means "no supplier you
    can buy from" and deliberately does NOT distinguish why — a caller that has
    to explain itself to an operator should use :func:`select_supplier` and read
    the reason.
    """
    return select_supplier(item).item_supplier


def primary_suppliers_for(items: Iterable) -> Dict:
    """``{item_id: orderable ItemSupplier | None}`` for ``items`` in ONE query.

    The row half of :func:`select_suppliers_for`, mirroring
    :func:`primary_item_supplier`'s relationship to :func:`select_supplier`.
    """
    return {
        item_id: choice.item_supplier for item_id, choice in select_suppliers_for(items).items()
    }
