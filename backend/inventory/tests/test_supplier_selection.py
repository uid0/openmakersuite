"""Tests for the named primary-supplier selection service (issue #882).

The service (``inventory.services.supplier_selection``) and the thin
``InventoryItem.primary_item_supplier`` shim that delegates to it must:

* select the IDENTICAL row the legacy ``filter(is_primary=True).first() or
  first()`` chose (primary-first, then cheapest);
* cost ZERO extra queries when ``item_suppliers`` is prefetched (killing the
  per-row N+1 behind the seven flat compat fields), and one query otherwise;
* resolve a whole page in a single query via the batch helper.
"""

from decimal import Decimal

from django.db import connection
from django.test.utils import CaptureQueriesContext

import pytest

from inventory.models import InventoryItem, ItemSupplier, Supplier
from inventory.services.supplier_selection import primary_item_supplier, primary_suppliers_for

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


def _link(item, name, *, is_primary, unit_cost, quantity_per_package=1):
    return ItemSupplier.objects.create(
        item=item,
        supplier=_supplier(name),
        supplier_sku=f"{name}-sku",
        unit_cost=None if unit_cost is None else Decimal(unit_cost),
        quantity_per_package=quantity_per_package,
        is_primary=is_primary,
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
    """For every supplier arrangement, the service picks the legacy row."""
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
