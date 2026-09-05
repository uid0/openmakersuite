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

from decimal import Decimal

from django.contrib.auth import get_user_model

import pytest
from rest_framework.test import APIClient

from inventory.models import InventoryItem, ItemSupplier, Supplier
from inventory.services.pack_size import PACK_SIZE_RECORDED_ZERO
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
    # ``include_vendor_data=True``: the lead lines are vendor data and the
    # renderer omits them by default now (op-anonymous-read-posture). This test
    # is about WHICH supplier's lead time the card sources, so it asks for the
    # card an operator prints — the one that has the lines at all.
    lines = IndexCardRenderer(
        base_url="http://localhost:3000", include_vendor_data=True
    )._stock_info_lines(fresh)

    assert "Avg Lead: 20 days" in lines
    assert not any("Lead: 1 day" in line for line in lines)


def test_kanban_card_prints_no_lead_time_at_all_for_an_anonymous_render():
    """The default the anonymous ``download_card`` path takes.

    A printed card cannot be recalled, so the renderer withholds by default and
    an operator surface must ask for the lines. The reorder-point line stays —
    it is a shelf threshold this makerspace set, not a wait a vendor quoted.
    """
    from index_cards.services import IndexCardRenderer

    item = _item("Grommet")
    _cheap_dead_dear_live(item)

    fresh = InventoryItem.objects.prefetch_related("item_suppliers__supplier").get(pk=item.pk)
    lines = IndexCardRenderer(base_url="http://localhost:3000")._stock_info_lines(fresh)

    assert not any("Lead" in line for line in lines), lines
    assert any(line.startswith("Reorder at:") for line in lines), lines


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
# QUESTION BEING ASKED. ``current_cases`` asks how many units are in a box on
# the shelf, which has nothing to do with who we buy from. Routing it through
# the orderability rule suppressed a low-stock alert on exactly the item that
# most needs one, so it reads the pack size from the FIRST link — orderable or
# not — through :func:`inventory.services.pack_size.shelf_pack_size`.
#
# These pin that a dead vendor's recorded pack size still describes the box
# already sitting on the shelf, and that op-c1ke did not quietly re-route this
# through the orderability filter.


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


def test_a_zero_pack_first_link_is_unknown_not_one_unit_per_package():
    """A box holding no units is not a case size, and never was (op-c1ke).

    BEFORE: the first row recorded ``quantity_per_package`` of 0, the guard was
    truthiness, and ``current_cases`` fell through to "1 unit per package" — so
    ten loose units read as ten cases, ``10 <= 1`` was False, and an item at a
    tenth of its reorder point silently dropped off every low-stock surface.

    AFTER: the case count is reported as unknown and the item is judged in the
    unit that CAN be counted — base units against ``minimum_stock``, which is
    the predicate ``low_stock_q`` has always applied to it in SQL.

    Still only the FIRST row is consulted. Scanning on to ``SecondPacksFifty``
    would substitute a different vendor's box for the one on the shelf; which
    vendor's box that is cannot be known, so a later row's pack size is another
    guess rather than a better answer.
    """
    item = _item(
        "Acetone",
        current_stock=10,
        minimum_stock=10,
        use_case_based_reorder=True,
        minimum_cases=1,
        reorder_cases=2,
    )
    for name, pack in (("FirstNoPack", 0), ("SecondPacksFifty", 50)):
        ItemSupplier.objects.create(
            item=item,
            supplier=Supplier.objects.create(name=name, supplier_type=Supplier.SupplierType.LOCAL),
            supplier_sku=f"{name}-sku",
            unit_cost=Decimal("1.00"),
            quantity_per_package=pack,
            average_lead_time=7,
            is_primary=(pack == 0),
        )

    fresh = InventoryItem.objects.get(pk=item.pk)

    assert fresh._shelf_pack_size.state == PACK_SIZE_RECORDED_ZERO
    assert fresh.current_cases is None
    assert fresh.needs_reorder is True


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
    """The alert this branch must not suppress — identical to base.

    Routing the pack size through the orderability-filtered helper made
    ``current_cases`` return the raw 10, so ``10 <= 1`` was False and the item
    whose last supplier just died dropped off every low-stock surface. The pack
    size comes from any link, so the count and the flag are what they were.
    """
    item = _case_based_item_with_a_dead_supplier()

    assert item.current_cases == pytest.approx(0.2)
    assert item.needs_reorder is True


def test_the_kanban_card_counts_a_dead_vendors_cases_not_loose_units():
    """ "10 cases on hand" for 10 loose units is a wrong number on a printed card."""
    from inventory.services.packaging import reorder_display

    item = _case_based_item_with_a_dead_supplier()
    display = reorder_display(item)

    assert display["unit"] == "case"
    assert display["current"] == pytest.approx(0.2)
    assert display["needs_reorder"] is True
    assert "10 cases on hand" not in display["text"]


def test_needs_reorder_and_the_low_stock_query_agree_for_that_shape():
    """The property and its SQL twin disagreed while the bug was live."""
    from inventory.services.packaging import low_stock_q

    item = _case_based_item_with_a_dead_supplier()
    matched = InventoryItem.objects.filter(low_stock_q(), pk=item.pk).exists()

    assert item.needs_reorder is True
    assert matched is True
