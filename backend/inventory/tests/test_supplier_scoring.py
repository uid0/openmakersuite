"""Does the weighted score CHOOSE WELL? (op-2rsp)

A rule that runs without erroring is not the same as a rule that chooses well,
and this scoring has never once run in production: it raised ``TypeError`` on
``Decimal * float`` for any candidate priced below 150% of the item's average,
which is nearly every real candidate and always so for a single-supplier item.
The single test that reached it set ``unit_cost=None`` on its fixtures
specifically to route around the crash, so no test has ever asserted an outcome
of this scoring. Adopting it is therefore not a reconciliation of two live
rules — it is switching on a rule whose behaviour was unobserved.

So these tests are about JUDGEMENT, on real-shaped catalogue data: a modest
premium buying a large lead-time saving, a large premium buying a small one,
suppliers separated on only one axis, a single-supplier item, missing prices.
``test_supplier_selection.py`` covers the plumbing; this covers the choices.

Where the scoring's judgement is questionable it is PINNED AND NAMED here rather
than quietly corrected — the captain authorised repairing the scoring, not
retuning it, so what cost or lead time is worth stays a product question. FIVE
such findings, each with its own test, all named ``REPORTED, NOT FIXED``:

* ``test_an_unpriced_supplier_can_never_beat_a_priced_one`` — a missing price is
  scored as if it were a bad price, and cost outweighs lead time, so an unpriced
  supplier is unpickable whenever any priced rival exists. This one decides real
  purchases and is the most consequential of the five.
* ``test_a_same_day_supplier_scores_worse_on_speed_than_a_next_day_one`` — a
  lead time of 0 is falsy and reads as "unknown", so the best possible lead time
  scores worst.
* ``test_the_cost_term_is_a_cliff_not_a_curve`` — at or above 150% of average
  every price scores alike; ``..._is_not_clamped_above_...`` covers the other end.
* ``test_the_performance_term_cannot_affect_any_ordering`` — a constant, and 1%
  rather than the 10% its comment claimed.
* ``test_a_free_supplier_earns_nothing_for_being_free`` — a ``unit_cost`` of 0 is
  falsy too, so a free link is scored as unpriced while still counting as a real
  price in the yardstick every rival is measured against.
"""

from decimal import Decimal

import pytest

from inventory.models import InventoryItem, ItemSupplier, Supplier
from inventory.services.supplier_selection import (
    BASIS_BEST_SCORED,
    BASIS_FLAGGED_PRIMARY,
    _best_scored,
    average_orderable_unit_cost,
    score_candidate,
    select_supplier,
)

pytestmark = pytest.mark.django_db


def _item(name="Widget"):
    return InventoryItem.objects.create(
        name=name, description="x", reorder_quantity=5, current_stock=0, minimum_stock=10
    )


def _link(item, name, *, cost, lead, is_primary=False, **flags):
    return ItemSupplier.objects.create(
        item=item,
        supplier=Supplier.objects.create(name=name, supplier_type=Supplier.SupplierType.LOCAL),
        supplier_sku=f"{name}-sku",
        unit_cost=None if cost is None else Decimal(cost),
        quantity_per_package=1,
        average_lead_time=lead,
        is_primary=is_primary,
        is_active=flags.get("is_active", True),
        is_discontinued=flags.get("is_discontinued", False),
    )


def _chosen(item):
    return select_supplier(InventoryItem.objects.get(pk=item.pk))


# ── The gate: an operator's choice is binding, not merely expensive ──────────


def test_a_flagged_primary_wins_however_badly_it_would_score():
    """Worst on BOTH axes, and it still wins. That is what "gate" means.

    Under the previous shape the flag was a ``+0.2`` term, so this supplier lost
    to the cheap fast one — the operator's explicit choice was outbid rather
    than honoured.
    """
    item = _item()
    flagged = _link(item, "Chosen", cost="50.00", lead=29, is_primary=True)
    _link(item, "CheapAndFast", cost="1.00", lead=1)

    choice = _chosen(item)
    assert choice.item_supplier.pk == flagged.pk
    assert choice.basis == BASIS_FLAGGED_PRIMARY


def test_no_weight_exists_that_could_outbid_the_gate():
    """Push the rival to the best score the formula can produce; the gate holds."""
    item = _item()
    flagged = _link(item, "Chosen", cost="9999.00", lead=30, is_primary=True)
    rival = _link(item, "Perfect", cost="0.01", lead=1)

    average = average_orderable_unit_cost([flagged, rival])
    # The rival really does score far better — the gate is not winning on points.
    assert score_candidate(rival, average) > score_candidate(flagged, average)
    assert _chosen(item).item_supplier.pk == flagged.pk


