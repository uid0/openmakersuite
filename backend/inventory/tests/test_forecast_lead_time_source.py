"""The reorder point is computed from the supplier we would ACTUALLY buy from.

The rule, in one sentence (op-3vqk): **the reorder point must be computed from
the lead time of the supplier we would actually buy from; and an item with no
orderable supplier must still appear on the forecast, with its lead time
honestly attributed.**

Both halves bind, and satisfying the first by dropping items is the failure
op-2rsp round 5 shipped and had to revert. So the shape here is a PREFERENCE,
not a filter: :func:`inventory.services.component_forecast.lead_times_for` asks
:mod:`inventory.services.supplier_selection` — the one owner of "which
supplier" — and only when that answers ``NONE_ORDERABLE`` does it fall back to
reading every link, exactly as the pre-op-3vqk rule did.

Three states are kept apart throughout, because collapsing any two of them is
this codebase's recurring defect:

* the lead time of the supplier we would buy from (``orderable_supplier``);
* a lead time we only have because every link is dead — real information, about
  a vendor we cannot use (``unorderable_supplier``);
* no lead time on record at all (``no_supplier``).

``inventory/tests/test_alert_suppression.py`` holds the CONTROLs for the second
and third; this module holds the first and the boundary between them.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from inventory.models import (
    ComponentUsageEvent,
    InventoryItem,
    ItemSupplier,
    SerializedComponent,
    Supplier,
)
from inventory.services.component_forecast import (
    LEAD_TIME_FROM_ORDERABLE,
    LEAD_TIME_FROM_UNORDERABLE,
    LEAD_TIME_UNKNOWN,
    build_component_forecast,
    lead_times_for,
)

pytestmark = pytest.mark.django_db

User = get_user_model()

FORECAST_URL = "/api/inventory/reports/inventory/serialized_forecast/"


def _item(name, **kwargs):
    defaults = dict(
        name=name,
        description="x",
        sku=f"SKU-{name}",
        reorder_quantity=5,
        current_stock=5,
        minimum_stock=0,
        is_active=True,
    )
    defaults.update(kwargs)
    return InventoryItem.objects.create(**defaults)


def _link(item, name, *, lead=7, unit_cost="1.00", **flags):
    return ItemSupplier.objects.create(
        item=item,
        supplier=Supplier.objects.create(name=name, supplier_type=Supplier.SupplierType.LOCAL),
        supplier_sku=f"{name}-sku",
        unit_cost=Decimal(unit_cost),
        quantity_per_package=1,
        average_lead_time=lead,
        is_primary=flags.get("is_primary", False),
        is_active=flags.get("is_active", True),
        is_discontinued=flags.get("is_discontinued", False),
    )


def _delivered_in(link, days, user):
    """Record one completed delivery of ``days`` against ``link``."""
    from reorder_queue.models import LeadTimeLog, PurchaseOrder

    order = PurchaseOrder.objects.create(
        supplier=link.supplier, status=PurchaseOrder.Status.SENT, created_by=user
    )
    return LeadTimeLog.objects.create(
        item_supplier=link,
        purchase_order=order,
        order_date=timezone.now() - timedelta(days=days + 1),
        expected_delivery_date=(timezone.now() - timedelta(days=1)).date(),
        actual_delivery_date=(timezone.now() - timedelta(days=1)).date(),
        estimated_lead_time_days=days,
        actual_lead_time_days=days,
        quantity_ordered=1,
        quantity_received=1,
    )


def _serialized(name, *, available=3, depleted=9, now, **kwargs):
    """A consumable serialized item depleting 0.1/day with ``available`` in stock.

    ``avg_daily_use`` is 9/90, so ``reorder_point`` is ``ceil(lead / 10)`` with
    no safety stock — one unit of reorder point per ten days of lead time,
    which is enough resolution to tell 7 days from 30.
    """
    item = _item(
        name,
        is_serialized=True,
        serial_tracking_mode=InventoryItem.SerialTrackingMode.CONSUMABLE,
        **kwargs,
    )
    for n in range(available):
        SerializedComponent.objects.create(
            item=item, serial_number=f"{name}-{n}", status=SerializedComponent.Status.IN_STOCK
        )
    for n in range(depleted):
        unit = SerializedComponent.objects.create(
            item=item, serial_number=f"{name}-x{n}", status=SerializedComponent.Status.CONSUMED
        )
        ComponentUsageEvent.objects.create(
            component=unit,
            action=SerializedComponent.Action.CONSUME,
            at=now - timedelta(days=n + 1),
        )
    return item


@pytest.fixture
def buyer():
    return User.objects.create_user(username="lead-time-buyer", password="pw")


@pytest.fixture
def api():
    client = APIClient()
    client.force_authenticate(
        user=User.objects.create_user(
            username="lead-time-staff", password="pw", is_staff=True, is_superuser=True
        )
    )
    return client


# ── The defect, exactly as reported ──────────────────────────────────────────


def test_a_flagged_thirty_day_primary_is_not_costed_at_a_cheaper_vendors_seven(buyer):
    """THE acceptance criterion.

    An operator flagged the 30-day vendor primary. A cheaper 7-day rival also
    carries the part and has one 5-day delivery on record. Before op-3vqk the
    observed average read EVERY link, so the reorder point was computed at 5
    days — roughly a sixth of the real wait — and the item sat below its true
    trigger unflagged.
    """
    now = timezone.now()
    item = _serialized("cheap-rival", now=now)
    _link(item, "SlowButChosen", lead=30, unit_cost="5.00", is_primary=True)
    rival = _link(item, "CheapAndFast", lead=7, unit_cost="1.00")
    _delivered_in(rival, 5, buyer)

    row = build_component_forecast(now=now)[0]

    assert row["lead_time_days"] == 30.0
    assert row["lead_time_basis"] == LEAD_TIME_FROM_ORDERABLE
    # ceil(0.1 * 30) == 3, not ceil(0.1 * 5) == 1.
    assert row["reorder_point"] == 3
    assert row["needs_reorder"] is True


def test_deliveries_from_a_link_we_do_not_buy_from_never_enter_the_average(buyer):
    """The observed mean is that ONE link's deliveries, not the item's.

    A discontinued vendor that took 60 days says nothing about how long the
    live 30-day vendor takes. Averaging them (or, worse, taking the dead one
    alone) inflates the reorder point on a wait nobody can order.
    """
    now = timezone.now()
    item = _serialized("mixed-history", now=now)
    _link(item, "LiveVendor", lead=30, unit_cost="1.00")
    dead = _link(item, "DeadVendor", lead=45, unit_cost="2.00", is_discontinued=True)
    _delivered_in(dead, 60, buyer)

    row = build_component_forecast(now=now)[0]

    # The live link has no deliveries of its own, so its own estimate stands.
    assert row["lead_time_days"] == 30.0
    assert row["lead_time_basis"] == LEAD_TIME_FROM_ORDERABLE
    assert row["reorder_point"] == 3


def test_a_discontinued_flagged_primary_does_not_set_the_reorder_point(buyer):
    """``mark_discontinued`` leaves ``is_primary`` set, so the flag outlives the vendor.

    Before op-3vqk the estimated fallback ordered by ``-is_primary`` and took
    the first row, so a dead flagged primary beat the live link beside it.
    """
    now = timezone.now()
    item = _serialized("dead-flag", now=now)
    _link(item, "DeadFlagged", lead=45, unit_cost="5.00", is_primary=True, is_discontinued=True)
    _link(item, "StillSellsIt", lead=7, unit_cost="1.00")

    row = build_component_forecast(now=now)[0]

    assert row["lead_time_days"] == 7.0
    assert row["lead_time_basis"] == LEAD_TIME_FROM_ORDERABLE
    assert row["reorder_point"] == 1


def test_an_inactive_flagged_primary_does_not_set_the_reorder_point(buyer):
    """Orderability is ``is_active`` AND not ``is_discontinued`` — both halves."""
    now = timezone.now()
    item = _serialized("inactive-flag", now=now)
    _link(item, "InactiveFlagged", lead=30, unit_cost="5.00", is_primary=True, is_active=False)
    _link(item, "StillActive", lead=7, unit_cost="1.00")

    row = build_component_forecast(now=now)[0]

    assert row["lead_time_days"] == 7.0
    assert row["lead_time_basis"] == LEAD_TIME_FROM_ORDERABLE


def test_with_nothing_flagged_the_scored_winner_sets_the_reorder_point(buyer):
    """Not "whichever row the database returned first".

    The pre-op-3vqk estimate ordered by ``-is_primary`` and nothing else, so for
    an item with two unflagged links the answer was whatever the planner
    happened to emit — the same shape could resolve two different ways in one
    request. The derivation ranks on cost and lead time and is a pure function
    of the rows.
    """
    now = timezone.now()
    item = _serialized("unflagged-pair", now=now)
    _link(item, "CheapFast", lead=7, unit_cost="1.00")
    _link(item, "DearSlow", lead=30, unit_cost="2.00")

    row = build_component_forecast(now=now)[0]

    assert row["lead_time_days"] == 7.0
    assert row["lead_time_basis"] == LEAD_TIME_FROM_ORDERABLE


# ── The half that must not be satisfied by dropping the item ────────────────


def test_an_unbuyable_items_lead_time_is_kept_and_attributed_as_unbuyable(buyer):
    """CONTROL for the reverted round, plus the attribution the fix adds.

    Every link is dead. The lead time is still read from all of them — the
    number is real, it is how long that vendor took — so the row keeps its full
    lead component and its flag. What changes is that the row now SAYS the
    number belongs to a supplier nobody can buy from.
    """
    now = timezone.now()
    item = _serialized("all-dead", now=now)
    _link(item, "DeadA", lead=45, unit_cost="5.00", is_primary=True, is_discontinued=True)
    _link(item, "DeadB", lead=20, unit_cost="1.00", is_active=False)

    row = build_component_forecast(now=now)[0]

    assert row["lead_time_days"] == 45.0
    assert row["lead_time_known"] is True
    assert row["lead_time_basis"] == LEAD_TIME_FROM_UNORDERABLE
    assert row["reorder_point"] == 5
    assert row["needs_reorder"] is True


def test_an_unbuyable_items_observed_deliveries_are_still_averaged_across_links(buyer):
    """The fallback is the OLD expression, unchanged, for the population that needs it.

    With nothing orderable there is no "the link we buy from" to scope the mean
    to, so every link's deliveries count — which is what the pre-op-3vqk rule
    did for every item, and what it must keep doing for this one.
    """
    now = timezone.now()
    item = _serialized("all-dead-history", now=now)
    _link(item, "DeadPrimary", lead=45, unit_cost="5.00", is_primary=True, is_discontinued=True)
    other = _link(item, "DeadOther", lead=20, unit_cost="1.00", is_discontinued=True)
    _delivered_in(other, 12, buyer)

    row = build_component_forecast(now=now)[0]

    assert row["lead_time_days"] == 12.0
    assert row["lead_time_basis"] == LEAD_TIME_FROM_UNORDERABLE


def test_no_supplier_recorded_is_not_the_same_fact_as_no_orderable_supplier():
    """The two must never collapse: they point at OPPOSITE operator actions.

    An item with no link has nothing on record (add a supplier); an item whose
    links are all dead has a real wait and an unbuyable vendor (find one that
    still carries it). Only the first suppresses the lead component.
    """
    now = timezone.now()
    _serialized("orphan", now=now)
    unbuyable = _serialized("unbuyable", now=now)
    _link(unbuyable, "GoneAway", lead=30, is_discontinued=True)

    rows = {r["item_name"]: r for r in build_component_forecast(now=now)}

    assert rows["orphan"]["lead_time_basis"] == LEAD_TIME_UNKNOWN
    assert rows["orphan"]["lead_time_known"] is False
    assert rows["orphan"]["lead_time_days"] is None

    assert rows["unbuyable"]["lead_time_basis"] == LEAD_TIME_FROM_UNORDERABLE
    assert rows["unbuyable"]["lead_time_known"] is True
    assert rows["unbuyable"]["lead_time_days"] == 30.0


def test_every_item_keeps_its_row_whatever_its_supplier_shape():
    """The branch invariant: nothing leaves the report.

    Reorder points move; the SET of items on the report does not. This is the
    check op-2rsp round 5 would have failed.
    """
    now = timezone.now()
    _serialized("shape-none", now=now)
    live = _serialized("shape-live", now=now)
    _link(live, "Live", lead=7)
    dead = _serialized("shape-dead", now=now)
    _link(dead, "Dead", lead=30, is_discontinued=True)
    mixed = _serialized("shape-mixed", now=now)
    _link(mixed, "MixedDead", lead=45, is_primary=True, is_discontinued=True)
    _link(mixed, "MixedLive", lead=7)

    names = {r["item_name"] for r in build_component_forecast(now=now)}

    assert names == {"shape-none", "shape-live", "shape-dead", "shape-mixed"}


# ── One resolver, two surfaces ───────────────────────────────────────────────


def test_the_nightly_task_and_the_serialized_report_resolve_the_same_lead_time(buyer):
    """Both surfaces go through ``lead_times_for``, so they cannot disagree.

    The nightly demand forecast used to import the serialized report's resolver
    for the N+1 saving alone; it now inherits the rule as well.
    """
    from inventory.models import DemandForecast
    from inventory.tasks import generate_demand_forecasts
    from reorder_queue.models import PurchaseOrder, PurchaseOrderItem

    item = _item("nightly-agreement", reorder_alerts_enabled=True)
    chosen = _link(item, "NightlyChosen", lead=30, unit_cost="5.00", is_primary=True)
    rival = _link(item, "NightlyRival", lead=7, unit_cost="1.00")
    _delivered_in(rival, 5, buyer)

    for days_ago in (58, 28):
        order = PurchaseOrder.objects.create(
            supplier=chosen.supplier, status=PurchaseOrder.Status.SENT, created_by=buyer
        )
        PurchaseOrderItem.objects.create(
            purchase_order=order,
            item_supplier=chosen,
            quantity_ordered=1,
            unit_cost_ordered=Decimal("1.0000"),
        )
        PurchaseOrder.objects.filter(pk=order.pk).update(
            order_date=timezone.now() - timedelta(days=days_ago)
        )

    resolved = lead_times_for([item])[item.id]
    generate_demand_forecasts()

    assert resolved.days == 30.0
    assert resolved.basis == LEAD_TIME_FROM_ORDERABLE
    stored = DemandForecast.objects.get(item=item)
    assert stored.lead_time_days == 30
    # Due in 2 days, inside the chosen supplier's 30-day wait. At the rival's
    # logged 5 days it would have been flagged too — but at its 7-day estimate
    # this item is only ever flagged on the number it will actually wait.
    assert stored.needs_reorder is True


def test_the_resolver_costs_a_bounded_number_of_queries(django_assert_max_num_queries, buyer):
    """No N+1 across a page: three queries whatever the number of items.

    One supplier derivation, one ``LeadTimeLog`` aggregate covering both
    branches, and one ``ItemSupplier`` scan for the unbuyable population.
    """
    now = timezone.now()
    items = []
    for n in range(6):
        item = _serialized(f"budget-{n}", now=now)
        _link(item, f"BudgetLive-{n}", lead=7)
        _link(item, f"BudgetDead-{n}", lead=45, is_discontinued=True)
        items.append(item)
    orphan = _serialized("budget-orphan", now=now)
    unbuyable = _serialized("budget-unbuyable", now=now)
    _link(unbuyable, "BudgetAllDead", lead=30, is_discontinued=True)
    items += [orphan, unbuyable]

    with django_assert_max_num_queries(3):
        resolved = lead_times_for(items)

    assert len(resolved) == len(items)


# ── The payload the operator's screen reads ─────────────────────────────────


def test_the_three_bases_are_distinct_literals_the_front_end_can_match_on():
    """The wire values are the contract, not the symbol names.

    ``SerializedForecastLeadBasis`` in ``frontend/src/services/api.ts`` is a
    union of these exact strings and ``SerializedForecastPanel`` branches on
    them, so renaming one value silently stops a member-facing screen wording
    the difference. Asserting ``basis == LEAD_TIME_FROM_UNORDERABLE`` elsewhere
    in this module cannot catch that — both sides of the comparison move
    together — which is why the literals are pinned here.
    """
    assert LEAD_TIME_FROM_ORDERABLE == "orderable_supplier"
    assert LEAD_TIME_FROM_UNORDERABLE == "unorderable_supplier"
    assert LEAD_TIME_UNKNOWN == "no_supplier"
    # And, above all, that no two of them are the same string: an unbuyable
    # vendor and a missing one are opposite operator actions.
    assert len({LEAD_TIME_FROM_ORDERABLE, LEAD_TIME_FROM_UNORDERABLE, LEAD_TIME_UNKNOWN}) == 3


def test_the_basis_reaches_the_serialized_forecast_endpoint(api):
    """``lead_time_basis`` is on the wire, so a surface can word the difference.

    Additive: no existing key changes type or nullability. ScanTTY's
    ``ComponentForecastRow`` ignores fields it does not declare.
    """
    now = timezone.now()
    live = _serialized("wire-live", now=now)
    _link(live, "WireLive", lead=7)
    dead = _serialized("wire-dead", now=now)
    _link(dead, "WireDead", lead=30, is_discontinued=True)
    _serialized("wire-orphan", now=now)

    response = api.get(FORECAST_URL)

    assert response.status_code == 200, response.content
    by_name = {row["item_name"]: row for row in response.data}
    assert by_name["wire-live"]["lead_time_basis"] == LEAD_TIME_FROM_ORDERABLE
    assert by_name["wire-dead"]["lead_time_basis"] == LEAD_TIME_FROM_UNORDERABLE
    assert by_name["wire-orphan"]["lead_time_basis"] == LEAD_TIME_UNKNOWN
    # The keys that were already there keep their shape.
    assert by_name["wire-dead"]["lead_time_days"] == 30.0
    assert by_name["wire-orphan"]["lead_time_days"] is None
