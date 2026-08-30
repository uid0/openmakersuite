"""The single derivation of "which supplier do we buy this item from?" (#882, op-2rsp).

Historically ``InventoryItem`` exposed its "primary supplier" through a cluster
of model properties (``supplier``, ``supplier_sku``, ``unit_cost``,
``average_lead_time`` …) that each ran a hidden
``item_suppliers.filter(is_primary=True).first()`` query. Reading them across a
page of items was therefore an N+1, and the selection logic was buried in the
model rather than named anywhere. #882 named it here.

op-2rsp made it the ONLY answer, and gave it a filter. The rule is:

    Among the item's ORDERABLE supplier links — ``is_active`` and not
    ``is_discontinued`` — take the first under ``ItemSupplier.Meta.ordering``
    (``["-is_primary", "unit_cost"]``): the link flagged primary, or, when
    nothing orderable is flagged, the cheapest orderable one.

**Orderability is not a tiebreak, it is a precondition.** An inactive or
discontinued link is a supplier nobody can buy from; handing one to a purchase
order, an order pad, or a lead-time estimate presents an unbuyable option as the
one to buy. The item-detail page already dims those rows so they do not read as
actionable — this module is why that dimming is not contradicted one screen
later. ``mark_discontinued`` deliberately does not clear ``is_primary``, so even
a FLAGGED primary can be unorderable and gets skipped like any other.

**A refusal is a result, not a blank.** ``select_supplier`` distinguishes "this
item has no suppliers at all" from "it has suppliers but none you can buy from"
(:class:`NoChoice`), because those need different words in front of an operator
and different actions from them. Callers that only need the row keep using
:func:`primary_item_supplier`, which is that result with the reason dropped.

**Ranking among orderable candidates is deliberately unchanged** and is NOT this
module's to decide. ``reorder_queue.views.PurchaseOrderViewSet._find_best_supplier``
scores orderable candidates on cost/lead time/primary-flag instead, and whether
that weighted score should replace "cheapest" as the fallback when nothing is
flagged primary is an open product question — it changes what gets bought and
what gets spent. Until it is answered the two paths agree on WHO IS ELIGIBLE and
differ only in how they rank the eligible, which is the difference that is
actually about money.
"""

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

from inventory.models import ItemSupplier

#: No supplier link exists for this item at all — nobody has said where it
#: comes from. The operator's action is to add one.
NO_SUPPLIERS = "no_suppliers"

#: Supplier links exist, but every one is inactive or discontinued. The item is
#: described but not buyable. The operator's action is to reactivate a link or
#: add a supplier that still carries it. Distinct from :data:`NO_SUPPLIERS` on
#: purpose: "we were never told" and "we were told, and the answer is no" are
#: different facts and must not collapse into the same silence.
NONE_ORDERABLE = "none_orderable"

#: The chosen link is the one an operator flagged primary.
BASIS_FLAGGED_PRIMARY = "flagged_primary"

#: Nothing orderable is flagged primary, so the cheapest orderable link was
#: taken. This is the fallback the system picks for you; see the module
#: docstring on why the ranking is not settled.
BASIS_CHEAPEST_ORDERABLE = "cheapest_orderable"


@dataclass(frozen=True)
class SupplierChoice:
    """Which supplier to buy an item from, and why that one — or why none.

    Exactly one of ``item_supplier`` / ``reason`` is set. ``basis`` says which
    rule produced a choice, so a caller can tell an operator "you flagged this"
    apart from "the system picked this for you"; ``flagged_primary_unorderable``
    marks the case where an operator DID flag one and it was skipped because it
    cannot be bought from, which reads to them as their choice being ignored
    unless it is said out loud.
    """

    item_supplier: Optional[ItemSupplier] = None
    basis: Optional[str] = None
    reason: Optional[str] = None
    flagged_primary_unorderable: bool = False

    def __bool__(self) -> bool:
        return self.item_supplier is not None


def _orderable(link: ItemSupplier) -> bool:
    """Can anything actually be bought through this link right now?"""
    return link.is_active and not link.is_discontinued


