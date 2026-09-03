"""``supplier_choice`` carries the derivation, not just its winner (op-3xsp).

``inventory/tests/test_supplier_selection*.py`` pin WHICH supplier is chosen.
These pin what a client is told ABOUT that choice, because the defect this
closes was never a wrong pick — the flat ``supplier_name`` has resolved through
the shared derivation since #882. It was that the flat key is the winner with
the derivation thrown away: it cannot say that four other suppliers were on
offer, that the scoring knew no price for this one, or that the operator's own
flagged primary was skipped as unbuyable. Surfaces that named a supplier off
that key therefore presented an item with several sources as an item with one,
and an exported CSV somebody ordered from was one of them.

Every test here would pass just as well against the flat key EXCEPT for what it
asserts about the qualifiers and the alternatives, which is the point: the fix
is additive, and the flat fields are deliberately left in place.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from inventory.models import InventoryItem, ItemSupplier, Supplier
from inventory.services.supplier_selection import (
    BASIS_BEST_SCORED,
    BASIS_FLAGGED_PRIMARY,
    NO_SUPPLIERS,
    NONE_ORDERABLE,
    select_supplier,
)
from reorder_queue.models import (
    LeadTimeLog,
    PurchaseOrder,
    PurchaseOrderItem,
    ReorderRequest,
)

pytestmark = pytest.mark.django_db


def _item(name="Widget", **kwargs):
    defaults = dict(
        name=name,
        description="x",
        sku=f"SKU-{name}",
        reorder_quantity=5,
        current_stock=4,
        minimum_stock=10,
        is_active=True,
    )
    defaults.update(kwargs)
    return InventoryItem.objects.create(**defaults)


def _link(item, name, *, unit_cost="5.00", lead=7, **flags):
    return ItemSupplier.objects.create(
        item=item,
        supplier=Supplier.objects.create(name=name, supplier_type=Supplier.SupplierType.LOCAL),
        supplier_sku=f"{name}-sku",
        unit_cost=None if unit_cost is None else Decimal(unit_cost),
        package_cost=None if unit_cost is None else Decimal(unit_cost),
        quantity_per_package=1,
        average_lead_time=lead,
        is_primary=flags.get("is_primary", False),
        is_active=flags.get("is_active", True),
        is_discontinued=flags.get("is_discontinued", False),
    )


def _delivered(link, user):
    """One recorded delivery through ``link`` — enough to give it a history."""
    ordered = timezone.now()
    return LeadTimeLog.objects.create(
        item_supplier=link,
        purchase_order=PurchaseOrder.objects.create(supplier=link.supplier, created_by=user),
        order_date=ordered,
        expected_delivery_date=ordered.date() + timedelta(days=link.average_lead_time),
        actual_delivery_date=ordered.date() + timedelta(days=link.average_lead_time),
        estimated_lead_time_days=link.average_lead_time,
        actual_lead_time_days=link.average_lead_time,
        quantity_ordered=1,
        quantity_received=1,
    )


@pytest.fixture
def purchaser():
    return get_user_model().objects.create_user(
        username="purchasing", password="pw", is_staff=True, is_superuser=True
    )


@pytest.fixture
def api(purchaser):
    client = APIClient()
    client.force_authenticate(user=purchaser)
    return client


def _choice(api, item):
    response = api.get(f"/api/inventory/items/{item.id}/")
    assert response.status_code == 200, response.content
    return response.data["supplier_choice"], response.data


# ── The service: the derivation now hands back the rest of the field ─────────


def test_alternatives_are_the_other_orderable_links_and_never_the_winner():
    item = _item("Bolt")
    winner = _link(item, "Acme", unit_cost="1.00", lead=2)
    rival = _link(item, "Beta", unit_cost="9.00", lead=20)

    choice = select_supplier(item)

    assert choice.item_supplier.pk == winner.pk
    assert [link.pk for link in choice.alternatives] == [rival.pk]


def test_alternatives_exclude_links_nobody_can_order_from():
    item = _item("Nut")
    _link(item, "Acme", unit_cost="1.00", lead=2)
    _link(item, "Dead", unit_cost="0.50", lead=1, is_discontinued=True)
    _link(item, "Inactive", unit_cost="0.50", lead=1, is_active=False)

    choice = select_supplier(item)

    # An unbuyable link is not an alternative — offering one as "you could also
    # use this" is the same mistake as choosing it.
    assert choice.alternatives == ()


def test_a_flagged_primary_still_reports_what_else_was_available():
    item = _item("Washer")
    flagged = _link(item, "Chosen", unit_cost="9.00", lead=20, is_primary=True)
    rival = _link(item, "Cheaper", unit_cost="1.00", lead=1)

    choice = select_supplier(item)

    assert choice.basis == BASIS_FLAGGED_PRIMARY
    assert choice.item_supplier.pk == flagged.pk
    # The gate decides WHO, not whether there was anyone else.
    assert [link.pk for link in choice.alternatives] == [rival.pk]


def test_a_sole_supplier_reports_no_alternatives():
    item = _item("Sole")
    _link(item, "Only", unit_cost="3.00")

    assert select_supplier(item).alternatives == ()


# ── The payload: what a surface that names a supplier is told ────────────────


def test_payload_names_the_supplier_the_system_would_buy_from(api):
    item = _item("Screw")
    _link(item, "DeadCheap", unit_cost="1.00", lead=1, is_discontinued=True)
    _link(item, "LiveDear", unit_cost="9.00", lead=20)

    choice, data = _choice(api, item)

    assert choice["supplier_name"] == "LiveDear"
    assert choice["basis"] == BASIS_BEST_SCORED
    assert choice["reason"] is None
    # And it agrees with the flat key it supersedes, which is the whole reason a
    # surface can be moved onto it without the displayed name changing.
    assert data["supplier_name"] == "LiveDear"


def test_payload_carries_the_link_pk_of_the_chosen_supplier(api):
    """The chosen row is identified, not just named.

    ``item_supplier_id`` is the ``ItemSupplier`` link's own pk, which is what
    ``suppliers[]`` is keyed on, so a surface can line the choice up against the
    full row without matching on the name.
    """
    item = _item("Keyed")
    link = _link(item, "Acme", unit_cost="1.00")

    choice, _ = _choice(api, item)

    assert choice["item_supplier_id"] == link.pk


def test_the_link_pk_is_null_when_there_is_nothing_to_buy_from(api):
    item = _item("Unkeyed")
    _link(item, "Gone", unit_cost="1.00", is_discontinued=True)

    choice, _ = _choice(api, item)

    assert choice["item_supplier_id"] is None


def test_payload_names_the_other_suppliers_so_one_name_is_not_the_only_name(api):
    item = _item("Anchor")
    _link(item, "Acme", unit_cost="1.00", lead=2)
    _link(item, "Beta", unit_cost="4.00", lead=5)
    _link(item, "Gamma", unit_cost="5.00", lead=6)

    choice, _ = _choice(api, item)

    assert choice["supplier_name"] == "Acme"
    assert [row["supplier_name"] for row in choice["alternatives"]] == ["Beta", "Gamma"]
    # The ids are the ones in ``suppliers[]``, so a client can join to the row.
    assert all(isinstance(row["id"], int) for row in choice["alternatives"])


def test_the_payload_names_the_SCORED_winner_not_the_first_row(api):
    """A modest premium that buys a large lead-time saving wins.

    ``ItemSupplier.Meta.ordering`` is ``-is_primary, unit_cost``, so the rows
    arrive cheapest-first — and here the cheapest one is NOT the answer. Any
    surface that "reaches for the first row" (or for the cheapest, or for
    ``is_primary`` alone) names Slow here; the derivation names Quick. This is
    the shape that tells a real reading of ``supplier_choice`` apart from a
    plausible-looking shortcut.
    """
    item = _item("Premium")
    _link(item, "Slow", unit_cost="10.00", lead=30)
    _link(item, "Quick", unit_cost="12.00", lead=0)

    choice, data = _choice(api, item)

    assert choice["supplier_name"] == "Quick"
    assert [row["supplier_name"] for row in choice["alternatives"]] == ["Slow"]
    assert data["suppliers"][0]["supplier_name"] == "Slow"  # the first row, for contrast


def test_payload_says_when_the_choice_was_made_without_a_price(api):
    item = _item("Unpriced")
    _link(item, "NoPrice", unit_cost=None)

    choice, data = _choice(api, item)

    assert choice["supplier_name"] == "NoPrice"
    # The scoring does not punish a missing price, so this supplier won WITH one
    # missing. Without saying so, the blank cost cell beside it reads as "no
    # supplier" rather than "a supplier nobody has priced".
    assert choice["scored_without_price"] is True
    assert data["unit_cost"] is None


def test_payload_says_when_the_choice_was_made_without_delivery_history(api):
    item = _item("Untested")
    _link(item, "NeverDelivered", unit_cost="2.00")

    choice, _ = _choice(api, item)

    assert choice["supplier_name"] == "NeverDelivered"
    assert choice["scored_without_history"] is True


def test_a_supplier_with_a_delivery_record_carries_no_history_caveat(api, purchaser):
    item = _item("Tested")
    link = _link(item, "HasDelivered", unit_cost="2.00")
    _delivered(link, purchaser)

    choice, _ = _choice(api, item)

    assert choice["supplier_name"] == "HasDelivered"
    assert choice["scored_without_history"] is False


def test_payload_says_when_the_operators_flagged_primary_was_skipped(api):
    item = _item("Skipped")
    _link(item, "FlaggedButDead", unit_cost="1.00", is_primary=True, is_discontinued=True)
    _link(item, "Usable", unit_cost="8.00")

    choice, _ = _choice(api, item)

    assert choice["supplier_name"] == "Usable"
    # The operator flagged one. It was skipped. Silence reads to them as their
    # choice being ignored.
    assert choice["flagged_primary_unorderable"] is True


def test_a_flagged_primary_that_won_carries_no_scoring_caveats(api):
    item = _item("Honoured")
    _link(item, "Flagged", unit_cost=None, is_primary=True)
    _link(item, "Other", unit_cost="1.00")

    choice, _ = _choice(api, item)

    assert choice["basis"] == BASIS_FLAGGED_PRIMARY
    # The gate weighs nothing, so no gap in what we know decided anything —
    # even though this link has neither a price nor a delivery record.
    assert choice["scored_without_price"] is False
    assert choice["scored_without_history"] is False
    assert choice["flagged_primary_unorderable"] is False


def test_payload_tells_no_suppliers_apart_from_none_orderable(api):
    bare = _item("Bare")
    dead = _item("AllDead")
    _link(dead, "Gone", unit_cost="1.00", is_discontinued=True)

    bare_choice, _ = _choice(api, bare)
    dead_choice, _ = _choice(api, dead)

    assert bare_choice["supplier_name"] is None
    assert bare_choice["reason"] == NO_SUPPLIERS
    assert bare_choice["alternatives"] == []
    assert dead_choice["supplier_name"] is None
    assert dead_choice["reason"] == NONE_ORDERABLE
    # "Nobody told us" and "we were told, and the answer is no" need different
    # words and different actions from an operator.
    assert bare_choice["reason"] != dead_choice["reason"]


def test_the_list_endpoint_carries_the_choice_too(api):
    """The CSV export pages the LIST endpoint, so the field has to be there."""
    item = _item("Listed")
    _link(item, "Acme", unit_cost="1.00")
    _link(item, "Beta", unit_cost="4.00")

    response = api.get("/api/inventory/items/")

    assert response.status_code == 200, response.content
    row = next(r for r in response.data["results"] if r["id"] == str(item.id))
    assert row["supplier_choice"]["supplier_name"] == "Acme"
    assert [a["supplier_name"] for a in row["supplier_choice"]["alternatives"]] == ["Beta"]


def test_the_reorder_queue_carries_the_choice_on_its_nested_item(api):
    """The admin dashboard reads ``item_details``, not the item endpoint."""
    item = _item("Queued")
    _link(item, "Acme", unit_cost="1.00")
    _link(item, "Beta", unit_cost="4.00")
    ReorderRequest.objects.create(item=item, quantity=3, requested_by="member")

    response = api.get("/api/reorders/requests/")

    assert response.status_code == 200, response.content
    row = response.data["results"][0]
    assert row["item_details"]["supplier_choice"]["supplier_name"] == "Acme"
    assert len(row["item_details"]["supplier_choice"]["alternatives"]) == 1


def test_the_kit_endpoints_carry_the_choice_too(api):
    """The kit list's "From" column and the kit form's attribution note read it.

    ``KitSerializer`` gets the field only by inheriting
    ``InventoryItemSerializer.Meta.fields``, so a later narrowing of that list —
    or a Kit-specific ``fields`` override — would blank both surfaces silently.
    Every frontend test of those two screens mocks ``kitAPI`` with a hand-built
    payload, so this is the only check that the real endpoints serve the key.
    """
    kit = _item("Ink Kit", is_kit=True, current_stock=0)
    _link(kit, "Acme", unit_cost="1.00")
    _link(kit, "Beta", unit_cost="4.00")

    detail = api.get(f"/api/inventory/kits/{kit.id}/")
    listing = api.get("/api/inventory/kits/")

    assert detail.status_code == 200, detail.content
    assert detail.data["supplier_choice"]["supplier_name"] == "Acme"
    assert [a["supplier_name"] for a in detail.data["supplier_choice"]["alternatives"]] == ["Beta"]

    assert listing.status_code == 200, listing.content
    row = next(r for r in listing.data["results"] if r["id"] == str(kit.id))
    assert row["supplier_choice"]["supplier_name"] == "Acme"
    assert [a["supplier_name"] for a in row["supplier_choice"]["alternatives"]] == ["Beta"]


# ── The legacy fields stay. That is a requirement, not an oversight. ─────────


def test_every_legacy_flat_field_is_still_served(api):
    """ScanTTY's detail screen reads all seven; removing one breaks it."""
    item = _item("Compat")
    _link(item, "Acme", unit_cost="3.50", lead=9)

    _, data = _choice(api, item)

    assert data["supplier_name"] == "Acme"
    assert data["supplier_sku"] == "Acme-sku"
    assert data["supplier_url"] == ""
    assert Decimal(str(data["unit_cost"])) == Decimal("3.50")
    assert Decimal(str(data["package_cost"])) == Decimal("3.50")
    assert data["quantity_per_package"] == 1
    assert data["average_lead_time"] == 9


