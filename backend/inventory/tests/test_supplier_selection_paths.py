"""Every surface that answers "which supplier?" answers it the same way (op-2rsp).

``inventory/tests/test_supplier_selection.py`` pins the derivation itself. These
tests go through the REAL endpoints and services the derivation feeds, because
the defect this closes was never in the rule — it was that six surfaces read a
rule with no orderability filter while the reorder recommendations engine
applied one, so the same item could be quoted from a supplier that had been
marked discontinued.

The fixture shape is deliberately the awkward one: **the cheapest supplier is
discontinued**. Under the old rule it won every one of these surfaces, because
selection was ``ORDER BY -is_primary, unit_cost`` with nothing filtered.

``_find_best_supplier`` — once the rival rule — now delegates here too, so the
last two surfaces that could name different suppliers for one item agree; see
``test_the_order_pad_and_the_recommendations_name_the_same_supplier``, which
could not be written while the two rules disagreed AND the second one crashed.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from inventory.models import InventoryItem, ItemSupplier, Supplier
from reorder_queue.models import ReorderRequest

pytestmark = pytest.mark.django_db


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


def _link(item, name, *, unit_cost, lead=7, **flags):
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


def _cheap_dead_dear_live(item):
    """The awkward pair: cheapest is discontinued, only the dear one is buyable."""
    dead = _link(item, "DeadCheap", unit_cost="1.00", lead=1, is_discontinued=True)
    live = _link(item, "LiveDear", unit_cost="9.00", lead=20)
    return dead, live


@pytest.fixture
def api():
    client = APIClient()
    client.force_authenticate(
        user=get_user_model().objects.create_user(
            username="purchasing", password="pw", is_staff=True, is_superuser=True
        )
    )
    return client


# ── Item detail / list serializer: the flat compat fields ScanTTY reads ──────


def test_item_detail_never_names_a_discontinued_supplier(api):
    item = _item("Bolt")
    _cheap_dead_dear_live(item)

    response = api.get(f"/api/inventory/items/{item.id}/")

    assert response.status_code == 200, response.content
    assert response.data["supplier_name"] == "LiveDear"
    assert response.data["supplier_sku"] == "LiveDear-sku"
    assert Decimal(str(response.data["unit_cost"])) == Decimal("9.00")
    # The full array still shows every link, discontinued ones included — the
    # page dims them. Hiding them would lose the operator's own history.
    assert {row["supplier_name"] for row in response.data["all_suppliers"]} == {
        "DeadCheap",
        "LiveDear",
    }


def test_item_detail_reports_no_supplier_when_none_is_orderable(api):
    item = _item("Orphan")
    _link(item, "DeadOnly", unit_cost="1.00", is_discontinued=True)

    response = api.get(f"/api/inventory/items/{item.id}/")

    assert response.status_code == 200, response.content
    # Not the dead one dressed up as the choice. The link is still listed, so
    # the operator can see WHY there is no supplier name.
    assert response.data["supplier_name"] is None
    assert response.data["unit_cost"] is None
    assert [row["supplier_name"] for row in response.data["all_suppliers"]] == ["DeadOnly"]


# ── /metrics/ — the pinned ScanTTY TUI contract ──────────────────────────────


def test_metrics_costs_and_lead_time_come_from_the_orderable_supplier(api):
    item = _item("Nut")
    _cheap_dead_dear_live(item)

    response = api.get(f"/api/inventory/items/{item.id}/metrics/")

    assert response.status_code == 200, response.content
    assert Decimal(str(response.data["unit_cost"])) == Decimal("9.00")
    assert response.data["lead_time_days"] == 20


def test_metrics_says_nothing_rather_than_quoting_a_dead_supplier(api):
    item = _item("Ghost")
    _link(item, "DeadOnly", unit_cost="1.00", lead=1, is_discontinued=True)

    response = api.get(f"/api/inventory/items/{item.id}/metrics/")

    assert response.status_code == 200, response.content
    assert response.data["unit_cost"] is None
    assert response.data["lead_time_days"] is None


def test_metrics_batch_and_detail_agree_on_the_same_item(api):
    """``?with_metrics=1`` (batch) must not diverge from ``/metrics/`` (single)."""
    item = _item("Washer")
    _cheap_dead_dear_live(item)

    single = api.get(f"/api/inventory/items/{item.id}/metrics/")
    listing = api.get("/api/inventory/items/?with_metrics=1")

    assert single.status_code == 200 and listing.status_code == 200
    rows = [row for row in listing.data["results"] if str(row["id"]) == str(item.id)]
    assert rows, listing.data
    assert rows[0]["metrics"]["unit_cost"] == single.data["unit_cost"]
    assert rows[0]["metrics"]["lead_time_days"] == single.data["lead_time_days"]


# ── The order pad: a part number an operator pastes into a vendor's site ─────


def test_order_pad_groups_under_a_supplier_that_still_sells_the_item(api):
    item = _item("Screw")
    _cheap_dead_dear_live(item)
    ReorderRequest.objects.create(
        item=item,
        quantity=3,
        status=ReorderRequest.Status.APPROVED,
        requested_by="tester",
    )

    response = api.get("/api/reorders/requests/generate_cart_links/")

    assert response.status_code == 200, response.content
    assert "DeadCheap" not in response.data
    assert "LiveDear" in response.data
    assert "LiveDear-sku" in response.data["LiveDear"]["csv"]


def test_order_pad_omits_an_item_no_supplier_can_fill(api):
    item = _item("Unbuyable")
    _link(item, "DeadOnly", unit_cost="1.00", is_discontinued=True)
    ReorderRequest.objects.create(
        item=item,
        quantity=3,
        status=ReorderRequest.Status.APPROVED,
        requested_by="tester",
    )

    response = api.get("/api/reorders/requests/generate_cart_links/")

    assert response.status_code == 200, response.content
    assert response.data == {}


# ── Pending requests grouped by supplier ─────────────────────────────────────


def test_pending_requests_group_under_the_orderable_supplier(api):
    item = _item("Anchor")
    _cheap_dead_dear_live(item)
    ReorderRequest.objects.create(
        item=item,
        quantity=2,
        status=ReorderRequest.Status.PENDING,
        requested_by="tester",
    )

    response = api.get("/api/reorders/requests/by_supplier/")

    assert response.status_code == 200, response.content
    assert {group["supplier"] for group in response.data} == {"LiveDear"}


def test_pending_request_with_no_orderable_supplier_is_not_filed_under_a_dead_one(api):
    item = _item("Stranded")
    _link(item, "DeadOnly", unit_cost="1.00", is_discontinued=True)
    ReorderRequest.objects.create(
        item=item,
        quantity=2,
        status=ReorderRequest.Status.PENDING,
        requested_by="tester",
    )

    response = api.get("/api/reorders/requests/by_supplier/")

    assert response.status_code == 200, response.content
    # "No Supplier" is the existing bucket for an item with nothing to buy from;
    # what matters is that the discontinued vendor does not get a heading.
    assert "DeadOnly" not in {group["supplier"] for group in response.data}


# ── The PO-building screen names what it cannot offer ────────────────────────


def test_reorder_data_names_a_low_item_no_supplier_can_fill(api):
    stranded = _item("Stranded")
    _link(stranded, "DeadOnly", unit_cost="1.00", is_discontinued=True)
    _item("Bare")
    buyable = _item("Buyable")
    _link(buyable, "Live", unit_cost="2.00")

    response = api.get("/api/reorders/purchase-orders/reorder_data/")

    assert response.status_code == 200, response.content
    reported = {row["item_name"]: row for row in response.data["items_without_orderable_supplier"]}
    assert set(reported) == {"Stranded", "Bare"}

    # The two reasons are distinct, and each names an action the operator can take.
    assert reported["Stranded"]["reason"] == "none_orderable"
    assert "discontinued" in reported["Stranded"]["detail"]
    assert "add a supplier" in reported["Stranded"]["detail"]

    assert reported["Bare"]["reason"] == "no_suppliers"
    assert "No supplier is linked" in reported["Bare"]["detail"]

    # The buyable item is still offered as normal, under its live supplier only.
    assert {group["name"] for group in response.data["suppliers"]} == {"Live"}


def test_reorder_data_offers_no_supplier_group_for_a_discontinued_only_item(api):
    item = _item("Screw")
    _cheap_dead_dear_live(item)

    response = api.get("/api/reorders/purchase-orders/reorder_data/")

    assert response.status_code == 200, response.content
    assert {group["name"] for group in response.data["suppliers"]} == {"LiveDear"}
    assert response.data["items_without_orderable_supplier"] == []


# ── Lead-time forecasting reads the same supplier as everything else ─────────


def _serialized_item(name):
    return _item(name, is_serialized=True)


def _forecast_row(item, **kwargs):
    from inventory.services.component_forecast import build_component_forecast

    rows = [row for row in build_component_forecast(**kwargs) if row["item_id"] == str(item.id)]
    assert rows, "item missing from forecast"
    return rows[0]


def _stock_and_daily_use(item, *, in_stock, consumed_today):
    """``in_stock`` available units, and ``consumed_today`` depletions in a 1-day window.

    With ``window_days=1`` the depletion rate is exactly ``consumed_today`` per
    day, which keeps the reorder-point arithmetic legible.
    """
    from inventory.models import ComponentUsageEvent, SerializedComponent

    for i in range(in_stock):
        SerializedComponent.objects.create(
            item=item,
            serial_number=f"{item.sku}-stock-{i}",
            status=SerializedComponent.Status.IN_STOCK,
        )
    for i in range(consumed_today):
        component = SerializedComponent.objects.create(
            item=item,
            serial_number=f"{item.sku}-used-{i}",
            status=SerializedComponent.Status.CONSUMED,
        )
        ComponentUsageEvent.objects.create(
            component=component,
            action=SerializedComponent.Action.CONSUME,
            at=timezone.now() - timedelta(hours=1),
        )


def _observed_delivery(link, days):
    """Record an actual delivery of ``days`` against ``link``.

    The forecast prefers OBSERVED history over the supplier's estimate, so a
    test that creates no ``LeadTimeLog`` never reaches that branch at all.
    """
    from reorder_queue.models import LeadTimeLog, PurchaseOrder

    user = get_user_model().objects.filter(username="forecast").first() or (
        get_user_model().objects.create_user(username="forecast", password="pw")
    )
    po = PurchaseOrder.objects.create(supplier=link.supplier, created_by=user)
    return LeadTimeLog.objects.create(
        item_supplier=link,
        purchase_order=po,
        order_date=timezone.now() - timedelta(days=days + 5),
        expected_delivery_date=(timezone.now() - timedelta(days=5)).date(),
        actual_delivery_date=timezone.now().date(),
        estimated_lead_time_days=days,
        actual_lead_time_days=days,
        quantity_ordered=1,
        quantity_received=1,
    )


def test_forecast_lead_time_uses_the_orderable_supplier():
    item = _serialized_item("Fuse")
    _cheap_dead_dear_live(item)

    assert _forecast_row(item)["lead_time_days"] == 20.0


def test_forecast_lead_time_is_unknown_when_nothing_is_orderable():
    """``None`` is "we cannot tell you", which is the truth — not a dead vendor's 1 day.

    The dead link carries DELIVERY HISTORY, so this drives the observed branch —
    the one that takes precedence. Without those logs the test passed for the
    wrong reason: it exercised only the estimated fallback, which is how the
    observed branch went on reading dead links unnoticed.
    """
    item = _serialized_item("Relay")
    dead = _link(item, "DeadOnly", unit_cost="1.00", lead=1, is_discontinued=True)
    _observed_delivery(dead, 45)

    assert _forecast_row(item)["lead_time_days"] is None


def test_a_dead_vendors_delivery_history_does_not_contaminate_the_forecast():
    """45 days of history from a vendor who no longer sells it, 7 from one who does.

    Averaging both would forecast a 26-day wait that nobody will ever make you
    serve, and would inflate the reorder point of every item a lapsed vendor
    was ever slow on.
    """
    item = _serialized_item("Contactor")
    dead = _link(item, "DeadSlow", unit_cost="1.00", lead=1, is_discontinued=True)
    live = _link(item, "LiveQuick", unit_cost="9.00", lead=30)
    _observed_delivery(dead, 45)
    _observed_delivery(live, 7)

    assert _forecast_row(item)["lead_time_days"] == 7.0


def test_forecast_lead_time_follows_the_flagged_primary_not_a_faster_rival():
    """History against a vendor we will NOT buy from must not set the reorder point.

    The operator flagged SlowVendor primary, so the gate makes it binding on
    every other surface. Delivery history exists only against a live FastVendor
    link averaging 7 days. Averaging across every orderable link answered 7 for
    an item that will in fact take 30 days to arrive — a reorder point roughly
    four times too low, which is running out of stock while the numbers look
    fine.
    """
    item = _serialized_item("Solenoid")
    _link(item, "SlowVendor", unit_cost="9.00", lead=30, is_primary=True)
    fast = _link(item, "FastVendor", unit_cost="1.00", lead=3)
    _observed_delivery(fast, 7)

    assert _forecast_row(item)["lead_time_days"] == 30.0


def test_observed_history_still_beats_the_estimate_for_the_chosen_supplier():
    """Scoping to the chosen supplier must not demote history where it applies.

    What that supplier ACTUALLY delivered in beats what it claims it will.
    """
    item = _serialized_item("Rectifier")
    chosen = _link(item, "OnlyVendor", unit_cost="4.00", lead=30)
    _observed_delivery(chosen, 12)

    assert _forecast_row(item)["lead_time_days"] == 12.0


def test_forecast_lead_time_matches_the_item_property_it_claims_to_mirror():
    """It previously ordered by ``-is_primary`` alone and could pick another row."""
    item = _serialized_item("Diode")
    _link(item, "Dear", unit_cost="9.00", lead=30)
    _link(item, "Cheap", unit_cost="1.00", lead=3)

    fresh = InventoryItem.objects.get(pk=item.pk)
    assert _forecast_row(item)["lead_time_days"] == float(fresh.average_lead_time)


# ── Printed kanban card ──────────────────────────────────────────────────────


def test_kanban_card_lead_time_comes_from_a_supplier_you_can_still_buy_from():
    """The card is printed and stuck on a shelf; a dead vendor's promise outlives it.

    Drives the renderer's own stock-info block rather than the model property it
    reads, so this keeps holding the CARD to the rule — it would fail if the
    card stopped sourcing "Avg Lead" from the shared derivation.
    """
    from index_cards.services import IndexCardRenderer

    item = _item("Grommet")
    _cheap_dead_dear_live(item)

    fresh = InventoryItem.objects.prefetch_related("item_suppliers__supplier").get(pk=item.pk)
    lines = IndexCardRenderer(base_url="http://localhost:3000")._stock_info_lines(fresh)

    assert "Avg Lead: 20 days" in lines
    assert not any("Lead: 1 day" in line for line in lines)


# ── The two rules that used to disagree ─────────────────────────────────────


def test_create_optimized_order_no_longer_500s_on_a_priced_supplier(api):
    """The crash that made the weighted rule inert: ``Decimal * float``.

    It fired for any candidate priced below 150% of the item's average, so a
    single-supplier low-stock item — the commonest shape there is — took the
    endpoint down. Nothing in the suite caught it because the one test that
    reached this code set ``unit_cost=None`` to route around it.
    """
    item = _item("Bracket")
    _link(item, "Only", unit_cost="7.00", lead=9)

    response = api.post("/api/reorders/purchase-orders/create_optimized_order/")

    assert response.status_code == 200, response.content
    lines = [line for rec in response.data["recommendations"] for line in rec["items"]]
    assert [line["item_name"] for line in lines] == ["Bracket"]


def test_the_order_pad_and_the_recommendations_name_the_same_supplier(api):
    """One item, two surfaces, one answer — on the shape that used to split them.

    Cheapest-but-slow against slightly-dearer-but-fast is exactly where a
    price-only rule and a weighted one part company. The order pad used to group
    under the cheapest link while the recommendations engine scored lead time in
    (or rather, would have, had it not crashed first).
    """
    item = _item("Coupling")
    _link(item, "SlowCheap", unit_cost="5.00", lead=28)
    _link(item, "FastDear", unit_cost="5.25", lead=3)
    ReorderRequest.objects.create(
        item=item,
        quantity=3,
        status=ReorderRequest.Status.APPROVED,
        requested_by="tester",
    )

    pad = api.get("/api/reorders/requests/generate_cart_links/")
    recommendations = api.post("/api/reorders/purchase-orders/create_optimized_order/")

    assert pad.status_code == 200, pad.content
    assert recommendations.status_code == 200, recommendations.content
    assert list(pad.data) == [
        rec["supplier_name"] for rec in recommendations.data["recommendations"]
    ]
    # And it is the weighted answer, not the price-only one.
    assert list(pad.data) == ["FastDear"]


def test_a_flagged_primary_gates_every_surface_alike(api):
    """The operator's choice binds the pad and the recommendations equally."""
    item = _item("Flange")
    _link(item, "Chosen", unit_cost="20.00", lead=25, is_primary=True)
    _link(item, "CheapAndFast", unit_cost="1.00", lead=2)
    ReorderRequest.objects.create(
        item=item,
        quantity=3,
        status=ReorderRequest.Status.APPROVED,
        requested_by="tester",
    )

    pad = api.get("/api/reorders/requests/generate_cart_links/")
    recommendations = api.post("/api/reorders/purchase-orders/create_optimized_order/")
    detail = api.get(f"/api/inventory/items/{item.id}/")

    assert list(pad.data) == ["Chosen"]
    assert [rec["supplier_name"] for rec in recommendations.data["recommendations"]] == ["Chosen"]
    assert detail.data["supplier_name"] == "Chosen"


