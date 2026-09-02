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
       cost, lead time and delivery record, and the best scoring one wins.

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

**Every term starts at its full weight and is DISCOUNTED by evidence against
the candidate.** That one sentence is the arithmetic of :func:`score_candidate`
and it is what makes "a gap in the data must not be punished" precise:

* Cost starts at ``COST_WEIGHT`` and is discounted by how far above the item's
  average orderable price this vendor quotes, reaching zero at the
  ``COST_TOLERANCE`` cliff. A vendor at or below that average is not dear, so
  nothing is discounted.
* Lead time starts at ``LEAD_TIME_WEIGHT`` and is discounted by how much of the
  ``MAX_REASONABLE_LEAD_DAYS`` horizon the vendor's quoted wait consumes.
* Performance starts at ``PERFORMANCE_WEIGHT`` and is discounted by the share of
  this link's recorded deliveries that arrived LATE.

An absent value is therefore not a bad value: it is an absence of evidence, and
there is nothing to discount for. **An unpriced candidate scores on cost exactly
as one priced at the item's average orderable price does, and a candidate with
no recorded delivery scores on performance exactly as one that has never been
late does.** Neither is rewarded — every factor is bounded at 1, so no candidate
can score above its term's full weight and an unknown can never BEAT a known
value on that axis — and neither is penalised, because the term is not skipped.
The consequence is deliberate and is a behaviour change: an unpriced supplier
now outscores, on the cost axis, a rival priced ABOVE the item's average, just
as a supplier quoting exactly that average does.

**Where the choice turned on an absence, it is said out loud.**
:class:`SupplierChoice` carries ``scored_without_price`` and
``scored_without_history`` for the winner, and ``/items/{id}/metrics/`` — the
payload the web item-detail row and ScanTTY both read — carries them onto the
wire. An operator seeing a blank cost should not have to infer whether the
system knew the price and chose anyway.

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

from django.db.models import Count, IntegerField, OuterRef, Prefetch, Q, Subquery, Value
from django.db.models.functions import Coalesce

from inventory.models import ItemSupplier
from inventory.services.pricing import unit_price_of

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
# **These are the weights the code applies.** Each term's factor is bounded to
# ``[0, 1]`` — cost by an explicit clamp at both ends, lead time because
# ``average_lead_time`` is a non-negative column and the horizon is the divisor,
# performance because it is a share of a count of itself — so each contributes
# AT MOST its weight and a candidate's total score lies in ``[0, 0.8]``.
# ``test_no_candidate_can_score_above_the_sum_of_the_stated_weights`` is the
# check. The three weights sum to 0.8 rather than 1.0
# because the rival rule this came from also carried a ``+0.2`` primary-supplier
# term; that term is gone — a flagged primary is a GATE, not points — and the
# remaining weights were deliberately not rescaled, because scaling every score
# by the same constant cannot change an ordering and rescaling would silently
# retune the cost/lead-time trade-off these tests pin.
#
# Two shape decisions a reader should know, both now stated truthfully:
#
# * ``COST_TOLERANCE`` makes the cost term a CLIFF, not a curve. A candidate
#   priced at or above 150% of the item's average orderable price scores 0 on
#   cost, and everything past that point is equally bad. The other end IS
#   clamped: at or below the average the factor is 1, so the term can never
#   exceed ``COST_WEIGHT``. It is the clamp that makes "an unpriced candidate is
#   not rewarded" true — without it a bargain could outbid the full weight an
#   unknown price earns, and awarding the full weight WOULD be a thumb on the
#   scale. Retuning where the cliff sits stays a product question.
# * ``PERFORMANCE_WEIGHT`` is a real 10% driven by ``LeadTimeLog`` (see
#   :func:`delivery_records_for`). It was a constant ``0.01`` added to every
#   candidate alike — inert, and 1% while its comment claimed 10%.
COST_WEIGHT = Decimal("0.4")
LEAD_TIME_WEIGHT = Decimal("0.3")
PERFORMANCE_WEIGHT = Decimal("0.1")

#: Percentage points above the item's average orderable unit cost at which the
#: cost term reaches zero.
COST_TOLERANCE = Decimal("50")

#: Lead time (days) at which the lead-time term reaches zero.
MAX_REASONABLE_LEAD_DAYS = Decimal("30")