def test_the_choice_and_the_flat_key_can_never_disagree(api):
    """Both resolve the same derivation, so they name the same supplier.

    The fix moves surfaces from one to the other; if these could drift, moving
    a surface would change what it displays, and the flats would then be a
    second answer to a question with one owner.
    """
    flagged = _item("AgreementFlagged")
    _link(flagged, "FlaggedDear", unit_cost="99.00", lead=30, is_primary=True)
    _link(flagged, "Cheap", unit_cost="0.01", lead=1)

    # And the shape where the first row by ``Meta.ordering`` is NOT the answer,
    # so "read the first link" would make the two disagree rather than merely
    # arriving at the same place by luck.
    scored = _item("AgreementScored")
    _link(scored, "Slow", unit_cost="10.00", lead=30)
    _link(scored, "Quick", unit_cost="12.00", lead=0)

    flagged_choice, flagged_data = _choice(api, flagged)
    scored_choice, scored_data = _choice(api, scored)

    assert flagged_choice["supplier_name"] == flagged_data["supplier_name"] == "FlaggedDear"
    assert scored_choice["supplier_name"] == scored_data["supplier_name"] == "Quick"


# ── Query budget: the reason beside the row is free ──────────────────────────


def test_reading_the_reason_and_the_row_costs_ONE_resolution(django_assert_num_queries):
    """The memo is on the whole choice, not on the row it contains.

    ``primary_item_supplier`` used to be the ``cached_property``, so a caller
    that wanted the reason as well as the row resolved the derivation twice.
    The memo moved up to :attr:`InventoryItem.supplier_choice` and
    ``primary_item_supplier`` now reads through it, which is what makes the new
    serializer field free. Deliberately WITHOUT a prefetch, because that is the
    only shape in which a second resolution costs a query and is therefore
    visible at all.
    """
    item = _item("Memo")
    _link(item, "Acme", unit_cost="1.00")
    _link(item, "Beta", unit_cost="2.00")

    fresh = InventoryItem.objects.get(pk=item.pk)
    with django_assert_num_queries(1):
        assert fresh.supplier.name == "Acme"
        assert fresh.supplier_choice.item_supplier is not None
        assert len(fresh.supplier_choice.alternatives) == 1


