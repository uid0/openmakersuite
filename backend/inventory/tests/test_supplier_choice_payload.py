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
from reorder_queue.models import LeadTimeLog, PurchaseOrder, ReorderRequest

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
