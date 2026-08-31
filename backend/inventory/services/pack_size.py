"""The single derivation of "how many base units are in one package?" (op-c1ke).

The sibling of :mod:`inventory.services.supplier_selection`, which owns "which
supplier do we buy this item from?". This module owns a different fact — how
many loose units a box holds — and it is deliberately NOT the same question:
``AGENTS.md`` records that routing pack size through the orderability filter
suppressed a low-stock alert, because a dead vendor's recorded pack size still
describes the box already sitting on the shelf.

Before this module the fact had no owner. ``InventoryItem._case_pack_size`` read
the FIRST link, orderable or not; ``InventoryItem.quantity_per_package``,
``item_metrics``'s ``case_size`` and ``bridge_case_reorder_to_packaging`` read
the orderable derivation; and ``reorder_queue``'s ordering paths each carried
their own ``quantity_per_package or 1``. They could disagree on one item, and
three of them turned "nobody recorded a pack size" into the confident number 1.

**Three states, kept distinct.** The whole point of this module is that a caller
can tell them apart, because they need different words in front of an operator
and they must never collapse:

* :data:`PACK_SIZE_KNOWN` — a link records a usable pack size. The number is
  real; use it.
* :data:`PACK_SIZE_NOT_RECORDED` — the consulted row does not record one, and
  for the item-level entry points that means there is no row to consult at all:
  the item has NO supplier links. We were never told. The operator's action is
  to add a supplier link.
* :data:`PACK_SIZE_RECORDED_ZERO` — a link records ``quantity_per_package`` of
  ``0``: a box holding no units, which is not a box. ``PositiveIntegerField``
  permits it and ``MinValueValidator(1)`` only bites under ``full_clean()``, so
  ``InventoryItemViewSet._sync_primary_supplier`` — which writes through
  ``update_or_create`` — persists a posted ``0`` unchallenged. The operator's
  action is to correct that row.

:func:`order_pack_size` adds a fourth, because the question it asks has one more
way to come back empty:

* :data:`PACK_SIZE_NO_ORDERABLE_LINK` — supplier rows exist, and one of them may
  well record a perfectly good pack size, but every one is inactive or
  discontinued, so nothing we can BUY sizes the next order. The operator's
  action is to re-activate a link or add a vendor that still carries the item —
  NOT "add a supplier link", which is the wrong screen for an item that already
  has three of them. Reusing ``PACK_SIZE_NOT_RECORDED`` here would be the
  ``NO_SUPPLIERS`` / ``NONE_ORDERABLE`` collapse this branch exists to stop,
  moved to the state level.

The last three are all **unknown**: :attr:`PackSize.units` is ``None`` and
:meth:`PackSize.__bool__` is ``False`` for each. They are reported separately
anyway, because "we were never told", "we were told something impossible" and
"we were told, and the answer is no" send an operator to different screens —
the same reason ``supplier_selection`` keeps ``NO_SUPPLIERS`` apart from
``NONE_ORDERABLE``, and this state is derived from that very distinction rather
than re-deciding it here.

**A pack size of 1 is KNOWN, not missing.** ``quantity_per_package`` defaults to
1, so a link that records 1 cannot be told apart from one nobody filled in —
but the honest reading of the column is what it says, and a vendor really can
sell singles. Callers that need "did this vendor declare a CASE?" ask a
different question and get it from :func:`declares_a_case`, which is the op-ev14
ladder's entry condition rather than a claim about what is known.

**Two questions, one rule.** :func:`pack_size_of` reads ONE link and is the only
place the column is interpreted. The two item-level entry points differ solely
in WHICH link they ask, and the difference is load-bearing:

* :func:`shelf_pack_size` — "how many units are in the box on the shelf?" The
  FIRST link in ``ItemSupplier.Meta.ordering``, orderable or not. Stock already
  bought was bought from somebody, possibly somebody we can no longer buy from.
  This is what ``InventoryItem.current_cases`` counts with.
* :func:`order_pack_size` — "how many units will be in the box if we buy one
  now?" The link :mod:`inventory.services.supplier_selection` chose, so an
  inactive or discontinued vendor never sizes an order. This is what
  ``item_metrics``'s ``case_size``, ``InventoryItem.quantity_per_package`` and
  the packaging bridge read.

Both ride an ``item_suppliers`` prefetch when the caller set one up, so
serialising a page costs no extra query. A caller that has already resolved the
link for a whole page — ``item_metrics`` batches it through
``primary_suppliers_for`` — feeds those rows straight to :func:`pack_size_of`
rather than asking again, so the batched read paths keep their query budget.

``inventory/tests/test_pack_size_single_owner.py`` pins the reader set: a new
read of ``quantity_per_package`` anywhere in ``backend/`` fails the build until
it either goes through this module or is added to that snapshot deliberately.
"""