# ── Case counting is NOT a "which supplier" question ─────────────────────────
#
# Deriving from the READERS OF A SYMBOL is not the same as deriving from the
# QUESTION BEING ASKED. ``current_cases`` reads ``primary_item_supplier`` but
# asks a different question — how many units are in a box on the shelf — which
# has nothing to do with who we buy from. Routing it through the orderability
# rule made a ``None`` invert a boolean rather than degrade to a null: the item
# whose last supplier just died is exactly the one that most needs a low-stock
# alert, and it stopped getting one.


def _case_based_item_with_a_dead_supplier():
    """10 loose units, a discontinued link packing 50 to a case, reorder at 1 case."""
    item = _item(
        "Solvent",
        current_stock=10,
        minimum_stock=10,
        use_case_based_reorder=True,
        minimum_cases=1,
        reorder_cases=2,
    )
    ItemSupplier.objects.create(
        item=item,
        supplier=Supplier.objects.create(
            name="GoneAway", supplier_type=Supplier.SupplierType.LOCAL
        ),
        supplier_sku="GoneAway-sku",
        unit_cost=Decimal("1.00"),
        quantity_per_package=50,
        average_lead_time=7,
        is_discontinued=True,
    )
    return InventoryItem.objects.get(pk=item.pk)


def _case_based_item_with_a_live_supplier():
    """The unaffected control: same numbers, a supplier you can still buy from."""
    item = _item(
        "Thinner",
        current_stock=10,
        minimum_stock=10,
        use_case_based_reorder=True,
        minimum_cases=1,
        reorder_cases=2,
    )
    ItemSupplier.objects.create(
        item=item,
        supplier=Supplier.objects.create(
            name="StillHere", supplier_type=Supplier.SupplierType.LOCAL
        ),
        supplier_sku="StillHere-sku",
        unit_cost=Decimal("1.00"),
        quantity_per_package=50,
        average_lead_time=7,
    )
    return InventoryItem.objects.get(pk=item.pk)