def test_the_choice_adds_no_per_row_query_to_a_listed_page(api, django_assert_num_queries):
    """A page of ten costs what a page of two costs.

    An absolute count would pin every unrelated query on the endpoint; what this
    change could actually break is the per-row shape, so that is what is
    measured. A serializer field resolving the derivation per item would make
    the second number larger than the first.
    """
    for index in range(2):
        small = _item(f"Small{index}")
        _link(small, f"S{index}A", unit_cost="1.00")
        _link(small, f"S{index}B", unit_cost="2.00")

    api.get("/api/inventory/items/")  # warm auth/content-type caches
    with django_assert_num_queries(6) as small_page:
        assert api.get("/api/inventory/items/").status_code == 200

    for index in range(8):
        big = _item(f"Big{index}")
        _link(big, f"B{index}A", unit_cost="1.00")
        _link(big, f"B{index}B", unit_cost="2.00")

    with django_assert_num_queries(len(small_page)):
        response = api.get("/api/inventory/items/")

    assert len(response.data["results"]) == 10
    assert all(row["supplier_choice"]["supplier_name"] for row in response.data["results"])


# ── The derivation metadata has an AUDIENCE, enforced on the wire ────────────
#
# Every other anonymous protection on this branch is client-side: a page
# withholds the caveats, the alternative names, the kit SKU. Those narrow what a
# VISITOR IS HANDED and not what a CLIENT CAN FETCH, because the item endpoints
# read as ``AllowAny`` and the kit endpoints as ``IsAuthenticatedOrReadOnly``.
# The four keys below are the ones this branch newly put on that public wire, so
# they are the ones it closes. What stays open stays open deliberately:
# ``alternatives`` names nobody who is not already named in ``suppliers[]`` on
# the same response, and that array predates this work.