from dataclasses import dataclass
from typing import Optional

from inventory.models import ItemSupplier

#: A link records a usable pack size — :attr:`PackSize.units` holds it.
PACK_SIZE_KNOWN = "known"

#: No supplier link exists to record a pack size. A data gap, not a number.
PACK_SIZE_NOT_RECORDED = "not_recorded"

#: A link records ``quantity_per_package`` of 0 — a box holding no units.
PACK_SIZE_RECORDED_ZERO = "recorded_zero"

#: Supplier links exist, but none is orderable, so nothing we can BUY records a
#: pack size for the next order. Only :func:`order_pack_size` returns this. A
#: DIFFERENT fact from :data:`PACK_SIZE_NOT_RECORDED`, pointing the operator at
#: a different action (revive a vendor, not add one) — the pack-size face of
#: ``supplier_selection``'s ``NONE_ORDERABLE`` versus ``NO_SUPPLIERS``.
PACK_SIZE_NO_ORDERABLE_LINK = "no_orderable_link"


@dataclass(frozen=True)
class PackSize:
    """How many base units one package holds, and how well we know it.

    ``units`` is the number when it is known and ``None`` when it is not;
    ``state`` says which case produced that, so a caller can tell an operator
    "nobody told us" apart from "this row says a box holds nothing" apart from
    "every vendor who could tell us is dead". ``link`` is the row consulted, or
    ``None`` when there was none to consult.

    Truthiness follows ``units``, so ``if pack:`` reads as "do we know?".
    """

    units: Optional[int] = None
    state: str = PACK_SIZE_NOT_RECORDED
    link: Optional[ItemSupplier] = None

    def __bool__(self) -> bool:
        return self.units is not None

    @property
    def is_known(self) -> bool:
        """``True`` when :attr:`units` is a usable number."""
        return self.units is not None


#: The answer when there is no link at all. Shared so the common case allocates
#: nothing and so identity comparisons in tests are stable.
NOT_RECORDED = PackSize()

#: The answer :func:`order_pack_size` gives when links exist but none is
#: orderable. Shared for the same reason as :data:`NOT_RECORDED`.
NO_ORDERABLE_LINK = PackSize(state=PACK_SIZE_NO_ORDERABLE_LINK)


def pack_size_of(link: Optional[ItemSupplier]) -> PackSize:
    """Interpret ONE supplier link's ``quantity_per_package``.

    The only place in the codebase that column is turned into an answer. A
    positive value is :data:`PACK_SIZE_KNOWN`; ``0`` is
    :data:`PACK_SIZE_RECORDED_ZERO`; no link is :data:`PACK_SIZE_NOT_RECORDED`.

    Written as ``is None`` / ``<= 0`` rather than truthiness on purpose: ``or 1``
    and ``or 0`` on this column are exactly the collapse this module exists to
    stop, and a guard spelled with ``or`` cannot tell a recorded 0 from a
    missing row.
    """
    if link is None:
        return NOT_RECORDED
    units = link.quantity_per_package
    if units is None:
        return PackSize(state=PACK_SIZE_NOT_RECORDED, link=link)
    if units <= 0:
        return PackSize(state=PACK_SIZE_RECORDED_ZERO, link=link)
    return PackSize(units=units, state=PACK_SIZE_KNOWN, link=link)