def test_a_case_based_item_with_a_live_supplier_is_completely_unaffected():
    """A real count, a normal flag, a normal display — no unknown anywhere."""
    from inventory.services.packaging import reorder_display

    item = _case_based_item_with_a_live_supplier()
    display = reorder_display(item)

    assert item.current_cases == pytest.approx(0.2)
    assert item.needs_reorder is True
    assert display["current"] == pytest.approx(0.2)
    assert "unknown" not in display["text"]


def test_a_case_based_item_stays_flagged_low_when_its_last_supplier_dies():
    """Uncomputable is not "fine" — the alert stays up.

    This is the alert that was silently suppressed: ``current_cases`` returned
    the raw 10, ``10 <= 1`` was False, and the item whose last supplier just
    died dropped off every low-stock surface. The count is now reported as
    UNKNOWN rather than fabricated, and unknown flags rather than clears.
    """
    item = _case_based_item_with_a_dead_supplier()

    assert item.current_cases is None
    assert item.needs_reorder is True


def test_the_kanban_card_says_unknown_rather_than_counting_loose_units_as_cases():
    """ "10 cases on hand" for 10 loose units is a wrong number on a printed card.

    The card gets stuck on a shelf and outlives the screen it came from, so a
    fabricated count is worse there than an honest "we cannot tell you".
    """
    from inventory.services.packaging import reorder_display

    item = _case_based_item_with_a_dead_supplier()
    display = reorder_display(item)

    assert display["unit"] == "case"
    assert display["current"] is None
    assert display["needs_reorder"] is True
    assert "10 cases on hand" not in display["text"]
    assert "unknown" in display["text"]


