"""The ONE derivation of "how many base units are in one package?" (op-c1ke).

``inventory/tests/test_pack_size_single_owner.py`` pins that nothing reads the
column around this module; these pin what the module SAYS.

The three states are the point. Before this derivation existed the fact was read
through ``or 1`` / ``or 0`` guards that turned "nobody recorded a pack size" and
"a link records a box holding no units" into the confident number 1 — which is
how a case-based item at a tenth of its reorder point stopped being flagged.
"""

from decimal import Decimal

import pytest

from inventory.models import InventoryItem, ItemSupplier, Supplier
from inventory.services.pack_size import (
    PACK_SIZE_KNOWN,
    PACK_SIZE_NOT_RECORDED,
    PACK_SIZE_RECORDED_ZERO,
    declares_a_case,
    order_pack_size,
    pack_size_of,
    shelf_pack_size,
)

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


def _link(item, name, *, pack, unit_cost="1.00", **flags):
    return ItemSupplier.objects.create(
        item=item,
        supplier=Supplier.objects.create(name=name, supplier_type=Supplier.SupplierType.LOCAL),
        supplier_sku=f"{name}-sku",
        unit_cost=Decimal(unit_cost),
        quantity_per_package=pack,
        average_lead_time=7,
        is_primary=flags.get("is_primary", False),
        is_active=flags.get("is_active", True),
        is_discontinued=flags.get("is_discontinued", False),
    )


# ── The three states, on one link ────────────────────────────────────────────


def test_a_positive_pack_size_is_known():
    link = _link(_item("Known"), "Vendor", pack=24)

    pack = pack_size_of(link)

    assert pack.state == PACK_SIZE_KNOWN
    assert pack.units == 24
    assert pack.is_known is True
    assert bool(pack) is True


def test_a_pack_size_of_one_is_KNOWN_not_missing():
    """The column defaults to 1, but the honest reading of 1 is "sells singles".

    Treating 1 as "nobody filled it in" would make every unconfigured link
    unknown and flood the surface; treating it as known is what keeps a vendor
    that really does sell singles working. "Did this vendor declare a CASE?" is
    a different question with its own answer — see ``declares_a_case``.
    """
    link = _link(_item("Singles"), "Vendor", pack=1)

    assert pack_size_of(link).state == PACK_SIZE_KNOWN
    assert pack_size_of(link).units == 1
    assert declares_a_case(link) is None


def test_a_recorded_zero_is_UNKNOWN_and_says_which_kind():
    """A box holding no units is not a box. ``or 1`` read it as one unit."""
    link = _link(_item("ZeroPack"), "Vendor", pack=0)

    pack = pack_size_of(link)

    assert pack.state == PACK_SIZE_RECORDED_ZERO
    assert pack.units is None
    assert bool(pack) is False


def test_no_link_at_all_is_UNKNOWN_and_says_which_kind():
    pack = pack_size_of(None)

    assert pack.state == PACK_SIZE_NOT_RECORDED
    assert pack.units is None
    assert pack.link is None


def test_the_two_unknowns_are_never_collapsed():
    """ "We were never told" and "we were told something impossible" differ.

    They send an operator to different screens — add a supplier, versus fix the
    row that says a case holds nothing — for the same reason
    ``supplier_selection`` keeps ``NO_SUPPLIERS`` apart from ``NONE_ORDERABLE``.
    """
    zero = pack_size_of(_link(_item("Z"), "Vendor", pack=0))
    absent = pack_size_of(None)

    assert zero.is_known is absent.is_known is False
    assert zero.state != absent.state


def test_a_recorded_zero_is_not_a_case_either():
    """The op-ev14 ladder must not read an impossible box as "sells singles"."""
    assert declares_a_case(_link(_item("Z2"), "Vendor", pack=0)) is None
    assert declares_a_case(None) is None
    assert declares_a_case(_link(_item("C"), "Vendor", pack=12)) == 12


# ── Two questions, and why they must not collapse ────────────────────────────


def test_the_shelf_asks_the_first_link_orderable_or_not():
    """A dead vendor's recorded pack size still describes the box on the shelf.

    Routing this through the orderability filter is what suppressed a low-stock
    alert during op-2rsp (see AGENTS.md). It stays the first row in
    ``Meta.ordering``, orderable or not.
    """
    item = _item("Solvent")
    _link(item, "GoneAway", pack=50, unit_cost="1.00", is_discontinued=True)
    _link(item, "StillHere", pack=12, unit_cost="9.00")
    fresh = InventoryItem.objects.get(pk=item.pk)

    assert shelf_pack_size(fresh).units == 50


def test_the_next_order_asks_the_supplier_we_can_actually_buy_from():
    """Same item, same rows, different question — and a different answer."""
    item = _item("Solvent2")
    _link(item, "GoneAway2", pack=50, unit_cost="1.00", is_discontinued=True)
    _link(item, "StillHere2", pack=12, unit_cost="9.00")
    fresh = InventoryItem.objects.get(pk=item.pk)

    assert order_pack_size(fresh).units == 12
    # The two answers differ on this item ON PURPOSE. Collapsing them either
    # loses the box on the shelf or quotes a case nobody can buy.
    assert shelf_pack_size(fresh).units != order_pack_size(fresh).units


def test_an_item_whose_every_link_is_dead_can_still_count_its_shelf():
    item = _item("Solvent3")
    _link(item, "GoneAway3", pack=50, is_discontinued=True)
    fresh = InventoryItem.objects.get(pk=item.pk)

    assert shelf_pack_size(fresh).units == 50
    # Nothing we can BUY records a pack size, which is a different fact.
    assert order_pack_size(fresh).is_known is False


def test_an_item_with_no_links_is_not_recorded_on_either_question():
    fresh = InventoryItem.objects.get(pk=_item("Orphan").pk)

    assert shelf_pack_size(fresh).state == PACK_SIZE_NOT_RECORDED
    assert order_pack_size(fresh).state == PACK_SIZE_NOT_RECORDED


def test_the_shelf_reads_only_the_first_row_and_does_not_scan_past_a_zero():
    """Which vendor's box is on the shelf is unknowable; a later row is a guess.

    Scanning on would substitute a different vendor's case size for the one
    actually sitting there — another guess, not a better answer.
    """
    item = _item("Acetone")
    _link(item, "FirstNoPack", pack=0, is_primary=True)
    _link(item, "SecondPacksFifty", pack=50)
    fresh = InventoryItem.objects.get(pk=item.pk)

    assert shelf_pack_size(fresh).state == PACK_SIZE_RECORDED_ZERO
