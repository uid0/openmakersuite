"""Named primary-supplier selection for inventory items (issue #882).

Historically ``InventoryItem`` exposed its "primary supplier" through a cluster
of model properties (``supplier``, ``supplier_sku``, ``unit_cost``,
``average_lead_time`` …) that each ran a hidden
``item_suppliers.filter(is_primary=True).first()`` query. Reading them across a
page of items was therefore an N+1, and the selection logic was buried in the
model rather than named anywhere.

This module is the single, named home for that selection — for one item or a
whole page — built to ride the ``item_suppliers`` prefetch cache when the caller
set one up. ``InventoryItem.primary_item_supplier`` is now a thin, cached shim
that delegates here.

Selection is defined by ``ItemSupplier.Meta.ordering`` (``["-is_primary",
"unit_cost"]``): the supplier flagged primary with the lowest unit cost, or —
when none is flagged primary — the cheapest supplier overall. Both helpers
return byte-for-byte the row the legacy ``filter(is_primary=True).first() or
first()`` chose, because that pair of queries and this resolution both defer to
the same database ordering.
"""

from typing import Dict, Iterable, Optional

from inventory.models import ItemSupplier


def primary_item_supplier(item) -> Optional[ItemSupplier]:
    """Return the preferred :class:`ItemSupplier` for ``item`` (or ``None``).

    When ``item`` was loaded with ``prefetch_related("item_suppliers")`` this
    reads the prefetch cache and costs ZERO queries; otherwise it runs a single
    query, pulling ``supplier`` in the same round-trip so a downstream
    ``.supplier`` access does not add another. The first row under
    ``item_suppliers``' ``Meta.ordering`` is the primary — identical to the
    legacy ``filter(is_primary=True).first() or first()``.
    """
    prefetched = getattr(item, "_prefetched_objects_cache", None) or {}
    if "item_suppliers" in prefetched:
        item_suppliers = item.item_suppliers.all()
    else:
        item_suppliers = item.item_suppliers.select_related("supplier").all()
    return next(iter(item_suppliers), None)


def primary_suppliers_for(items: Iterable) -> Dict:
    """Return ``{item_id: primary ItemSupplier | None}`` for ``items`` in ONE query.

    ``items`` is an iterable of :class:`~inventory.models.InventoryItem`
    instances (typically a single paginated page). Every item id is present in
    the result — ``None`` where the item has no supplier — so callers can index
    the map directly. The batch pulls every candidate ``ItemSupplier`` for the
    page in a single ``select_related("supplier")`` query ordered by ``item``
    then the primary-selection ordering, keeping the first row seen per item.
    That first row is exactly what :func:`primary_item_supplier` would return for
    each item, but resolved once for the whole page rather than per row — the
    same shape as ``inventory.services.item_metrics.compute_item_metrics_batch``.
    """
    items = list(items)
    result: Dict = {item.id: None for item in items}
    if not items:
        return result

    for link in (
        ItemSupplier.objects.filter(item_id__in=list(result))
        .select_related("supplier")
        .order_by("item", "-is_primary", "unit_cost")
    ):
        # Rows arrive primary-first within each item; keep the first one seen.
        if result[link.item_id] is None:
            result[link.item_id] = link
    return result