@dataclass(frozen=True)
class DeliveryRecord:
    """One link's delivery history, as the two counts the score needs.

    ``total`` is how many :class:`~reorder_queue.models.LeadTimeLog` rows this
    link has; ``on_time`` is how many of them arrived no later than the date the
    order promised (``variance_days <= 0``). Both are counts of ROWS, so
    ``total == 0`` means "nothing has ever been delivered through this link",
    which is an absence of evidence and not a bad record — see :attr:`factor`.
    """

    on_time: int = 0
    total: int = 0

    @property
    def has_history(self) -> bool:
        """``True`` when at least one delivery has been recorded for this link."""
        return self.total > 0

    @property
    def factor(self) -> Decimal:
        """The share of recorded deliveries that were not late, in ``[0, 1]``.

        ``1`` when there is no history at all. That is the "do not punish the
        gap" rule applied to the delivery record, and it follows from what the
        term MEANS: ``average_lead_time`` is the wait this vendor promises and
        the lead-time term already scores that promise, so this term exists only
        to discount the promise by how often the vendor has broken it. A link
        nobody has ever ordered through has broken nothing, so there is nothing
        to discount — exactly as an unpriced candidate has no premium to discount
        for. Scoring it any lower would be discounting a promise for lack of
        evidence, which is punishing the gap.

        The arithmetic is a plain unweighted share: **every recorded delivery
        counts the same, however old**, and **arriving early counts exactly as
        arriving on time, not better**. Both are decisions, not oversights.
        Recency weighting would introduce a decay constant nobody has data to
        set, over a handful of orders a year per link, and would make the number
        that decides a purchase computed a different WAY from the unweighted
        ``variance_days <= 0`` share the supplier screen's ``on_time_percentage``
        already reports (that one is per supplier where this is per link, so the
        two are different numbers; they should at least be the same
        arithmetic). Paying extra for earliness would pay
        twice for the same fact: ``average_lead_time`` is the vendor's OWN
        per-link quoted promise, operator-entered and maintained per link, so a
        reliably quick vendor already collects its speed on the LEAD-TIME axis. The known cost
        of the unweighted share is that a vendor who was chronically late years
        ago carries it forever; the remedy for that is a window on the query, not
        a decay, and it is filed rather than guessed at here.
        """
        if not self.has_history:
            return Decimal(1)
        return Decimal(self.on_time) / Decimal(self.total)


#: The answer for a link with nothing delivered through it yet. Shared so the
#: common case allocates nothing.
NO_DELIVERY_HISTORY = DeliveryRecord()


@dataclass(frozen=True)
class SupplierChoice:
    """Which supplier to buy an item from, and why that one — or why none.

    Exactly one of ``item_supplier`` / ``reason`` is set. ``basis`` says which
    rule produced a choice, so a caller can tell an operator "you flagged this"
    apart from "the system picked this for you"; ``flagged_primary_unorderable``
    marks the case where an operator DID flag one and it was skipped because it
    cannot be bought from, which reads to them as their choice being ignored
    unless it is said out loud.

    ``scored_without_price`` and ``scored_without_history`` are the same
    courtesy for the two gaps the SCORING is allowed to shrug off: the winner
    carries no ``unit_cost``, or no delivery has ever been recorded through it,
    and it won anyway because neither gap is punished. Both are ``False`` under
    the gate — a flagged primary is not weighed against anything, so its choice
    did not turn on what we do or do not know about it — and both describe the
    WINNER, not the field.
    """

    item_supplier: Optional[ItemSupplier] = None
    basis: Optional[str] = None
    reason: Optional[str] = None
    flagged_primary_unorderable: bool = False
    scored_without_price: bool = False
    scored_without_history: bool = False

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