def test_an_unorderable_flagged_primary_does_not_gate_and_scoring_decides():
    """The gate is for a choice you can still act on; a dead one is not one."""
    item = _item()
    _link(item, "ChosenButDead", cost="1.00", lead=1, is_primary=True, is_discontinued=True)
    _link(item, "Slow", cost="2.00", lead=28)
    fast = _link(item, "Fast", cost="2.10", lead=2)

    choice = _chosen(item)
    assert choice.item_supplier.pk == fast.pk
    assert choice.basis == BASIS_BEST_SCORED
    assert choice.flagged_primary_unorderable is True


# ── Judgement on one axis at a time ─────────────────────────────────────────


def test_cheaper_wins_when_lead_times_match():
    item = _item()
    _link(item, "Dear", cost="10.00", lead=7)
    cheap = _link(item, "Cheap", cost="4.00", lead=7)

    assert _chosen(item).item_supplier.pk == cheap.pk


def test_faster_wins_when_prices_match():
    item = _item()
    _link(item, "Slow", cost="5.00", lead=28)
    fast = _link(item, "Fast", cost="5.00", lead=3)

    assert _chosen(item).item_supplier.pk == fast.pk


# ── Judgement on the trade-off the weighting exists to make ─────────────────


def test_a_small_premium_buys_a_large_lead_time_saving():
    """$5.25 tomorrow beats $5.00 in four weeks — 5% more to wait 25 fewer days."""
    item = _item()
    _link(item, "SlowCheap", cost="5.00", lead=28)
    fast = _link(item, "FastDear", cost="5.25", lead=3)

    assert _chosen(item).item_supplier.pk == fast.pk


def test_a_large_premium_does_not_buy_a_small_lead_time_saving():
    """Nearly double the price to save four days is a bad trade, and loses."""
    item = _item()
    cheap = _link(item, "Cheap", cost="5.00", lead=11)
    _link(item, "Dearer", cost="9.50", lead=7)

    assert _chosen(item).item_supplier.pk == cheap.pk


def test_the_trade_off_has_a_boundary_rather_than_always_favouring_speed():
    """Same 25-day saving, two premiums: the small one wins, the large one loses.

    Pinned as a pair, because a rule that always preferred speed would satisfy
    the "small premium" test above while being no trade-off at all.
    """
    modest = _item("Modest")
    _link(modest, "SlowCheap", cost="5.00", lead=28)
    fast_modest = _link(modest, "FastDear", cost="5.25", lead=3)
    assert _chosen(modest).item_supplier.pk == fast_modest.pk

    steep = _item("Steep")
    slow_cheap = _link(steep, "SlowCheap", cost="5.00", lead=28)
    _link(steep, "FastVeryDear", cost="18.00", lead=3)
    assert _chosen(steep).item_supplier.pk == slow_cheap.pk


# ── Real-shaped edges ───────────────────────────────────────────────────────


def test_a_single_supplier_item_resolves_to_that_supplier():
    """The case that ALWAYS crashed before: unit_cost equals the average, so the
    old ``max(0, ...)`` returned a Decimal and ``Decimal * float`` raised."""
    item = _item()
    only = _link(item, "Only", cost="7.00", lead=9)

    assert _chosen(item).item_supplier.pk == only.pk


def test_a_priced_supplier_is_preferred_over_an_unpriced_one_at_equal_speed():
    """No price scores no cost points, so a real quote wins a tie on lead time."""
    item = _item()
    priced = _link(item, "Priced", cost="6.00", lead=7)
    _link(item, "Unpriced", cost=None, lead=7)

    assert _chosen(item).item_supplier.pk == priced.pk


def test_an_unpriced_supplier_can_never_beat_a_priced_one():
    """REPORTED, NOT FIXED: a missing price is scored as though it were a bad one.

    A supplier with no ``unit_cost`` earns no cost points at all, and cost is
    worth 0.4 against lead time's 0.3. On a two-supplier item the priced one
    also IS the average, so it banks the full 0.4 automatically. 0.4 therefore
    beats the 0.3 an unpriced rival could earn at best, however much faster it
    is — here, 28 days faster and it still loses.

    That is a data-completeness problem being decided as a purchasing question:
    "we never recorded a price" is not the same fact as "this is expensive".
    Pinned rather than corrected — the weights are the captain's to retune.
    """
    item = _item()
    priced_and_slow = _link(item, "PricedSlow", cost="6.00", lead=29)
    _link(item, "UnpricedQuick", cost=None, lead=1)

    assert _chosen(item).item_supplier.pk == priced_and_slow.pk


def test_an_item_whose_suppliers_have_no_prices_at_all_still_resolves():
    """``average_orderable_unit_cost`` is None here; scoring must not divide by it."""
    item = _item()
    _link(item, "SlowNoPrice", cost=None, lead=20)
    fast = _link(item, "FastNoPrice", cost=None, lead=2)

    assert average_orderable_unit_cost(list(item.item_suppliers.all())) is None
    assert _chosen(item).item_supplier.pk == fast.pk


