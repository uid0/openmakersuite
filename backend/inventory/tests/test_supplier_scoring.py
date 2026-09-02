"""Does the weighted score CHOOSE WELL? (op-2rsp)

A rule that runs without erroring is not the same as a rule that chooses well,
and this scoring had never once run in production: it raised ``TypeError`` on
``Decimal * float`` for any candidate priced below 150% of the item's average,
which is nearly every real candidate and always so for a single-supplier item.
The single test that reached it set ``unit_cost=None`` on its fixtures
specifically to route around the crash, so no test had ever asserted an outcome
of this scoring. Adopting it was therefore not a reconciliation of two live
rules — it was switching on a rule whose behaviour was unobserved.

So these tests are about JUDGEMENT, on real-shaped catalogue data: a modest
premium buying a large lead-time saving, a large premium buying a small one,
suppliers separated on only one axis, a single-supplier item, missing prices,
missing delivery records. ``test_supplier_selection.py`` covers the plumbing;
this covers the choices.

The first round of this work PINNED five questionable judgements rather than
correcting them, each named ``REPORTED, NOT FIXED``, because retuning was the
captain's call and not a defect fix. **The captain has now decided all five**,
and every one of those tests is INVERTED below rather than deleted, so the
retune is a visible change in this file and not a quiet one:

* ``test_an_unpriced_supplier_can_never_beat_a_priced_one`` →
  ``test_an_unpriced_supplier_beats_a_priced_one_when_it_is_faster``. A missing
  price is no longer scored as a bad price: **the cost term is not skipped for
  an unpriced candidate, it is kept whole**, exactly as it is for one priced at
  the item's average. ``test_a_priced_supplier_and_an_unpriced_one_at_the_average_score_identically``
  is the arithmetic statement of that, and
  ``test_an_unpriced_supplier_is_not_rewarded_for_the_gap_either`` is the other
  half — it ties the cheap rival rather than beating it.
* ``test_a_same_day_supplier_scores_worse_on_speed_than_a_next_day_one`` →
  ``test_a_same_day_supplier_scores_best_on_speed``. A lead time of 0 is a
  KNOWN lead time.
* ``test_a_free_supplier_earns_nothing_for_being_free`` →
  ``test_a_free_supplier_is_priced_at_zero_and_wins_on_it``. A ``unit_cost`` of
  0.00 is a KNOWN price.
* ``test_the_cost_term_is_not_clamped_above_and_can_exceed_its_nominal_weight``
  → ``test_the_cost_term_never_exceeds_its_stated_weight``.
* ``test_the_performance_term_cannot_affect_any_ordering`` →
  ``test_the_performance_term_is_a_real_ten_percent_of_the_delivery_record``.

**One finding is still REPORTED, NOT FIXED, and its test still says so**:
``test_the_cost_term_is_a_cliff_not_a_curve``. Where the cliff sits — 150% of
the item's average — was not among the five the captain decided, so it stands
as it was, and the marker on it is still an accurate description of the code.
"""

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

import pytest

from inventory.models import InventoryItem, ItemSupplier, Supplier
from inventory.services.supplier_selection import (
    BASIS_BEST_SCORED,
    BASIS_FLAGGED_PRIMARY,
    COST_WEIGHT,
    LEAD_TIME_WEIGHT,
    PERFORMANCE_WEIGHT,
    DeliveryRecord,
    _best_scored,
    average_orderable_unit_cost,
    cost_factor,
    delivery_record_annotations,
    delivery_records_for,
    lead_time_factor,
    score_candidate,
    select_supplier,
)
from reorder_queue.models import LeadTimeLog, PurchaseOrder
from reorder_queue.tests.factories import UserFactory

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