def cost_factor(link: ItemSupplier, average_unit_cost: Optional[Decimal]) -> Decimal:
    """How much of :data:`COST_WEIGHT` this candidate keeps, in ``[0, 1]``.

    ``1`` means "nothing to discount": the vendor quotes at or below the item's
    average orderable price, **or nobody has recorded what it quotes at all**.
    The factor falls to ``0`` at the :data:`COST_TOLERANCE` cliff, 150% of that
    average.

    The price is read through :func:`inventory.services.pricing.unit_price_of`,
    the ONE reading of ``unit_cost`` (op-9m2v), so a recorded ``0.00`` — donated
    stock, a vendor sample, an internal transfer — is the KNOWN price it is and
    lands at the cheap end of the curve. The guard here used to be
    ``if link.unit_cost``, which read free as unpriced while
    :func:`average_orderable_unit_cost` went on counting that ``0.00`` in the
    yardstick its rivals were measured against: the same row treated as two
    contradictory things at once, and the best possible price graded as the
    worst.

    Three separate reasons the curve cannot be evaluated, all of which mean the
    same thing — no evidence of a premium — and all of which therefore keep the
    full weight:

    * the candidate records no price;
    * ``average_unit_cost`` is ``None`` because no candidate records one, so
      there is no yardstick to measure against;
    * ``average_unit_cost`` is ``0.00``. That is a KNOWN average and not an
      absent one (it means every priced candidate is free), but it is an
      unusable DIVISOR. It is handled by asking the question the division would
      have answered — is this candidate dearer than its peers? — which for a
      free peer group is only true of a candidate that charges something.

    That last branch is not hypothetical and not optional: two donated links on
    one item give an average of exactly ``0.00``, and the truthiness guard this
    replaces was the only thing standing between that item and a
    ``ZeroDivisionError``.
    """
    price = unit_price_of(link)
    if not price.is_known or average_unit_cost is None:
        return Decimal(1)
    if average_unit_cost == 0:
        return Decimal(1) if price.amount == 0 else Decimal(0)
    relative = (price.amount / average_unit_cost - 1) * 100
    return min(Decimal(1), max(Decimal(0), (COST_TOLERANCE - relative) / COST_TOLERANCE))


def lead_time_factor(link: ItemSupplier) -> Decimal:
    """How much of :data:`LEAD_TIME_WEIGHT` this candidate keeps, in ``[0, 1]``.

    ``1`` for a same-day supplier, falling linearly to ``0`` at
    :data:`MAX_REASONABLE_LEAD_DAYS`.

    **A lead time of 0 days is a KNOWN lead time.** The guard here used to be
    ``if link.average_lead_time``, and 0 is falsy, so a vendor you can walk to
    today earned NOTHING on speed while a next-day one earned nearly the whole
    weight — the best possible lead time graded as the worst. There is no guard
    now because there is nothing to guard: ``ItemSupplier.average_lead_time`` is
    a non-null ``PositiveIntegerField``, so the value is always known and always
    at least zero, which is exactly why reading 0 as "unknown" was wrong.

    (The column's ``default=7`` does collapse "never measured" into "measured at
    seven days", but that is a schema-level absence this module cannot see and
    must not guess at. Reported, not fixed: it needs a nullable column and a
    decision about what an unmeasured lead time should score.)
    """
    return max(
        Decimal(0),
        (MAX_REASONABLE_LEAD_DAYS - Decimal(link.average_lead_time)) / MAX_REASONABLE_LEAD_DAYS,
    )


#: Annotation aliases carrying one link's delivery record on the row itself.
#: Underscored because they are this module's private wiring, not part of
#: ``ItemSupplier``'s public shape.
DELIVERIES_TOTAL = "_deliveries_total"
DELIVERIES_ON_TIME = "_deliveries_on_time"


def _delivery_count(*, on_time_only: bool) -> Subquery:
    """A correlated ``COUNT`` of one link's ``LeadTimeLog`` rows, as a subquery."""
    # Imported here rather than at module scope: ``reorder_queue.models``
    # imports ``inventory.models``, and inventory must not depend on the
    # reorder-queue app at import time to resolve its own supplier question.
    from reorder_queue.models import LeadTimeLog

    logs = LeadTimeLog.objects.filter(item_supplier=OuterRef("pk"))
    if on_time_only:
        # ``<= 0``, not ``< 0``: a variance of exactly zero is a delivery that
        # landed on the day the order promised — the best outcome this column
        # can express, and one more known value a falsy guard would have read as
        # an absence. Early is as good as on time, not better; see
        # :attr:`DeliveryRecord.factor`.
        logs = logs.filter(variance_days__lte=0)
    return Coalesce(
        Subquery(
            # ``order_by()`` clears ``Meta.ordering`` so it cannot contaminate
            # the GROUP BY and split the count across delivery dates.
            logs.order_by().values("item_supplier").annotate(n=Count("id")).values("n")[:1],
            output_field=IntegerField(),
        ),
        Value(0),
    )