OPERATOR_ONLY_KEYS = {
    "basis",
    "flagged_primary_unorderable",
    "scored_without_price",
    "scored_without_history",
}
PUBLIC_KEYS = {"item_supplier_id", "supplier_name", "reason", "alternatives"}


@pytest.fixture
def anon():
    return APIClient()


def _caveated_item(name, **item_kwargs):
    """An item whose choice sets every caveat there is to leak.

    The flagged primary is dead, so it is skipped; of the two orderable links
    the unpriced one wins on lead time and has never delivered — which is all
    three caveats at once, plus a non-empty ``alternatives``.
    """
    item = _item(name, **item_kwargs)
    _link(item, f"{name} Flagged", unit_cost="1.00", is_primary=True, is_active=False)
    _link(item, f"{name} Acme", unit_cost=None, lead=3)
    _link(item, f"{name} Beta", unit_cost="9.00", lead=4)
    return item


def test_an_anonymous_item_read_carries_no_derivation_metadata(anon):
    item = _caveated_item("Detail")

    response = anon.get(f"/api/inventory/items/{item.id}/")

    assert response.status_code == 200, response.content
    choice = response.data["supplier_choice"]
    assert OPERATOR_ONLY_KEYS.isdisjoint(choice), choice
    assert PUBLIC_KEYS <= set(choice), choice