def _choose(links: List[ItemSupplier]) -> SupplierChoice:
    """Resolve one item's ``ItemSupplier`` rows, already in ``Meta.ordering``.

    Filtering happens HERE, in Python, rather than as a ``.filter()`` on the
    queryset: the callers that matter serialise a whole page and rely on the
    ``item_suppliers`` prefetch cache, and a fresh ``.filter()`` would bypass
    that cache and reintroduce the per-row query #882 removed. The rows arrive
    primary-first then cheapest, so the first orderable row IS the answer.
    """
    if not links:
        return SupplierChoice(reason=NO_SUPPLIERS)

    flagged_exists = any(link.is_primary for link in links)
    for link in links:
        if not _orderable(link):
            continue
        basis = BASIS_FLAGGED_PRIMARY if link.is_primary else BASIS_CHEAPEST_ORDERABLE
        return SupplierChoice(
            item_supplier=link,
            basis=basis,
            flagged_primary_unorderable=flagged_exists and not link.is_primary,
        )
    return SupplierChoice(reason=NONE_ORDERABLE, flagged_primary_unorderable=flagged_exists)


def _links_for(item) -> List[ItemSupplier]:
    """This item's supplier rows in ``Meta.ordering``, riding a prefetch if set."""
    prefetched = getattr(item, "_prefetched_objects_cache", None) or {}
    if "item_suppliers" in prefetched:
        return list(item.item_suppliers.all())
    return list(item.item_suppliers.select_related("supplier").all())


def select_supplier(item) -> SupplierChoice:
    """Return the :class:`SupplierChoice` for ``item`` — the row AND the reason.

    When ``item`` was loaded with ``prefetch_related("item_suppliers")`` this
    reads the prefetch cache and costs ZERO queries; otherwise it runs a single
    query, pulling ``supplier`` in the same round-trip so a downstream
    ``.supplier`` access does not add another.
    """
    return _choose(_links_for(item))


def select_suppliers_for(items: Iterable) -> Dict:
    """Return ``{item_id: SupplierChoice}`` for ``items`` in ONE query.

    ``items`` is an iterable of :class:`~inventory.models.InventoryItem`
    instances (typically a single paginated page). Every item id is present in
    the result — a :class:`SupplierChoice` carrying :data:`NO_SUPPLIERS` where
    the item has no links — so callers can index the map directly.

    Every candidate row for the page is pulled, not just the orderable ones:
    "none orderable" can only be told apart from "no suppliers" by seeing the
    rows that were rejected. The batch is one ``select_related("supplier")``
    query ordered by ``item`` then the selection ordering, so each item's rows
    arrive together and in the same order :func:`select_supplier` sees them.
    """
    items = list(items)
    grouped: Dict = {item.id: [] for item in items}
    if not items:
        return {}

    for link in (
        ItemSupplier.objects.filter(item_id__in=list(grouped))
        .select_related("supplier")
        .order_by("item", "-is_primary", "unit_cost")
    ):
        grouped[link.item_id].append(link)
    return {item_id: _choose(links) for item_id, links in grouped.items()}


def primary_item_supplier(item) -> Optional[ItemSupplier]:
    """The orderable :class:`ItemSupplier` to buy ``item`` from, or ``None``.

    The row half of :func:`select_supplier`, for the many callers that only need
    somewhere to read a SKU or a cost from. ``None`` here means "no supplier you
    can buy from" and deliberately does NOT distinguish why — a caller that has
    to explain itself to an operator should use :func:`select_supplier` and read
    the reason.
    """
    return select_supplier(item).item_supplier


def primary_suppliers_for(items: Iterable) -> Dict:
    """``{item_id: orderable ItemSupplier | None}`` for ``items`` in ONE query.

    The row half of :func:`select_suppliers_for`, mirroring
    :func:`primary_item_supplier`'s relationship to :func:`select_supplier`.
    """
    return {
        item_id: choice.item_supplier for item_id, choice in select_suppliers_for(items).items()
    }