def delivery_record_annotations() -> Dict[str, Subquery]:
    """The two annotations that put a link's delivery record on the row.

    Applied to every ``ItemSupplier`` queryset this module builds, and to the
    prefetch :func:`item_suppliers_prefetch` hands the read paths, so the
    delivery half of the score arrives in the SAME round-trip as the rows —
    never as a query per candidate, and never as a page of log rows pulled into
    memory only to be counted.
    """
    return {
        DELIVERIES_TOTAL: _delivery_count(on_time_only=False),
        DELIVERIES_ON_TIME: _delivery_count(on_time_only=True),
    }


def item_suppliers_prefetch(lookup: str = "item_suppliers") -> Prefetch:
    """The ``item_suppliers`` prefetch a read path should use, in one place.

    Replaces the bare ``"item_suppliers__supplier"`` string every list and
    detail queryset used to carry. It pulls the same rows with the same
    ``select_related("supplier")`` and the same ``Meta.ordering``, and adds the
    two delivery-record annotations, so serialising a page still costs ZERO
    queries here — the property #882 established and the reason this is a
    prefetch rather than a per-row lookup.

    ``lookup`` is the path to the relation, so a queryset over some OTHER model
    that reaches items can pass ``"item__item_suppliers"``.
    """
    return Prefetch(
        lookup,
        queryset=ItemSupplier.objects.select_related("supplier").annotate(
            **delivery_record_annotations()
        ),
    )


def delivery_records_for(links: List[ItemSupplier]) -> Dict[int, DeliveryRecord]:
    """``{link_id: DeliveryRecord}`` for ``links`` — the delivery half of the score.

    "Performance" is defined here, in one sentence: **the share of the
    deliveries recorded against THIS supplier link that arrived no later than
    the order promised.** ``reorder_queue.models.LeadTimeLog`` is the record and
    ``variance_days`` (``actual_lead_time_days - estimated_lead_time_days``,
    positive = late) is the column that says so, read as ``<= 0`` because a
    variance of exactly ``0`` is a delivery that landed on the promised day.

    **Per LINK, not per supplier.** A supplier-wide rate over
    ``item_supplier__supplier`` is the broader sample, and is what the supplier
    screen's ``on_time_percentage`` reports, but it cannot be reached from an
    item without pulling every log of every item that vendor carries. The
    per-link record is also the more specific evidence: how this vendor has done
    on THIS item is the question a purchase asks.

    Query budget, matching the rest of the module: rows carrying the
    :func:`delivery_record_annotations` — every row this module fetches, and
    every row a caller prefetched through :func:`item_suppliers_prefetch` — are
    read straight off the row and cost NOTHING. Anything left over (a caller
    still prefetching the bare string) is resolved in ONE grouped aggregate for
    the whole set, never one query per candidate. A link with no rows is absent
    from that aggregate and gets :data:`NO_DELIVERY_HISTORY`, which is the same
    answer as "we did not look" — see :attr:`DeliveryRecord.factor`.
    """
    from reorder_queue.models import LeadTimeLog

    records: Dict[int, DeliveryRecord] = {}
    unresolved: List[int] = []
    for link in links:
        if hasattr(link, DELIVERIES_TOTAL):
            records[link.pk] = DeliveryRecord(
                on_time=getattr(link, DELIVERIES_ON_TIME),
                total=getattr(link, DELIVERIES_TOTAL),
            )
        else:
            unresolved.append(link.pk)

    if unresolved:
        counted = {
            row["item_supplier"]: DeliveryRecord(on_time=row["on_time"], total=row["total"])
            for row in (
                LeadTimeLog.objects.filter(item_supplier_id__in=unresolved)
                .values("item_supplier")
                .annotate(
                    total=Count("id"),
                    on_time=Count("id", filter=Q(variance_days__lte=0)),
                )
                .order_by()  # clear Meta ordering so it can't contaminate GROUP BY
            )
        }
        for link_id in unresolved:
            records[link_id] = counted.get(link_id, NO_DELIVERY_HISTORY)
    return records