def test_an_anonymous_item_read_still_names_the_supplier_and_the_others(anon):
    """The gate closes the derivation detail, not the vendor roster.

    ``suppliers[]`` on this same response already lists every one of these
    names. Hiding ``alternatives`` while that array sits beside it would look
    like a protection and be none.
    """
    item = _caveated_item("Roster")

    response = anon.get(f"/api/inventory/items/{item.id}/")

    choice = response.data["supplier_choice"]
    assert choice["supplier_name"] == "Roster Acme"
    assert [a["supplier_name"] for a in choice["alternatives"]] == ["Roster Beta"]
    assert choice["reason"] is None
    assert choice["item_supplier_id"] is not None


def test_an_anonymous_list_read_carries_no_derivation_metadata(anon):
    """The CSV export pages the LIST endpoint, and it is `AllowAny` too."""
    item = _caveated_item("Listed")

    response = anon.get("/api/inventory/items/")

    assert response.status_code == 200, response.content
    choice = next(r for r in response.data["results"] if r["id"] == str(item.id))["supplier_choice"]
    assert OPERATOR_ONLY_KEYS.isdisjoint(choice), choice
    assert choice["supplier_name"] == "Listed Acme"


def test_an_anonymous_kit_read_carries_no_derivation_metadata(anon):
    """``KitSerializer`` subclasses ``InventoryItemSerializer`` — confirmed, not assumed."""
    kit = _caveated_item("Kitted", is_kit=True, current_stock=0)

    detail = anon.get(f"/api/inventory/kits/{kit.id}/")
    listing = anon.get("/api/inventory/kits/")

    assert detail.status_code == 200, detail.content
    assert OPERATOR_ONLY_KEYS.isdisjoint(detail.data["supplier_choice"])
    assert detail.data["supplier_choice"]["supplier_name"] == "Kitted Acme"

    assert listing.status_code == 200, listing.content
    row = next(r for r in listing.data["results"] if r["id"] == str(kit.id))
    assert OPERATOR_ONLY_KEYS.isdisjoint(row["supplier_choice"])