def test_the_bridge_command_refuses_exactly_when_the_case_count_is_uncomputable():
    """One predicate, so a refusal to migrate and an unknown count cannot drift.

    Asserted together on purpose: these were two separate reads of "how many
    units are in a box", and they disagreed.
    """
    from inventory.management.commands.bridge_case_reorder_to_packaging import Command

    dead = _case_based_item_with_a_dead_supplier()
    assert dead.current_cases is None
    assert Command()._skip_reason(dead) == "no supplier to take a case size from"

    live = _case_based_item_with_a_live_supplier()
    assert live.current_cases == pytest.approx(0.2)
    assert Command()._skip_reason(live) is None


def test_needs_reorder_and_the_low_stock_query_agree_for_that_shape():
    """The property and its SQL twin disagreed while the bug was live."""
    from inventory.services.packaging import low_stock_q

    item = _case_based_item_with_a_dead_supplier()
    matched = InventoryItem.objects.filter(low_stock_q(), pk=item.pk).exists()

    assert item.needs_reorder is True
    assert matched is True


# ── An honest null must not become a confident zero ──────────────────────────
#
# The class both of this branch's fix-review regressions belong to: a value made
# honestly ``None`` gets collapsed by downstream arithmetic into a confident,
# OPTIMISTIC answer, inverting a boolean and suppressing an alert on exactly the
# item that most needs one. Asserting the honest null and stopping there is what
# let it through — these follow the null into the arithmetic that consumes it.