def declares_a_case(link: Optional[ItemSupplier]) -> Optional[int]:
    """Base units in this vendor's CASE, or ``None`` if it declares none (op-ev14).

    The entry condition of the ordering ladder in
    :func:`reorder_queue.services.purchase_orders.order_package_size`: a pack
    size of 1 is a genuine single rather than a case, so the ladder falls
    through to the item's own packaging rung. Distinct from
    :func:`pack_size_of` — 1 is *known*, it is simply not a case — and from the
    old ``quantity_per_package or 1``, which mapped a recorded 0 onto the same
    "sells singles" answer as a recorded 1.
    """
    pack = pack_size_of(link)
    if not pack.is_known or pack.units <= 1:
        return None
    return pack.units


def _first_link(item) -> Optional[ItemSupplier]:
    """The first of ``item``'s links in ``Meta.ordering``, riding a prefetch.

    ``item_suppliers.all()`` rather than a fresh ``.filter()`` so a caller that
    prefetched (every list/detail read path does) pays no query — the same N+1
    discipline ``supplier_selection`` documents.
    """
    return next(iter(item.item_suppliers.all()), None)


def shelf_pack_size(item) -> PackSize:
    """Units in the box ALREADY ON THE SHELF — the first link, orderable or not.

    Stock on hand was bought from somebody, and that somebody may since have
    been marked inactive or discontinued; their recorded pack size still
    describes the box. Filtering for orderability here is what suppressed a
    low-stock alert during op-2rsp (``AGENTS.md``), so it deliberately does not.

    Only the FIRST row is consulted — byte-for-byte the row the pre-op-2rsp
    ``primary_item_supplier`` returned. A first row recording ``0`` yields
    :data:`PACK_SIZE_RECORDED_ZERO` rather than scanning on to a later link:
    which vendor's box is on the shelf is unknowable, so a later row's pack
    size is a different guess, not a better one.
    """
    return pack_size_of(_first_link(item))


def order_pack_size(item) -> PackSize:
    """Units in the box THE NEXT ORDER SHIPS IN — the link we would buy through.

    Resolved through ``InventoryItem.primary_item_supplier`` — the memoised,
    prefetch-riding face of :mod:`inventory.services.supplier_selection` — so an
    inactive or discontinued vendor never sizes an order, quotes a case on the
    item detail, or sets ``item_metrics``'s ``case_size``.

    When there is no such link the two ways of getting there stay APART:
    :data:`PACK_SIZE_NOT_RECORDED` for an item with no supplier rows (a data
    gap — add a vendor) and :data:`PACK_SIZE_NO_ORDERABLE_LINK` for one whose
    rows all name vendors we cannot buy from (unbuyable — revive or replace
    one). Both are unknown and neither yields a number, so no flag moves either
    way; what differs is the sentence an operator is owed. Which of the two it
    is comes from ``select_supplier``'s own reason rather than from a second
    count of the rows here, so the distinction has one owner.

    Read through the model accessor rather than by calling the service again, so
    an item that has already resolved its supplier does not resolve it twice —
    ``test_reading_all_flat_fields_is_one_query_unprefetched_and_cached`` pins
    that the whole flat compat block costs ONE query. The reason lookup runs
    ONLY on the empty path, which that budget never takes.
    """
    link = item.primary_item_supplier
    if link is not None:
        return pack_size_of(link)

    from inventory.services.supplier_selection import NONE_ORDERABLE, select_supplier

    if select_supplier(item).reason == NONE_ORDERABLE:
        return NO_ORDERABLE_LINK
    return NOT_RECORDED
