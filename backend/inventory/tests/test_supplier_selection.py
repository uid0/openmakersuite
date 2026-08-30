"""Tests for the named supplier-selection service (issue #882, op-2rsp).

The service (``inventory.services.supplier_selection``) and the thin
``InventoryItem.primary_item_supplier`` shim that delegates to it must:

* never hand back a link nobody can order through — inactive or discontinued
  (op-2rsp), including one an operator flagged primary and later discontinued;
* among the links that ARE orderable, select the IDENTICAL row the legacy
  ``filter(is_primary=True).first() or first()`` chose (primary-first, then
  cheapest) — the ranking is unchanged, only the candidate set narrowed;
* tell "this item has no suppliers" apart from "every supplier is dead", because
  those are different facts an operator acts on differently;
* cost ZERO extra queries when ``item_suppliers`` is prefetched (killing the
  per-row N+1 behind the seven flat compat fields), and one query otherwise;
* resolve a whole page in a single query via the batch helper.
"""

from decimal import Decimal

from django.db import connection
from django.test.utils import CaptureQueriesContext

import pytest

from inventory.models import InventoryItem, ItemSupplier, Supplier
from inventory.services.supplier_selection import (
    BASIS_CHEAPEST_ORDERABLE,
    BASIS_FLAGGED_PRIMARY,
    NO_SUPPLIERS,
    NONE_ORDERABLE,
    primary_item_supplier,
    primary_suppliers_for,
    select_supplier,
    select_suppliers_for,
)

pytestmark = pytest.mark.django_db


def _item(name="Widget"):
    return InventoryItem.objects.create(
        name=name,
        description="x",
        reorder_quantity=1,
        current_stock=10,
        minimum_stock=1,
    )


def _supplier(name):
    return Supplier.objects.create(name=name, supplier_type=Supplier.SupplierType.LOCAL)


def _link(
    item,
    name,
    *,
    is_primary,
    unit_cost,
    quantity_per_package=1,
    is_active=True,
    is_discontinued=False,
    average_lead_time=7,
):
    return ItemSupplier.objects.create(
        item=item,
        supplier=_supplier(name),
        supplier_sku=f"{name}-sku",
        unit_cost=None if unit_cost is None else Decimal(unit_cost),
        quantity_per_package=quantity_per_package,
        is_primary=is_primary,
        is_active=is_active,
        is_discontinued=is_discontinued,
        average_lead_time=average_lead_time,
    )


def _legacy_primary(item):
    """The exact selection the property used before #882."""
    link = item.item_suppliers.select_related("supplier").filter(is_primary=True).first()
    if link:
        return link
    return item.item_suppliers.select_related("supplier").first()


# ── Selection identical to the legacy behaviour ──────────────────────────────


def test_returns_none_when_item_has_no_suppliers():
    assert primary_item_supplier(_item()) is None


def test_prefers_the_flagged_primary_over_a_cheaper_non_primary():
    item = _item()
    primary = _link(item, "A", is_primary=True, unit_cost="5.00")
    _link(item, "B", is_primary=False, unit_cost="1.00")
    assert primary_item_supplier(item).pk == primary.pk


def test_falls_back_to_cheapest_when_none_is_primary():
    item = _item()
    _link(item, "A", is_primary=False, unit_cost="5.00")
    cheapest = _link(item, "B", is_primary=False, unit_cost="1.00")
    assert primary_item_supplier(item).pk == cheapest.pk


def test_among_multiple_primaries_picks_the_cheapest():
    item = _item()
    _link(item, "A", is_primary=True, unit_cost="5.00")
    cheaper_primary = _link(item, "B", is_primary=True, unit_cost="1.00")
    assert primary_item_supplier(item).pk == cheaper_primary.pk


@pytest.mark.parametrize(
    "rows",
    [
        [("A", True, "3.00")],
        [("A", False, "3.00")],
        [("A", True, "5.00"), ("B", False, "1.00")],
        [("A", False, "5.00"), ("B", False, "1.00")],
        [("A", True, "5.00"), ("B", True, "1.00")],
        [("A", False, None), ("B", False, "1.00")],
        [("A", True, None), ("B", False, "1.00")],
    ],
)
def test_selection_matches_legacy_query(rows):
    """For every ORDERABLE supplier arrangement, the service picks the legacy row.

    op-2rsp narrowed the candidate set but not the ranking, so with every link
    orderable — which every row here is — the answer is byte-for-byte what the
    pre-#882 ``filter(is_primary=True).first() or first()`` pair returned.
    """
    item = _item()
    for name, is_primary, cost in rows:
        _link(item, name, is_primary=is_primary, unit_cost=cost)

    # Re-fetch so neither path is warmed by a prefetch/cache.
    fresh = InventoryItem.objects.get(pk=item.pk)
    expected = _legacy_primary(fresh)
    assert primary_item_supplier(InventoryItem.objects.get(pk=item.pk)).pk == expected.pk