def test_an_item_nobody_can_order_is_flagged_however_much_stock_it_has():
    """40 on hand, burning 2/day, and the only vendor is gone.

    ``lead_time_days`` is honestly ``None``; ``reorder_point`` used to read that
    as a ZERO-day wait — the most optimistic assumption available — giving the
    hardest item to buy the shortest horizon and dropping it off the low-stock
    report as well stocked. The remedy here is a supplier, not a purchase order,
    so the row says which.
    """
    item = _serialized_item("Thermistor")
    item.minimum_stock = 10
    item.save(update_fields=["minimum_stock"])
    _link(item, "GoneAway", unit_cost="1.00", lead=30, is_discontinued=True)
    _stock_and_daily_use(item, in_stock=40, consumed_today=2)

    row = _forecast_row(item, window_days=1)

    assert row["available"] == 40
    assert row["avg_daily_use"] == 2.0
    assert row["lead_time_days"] is None
    assert row["needs_reorder"] is True
    assert row["no_orderable_supplier"] is True
    # And no reorder point beside the flag to contradict it: "40 available /
    # reorder point 10 / Reorder" is three statements that cannot all be true.
    assert row["reorder_point"] is None

    low_stock = _forecast_row(item, window_days=1, low_stock_only=True)
    assert low_stock["item_id"] == str(item.id)