def score_candidate(
    link: ItemSupplier,
    average_unit_cost: Optional[Decimal],
    record: Optional[DeliveryRecord] = None,
) -> Decimal:
    """Score one ORDERABLE, UNFLAGGED candidate. Higher is better.

    Three terms, each starting at its full weight and discounted only by
    evidence against this candidate, so the total lies in ``[0, 0.8]``:
    :func:`cost_factor` × :data:`COST_WEIGHT`, :func:`lead_time_factor` ×
    :data:`LEAD_TIME_WEIGHT`, and the delivery record's
    :attr:`DeliveryRecord.factor` × :data:`PERFORMANCE_WEIGHT`.

    ``record`` omitted means "no delivery history was looked up", which scores
    the same as looking it up and finding none — :data:`NO_DELIVERY_HISTORY`,
    the full performance weight. :func:`_best_scored` always passes one;
    the default exists so a caller reasoning about cost and lead time alone gets
    the same answer for every candidate on the axis it did not ask about.

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
    if record is None:
        record = NO_DELIVERY_HISTORY
    return (
        cost_factor(link, average_unit_cost) * COST_WEIGHT
        + lead_time_factor(link) * LEAD_TIME_WEIGHT
        + record.factor * PERFORMANCE_WEIGHT
    )


def _best_scored(
    candidates: List[ItemSupplier],
    records: Optional[Dict[int, DeliveryRecord]] = None,
) -> ItemSupplier:
    """Highest-scoring candidate; a tie goes to the FIRST one in ``candidates``.

    ``candidates`` arrives in ``Meta.ordering`` and ``max`` returns the first
    maximal element, so the answer is a pure function of that order.

    ``records`` is :func:`delivery_records_for`'s map, resolved here when the
    caller has not already resolved it for a whole page.

    Ties are now REACHABLE between differently-priced rows, which they were not
    before: the cost factor is clamped at 1, so every candidate at or below the
    item's average orderable price earns the identical full weight, and an
    unpriced candidate earns it too. Those rows are separated by lead time and
    delivery record; when those match as well there is genuinely nothing to
    choose between them and the first one wins. That order is
    ``Meta.ordering`` — ``-is_primary``, then ``unit_cost`` ascending with SQL's
    NULLs last — so a dead tie between a priced candidate and an unpriced one
    goes to the priced one, and a dead tie between two priced ones goes to the
    cheaper. Stable within a query, and deliberately not claimed to be more than
    that.
    """
    average = average_orderable_unit_cost(candidates)
    if records is None:
        records = delivery_records_for(candidates)
    return max(
        candidates,
        key=lambda link: score_candidate(link, average, records.get(link.pk)),
    )


def _scored_candidates(links: List[ItemSupplier]) -> List[ItemSupplier]:
    """The rows :func:`score_candidate` would actually rank, or ``[]``.

    Empty whenever scoring does not run: no links, nothing orderable, or an
    orderable link is flagged and takes the gate. It exists so
    :func:`select_suppliers_for` can pre-resolve exactly the delivery history
    the scoring is about to read, for a whole page at once, without keeping a
    second copy of the gate that could drift from :func:`_choose`'s.
    """
    candidates = [link for link in links if _orderable(link)]
    if any(link.is_primary for link in candidates):
        return []
    return candidates


def _choose(
    links: List[ItemSupplier],
    records: Optional[Dict[int, DeliveryRecord]] = None,
) -> SupplierChoice:
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

    ``records`` is :func:`delivery_records_for`'s map, passed in by
    :func:`select_suppliers_for` so a whole page resolves its delivery history
    in one aggregate; resolved here for a single item otherwise, and not at all
    when the gate fires or nothing is orderable.
    """
    if not links:
        return SupplierChoice(reason=NO_SUPPLIERS)

    flagged_exists = any(link.is_primary for link in links)
    candidates = [link for link in links if _orderable(link)]
    if not candidates:
        return SupplierChoice(reason=NONE_ORDERABLE, flagged_primary_unorderable=flagged_exists)

    # 1. The gate. ``enforce_single_primary`` keeps at most one flagged per item,
    #    and the rows arrive primary-first, so this is the first candidate if any.
    #    Nothing is looked up for it: the gate weighs nothing, so no gap in what
    #    we know about it can have decided anything.
    for link in candidates:
        if link.is_primary:
            return SupplierChoice(item_supplier=link, basis=BASIS_FLAGGED_PRIMARY)

    # 2. The score, over candidates none of which is flagged. The delivery
    #    records are resolved ONCE for the whole candidate set — off the row
    #    annotations every queryset here carries, else in a single grouped
    #    aggregate — and never once per candidate.
    if records is None:
        records = delivery_records_for(candidates)
    winner = _best_scored(candidates, records)
    return SupplierChoice(
        item_supplier=winner,
        basis=BASIS_BEST_SCORED,
        flagged_primary_unorderable=flagged_exists,
        # Said out loud rather than left to be inferred from a blank cost cell:
        # the winner won WITHOUT one of these, because neither gap is punished.
        scored_without_price=not unit_price_of(winner).is_known,
        scored_without_history=not records.get(winner.pk, NO_DELIVERY_HISTORY).has_history,
    )