# ── Query budget ─────────────────────────────────────────────────────────────


def test_single_lookup_costs_one_query_without_prefetch():
    item = _item()
    _link(item, "A", is_primary=True, unit_cost="1.00")
    fresh = InventoryItem.objects.get(pk=item.pk)
    with CaptureQueriesContext(connection) as ctx:
        link = primary_item_supplier(fresh)
        _ = link.supplier.name  # must NOT trigger a second query
    assert len(ctx.captured_queries) == 1


def test_single_lookup_costs_zero_queries_when_prefetched():
    item = _item()
    _link(item, "A", is_primary=True, unit_cost="1.00")
    prefetched = InventoryItem.objects.prefetch_related("item_suppliers__supplier").get(pk=item.pk)
    with CaptureQueriesContext(connection) as ctx:
        link = primary_item_supplier(prefetched)
        _ = link.supplier.name
    assert len(ctx.captured_queries) == 0


def test_reading_all_flat_fields_is_one_query_unprefetched_and_cached():
    item = _item()
    _link(item, "A", is_primary=True, unit_cost="2.50", quantity_per_package=6)
    fresh = InventoryItem.objects.get(pk=item.pk)
    with CaptureQueriesContext(connection) as ctx:
        # All seven flat compat reads + the derived ones share one cached load.
        _ = (
            fresh.supplier,
            fresh.primary_supplier,
            fresh.supplier_sku,
            fresh.supplier_url,
            fresh.unit_cost,
            fresh.package_cost,
            fresh.quantity_per_package,
            fresh.average_lead_time,
        )
    assert len(ctx.captured_queries) == 1


def test_property_delegates_to_service_and_is_cached():
    item = _item()
    link = _link(item, "A", is_primary=True, unit_cost="1.00")
    prefetched = InventoryItem.objects.prefetch_related("item_suppliers__supplier").get(pk=item.pk)
    assert prefetched.primary_item_supplier.pk == link.pk
    with CaptureQueriesContext(connection) as ctx:
        # Second access is memoised — no query even without a prefetch cache hit.
        _ = prefetched.primary_item_supplier
    assert len(ctx.captured_queries) == 0


# ── Batch helper ─────────────────────────────────────────────────────────────


def test_batch_resolves_a_page_in_one_query_and_matches_per_item():
    items = []
    for i in range(4):
        it = _item(f"Item-{i}")
        _link(it, f"P{i}", is_primary=True, unit_cost=str(5 + i))
        _link(it, f"C{i}", is_primary=False, unit_cost="0.50")
        items.append(it)
    supplierless = _item("Bare")
    items.append(supplierless)

    with CaptureQueriesContext(connection) as ctx:
        batch = primary_suppliers_for(items)
    assert len(ctx.captured_queries) == 1

    assert batch[supplierless.id] is None
    for it in items[:4]:
        expected = primary_item_supplier(InventoryItem.objects.get(pk=it.pk))
        assert batch[it.id].pk == expected.pk


def test_batch_empty_input_returns_empty_map_without_querying():
    with CaptureQueriesContext(connection) as ctx:
        assert primary_suppliers_for([]) == {}
    assert len(ctx.captured_queries) == 0


# ── Orderability: a supplier nobody can buy from is never the choice (op-2rsp) ─


@pytest.mark.parametrize(
    "dead_kwargs",
    [
        {"is_discontinued": True},
        {"is_active": False},
        {"is_active": False, "is_discontinued": True},
    ],
    ids=["discontinued", "inactive", "both"],
)
def test_cheapest_link_is_skipped_when_it_cannot_be_ordered(dead_kwargs):
    """The headline case: the cheapest supplier is one you cannot buy from."""
    item = _item()
    _link(item, "DeadCheap", is_primary=False, unit_cost="1.00", **dead_kwargs)
    live = _link(item, "LiveDear", is_primary=False, unit_cost="9.00")
    assert primary_item_supplier(item).pk == live.pk


def test_flagged_primary_is_skipped_when_it_cannot_be_ordered():
    """``mark_discontinued`` leaves ``is_primary`` set, so the flag is not enough.

    An operator flags a supplier, then later marks the item discontinued from
    them. Both flags now stand on the same row; orderability wins.
    """
    item = _item()
    _link(item, "FlaggedDead", is_primary=True, unit_cost="1.00", is_discontinued=True)
    live = _link(item, "Live", is_primary=False, unit_cost="9.00")
    choice = select_supplier(item)
    assert choice.item_supplier.pk == live.pk
    assert choice.basis == BASIS_CHEAPEST_ORDERABLE
    # The operator DID choose, and their choice was overridden — say so.
    assert choice.flagged_primary_unorderable is True


