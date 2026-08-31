"""A value we do not know must never make an item look adequately stocked (op-c1ke).

The rule, in one sentence: **a value the system does not know must never be
presented, computed with, or compared against as though it were a known number
— and must never make an item look adequately stocked.**

The branch invariant these pin: *no item's alerting or flagging behaviour
changes versus base EXCEPT where base was suppressing an alert because a value
was unknown.* Every test below is labelled BEFORE/AFTER where the flag moves,
and CONTROL where it must not.

The four rounds this class already destroyed (PR #1035) all failed the same way
— the reader set was recalled rather than derived, so a fix at one site opened
another. The two facts that were collapsed there are kept apart here and each
has its own test: "no supplier link recorded" (a data gap) and "no supplier you
can order from" (unbuyable) point in OPPOSITE directions, and treating them
alike over-flagged one population while under-flagging the other.
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
from inventory.services.component_forecast import build_component_forecast
from inventory.services.demand_forecast_engine import forecast_item_by_interval
from inventory.services.packaging import low_stock_q, reorder_display

pytestmark = pytest.mark.django_db

User = get_user_model()


def _item(name="Widget", **kwargs):
    defaults = dict(
        name=name,
        description="x",
        sku=f"SKU-{name}",
        reorder_quantity=5,
        current_stock=0,
        minimum_stock=10,
        is_active=True,
    )
    defaults.update(kwargs)
    return InventoryItem.objects.create(**defaults)


def _link(item, name, *, pack=1, lead=7, unit_cost="1.00", **flags):
    return ItemSupplier.objects.create(
        item=item,
        supplier=Supplier.objects.create(name=name, supplier_type=Supplier.SupplierType.LOCAL),
        supplier_sku=f"{name}-sku",
        unit_cost=Decimal(unit_cost),
        quantity_per_package=pack,
        average_lead_time=lead,
        is_primary=flags.get("is_primary", False),
        is_active=flags.get("is_active", True),
        is_discontinued=flags.get("is_discontinued", False),
    )


def _case_item(name, *, stock=10, minimum_stock=10, minimum_cases=1):
    return _item(
        name,
        current_stock=stock,
        minimum_stock=minimum_stock,
        use_case_based_reorder=True,
        minimum_cases=minimum_cases,
        reorder_cases=2,
    )


def _fresh(item):
    return InventoryItem.objects.get(pk=item.pk)


@pytest.fixture
def api():
    client = APIClient()
    client.force_authenticate(
        user=User.objects.create_user(
            username="stockroom", password="pw", is_staff=True, is_superuser=True
        )
    )
    return client


# ── Site 1: the pack-size fallback that read loose units as cases ────────────


def test_a_known_case_size_is_completely_unaffected():
    """CONTROL. Nothing unknown anywhere: same count, same flag, same words."""
    item = _case_item("Thinner")
    _link(item, "StillHere", pack=50)
    fresh = _fresh(item)

    assert fresh.current_cases == pytest.approx(0.2)
    assert fresh.needs_reorder is True
    assert reorder_display(fresh)["text"] == "0.2 cases on hand · reorder at 1 case"


def test_a_case_size_of_one_is_a_known_case_size():
    """CONTROL. A vendor selling singles is not a vendor that told us nothing.

    The column defaults to 1, so reading 1 as "missing" would make every
    unconfigured link unknown — a flood, which suppresses alerts of its own.
    """
    item = _case_item("Singles", stock=3, minimum_stock=0, minimum_cases=5)
    _link(item, "SellsSingles", pack=1)
    fresh = _fresh(item)

    assert fresh.current_cases == 3
    assert fresh.needs_reorder is True


def test_a_case_based_item_with_a_dead_supplier_still_counts_its_cases():
    """CONTROL, and the alert op-2rsp round 1 destroyed.

    The box on the shelf came from a vendor we can no longer buy from; their
    recorded pack size still describes it. Routing this through the
    orderability filter made ``current_cases`` return the raw 10, so ``10 <= 1``
    was False and the item whose last supplier just died dropped off every
    low-stock surface.
    """
    item = _case_item("Solvent")
    _link(item, "GoneAway", pack=50, is_discontinued=True)
    fresh = _fresh(item)

    assert fresh.current_cases == pytest.approx(0.2)
    assert fresh.needs_reorder is True


def test_a_case_based_item_with_no_supplier_link_is_no_longer_read_as_cases():
    """BEFORE: ``current_cases`` 10.0, ``needs_reorder`` False (SUPPRESSED).
    AFTER:  ``current_cases`` None, ``needs_reorder`` True.

    Nothing records how many units a box holds, so base fell through to "1 unit
    per package": ten loose units read as ten cases, ``10 <= 1`` was False, and
    an item sitting AT its base-unit reorder point read as well stocked.
    """
    fresh = _fresh(_case_item("Orphan"))

    assert fresh.current_cases is None
    assert fresh.needs_reorder is True


def test_a_case_based_item_whose_link_records_a_zero_case_is_no_longer_read_as_cases():
    """BEFORE: ``current_cases`` 10.0, ``needs_reorder`` False (SUPPRESSED).
    AFTER:  ``current_cases`` None, ``needs_reorder`` True.

    A box holding no units is not a box. ``PositiveIntegerField`` permits the 0
    and ``MinValueValidator(1)`` only bites under ``full_clean()``, so posting
    ``quantity_per_package: 0`` to the item endpoint persists it.
    """
    item = _case_item("ZeroPack")
    _link(item, "ImpossibleBox", pack=0)
    fresh = _fresh(item)

    assert fresh.current_cases is None
    assert fresh.needs_reorder is True


def test_an_unknown_case_size_never_removes_an_alert_base_raised():
    """The invariant is one-directional: unknowns may ADD a flag, never remove one.

    Small stock under a large ``minimum_cases`` is the shape where base's own
    (dimensionally confused) comparison was the only one flagging, so the
    base-unit fallback keeps it.
    """
    fresh = _fresh(_case_item("Trickle", stock=1, minimum_stock=0, minimum_cases=5))

    assert fresh.current_cases is None
    assert fresh.needs_reorder is True


def test_an_unknown_case_size_does_not_flag_an_item_that_is_well_stocked():
    """And it is NOT a flag that ignores stock.

    Round 4 of #1035 made every item with no supplier link permanently
    ``needs_reorder`` regardless of stock. Flagging a data-gap population that
    way floods the surface until people stop reading it, which suppresses
    alerts too. Plenty of stock, both floors clear: not flagged.
    """
    fresh = _fresh(_case_item("WellStocked", stock=500, minimum_stock=10, minimum_cases=1))

    assert fresh.current_cases is None
    assert fresh.needs_reorder is False


def test_the_property_and_its_sql_twin_agree_where_minimum_cases_fits_in_minimum_stock():
    """The divergence this closes — and exactly how far it closes.

    BEFORE: ``low_stock_q()`` matched the item (it has always compared base
    units for these) while ``needs_reorder`` said False — so the reorder
    recommendations engine listed it while the item detail, the low-stock
    action and the admin all called it well stocked.

    The agreement is CONDITIONAL, not universal: ``needs_reorder``'s first
    disjunct is the query's own predicate, so the two match wherever
    ``minimum_cases <= minimum_stock``. Here that is 1 <= 10. The other side of
    that boundary is pinned by the test below.
    """
    fresh = _fresh(_case_item("SplitBrain", minimum_stock=10, minimum_cases=1))

    assert fresh.minimum_cases <= fresh.minimum_stock
    assert fresh.needs_reorder is True
    assert InventoryItem.objects.filter(low_stock_q(), pk=fresh.pk).exists() is True


def test_above_that_boundary_the_property_still_flags_what_the_query_does_not():
    """The residual divergence, pinned deliberately rather than left to luck.

    Where ``minimum_cases > minimum_stock`` the property's SECOND disjunct —
    base's own comparison, kept so an unknown can never REMOVE an alert base
    raised — flags an item ``low_stock_q()`` does not match. That is the
    PRE-EXISTING divergence direction. Closing it by dropping the disjunct would
    delete an alert base raised, which the branch invariant forbids, so it stays
    and is asserted here so nobody has to guess whether it was intended.
    """
    fresh = _fresh(_case_item("OverTheBoundary", stock=1, minimum_stock=0, minimum_cases=5))

    assert fresh.minimum_cases > fresh.minimum_stock
    assert fresh.needs_reorder is True
    assert InventoryItem.objects.filter(low_stock_q(), pk=fresh.pk).exists() is False


def test_the_badge_and_the_threshold_printed_beside_it_never_contradict_each_other():
    """One payload must not call an item LOW and then print a threshold it clears.

    With the column defaults (``minimum_stock`` 0, ``minimum_cases`` 1) and one
    unit on hand, the item is flagged through the second disjunct while
    ``reorder_threshold`` reported the bare ``minimum_stock`` — so the badge said
    LOW beside "1 unit on hand · reorder at 0 units", and the kanban card that
    outlives the screen printed "Reorder at: 0 units". The threshold reported is
    now ``max(minimum_stock, minimum_cases)``, the exact boundary the flag uses.
    """
    from index_cards.services import IndexCardRenderer

    fresh = _fresh(_case_item("Contradiction", stock=1, minimum_stock=0, minimum_cases=1))
    display = reorder_display(fresh)

    assert display["needs_reorder"] is True
    assert display["threshold"] == 1
    assert display["current"] <= display["threshold"]
    assert display["text"] == "1 unit on hand · reorder at 1 unit"
    assert (
        IndexCardRenderer(base_url="http://localhost:3000")._reorder_at_line(fresh)
        == "Reorder at: 1 unit"
    )


def test_the_kanban_card_never_prints_a_case_count_it_cannot_compute():
    """The card outlives the screen it was printed from.

    BEFORE: "10 cases on hand · reorder at 1 case" for ten loose units.
    AFTER:  the same numbers named in the unit that CAN be counted.
    """
    display = reorder_display(_fresh(_case_item("Printed")))

    assert display["unit"] == "unit"
    assert display["current"] == 10
    assert display["text"] == "10 units on hand · reorder at 10 units"
    assert "cases" not in display["text"]


# ── The API surfaces that crashed in round 5 ─────────────────────────────────


def test_the_item_detail_renders_for_an_item_whose_case_size_is_unknown(api):
    """Round 5 made this value null server-side and left three ``.toFixed(1)``
    calls declaring it a number, which blanked the item-detail and scan pages.
    The wire contract is pinned here; the web consumers move in the same commit.
    """
    item = _case_item("DetailPage")
    _link(item, "ImpossibleBox2", pack=0)

    response = api.get(f"/api/inventory/items/{item.id}/")

    assert response.status_code == 200, response.content
    assert response.data["current_cases"] is None
    assert response.data["needs_reorder"] is True
    assert response.data["reorder_display"]["current"] == 10


def test_the_item_list_renders_for_an_item_whose_case_size_is_unknown(api):
    _case_item("ListPage")

    response = api.get("/api/inventory/items/", {"search": "ListPage"})

    assert response.status_code == 200, response.content
    rows = response.data["results"] if "results" in response.data else response.data
    row = next(r for r in rows if r["name"] == "ListPage")
    assert row["current_cases"] is None


def test_item_metrics_reports_an_impossible_case_size_as_unknown(api):
    """``case_size`` is the pinned ScanTTY contract — already ``*int`` there.

    BEFORE: 0, a confident number for a box that holds nothing.
    AFTER:  null, which the field already permitted.
    """
    item = _case_item("Metrics")
    _link(item, "ImpossibleBox3", pack=0)

    response = api.get(f"/api/inventory/items/{item.id}/metrics/")

    assert response.status_code == 200, response.content
    assert response.data["case_size"] is None


def test_item_metrics_still_quotes_a_real_case_size(api):
    """CONTROL for the ScanTTY contract: a known case size is unchanged."""
    item = _case_item("MetricsOK")
    _link(item, "RealBox", pack=24)

    response = api.get(f"/api/inventory/items/{item.id}/metrics/")

    assert response.data["case_size"] == 24


def test_one_payload_carries_an_unknown_shelf_case_beside_a_buyable_case_of_24(api):
    """The shelf-vs-order split, working as designed. NOT a contradiction.

    ``mark_discontinued`` deliberately leaves ``is_primary`` set and
    ``ItemSupplier.Meta.ordering`` is ``["-is_primary", "unit_cost"]``, so a dead
    flagged-primary row sorts FIRST. Give an item that row recording an
    impossible ``quantity_per_package`` of 0, plus a live row recording 24, and
    one payload carries ``current_cases: null`` alongside
    ``quantity_per_package: 24`` and ``case_size: 24``.

    The two fields answer DIFFERENT questions and both answers are true:

    * ``current_cases`` counts the box ALREADY ON THE SHELF, whose size the row
      that sold it records as an impossible 0 — so the count is unknown, and the
      page says "case size unknown". Scanning past that row to the live one
      would substitute a different vendor's box for the one actually sitting
      there, which is a guess, not a better answer.
    * ``quantity_per_package`` / ``case_size`` size the box the NEXT ORDER ships
      in, through the orderability-filtered derivation — 24, from the only
      vendor we can buy from.

    Collapsing these into one number is a bug in either direction: filtering the
    shelf for orderability is what suppressed a low-stock alert in op-2rsp round
    1, and letting a dead vendor size an order quotes a case nobody can buy. A
    future reader must not "fix" this pairing.
    """
    item = _case_item("TwoLinks")
    _link(item, "DeadFlaggedPrimary", pack=0, is_primary=True, is_discontinued=True)
    _link(item, "LiveVendor", pack=24)

    response = api.get(f"/api/inventory/items/{item.id}/")
    metrics = api.get(f"/api/inventory/items/{item.id}/metrics/")

    assert response.status_code == 200, response.content
    assert response.data["current_cases"] is None
    assert response.data["quantity_per_package"] == 24
    assert metrics.data["case_size"] == 24


# ── Sites 2 and 3: the lead time ─────────────────────────────────────────────
#
# ``ItemSupplier.average_lead_time`` is non-nullable with a default, so ANY link
# supplies an estimate — a discontinued one included. The whole population that
# reaches "no lead time known" is therefore items with NO supplier link at all,
# and these pin both halves of that: the unbuyable item keeps its full
# threshold, and the data-gap item is not turned into a permanent alert.


def _serialized(name, *, dead_link=False, no_link=False, available=3, depleted=9, now=None):
    """A consumable serialized item depleting ~0.1/day with ``available`` in stock."""
    item = _item(
        name,
        is_serialized=True,
        minimum_stock=0,
        serial_tracking_mode=InventoryItem.SerialTrackingMode.CONSUMABLE,
    )
    if dead_link:
        _link(item, f"Dead-{name}", lead=30, is_discontinued=True)
    elif not no_link:
        _link(item, f"Live-{name}", lead=30)
    for n in range(available):
        SerializedComponent.objects.create(
            item=item, serial_number=f"{name}-{n}", status=SerializedComponent.Status.IN_STOCK
        )
    for n in range(depleted):
        unit = SerializedComponent.objects.create(
            item=item,
            serial_number=f"{name}-x{n}",
            status=SerializedComponent.Status.CONSUMED,
        )
        ComponentUsageEvent.objects.create(
            component=unit,
            action=SerializedComponent.Action.CONSUME,
            at=now - timedelta(days=n + 1),
        )
    return item


def test_the_serialized_forecast_keeps_a_dead_vendors_lead_time():
    """CONTROL, and the AC that names it.

    An item whose only link is discontinued is still flagged low on stock: the
    dead vendor's recorded lead time still says how long a replacement takes to
    arrive, so the reorder point keeps its lead component. Routing this through
    the orderability filter — which op-2rsp briefly did — drops the item off the
    report entirely.
    """
    now = timezone.now()
    _serialized("cf-dead", dead_link=True, now=now)

    row = build_component_forecast(now=now)[0]

    assert row["lead_time_days"] == 30.0
    assert row["lead_time_known"] is True
    assert row["reorder_point"] == 3
    assert row["needs_reorder"] is True


def test_the_serialized_forecast_states_a_missing_lead_time_instead_of_assuming_zero_days():
    """BEFORE: ``reorder_point`` 0, presented as the classic reorder point.
    AFTER:  the same number, reported as a LOWER BOUND — the safety stock
    alone, with ``lead_time_known`` false saying the lead component is missing.

    The flag is deliberately unchanged. This population is exactly the items
    with no supplier link at all; flagging them regardless of what a lead time
    would have said turns a data gap into a permanent alert, which is round 4's
    failure. The actionable fact is the missing supplier, and the row says it.
    """
    now = timezone.now()
    _serialized("cf-orphan", no_link=True, now=now)

    row = build_component_forecast(now=now)[0]

    assert row["lead_time_days"] is None
    assert row["lead_time_known"] is False
    assert row["reorder_point"] == 0
    assert row["needs_reorder"] is False


def test_a_genuine_zero_day_lead_time_is_not_the_same_as_an_unknown_one():
    """The falsy guard swallowed both. They are different facts.

    A vendor you collect from the same afternoon is a KNOWN zero, and the row
    must say so — otherwise a later edit can re-collapse the two without any
    test noticing.
    """
    now = timezone.now()
    item = _serialized("cf-sameday", no_link=True, now=now)
    _link(item, "CollectToday", lead=0)

    row = build_component_forecast(now=now)[0]

    assert row["lead_time_days"] == 0.0
    assert row["lead_time_known"] is True


def test_the_demand_forecast_flags_an_item_whose_only_supplier_is_discontinued():
    """CONTROL, and the AC that names it: due in 2 days, 7-day lead, flagged."""
    now = timezone.now()
    item = _item("df-dead", current_stock=5)
    _link(item, "DfDead", lead=7, is_discontinued=True)
    events = [now.date() - timedelta(days=58), now.date() - timedelta(days=28)]

    result = forecast_item_by_interval(item, events, now=now, lead_time_days=7.0)

    assert result.days_until_due == 2.0
    assert result.needs_reorder is True


def test_the_demand_forecast_falls_back_to_the_due_date_when_no_lead_time_is_known():
    """With no lead time the only thing the engine can still say is WHEN it is due.

    The threshold is the due date itself — flagged once due, never earlier —
    and the row records ``lead_time_days: None`` so nobody reads a horizon into
    it. Not widened into "flag it anyway" for the same data-gap reason above.
    """
    now = timezone.now()
    item = _item("df-orphan", current_stock=5)
    events = [now.date() - timedelta(days=58), now.date() - timedelta(days=28)]

    due_soon = forecast_item_by_interval(item, events, now=now, lead_time_days=None)
    overdue = forecast_item_by_interval(
        item,
        [now.date() - timedelta(days=90), now.date() - timedelta(days=60)],
        now=now,
        lead_time_days=None,
    )

    assert due_soon.days_until_due == 2.0
    assert due_soon.needs_reorder is False
    assert due_soon.lead_time_days is None
    # Overdue is still flagged: the unknown never suppresses what IS knowable.
    assert overdue.days_until_due < 0
    assert overdue.needs_reorder is True


def test_a_known_zero_day_lead_time_flags_exactly_on_the_due_date():
    """CONTROL for the falsy guard: a recorded 0 keeps its own branch."""
    now = timezone.now()
    item = _item("df-sameday", current_stock=5)
    events = [now.date() - timedelta(days=60), now.date() - timedelta(days=30)]

    result = forecast_item_by_interval(item, events, now=now, lead_time_days=0.0)

    assert result.days_until_due == 0.0
    assert result.needs_reorder is True
    assert result.lead_time_days == 0


# ── The nightly run, end to end ──────────────────────────────────────────────


def _bought_on(item, days_ago, *, user):
    """Record a purchase of ``item`` ``days_ago`` days back."""
    from reorder_queue.models import PurchaseOrder, PurchaseOrderItem

    link = ItemSupplier.objects.filter(item=item).first()
    order = PurchaseOrder.objects.create(
        supplier=link.supplier, status=PurchaseOrder.Status.SENT, created_by=user
    )
    PurchaseOrderItem.objects.create(
        purchase_order=order,
        item_supplier=link,
        quantity_ordered=1,
        unit_cost_ordered=Decimal("1.0000"),
    )
    PurchaseOrder.objects.filter(pk=order.pk).update(
        order_date=timezone.now() - timedelta(days=days_ago)
    )


def test_an_item_whose_only_supplier_died_reaches_the_report_and_the_digest():
    """The AC in full, through the real nightly task.

    An item bought every 30 days, last bought 28 days ago, whose only supplier
    link has since been discontinued: it is due inside its recorded lead time,
    so it must appear in the stored demand-forecast report AND in the in-app
    reorder digest. This is what op-2rsp round 1/5 broke by resolving the lead
    time through the orderability filter — the item then had no lead time, its
    threshold collapsed to zero days, and it silently left both surfaces.
    """
    from inventory.models import DemandForecast
    from inventory.services.demand_forecast import (
        latest_demand_forecasts,
        reorder_alert_forecasts,
    )
    from inventory.tasks import REORDER_DIGEST_KIND, generate_demand_forecasts
    from notifications.models import Notification

    buyer = User.objects.create_user(username="buyer", password="pw")
    User.objects.create_user(username="boss", password="pw", is_staff=True, is_active=True)

    item = _item("LastVendorDied", current_stock=5, reorder_alerts_enabled=True)
    link = _link(item, "DiedLastWeek", lead=7)
    _bought_on(item, 58, user=buyer)
    _bought_on(item, 28, user=buyer)
    ItemSupplier.objects.filter(pk=link.pk).update(is_discontinued=True)

    generate_demand_forecasts()

    row = DemandForecast.objects.get(item=item)
    assert row.lead_time_days == 7
    assert row.days_until_due == 2.0
    assert row.needs_reorder is True
    assert item in [f.item for f in latest_demand_forecasts(low_stock_only=True)]
    assert item in [f.item for f in reorder_alert_forecasts()]

    digest = Notification.objects.filter(metadata__kind=REORDER_DIGEST_KIND).first()
    assert digest is not None
    assert str(item.id) in digest.metadata["item_ids"]