def test_a_signed_in_item_read_still_carries_every_caveat(api):
    """CONTROL: the gate is about the READER, not about dropping the fields."""
    item = _caveated_item("Operator")

    choice, _ = _choice(api, item)

    assert OPERATOR_ONLY_KEYS <= set(choice), choice
    assert choice["basis"] == BASIS_BEST_SCORED
    assert choice["flagged_primary_unorderable"] is True
    assert choice["scored_without_price"] is True
    assert choice["scored_without_history"] is True


def test_a_signed_in_kit_read_still_carries_every_caveat(api):
    kit = _caveated_item("OperatorKit", is_kit=True, current_stock=0)

    detail = api.get(f"/api/inventory/kits/{kit.id}/")
    listing = api.get("/api/inventory/kits/")

    assert OPERATOR_ONLY_KEYS <= set(detail.data["supplier_choice"])
    row = next(r for r in listing.data["results"] if r["id"] == str(kit.id))
    assert OPERATOR_ONLY_KEYS <= set(row["supplier_choice"])


def test_a_serializer_with_no_request_in_context_fails_closed():
    """A render nobody authenticated is a render that gets the restricted form.

    Management commands, shells and hand-built nested renders carry no
    ``request``. Defaulting those to the operator view would make the gate
    depend on every future call site remembering to pass context.
    """
    from inventory.serializers import SupplierChoiceSerializer

    item = _caveated_item("Contextless")

    data = SupplierChoiceSerializer(item.supplier_choice).data

    assert OPERATOR_ONLY_KEYS.isdisjoint(data), data
    assert data["supplier_name"] == "Contextless Acme"


# ── The gate fails CLOSED, so a hand-built serializer must carry the request ──
#
# The audience is read off ``context["request"]``, and a serializer built by
# hand has none. That is the right default — a shell or a management command
# has proven nothing about who is asking — but it makes every hand-built render
# a place where an OPERATOR silently receives the anonymous view. It does not
# raise; a caveated choice simply arrives looking clean, which is the exact
# silence op-3xsp exists to remove.
#
# Every endpoint below is ``IsAuthenticated``, so every caller reaching it is an
# operator, and each one builds its serializer outside ``get_serializer()``.
# They are asserted through the routed API rather than by constructing a
# serializer, because the defect is precisely that the hand-built path differed
# from the routed one.


def _po_line_for(api, purchaser, name):
    """A draft PO line for a caveated item, on the supplier the choice picked."""
    item = _caveated_item(name)
    chosen = item.supplier_choice.item_supplier
    order = PurchaseOrder.objects.create(supplier=chosen.supplier, created_by=purchaser)
    line = PurchaseOrderItem.objects.create(
        purchase_order=order,
        item_supplier=chosen,
        quantity_ordered=2,
        unit_cost_ordered=Decimal("1.00"),
        notes="",
    )
    return order, line, item


def test_the_by_supplier_order_pad_tells_an_operator_the_caveats(api):
    """The bulk-ordering surface, and the one an operator sizes an order from.

    It builds ``ReorderRequestSerializer`` per row rather than through
    ``get_serializer()``, so it was handing a signed-in purchaser a choice with
    no caveats on it — no "chosen without a price on file", no "your flagged
    primary was skipped".
    """
    item = _caveated_item("Padded")
    ReorderRequest.objects.create(item=item, quantity=3, requested_by="member")

    response = api.get("/api/reorders/requests/by_supplier/")

    assert response.status_code == 200, response.content
    rows = [row for group in response.data for row in group["requests"]]
    assert rows, response.data
    choice = rows[0]["item_details"]["supplier_choice"]
    assert OPERATOR_ONLY_KEYS <= set(choice), choice
    assert choice["flagged_primary_unorderable"] is True
    assert choice["scored_without_price"] is True