def test_orderable_flagged_primary_still_beats_a_cheaper_orderable_link():
    """Narrowing the candidates must not disturb the ranking among them."""
    item = _item()
    flagged = _link(item, "Flagged", is_primary=True, unit_cost="5.00")
    _link(item, "Cheaper", is_primary=False, unit_cost="1.00")
    choice = select_supplier(item)
    assert choice.item_supplier.pk == flagged.pk
    assert choice.basis == BASIS_FLAGGED_PRIMARY
    assert choice.flagged_primary_unorderable is False


def test_filtering_survives_a_prefetch_and_costs_no_query():
    """The filter runs in Python precisely so the prefetch cache still serves it."""
    item = _item()
    _link(item, "DeadCheap", is_primary=False, unit_cost="1.00", is_discontinued=True)
    live = _link(item, "LiveDear", is_primary=False, unit_cost="9.00")
    prefetched = InventoryItem.objects.prefetch_related("item_suppliers__supplier").get(pk=item.pk)
    with CaptureQueriesContext(connection) as ctx:
        chosen = primary_item_supplier(prefetched)
        _ = chosen.supplier.name
    assert chosen.pk == live.pk
    assert len(ctx.captured_queries) == 0


# ── "No suppliers" and "no orderable suppliers" are different facts (rule 4) ──


def test_no_suppliers_at_all_is_reported_as_such():
    choice = select_supplier(_item())
    assert choice.item_supplier is None
    assert choice.reason == NO_SUPPLIERS
    assert bool(choice) is False


def test_every_link_dead_is_reported_as_none_orderable_not_as_no_suppliers():
    item = _item()
    _link(item, "Dead", is_primary=False, unit_cost="1.00", is_discontinued=True)
    _link(item, "Off", is_primary=False, unit_cost="2.00", is_active=False)
    choice = select_supplier(item)
    assert choice.item_supplier is None
    assert choice.reason == NONE_ORDERABLE
    assert choice.reason != NO_SUPPLIERS


def test_none_orderable_records_that_a_primary_had_been_flagged():
    item = _item()
    _link(item, "FlaggedDead", is_primary=True, unit_cost="1.00", is_discontinued=True)
    choice = select_supplier(item)
    assert choice.reason == NONE_ORDERABLE
    assert choice.flagged_primary_unorderable is True


# ── The batch helper answers identically ─────────────────────────────────────


def test_batch_skips_unorderable_links_exactly_as_the_single_lookup_does():
    dead_only = _item("DeadOnly")
    _link(dead_only, "D1", is_primary=False, unit_cost="1.00", is_discontinued=True)

    mixed = _item("Mixed")
    _link(mixed, "M-dead", is_primary=True, unit_cost="1.00", is_active=False)
    mixed_live = _link(mixed, "M-live", is_primary=False, unit_cost="9.00")

    bare = _item("Bare")

    with CaptureQueriesContext(connection) as ctx:
        batch = select_suppliers_for([dead_only, mixed, bare])
    assert len(ctx.captured_queries) == 1

    assert batch[dead_only.id].reason == NONE_ORDERABLE
    assert batch[mixed.id].item_supplier.pk == mixed_live.pk
    assert batch[bare.id].reason == NO_SUPPLIERS

    # And identical to resolving each item on its own.
    for it in (dead_only, mixed, bare):
        single = select_supplier(InventoryItem.objects.get(pk=it.pk))
        assert single.reason == batch[it.id].reason
        assert (single.item_supplier and single.item_supplier.pk) == (
            batch[it.id].item_supplier and batch[it.id].item_supplier.pk
        )


# ── The flat compat properties inherit the filter ────────────────────────────


def test_flat_compat_properties_never_quote_an_unorderable_supplier():
    item = _item()
    _link(
        item,
        "DeadCheap",
        is_primary=True,
        unit_cost="1.00",
        quantity_per_package=99,
        is_discontinued=True,
        average_lead_time=1,
    )
    _link(item, "LiveDear", is_primary=False, unit_cost="9.00", quantity_per_package=3)
    fresh = InventoryItem.objects.get(pk=item.pk)
    assert fresh.supplier.name == "LiveDear"
    assert fresh.supplier_sku == "LiveDear-sku"
    assert fresh.unit_cost == Decimal("9.00")
    assert fresh.quantity_per_package == 3
    assert fresh.average_lead_time == 7


def test_flat_compat_properties_go_none_when_nothing_is_orderable():
    """The same shape an item with no suppliers has always produced."""
    item = _item()
    _link(item, "DeadOnly", is_primary=True, unit_cost="1.00", is_discontinued=True)
    fresh = InventoryItem.objects.get(pk=item.pk)
    assert fresh.supplier is None
    assert fresh.supplier_sku is None
    assert fresh.unit_cost is None
    assert fresh.average_lead_time is None