def _delivery(link, *, variance_days):
    """Record one delivery through ``link``, ``variance_days`` late (negative = early).

    ``LeadTimeLog.save`` derives ``variance_days`` from
    ``actual - estimated``, so the variance under test is expressed by moving
    the ACTUAL against a fixed 10-day estimate — writing the column directly
    would be overwritten and would pin nothing.
    """
    estimated = 10
    ordered_at = timezone.now() - timedelta(days=60)
    return LeadTimeLog.objects.create(
        item_supplier=link,
        purchase_order=PurchaseOrder.objects.create(
            supplier=link.supplier, created_by=UserFactory()
        ),
        order_date=ordered_at,
        expected_delivery_date=(ordered_at + timedelta(days=estimated)).date(),
        actual_delivery_date=(ordered_at + timedelta(days=estimated + variance_days)).date(),
        estimated_lead_time_days=estimated,
        actual_lead_time_days=estimated + variance_days,
        quantity_ordered=10,
        quantity_received=10,
    )


def _record(link):
    """This link's delivery record via the GROUPED AGGREGATE fallback path.

    A bare ``.get()`` carries no delivery annotations, so ``delivery_records_for``
    resolves it with its one grouped aggregate — the path a caller still
    prefetching the old bare string lands on.
    """
    return delivery_records_for([ItemSupplier.objects.get(pk=link.pk)])[link.pk]


def _annotated_record(link):
    """The same record via the ROW ANNOTATIONS — the path every read path uses.

    One rule, two implementations: a correlated subquery on the row and a
    grouped aggregate over the leftovers. They must agree, and the boundary is
    where two spellings of "no later than the vendor's standing quote" would
    drift first.
    """
    row = ItemSupplier.objects.annotate(**delivery_record_annotations()).get(pk=link.pk)
    return delivery_records_for([row])[link.pk]


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


def test_a_priced_supplier_and_an_unpriced_one_at_the_average_score_identically():
    """THE arithmetic statement of "a missing price is neither punished nor paid".

    Same lead time, same (empty) delivery record, one priced at exactly the
    item's average orderable price and one with no price at all — and they score
    the SAME NUMBER. That is what "neither rewarded nor penalised" means here:
    an unpriced candidate is scored on cost as an unremarkable, average-priced
    one is, not as a bad one (skipping the term, which is a penalty by another
    name) and not as a bargain (the full term unclamped, which would be a thumb
    on the scale).

    Asserted as a LITERAL, not as ``COST_WEIGHT + ...``: an assertion built from
    the constant it guards holds for every value that constant could take, so it
    could not detect the retune it exists to detect. 0.40 cost (at the average,
    nothing to discount) + 0.23 lead (7 of the 30-day horizon) + 0.10
    performance (no record, nothing to discount).

    The dead tie is then broken by ``Meta.ordering`` — ``unit_cost`` ascending
    with SQL's NULLs last — so the vendor whose price we DO know is the one that
    goes on the order. That is the only place "we know this one's price" gets to
    matter, and it matters only when nothing else separates them.
    """
    item = _item()
    priced = _link(item, "Priced", cost="6.00", lead=7)
    unpriced = _link(item, "Unpriced", cost=None, lead=7)

    average = average_orderable_unit_cost([priced, unpriced])
    assert average == Decimal("6.00")  # the unpriced row is not in the yardstick
    assert score_candidate(priced, average) == Decimal("0.73")
    assert score_candidate(unpriced, average) == Decimal("0.73")
    assert _chosen(item).item_supplier.pk == priced.pk


def test_an_unpriced_supplier_beats_a_priced_one_when_it_is_faster():
    """INVERTED from ``test_an_unpriced_supplier_can_never_beat_a_priced_one``.

    Before: a supplier with no ``unit_cost`` earned no cost points at all, cost
    outweighed lead time 0.4 to 0.3, and on a two-supplier item the priced one
    IS the average and banked the full 0.4 automatically. An unpriced rival was
    therefore unpickable however much faster it was — 28 days faster here, and
    it still lost. That decided a purchase on a data-completeness problem: "we
    never recorded a price" is not the same fact as "this is expensive".

    Now the cost term is kept whole for both, so the 28 days actually decide it.
    """
    item = _item()
    _link(item, "PricedSlow", cost="6.00", lead=29)
    unpriced_quick = _link(item, "UnpricedQuick", cost=None, lead=1)

    assert _chosen(item).item_supplier.pk == unpriced_quick.pk