def _prefetched_links(item) -> Optional[List[ItemSupplier]]:
    """This item's rows from an ``item_suppliers`` prefetch, or ``None`` if unset."""
    prefetched = getattr(item, "_prefetched_objects_cache", None) or {}
    if "item_suppliers" in prefetched:
        return list(item.item_suppliers.all())
    return None


def _links_for(item) -> List[ItemSupplier]:
    """This item's supplier rows in ``Meta.ordering``, riding a prefetch if set.

    The delivery-record annotations ride the SAME round-trip as the rows, so an
    unprefetched read still costs exactly one query — the performance term added
    no round-trip to this path.
    """
    prefetched = _prefetched_links(item)
    if prefetched is not None:
        return prefetched
    return list(
        item.item_suppliers.select_related("supplier")
        .annotate(**delivery_record_annotations())
        .all()
    )


def select_supplier(item) -> SupplierChoice:
    """Return the :class:`SupplierChoice` for ``item`` — the row AND the reason.

    When ``item`` was loaded with ``prefetch_related("item_suppliers")`` this
    reads the prefetch cache and costs ZERO queries for the rows; otherwise it
    runs a single query, pulling ``supplier`` in the same round-trip so a
    downstream ``.supplier`` access does not add another.

    The delivery records behind the performance term ride that same query as
    annotations, so scoring adds no round-trip of its own — and a caller that
    prefetched through :func:`item_suppliers_prefetch` still pays nothing at
    all. Reading a PAGE should go through :func:`select_suppliers_for`.
    """
    return _choose(_links_for(item))


def select_suppliers_for(items: Iterable) -> Dict:
    """Return ``{item_id: SupplierChoice}`` for ``items`` in ONE query.

    ``items`` is an iterable of :class:`~inventory.models.InventoryItem`
    instances (typically a single paginated page). Every item id is present in
    the result — a :class:`SupplierChoice` carrying :data:`NO_SUPPLIERS` where
    the item has no links — so callers can index the map directly.

    Items that already carry an ``item_suppliers`` prefetch are resolved from
    that cache and cost NOTHING — the list endpoints that annotate a page with
    metrics prefetch it, so re-querying identical rows here would be a wasted
    round-trip per page. Only the remainder is fetched, in ONE
    ``select_related("supplier")`` query ordered by ``item`` then the selection
    ordering, so each item's rows arrive together and in the same order
    :func:`select_supplier` sees them.

    Every candidate row is pulled, not just the orderable ones: "none orderable"
    can only be told apart from "no suppliers" by seeing the rows that were
    rejected.

    **Delivery history rides the same round-trip**, as two annotations on the
    supplier rows themselves (:func:`delivery_record_annotations`), so the
    performance term costs no query of its own. A caller whose prefetch predates
    :func:`item_suppliers_prefetch` — the bare ``"item_suppliers__supplier"``
    string — has rows without those annotations, and pays ONE grouped aggregate
    for the whole page rather than one per item.
    """
    items = list(items)
    if not items:
        return {}

    grouped: Dict = {}
    pending: Dict = {}
    for item in items:
        prefetched = _prefetched_links(item)
        if prefetched is not None:
            pending[item.id] = prefetched
        else:
            grouped[item.id] = []

    if grouped:
        for link in (
            ItemSupplier.objects.filter(item_id__in=list(grouped))
            .select_related("supplier")
            .annotate(**delivery_record_annotations())
            .order_by("item", "-is_primary", "unit_cost")
        ):
            grouped[link.item_id].append(link)
        pending.update(grouped)

    # One delivery-history lookup for every row the page is going to score.
    scored = [link for links in pending.values() for link in _scored_candidates(links)]
    records = delivery_records_for(scored) if scored else {}
    return {item_id: _choose(links, records) for item_id, links in pending.items()}


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
    """``{item_id: orderable ItemSupplier | None}`` for ``items``, batched.

    The row half of :func:`select_suppliers_for`, mirroring
    :func:`primary_item_supplier`'s relationship to :func:`select_supplier`.
    """
    return {
        item_id: choice.item_supplier for item_id, choice in select_suppliers_for(items).items()
    }