def test_a_free_supplier_earns_nothing_for_being_free():
    """REPORTED, NOT FIXED: ``unit_cost`` of 0 is falsy, so FREE reads as "unpriced".

    Structurally the same falsy-guard flaw as the ``average_lead_time`` of 0
    below, and the row is treated as two contradictory things at once: the cost
    term skips it as unpriced, while ``average_orderable_unit_cost`` counts the
    0.00 as a real price and lets it drag the yardstick down.

    The consequence, at IDENTICAL lead times: a link that costs $4.00 outscores
    a link that costs nothing, and the free link scores exactly what the most
    expensive candidate scores — the best possible price is graded as the worst.

    Not hypothetical at a makerspace: donated stock, vendor samples and
    zero-cost internal transfers are all real, and all get a $0.00 link.

    Pinned rather than corrected — the guard and the weights are the captain's
    to retune, and this test exists so that retune is a visible change.
    """
    item = _item()
    free = _link(item, "Free", cost="0.00", lead=7)
    four = _link(item, "FourDollars", cost="4.00", lead=7)
    five = _link(item, "FiveDollars", cost="5.00", lead=7)

    average = average_orderable_unit_cost([free, four, five])
    assert score_candidate(four, average) > score_candidate(free, average)
    assert score_candidate(free, average) == score_candidate(five, average)
    assert _chosen(item).item_supplier.pk == four.pk


def test_a_tie_resolves_to_the_first_candidate_offered():
    """The only ties that can actually arise are between interchangeable rows.

    A tie between DIFFERENTLY priced candidates is not constructible: the cost
    yardstick is the mean of the candidates themselves, so two of them can never
    both sit past the 150% cliff, and any third candidate cheap enough to drag
    the mean down would outscore them both. So a real tie means same price and
    same lead time — rows with nothing to choose between them, where "the
    cheaper one" is not a meaningful answer.

    What is worth pinning is that the function is a pure first-maximal-wins over
    the order it is handed, so the resolution is stable rather than accidental.
    An earlier version of this test asserted a set of repeated calls equalled
    itself, which was a tautology and passed under a reversed tie-break.
    """
    item = _item()
    first = _link(item, "A-same", cost="4.00", lead=7)
    second = _link(item, "B-same", cost="4.00", lead=7)

    average = average_orderable_unit_cost([first, second])
    assert score_candidate(first, average) == score_candidate(second, average)
    assert _best_scored([first, second]).pk == first.pk
    assert _best_scored([second, first]).pk == second.pk


def test_the_lead_time_horizon_is_thirty_days():
    """PINNED: the horizon is a weight, and the captain reserved retuning them.

    At 30 days the lead-time term is exactly zero, and every slower supplier is
    equally slow as far as the score is concerned.
    """
    item = _item()
    at_horizon = _link(item, "AtHorizon", cost="5.00", lead=30)
    beyond = _link(item, "Beyond", cost="5.00", lead=45)
    inside = _link(item, "Inside", cost="5.00", lead=29)

    average = Decimal("5.00")
    assert score_candidate(at_horizon, average) == score_candidate(beyond, average)
    assert score_candidate(inside, average) > score_candidate(at_horizon, average)
    # 30 days earns exactly nothing on speed — the whole lead-time weight is
    # spent inside the horizon, so where the horizon sits decides every trade-off
    # this scoring makes.
    unpriced_at_horizon = _link(item, "UnpricedAtHorizon", cost=None, lead=30)
    assert score_candidate(unpriced_at_horizon, average) == Decimal("0.01")


# ── Arithmetic: Decimal throughout, which is what the crash was about ────────


def test_scoring_is_decimal_throughout_and_never_mixes_in_a_float():
    item = _item()
    link = _link(item, "Only", cost="7.00", lead=9)
    score = score_candidate(link, average_orderable_unit_cost([link]))
    assert isinstance(score, Decimal)


def test_a_price_difference_too_small_for_a_float_to_hold_still_decides():
    """Money is Decimal because binary floats lose exactly these cases."""
    item = _item()
    _link(item, "Dearer", cost="0.70", lead=7)
    cheaper = _link(item, "Cheaper", cost="0.69", lead=7)

    assert _chosen(item).item_supplier.pk == cheaper.pk


# ── Weights pinned as they stand, and named where questionable ───────────────