def test_an_unpriced_supplier_is_not_rewarded_for_the_gap_either():
    """The other half of the decision, and the reason the clamp is load-bearing.

    Three candidates at IDENTICAL lead times and no delivery history, so cost is
    the only axis: one cheap, one dear, one unpriced. The unpriced one beats the
    dear one — it is scored as an average price, and the dear one is above
    average — but it does NOT beat the cheap one; they tie, and the tie goes to
    the row whose price we know. An unpriced candidate can never OUTSCORE a
    priced one on cost, because the clamp puts the full weight at the top of the
    range and the unpriced candidate gets exactly that and no more.
    """
    item = _item()
    cheap = _link(item, "Cheap", cost="2.00", lead=7)
    dear = _link(item, "Dear", cost="10.00", lead=7)
    unpriced = _link(item, "Unpriced", cost=None, lead=7)

    average = average_orderable_unit_cost([cheap, dear, unpriced])
    assert average == Decimal("6.00")
    assert score_candidate(unpriced, average) > score_candidate(dear, average)
    assert score_candidate(unpriced, average) == score_candidate(cheap, average)
    assert _chosen(item).item_supplier.pk == cheap.pk


def test_an_item_whose_suppliers_have_no_prices_at_all_still_resolves():
    """``average_orderable_unit_cost`` is None here; scoring must not divide by it."""
    item = _item()
    _link(item, "SlowNoPrice", cost=None, lead=20)
    fast = _link(item, "FastNoPrice", cost=None, lead=2)

    assert average_orderable_unit_cost(list(item.item_suppliers.all())) is None
    assert _chosen(item).item_supplier.pk == fast.pk


def test_a_free_supplier_is_priced_at_zero_and_wins_on_it():
    """INVERTED from ``test_a_free_supplier_earns_nothing_for_being_free``.

    Before: ``unit_cost`` of 0 is falsy, so a free link was skipped as
    "unpriced" by the cost term while ``average_orderable_unit_cost`` went on
    counting that 0.00 as a real price. The same row was treated as two
    contradictory things at once — it earned nothing for being free AND dragged
    down the yardstick its rivals were measured against — so at identical lead
    times a $4.00 link outscored a free one, and the free one scored exactly
    what the DEAREST candidate scored. The best possible price was graded as the
    worst.

    Not hypothetical at a makerspace: donated stock, vendor samples and
    zero-cost internal transfers are all real, and all get a $0.00 link.

    Now 0.00 is the known price it is: free beats $4.00 beats $5.00, in the
    order a purchaser would put them. The yardstick is unchanged and still
    counts the 0.00 — that half was always right.
    """
    item = _item()
    free = _link(item, "Free", cost="0.00", lead=7)
    four = _link(item, "FourDollars", cost="4.00", lead=7)
    five = _link(item, "FiveDollars", cost="5.00", lead=7)

    average = average_orderable_unit_cost([free, four, five])
    assert average == Decimal("3.00")  # the 0.00 counts, as it always did
    assert score_candidate(free, average) > score_candidate(four, average)
    assert score_candidate(four, average) > score_candidate(five, average)
    assert _chosen(item).item_supplier.pk == free.pk


def test_an_item_whose_every_supplier_is_free_resolves_instead_of_dividing_by_zero():
    """An average of exactly 0.00 is a KNOWN average and an unusable DIVISOR.

    Two donated links make ``average_orderable_unit_cost`` exactly ``0.00``.
    The truthiness guard this replaced (``if link.unit_cost and
    average_unit_cost``) was the only thing standing between that item and a
    ``ZeroDivisionError``, and it "worked" by treating both free links as
    unpriced — so removing it without handling the divisor would have turned a
    silently-wrong answer into a 500 on the item detail page.

    Both are at the cheap end of a free peer group, so nothing separates them on
    cost and the faster one wins.
    """
    item = _item()
    _link(item, "SlowDonation", cost="0.00", lead=21)
    fast = _link(item, "FastDonation", cost="0.00", lead=2)

    average = average_orderable_unit_cost(list(item.item_suppliers.all()))
    assert average == Decimal("0.00")
    assert _chosen(item).item_supplier.pk == fast.pk