def test_the_authenticated_create_response_tells_an_operator_the_caveats(api):
    """The richer shape a signed-in caller gets back from POSTing a request."""
    item = _caveated_item("Created")

    response = api.post(
        "/api/reorders/requests/",
        {"item": str(item.id), "quantity": 2, "requested_by": "member"},
        format="json",
    )

    assert response.status_code in (200, 201), response.content
    choice = response.data["item_details"]["supplier_choice"]
    assert OPERATOR_ONLY_KEYS <= set(choice), choice
    assert choice["flagged_primary_unorderable"] is True


def test_adding_a_purchase_order_line_tells_an_operator_the_caveats(api, purchaser):
    order, _, item = _po_line_for(api, purchaser, "Added")
    order.items.all().delete()
    chosen = item.supplier_choice.item_supplier

    response = api.post(
        f"/api/reorders/purchase-orders/{order.id}/items/",
        {"item_supplier": str(chosen.id), "quantity": 2, "unit_cost": "1.00"},
        format="json",
    )

    assert response.status_code in (200, 201), response.content
    choice = response.data["line_item"]["item_details"]["supplier_choice"]
    assert OPERATOR_ONLY_KEYS <= set(choice), choice


def test_updating_a_purchase_order_line_tells_an_operator_the_caveats(api, purchaser):
    order, line, _ = _po_line_for(api, purchaser, "Updated")

    response = api.patch(
        f"/api/reorders/purchase-orders/{order.id}/items/{line.id}/",
        {"quantity": 5},
        format="json",
    )

    assert response.status_code == 200, response.content
    choice = response.data["item_details"]["supplier_choice"]
    assert OPERATOR_ONLY_KEYS <= set(choice), choice


def test_voiding_a_purchase_order_line_tells_an_operator_the_caveats(api, purchaser):
    order, line, _ = _po_line_for(api, purchaser, "Voided")

    response = api.post(
        f"/api/reorders/purchase-orders/{order.id}/items/{line.id}/void/",
        {"reason": "discontinued"},
        format="json",
    )

    assert response.status_code == 200, response.content
    choice = response.data["item_details"]["supplier_choice"]
    assert OPERATOR_ONLY_KEYS <= set(choice), choice


def test_a_suppliers_recent_orders_tell_an_operator_the_caveats(api, purchaser):
    """Found by sweeping for the pattern, not named in the report.

    ``SupplierDetailSerializer.get_purchase_orders`` builds
    ``PurchaseOrderSerializer`` inside a method field, which reaches
    ``item_details`` two serializers down — the same hand-built render, in a
    serializer rather than a view.
    """
    order, _, item = _po_line_for(api, purchaser, "Recent")

    response = api.get(f"/api/inventory/suppliers/{order.supplier.id}/")

    assert response.status_code == 200, response.content
    orders = response.data["purchase_orders"]
    lines = [line for entry in orders for line in entry["items"]]
    choice = next(
        line["item_details"]["supplier_choice"]
        for line in lines
        if line["item_details"]["id"] == str(item.id)
    )
    assert OPERATOR_ONLY_KEYS <= set(choice), choice


def test_an_anonymous_caller_on_a_public_supplier_page_still_gets_the_narrow_view(
    anon, api, purchaser
):
    """CONTROL: forwarding context did not widen anything.

    ``SupplierViewSet`` is ``IsAuthenticatedOrReadOnly``, so this page reads
    publicly. Passing the request through changes what an OPERATOR sees; an
    anonymous caller resolves to the same restricted form the missing context
    used to produce for everybody.
    """
    order, _, _ = _po_line_for(api, purchaser, "PublicRecent")

    response = anon.get(f"/api/inventory/suppliers/{order.supplier.id}/")

    assert response.status_code == 200, response.content
    lines = [line for entry in response.data["purchase_orders"] for line in entry["items"]]
    for line in lines:
        choice = line["item_details"]["supplier_choice"]
        assert OPERATOR_ONLY_KEYS.isdisjoint(choice), choice
        assert PUBLIC_KEYS <= set(choice), choice