def test_the_cost_term_is_a_cliff_not_a_curve():
    """REPORTED, NOT FIXED: at/above 150% of average, every price scores alike.

    Two candidates 5x apart in price score identically on cost, so only lead
    time separates them. Retuning the tolerance is a product decision the
    captain reserved; this pins the behaviour so the retune is a visible change.
    """
    item = _item()
    expensive = _link(item, "Expensive", cost="100.00", lead=7)
    absurd = _link(item, "Absurd", cost="500.00", lead=7)

    # Stated explicitly rather than derived: both candidates must sit past the
    # cliff, and a catalogue average computed from them would be dragged up by
    # the very outlier under test.
    average = Decimal("10.00")
    assert score_candidate(expensive, average) == score_candidate(absurd, average)
    # And the cliff really is at 150% of average — 15.00 against an average of
    # 10.00 scores nothing on cost, while a hair under it still scores something.
    at_cliff = _link(item, "AtCliff", cost="15.00", lead=7)
    just_inside = _link(item, "JustInside", cost="14.90", lead=7)
    assert score_candidate(at_cliff, average) == score_candidate(absurd, average)
    assert score_candidate(just_inside, average) > score_candidate(at_cliff, average)


def test_the_cost_term_is_not_clamped_above_and_can_exceed_its_nominal_weight():
    """REPORTED, NOT FIXED: "cost 40%" can contribute more than 0.4.

    A candidate far below its peers' average earns an unclamped bonus. Harmless
    against the gate — an operator's choice is not on points any more — but the
    stated weight and the real one differ, which a future reader should know.
    """
    item = _item()
    bargain = _link(item, "Bargain", cost="1.00", lead=30)
    _link(item, "Dear", cost="99.00", lead=30)

    average = average_orderable_unit_cost(list(item.item_suppliers.all()))
    # A candidate priced exactly at the average earns exactly the nominal weight.
    # Asserted as a LITERAL, not as ``COST_WEIGHT + ...``: an assertion built
    # from the constant it guards holds for every value that constant could
    # take, so it cannot detect the retune it exists to detect. 0.40 cost + 0.00
    # lead (at the 30-day horizon) + 0.01 performance.
    at_average = _link(item, "AtAverage", cost=str(average), lead=30)
    assert score_candidate(at_average, average) == Decimal("0.41")
    # The bargain earns MORE than the nominal weight, which a clamp would cap.
    assert score_candidate(bargain, average) > score_candidate(at_average, average)


def test_the_performance_term_cannot_affect_any_ordering():
    """REPORTED, NOT FIXED: a constant 0.01 added to every candidate alike.

    Its comment called it a "10% weight"; ``0.1 * 0.1`` is 1%, and being
    constant it is inert regardless. It is a placeholder for LeadTimeLog-driven
    scoring that does not exist yet.

    Asserted by subtracting each candidate's cost and lead-time contributions,
    computed here independently of the implementation, and checking that what is
    LEFT OVER is the same number for both. An earlier version asserted that
    subtracting the same constant from two scores preserved their difference,
    which is a Decimal identity — true of every possible implementation,
    including one where the term varies per candidate.
    """

    def _cost_and_lead(link, average):
        cost = (
            max(Decimal(0), Decimal("50") - (link.unit_cost / average - 1) * 100)
            / Decimal("50")
            * Decimal("0.4")
        )
        lead = max(
            Decimal(0), (Decimal("30") - Decimal(link.average_lead_time)) / Decimal("30")
        ) * Decimal("0.3")
        return cost + lead

    item = _item()
    a = _link(item, "A", cost="5.00", lead=7)
    b = _link(item, "B", cost="9.00", lead=20)

    average = average_orderable_unit_cost([a, b])
    residual_a = score_candidate(a, average) - _cost_and_lead(a, average)
    residual_b = score_candidate(b, average) - _cost_and_lead(b, average)

    # Same leftover for two candidates that differ on BOTH scored axes, so the
    # term cannot be tracking either of them.
    assert residual_a == residual_b
    # And it is the 1% constant, not the 10% its comment claimed.
    assert residual_a == Decimal("0.01")


def test_a_same_day_supplier_scores_worse_on_speed_than_a_next_day_one():
    """REPORTED, NOT FIXED: ``average_lead_time`` of 0 is read as "unknown".

    The lead-time term is guarded by ``if link.average_lead_time``, and 0 is
    falsy, so a supplier you can walk to today earns NOTHING on speed while a
    next-day one earns nearly the full 0.3. The best possible lead time scores
    worst. Reachable: the column is a ``PositiveIntegerField``, and
    ``inventory.tasks.update_average_lead_times`` derives it from observed
    deliveries, which for a local counter pickup is 0 days.
    """
    item = _item()
    same_day = _link(item, "SameDay", cost="5.00", lead=0)
    next_day = _link(item, "NextDay", cost="5.00", lead=1)

    average = average_orderable_unit_cost([same_day, next_day])
    assert score_candidate(same_day, average) < score_candidate(next_day, average)
    assert _chosen(item).item_supplier.pk == next_day.pk