def test_against_a_free_peer_group_anything_charged_is_dear():
    """The zero-divisor branch answers the question the division would have.

    ``score_candidate`` is public and takes the yardstick as an argument, so a
    caller can hand it an average of 0.00 alongside a candidate that charges
    something — a combination the item-level derivation cannot produce, because
    a mean of 0.00 over non-negative prices means every priced candidate is
    free. Asked anyway, the answer is the one the curve would give: a vendor
    charging more than its peers' average is dear, and here the peers charge
    nothing.
    """
    item = _item()
    charged = _link(item, "Charged", cost="5.00", lead=7)
    free = _link(item, "Free", cost="0.00", lead=7)

    assert cost_factor(free, Decimal("0.00")) == Decimal(1)
    assert cost_factor(charged, Decimal("0.00")) == Decimal(0)


def test_a_tie_resolves_to_the_first_candidate_offered():
    """The only ties that can actually arise are between interchangeable rows.

    A tie between DIFFERENTLY priced candidates IS now constructible: the cost
    factor is clamped at 1, so every candidate at or below the item's average
    orderable price earns the identical full weight — see
    ``test_below_the_average_price_stops_separating_candidates_and_speed_decides``,
    which builds exactly such a tie. A tie therefore means equal cost factor,
    equal lead time AND equal delivery record — rows with nothing to choose
    between them, where "the cheaper one" is not a meaningful answer. The order
    that resolves it is ``Meta.ordering`` — ``-is_primary``, then ``unit_cost``
    ascending with SQL's NULLs last.

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
    # 30 days earns exactly nothing on speed, so a candidate sitting there scores
    # only what the other two terms give it: 0.40 cost (unpriced, so nothing to
    # discount) + 0.00 lead + 0.10 performance (no record, nothing to discount).
    unpriced_at_horizon = _link(item, "UnpricedAtHorizon", cost=None, lead=30)
    assert score_candidate(unpriced_at_horizon, average) == Decimal("0.50")


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


def test_the_cost_term_never_exceeds_its_stated_weight():
    """INVERTED from ``..._is_not_clamped_above_and_can_exceed_its_nominal_weight``.

    Before: a candidate far below its peers' average earned an UNCLAMPED bonus,
    so a term documented as 40% contributed 1.18 for a $1.00 link against a
    $50.00 average. The stated weight and the real one differed, which is a
    documented claim the code did not honour — and it is what would have made
    "an unpriced candidate gets the full weight" a penalty rather than a
    neutrality, since a bargain could still outbid the full weight.

    Now the factor is clamped at 1: at or below the average there is nothing to
    discount, and the term is exactly ``COST_WEIGHT``. Asserted as LITERALS, not
    as ``COST_WEIGHT + ...``: an assertion built from the constant it guards
    holds for every value that constant could take, so it could not detect the
    retune it exists to detect. 0.40 cost + 0.00 lead (at the 30-day horizon) +
    0.10 performance (no delivery record, nothing to discount).
    """
    item = _item()
    bargain = _link(item, "Bargain", cost="1.00", lead=30)
    _link(item, "Dear", cost="99.00", lead=30)

    average = average_orderable_unit_cost(list(item.item_suppliers.all()))
    at_average = _link(item, "AtAverage", cost=str(average), lead=30)
    assert score_candidate(at_average, average) == Decimal("0.50")
    # The bargain is capped at the same full weight rather than bidding past it.
    assert cost_factor(bargain, average) == Decimal(1)
    assert score_candidate(bargain, average) == Decimal("0.50")


def test_below_the_average_price_stops_separating_candidates_and_speed_decides():
    """The clamp's real cost, pinned so it is a decision and not a surprise.

    The cost curve reaches 1 AT the item's average, so clamping it there means
    every candidate at or below the average earns the identical full weight and
    cost stops discriminating among them. Two vendors under the average are
    therefore separated by lead time and delivery record alone: $12.00 in a week
    beats $8.00 in a fortnight, where the unclamped term let the cheaper one
    outbid the speed.

    That is a consequence of "the cost term cannot exceed its stated weight",
    which is the decision, not an accident of it — and it is the strongest
    argument for revisiting where the cliff sits, which
    ``test_the_cost_term_is_a_cliff_not_a_curve`` still reports as unfixed.

    When lead time and record match too, there is genuinely nothing left to
    choose between them, and ``Meta.ordering`` hands the win to the cheaper row.
    """
    item = _item("Resin")
    bargain = _link(item, "Bargain", cost="8.00", lead=14)
    cheapish = _link(item, "Cheapish", cost="12.00", lead=7)
    _link(item, "Dear", cost="40.00", lead=5)

    average = average_orderable_unit_cost(list(item.item_suppliers.all()))
    assert average == Decimal("20.00")
    assert cost_factor(bargain, average) == cost_factor(cheapish, average) == Decimal(1)
    assert _chosen(item).item_supplier.pk == cheapish.pk

    # ... and with the speed matched too, the cheaper row takes the dead tie.
    tied = _item("Tied")
    cheaper = _link(tied, "Cheaper", cost="8.00", lead=7)
    dearer = _link(tied, "Dearer", cost="12.00", lead=7)
    _link(tied, "Dearest", cost="40.00", lead=7)
    tied_average = average_orderable_unit_cost(list(tied.item_suppliers.all()))
    assert score_candidate(cheaper, tied_average) == score_candidate(dearer, tied_average)
    assert _chosen(tied).item_supplier.pk == cheaper.pk


def test_no_candidate_can_score_above_the_sum_of_the_stated_weights():
    """The stated weights are the real ceiling: 0.4 + 0.3 + 0.1, and no more.

    A free, same-day supplier with a spotless delivery record is the best input
    the formula can be given, and it scores exactly 0.80. Every term is at its
    cap and none of them overshoots.
    """
    item = _item()
    perfect = _link(item, "Perfect", cost="0.00", lead=0)
    _delivery(perfect, variance_days=0)

    average = average_orderable_unit_cost([perfect])
    assert cost_factor(perfect, average) == Decimal(1)
    assert lead_time_factor(perfect) == Decimal(1)
    assert _record(perfect).factor == Decimal(1)
    assert score_candidate(perfect, average, _record(perfect)) == Decimal("0.80")
    assert COST_WEIGHT + LEAD_TIME_WEIGHT + PERFORMANCE_WEIGHT == Decimal("0.8")


def test_the_performance_term_is_a_real_ten_percent_of_the_delivery_record():
    """INVERTED from ``test_the_performance_term_cannot_affect_any_ordering``.

    Before: ``PERFORMANCE_FACTOR * PERFORMANCE_WEIGHT`` was a constant 0.01
    added to every candidate alike — inert regardless, and 1% while the comment
    beside it claimed a 10% weight. It was a placeholder for LeadTimeLog-driven
    scoring that did not exist.

    Now it is that scoring: **the share of the deliveries recorded against this
    supplier link that arrived no later than the vendor's STANDING QUOTED lead
    time** — the yardstick because it is the promise the lead-time term scores,
    not the order's separately confirmed ``expected_delivery_date``. Asserted by
    subtracting each candidate's cost and lead-time contributions, computed here
    independently of the implementation, and checking that what is LEFT OVER is
    the link's on-time share times a tenth. An earlier version of this test
    asserted that subtracting the same constant from two scores preserved their
    difference, which is a Decimal identity — true of every possible
    implementation, including one where the term varies per candidate.
    """

    def _cost_and_lead(link, average):
        cost = min(
            Decimal(1),
            max(Decimal(0), Decimal("50") - (link.unit_cost / average - 1) * 100) / Decimal("50"),
        ) * Decimal("0.4")
        lead = max(
            Decimal(0), (Decimal("30") - Decimal(link.average_lead_time)) / Decimal("30")
        ) * Decimal("0.3")
        return cost + lead

    item = _item()
    a = _link(item, "A", cost="5.00", lead=7)
    b = _link(item, "B", cost="9.00", lead=20)
    # A: four deliveries, one of them late. B: two, both on time.
    _delivery(a, variance_days=-1)
    _delivery(a, variance_days=0)
    _delivery(a, variance_days=4)
    _delivery(a, variance_days=0)
    _delivery(b, variance_days=0)
    _delivery(b, variance_days=-3)

    average = average_orderable_unit_cost([a, b])
    residual_a = score_candidate(a, average, _record(a)) - _cost_and_lead(a, average)
    residual_b = score_candidate(b, average, _record(b)) - _cost_and_lead(b, average)

    # It TRACKS the record now — different leftovers for different records, which
    # is exactly what the old constant could not do.
    assert residual_a != residual_b
    assert residual_a == Decimal("0.075")  # 3 of 4 on time, times a tenth
    assert residual_b == Decimal("0.1")  # 2 of 2 on time, the whole weight
    # And it is worth a tenth, not a hundredth: the gap between a spotless
    # record and a hopeless one is the whole weight.
    hopeless = _link(item, "Hopeless", cost="5.00", lead=7)
    _delivery(hopeless, variance_days=6)
    assert score_candidate(a, average, DeliveryRecord(on_time=1, total=1)) - score_candidate(
        a, average, DeliveryRecord(on_time=0, total=1)
    ) == Decimal("0.1")
    assert _record(hopeless).factor == Decimal(0)


def test_a_late_record_loses_to_a_clean_one_when_nothing_else_separates_them():
    """The term CHOOSES, not merely computes: same price, same speed, one late.

    This is the whole point of making the weight real — two vendors quoting the
    same price and the same wait are separated by which of them has actually
    kept that promise.
    """
    item = _item()
    flaky = _link(item, "Flaky", cost="5.00", lead=7)
    reliable = _link(item, "Reliable", cost="5.00", lead=7)
    _delivery(flaky, variance_days=9)
    _delivery(flaky, variance_days=0)
    _delivery(reliable, variance_days=0)
    _delivery(reliable, variance_days=1)  # late too, but only once in three
    _delivery(reliable, variance_days=-2)

    assert _record(flaky).factor == Decimal(1) / Decimal(2)
    assert _record(reliable).factor == Decimal(2) / Decimal(3)
    assert _chosen(item).item_supplier.pk == reliable.pk


def test_a_supplier_with_no_delivery_history_is_not_punished_for_the_gap():
    """The same rule as the missing price, applied to the missing record.

    A link nobody has ever ordered through scores the FULL performance weight —
    it ties a vendor with a spotless record and beats one that has been late.
    That is not generosity: ``average_lead_time`` is the wait this vendor
    PROMISES and the lead-time term already scores that promise, so this term
    exists only to discount the promise by how often the vendor has broken it. A
    link that has delivered nothing has broken nothing. Scoring it lower would
    be discounting a promise for want of evidence, which is punishing the gap.
    """
    item = _item()
    untried = _link(item, "Untried", cost="5.00", lead=7)
    spotless = _link(item, "Spotless", cost="5.00", lead=7)
    tardy = _link(item, "Tardy", cost="5.00", lead=7)
    _delivery(spotless, variance_days=0)
    _delivery(tardy, variance_days=3)

    average = average_orderable_unit_cost([untried, spotless, tardy])
    assert _record(untried) == DeliveryRecord(on_time=0, total=0)
    assert _record(untried).has_history is False
    assert score_candidate(untried, average, _record(untried)) == score_candidate(
        spotless, average, _record(spotless)
    )
    assert score_candidate(untried, average, _record(untried)) > score_candidate(
        tardy, average, _record(tardy)
    )


def test_a_delivery_on_the_promised_day_counts_as_on_time():
    """``variance_days`` of exactly 0 is the BEST the column can say, not an absence.

    The same falsy-zero shape as a ``unit_cost`` of 0.00 and an
    ``average_lead_time`` of 0, one layer down: a guard spelled
    ``if log.variance_days`` would read a delivery that landed exactly when
    quoted as having no variance recorded. The rule is ``<= 0``, measured
    against the vendor's standing quote.

    Asserted on BOTH implementations of the rule — the row annotation every read
    path rides and the grouped-aggregate fallback — because the boundary is
    exactly where two spellings of one rule drift, and a mixed record (one
    early, one on the day, one late) is what tells them apart.
    """
    item = _item()
    exact = _link(item, "Exact", cost="5.00", lead=7)
    _delivery(exact, variance_days=0)

    assert LeadTimeLog.objects.get(item_supplier=exact).variance_days == 0
    assert _record(exact) == DeliveryRecord(on_time=1, total=1)
    assert _annotated_record(exact) == DeliveryRecord(on_time=1, total=1)
    assert _record(exact).factor == Decimal(1)

    mixed = _link(item, "Mixed", cost="5.00", lead=7)
    _delivery(mixed, variance_days=-3)  # early
    _delivery(mixed, variance_days=0)  # exactly on the promised day
    _delivery(mixed, variance_days=4)  # late
    assert _record(mixed) == DeliveryRecord(on_time=2, total=3)
    assert _annotated_record(mixed) == _record(mixed)


def test_arriving_early_counts_as_on_time_and_no_better():
    """DECIDED: early keeps the promise; it does not earn more than keeping it.

    A link that has always been five days early scores the same as one that has
    always landed on the day. Paying extra for earliness would pay twice for the
    same fact — ``average_lead_time`` is the vendor's OWN per-link quoted
    promise, operator-entered and maintained per link, so a reliably quick
    vendor already collects its speed on the LEAD-TIME axis.
    """
    item = _item()
    early = _link(item, "Early", cost="5.00", lead=7)
    punctual = _link(item, "Punctual", cost="5.00", lead=7)
    _delivery(early, variance_days=-5)
    _delivery(early, variance_days=-9)
    _delivery(punctual, variance_days=0)
    _delivery(punctual, variance_days=0)

    assert _record(early).factor == _record(punctual).factor == Decimal(1)


def test_an_old_delivery_counts_exactly_as_much_as_a_recent_one():
    """DECIDED: the share is unweighted — no recency decay, no window.

    Two links with the same two-of-three record, one late long ago and one late
    last week, score identically. A decay constant is a tuning knob nobody has
    the data to set over a handful of orders a year, and it would make the
    number that decides a purchase disagree with the unweighted
    ``on_time_percentage`` the supplier screen already shows. The known cost is
    that a vendor who was chronically late years ago carries it forever; the
    remedy is a window on the query, and it is filed rather than guessed at.
    """
    item = _item()
    late_long_ago = _link(item, "LateLongAgo", cost="5.00", lead=7)
    late_recently = _link(item, "LateRecently", cost="5.00", lead=7)

    for link, late_index in ((late_long_ago, 0), (late_recently, 2)):
        for i in range(3):
            log = _delivery(link, variance_days=5 if i == late_index else 0)
            log.actual_delivery_date = (timezone.now() - timedelta(days=700 - i * 300)).date()
            log.save()

    assert _record(late_long_ago).factor == _record(late_recently).factor
    assert score_candidate(
        late_long_ago, Decimal("5.00"), _record(late_long_ago)
    ) == score_candidate(late_recently, Decimal("5.00"), _record(late_recently))


def test_a_same_day_supplier_scores_best_on_speed():
    """INVERTED from ``test_a_same_day_supplier_scores_worse_on_speed_than_a_next_day_one``.

    Before: the lead-time term was guarded by ``if link.average_lead_time`` and
    0 is falsy, so a supplier you can walk to today earned NOTHING on speed
    while a next-day one earned nearly the full 0.3. The best possible lead time
    was graded as the worst. Reachable: the column is a ``PositiveIntegerField``
    an operator sets per link, through the admin or the API, and 0 is what they
    enter for a local counter pickup. (``inventory.tasks.update_average_lead_times``
    cannot produce it — its ``if lead_time > 0`` filter drops same-day
    deliveries before the average is taken.)

    Now 0 is the known lead time it is, and takes the whole weight — there is no
    guard at all, because the column is non-null and so there is no absence for
    a guard to catch.
    """
    item = _item()
    same_day = _link(item, "SameDay", cost="5.00", lead=0)
    next_day = _link(item, "NextDay", cost="5.00", lead=1)

    average = average_orderable_unit_cost([same_day, next_day])
    assert lead_time_factor(same_day) == Decimal(1)
    assert score_candidate(same_day, average) > score_candidate(next_day, average)
    assert _chosen(item).item_supplier.pk == same_day.pk


# ── What the choice says out loud about what it did not know ─────────────────


def test_the_choice_says_when_it_picked_a_supplier_with_no_price_on_file():
    """An operator must not have to infer a blank Cost cell.

    The scoring does not punish a missing price, so an unpriced supplier can and
    does win — and when it does, ``scored_without_price`` says so, all the way
    onto ``/items/{id}/metrics/``.
    """
    item = _item()
    _link(item, "PricedSlow", cost="6.00", lead=29)
    unpriced = _link(item, "UnpricedQuick", cost=None, lead=1)

    choice = _chosen(item)
    assert choice.item_supplier.pk == unpriced.pk
    assert choice.scored_without_price is True
    assert choice.scored_without_history is True


def test_the_choice_reports_a_priced_winner_with_a_record_as_neither():
    item = _item()
    priced = _link(item, "Priced", cost="6.00", lead=2)
    _link(item, "Slow", cost="6.00", lead=29)
    _delivery(priced, variance_days=0)

    choice = _chosen(item)
    assert choice.item_supplier.pk == priced.pk
    assert choice.scored_without_price is False
    assert choice.scored_without_history is False


def test_a_free_winner_is_not_reported_as_having_no_price():
    """ "Free" and "unpriced" are different facts, and only one is a gap.

    A vendor that charges nothing HAS told us what it charges. Under the clamp
    the two earn the same cost term — a free link and an unpriced one both sit
    at the top of the range — so the score cannot tell them apart, and this flag
    is the only place the difference survives. Telling an operator that a
    donated link has "no price on file" would send them off to chase a price
    that is already recorded.
    """
    item = _item()
    free = _link(item, "Donated", cost="0.00", lead=7)
    _link(item, "Paid", cost="9.00", lead=7)

    choice = _chosen(item)
    assert choice.item_supplier.pk == free.pk
    assert choice.scored_without_price is False


def test_a_flagged_primary_reports_neither_gap_because_it_weighed_nothing():
    """The gate is not a comparison, so nothing it did not know decided anything.

    A flagged primary with no price and no delivery record still reports both
    flags ``False``: they describe a choice the SCORING made in spite of a gap,
    and the gate made no such choice. Saying otherwise would tell an operator
    the system shrugged off a gap when in fact it was obeying their own flag.
    """
    item = _item()
    flagged = _link(item, "Flagged", cost=None, lead=7, is_primary=True)
    _link(item, "Rival", cost="1.00", lead=1)

    choice = _chosen(item)
    assert choice.item_supplier.pk == flagged.pk
    assert choice.basis == BASIS_FLAGGED_PRIMARY
    assert choice.scored_without_price is False
    assert choice.scored_without_history is False