def test_an_item_that_never_had_a_supplier_is_not_flagged_when_stock_is_healthy():
    """A data gap is not an unbuyable item, and RULE 4 keeps them apart.

    Flagging every item nobody ever recorded a supplier for — permanently,
    regardless of stock — would bury the real alerts under a population that is
    not short of anything. A surface people learn to ignore suppresses alerts
    as surely as a missing one.
    """
    item = _serialized_item("Ferrite")
    item.minimum_stock = 10
    item.save(update_fields=["minimum_stock"])
    _stock_and_daily_use(item, in_stock=100, consumed_today=2)

    row = _forecast_row(item, window_days=1)

    assert row["lead_time_days"] is None
    assert row["reorder_point"] is None
    assert row["no_orderable_supplier"] is False
    assert row["needs_reorder"] is False


def test_a_live_supplier_still_drives_an_ordinary_horizon():
    """The unconditional flag must not swallow the normal path.

    Same burn rate and buffer, a live 30-day vendor and no delivery history:
    the ESTIMATE sets the reorder point (2/day x 30 + 10 = 70), and 100 on hand
    is comfortably above it, so this item is not flagged.
    """
    item = _serialized_item("Capacitor")
    item.minimum_stock = 10
    item.save(update_fields=["minimum_stock"])
    _link(item, "StillSelling", unit_cost="1.00", lead=30)
    _stock_and_daily_use(item, in_stock=100, consumed_today=2)

    row = _forecast_row(item, window_days=1)

    assert row["lead_time_days"] == 30.0
    assert row["reorder_point"] == 70
    assert row["needs_reorder"] is False
    assert row["no_orderable_supplier"] is False
