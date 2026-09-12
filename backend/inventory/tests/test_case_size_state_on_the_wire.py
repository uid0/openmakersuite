"""The unknown case size says WHICH unknown it is, on the wire (op-2t4e).

``inventory/services/pack_size.py`` has told these states apart internally since
op-c1ke, and its own docstring recorded the gap these pin: "no surface renders
:attr:`PackSize.state`, and the web pages say only 'case size unknown' for all
of them". Two different problems reached an operator wearing one sentence — one
of them wants a fact SUPPLIED, the other wants a wrong fact CORRECTED — and the
API sent nothing a client could tell them apart by.

So the assertions below are mostly about DISTINCTION rather than about any one
value: every test that names an unknown state also pins that a sibling unknown
answers differently on the same key. A test that merely asserted
``case_size is None`` would have passed against base, which is the whole defect.

Two questions, two payloads, and they are deliberately not the same question:

* ``InventoryItemSerializer.case_size_state`` pairs with ``current_cases`` — the
  SHELF question, ``shelf_pack_size``, the first link orderable or not.
* ``InventoryMetricsSerializer.case_size_state`` pairs with ``case_size`` — the
  ORDER question, the link ``supplier_selection`` would buy through, which is
  why only this one can answer ``no_orderable_link``.

The CONTROL block at the end pins the invariant this change must not break: no
number and no flag moves. This change is about what the API SAYS, not what the
system DOES.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse

import pytest
from rest_framework.test import APIClient

from inventory.models import InventoryItem, ItemSupplier, Supplier
from inventory.services.pack_size import (
    PACK_SIZE_KNOWN,
    PACK_SIZE_NO_ORDERABLE_LINK,
    PACK_SIZE_NOT_RECORDED,
    PACK_SIZE_RECORDED_ZERO,
)

pytestmark = pytest.mark.django_db

User = get_user_model()


def _item(name="Widget", **kwargs):
    defaults = dict(
        name=name,
        description="x",
        sku=f"SKU-{name}",
        reorder_quantity=5,
        current_stock=24,
        minimum_stock=10,
        is_active=True,
        use_case_based_reorder=True,
        minimum_cases=2,
        reorder_cases=4,
    )
    defaults.update(kwargs)
    return InventoryItem.objects.create(**defaults)


def _link(item, name, *, pack, **flags):
    return ItemSupplier.objects.create(
        item=item,
        supplier=Supplier.objects.create(name=name, supplier_type=Supplier.SupplierType.LOCAL),
        supplier_sku=f"{name}-sku",
        unit_cost=Decimal("1.00"),
        quantity_per_package=pack,
        average_lead_time=7,
        is_primary=flags.get("is_primary", False),
        is_active=flags.get("is_active", True),
        is_discontinued=flags.get("is_discontinued", False),
    )


def _zero_link(item, name, **flags):
    """A link recording a pack size of 0 — a box holding no units.

    Written past the validators on purpose: ``clean_pack_size`` is the write
    face and no supported endpoint mints one of these any more, but the rows
    already on disk are exactly what ``PACK_SIZE_RECORDED_ZERO`` describes, and
    they are what an operator is owed different words about.
    """
    link = _link(item, name, pack=1, **flags)
    ItemSupplier.objects.filter(pk=link.pk).update(quantity_per_package=0)
    link.refresh_from_db()
    return link


@pytest.fixture
def operator():
    user = User.objects.create_user(
        username="operator", email="operator@example.com", password="pw"
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _detail(client, item):
    response = client.get(reverse("inventoryitem-detail", kwargs={"pk": str(item.id)}))
    assert response.status_code == 200
    return response.json()


def _metrics(client, item):
    response = client.get(reverse("inventoryitem-metrics", kwargs={"pk": str(item.id)}))
    assert response.status_code == 200
    return response.json()


# ── The SHELF question: the payload that carries current_cases ───────────────


def test_the_shelf_says_KNOWN_and_still_sends_the_number(operator):
    item = _item()
    _link(item, "Acme", pack=12)

    body = _detail(operator, item)

    assert body["case_size_state"] == PACK_SIZE_KNOWN
    assert body["current_cases"] == pytest.approx(2.0)


def test_an_item_with_no_supplier_link_says_the_case_size_was_NEVER_RECORDED(operator):
    """Nothing was ever told to us — the operator SUPPLIES a missing fact."""
    item = _item()

    body = _detail(operator, item)

    assert body["current_cases"] is None
    assert body["case_size_state"] == PACK_SIZE_NOT_RECORDED


def test_a_link_recording_zero_says_the_case_size_is_RECORDED_ZERO(operator):
    """We were told something impossible — the operator CORRECTS a wrong fact."""
    item = _item()
    _zero_link(item, "Acme")

    body = _detail(operator, item)

    assert body["current_cases"] is None
    assert body["case_size_state"] == PACK_SIZE_RECORDED_ZERO


def test_the_two_shelf_unknowns_are_TOLD_APART_on_the_wire(operator):
    """The defect, stated as an assertion.

    Both items send ``current_cases: null``; base sent nothing else about them,
    so the web had no basis on which to say "record one" to the first and
    "correct it" to the second. This fails against base for the only reason
    that matters: the key it compares does not exist there.
    """
    never_recorded = _item(name="Never", sku="SKU-never")
    recorded_zero = _item(name="Zero", sku="SKU-zero")
    _zero_link(recorded_zero, "Acme")

    never_body = _detail(operator, never_recorded)
    zero_body = _detail(operator, recorded_zero)

    assert never_body["current_cases"] is None
    assert zero_body["current_cases"] is None
    assert never_body["case_size_state"] != zero_body["case_size_state"]


def test_the_shelf_reads_a_dead_vendors_link_rather_than_calling_it_unrecorded(operator):
    """Orderability is not the shelf's question (op-2rsp).

    The box on the shelf was bought from somebody who may since have died; their
    recorded pack size still describes it. So the shelf state has no
    ``no_orderable_link`` member — filtering for orderability here is the very
    thing that suppressed a low-stock alert.
    """
    item = _item()
    _link(item, "Dead", pack=12, is_active=False)

    body = _detail(operator, item)

    assert body["case_size_state"] == PACK_SIZE_KNOWN
    assert body["current_cases"] == pytest.approx(2.0)


# ── The ORDER question: the payload that carries case_size ───────────────────


def test_metrics_says_KNOWN_and_still_sends_the_number(operator):
    item = _item()
    _link(item, "Acme", pack=12)

    body = _metrics(operator, item)

    assert body["case_size"] == 12
    assert body["case_size_state"] == PACK_SIZE_KNOWN


def test_metrics_on_an_item_with_no_links_says_NEVER_RECORDED(operator):
    item = _item()

    body = _metrics(operator, item)

    assert body["case_size"] is None
    assert body["case_size_state"] == PACK_SIZE_NOT_RECORDED


def test_metrics_on_an_orderable_link_recording_zero_says_RECORDED_ZERO(operator):
    item = _item()
    _zero_link(item, "Acme")

    body = _metrics(operator, item)

    assert body["case_size"] is None
    assert body["case_size_state"] == PACK_SIZE_RECORDED_ZERO


def test_metrics_on_an_item_whose_every_vendor_is_dead_says_NO_ORDERABLE_LINK(operator):
    """A third unknown, and the reason this payload needs its own state.

    The links exist and one of them records a perfectly good 12. Nothing we can
    BUY sizes the next order, which points the operator at a different screen
    from "add a supplier" — revive a link, or add a vendor that still carries
    the item.
    """
    item = _item()
    _link(item, "Dead", pack=12, is_active=False)
    _link(item, "Gone", pack=6, is_discontinued=True)

    body = _metrics(operator, item)

    assert body["case_size"] is None
    assert body["case_size_state"] == PACK_SIZE_NO_ORDERABLE_LINK


def test_the_three_order_unknowns_are_TOLD_APART_on_the_wire(operator):
    """All three send ``case_size: null``; all three want different actions."""
    no_links = _item(name="NoLinks", sku="SKU-nolinks")
    zero = _item(name="Zero", sku="SKU-zero")
    _zero_link(zero, "Acme")
    dead = _item(name="Dead", sku="SKU-dead")
    _link(dead, "Deceased", pack=12, is_active=False)

    states = {}
    for item in (no_links, zero, dead):
        body = _metrics(operator, item)
        assert body["case_size"] is None
        states[item.name] = body["case_size_state"]

    assert len(set(states.values())) == 3, states


def test_no_orderable_link_is_NOT_the_shelfs_answer_for_the_same_item(operator):
    """The two payloads answer their OWN question about one item.

    Every link is dead, so the next order cannot be sized — but the box on the
    shelf is 12 and the case count is real. A client that read one state for
    both questions would have to pick which of these two true things to be
    wrong about.
    """
    item = _item()
    _link(item, "Dead", pack=12, is_active=False)

    assert _detail(operator, item)["case_size_state"] == PACK_SIZE_KNOWN
    assert _metrics(operator, item)["case_size_state"] == PACK_SIZE_NO_ORDERABLE_LINK


# ── CONTROL: this change says something new, it does not DO anything new ─────


@pytest.mark.parametrize(
    "build,expected_cases,expected_case_size,expected_flag",
    [
        # 24 units at 12 to a case is 2 cases, at a 2-case floor — flagged.
        (lambda item: _link(item, "Acme", pack=12), 2.0, 12, True),
        # No case count to compare, so judged in base units instead: 24 against
        # max(minimum_stock=10, minimum_cases=2). Not flagged, and the state
        # says so for a reason that has nothing to do with the new key.
        (lambda item: None, None, None, False),
        (lambda item: _zero_link(item, "Acme"), None, None, False),
        # The shelf still counts a dead vendor's box (op-2rsp), so this one is
        # flagged exactly like the KNOWN row even though nothing can be ordered.
        (lambda item: _link(item, "Dead", pack=12, is_active=False), 2.0, None, True),
    ],
    ids=["known", "not_recorded", "recorded_zero", "no_orderable_link"],
)
def test_no_number_and_no_flag_moves_in_any_state(
    operator, build, expected_cases, expected_case_size, expected_flag
):
    """Every figure beside the new key reads exactly as it did before it.

    ``needs_reorder`` is pinned per state because it is the figure this class of
    change has moved before: an item whose cases cannot be counted is judged on
    base units at ``max(minimum_stock, minimum_cases)``, and that judgement must
    not shift because the payload grew a word for WHY they cannot be counted.
    """
    item = _item()
    build(item)

    detail = _detail(operator, item)
    metrics = _metrics(operator, item)

    assert detail["current_cases"] == (
        None if expected_cases is None else pytest.approx(expected_cases)
    )
    assert detail["needs_reorder"] is expected_flag
    assert metrics["case_size"] == expected_case_size
