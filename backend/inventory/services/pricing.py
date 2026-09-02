"""The single derivation of "what does this cost, and do we know?" (op-9m2v).

The third of the single-owner derivations, after
:mod:`inventory.services.supplier_selection` ("which supplier do we buy this
item from?") and :mod:`inventory.services.pack_size` ("how many base units are
in one package?"). This module owns a different fact again — what one unit, or
one package, costs from a supplier — and the rule it enforces is one sentence:

    **A price the system does not know must never be presented, summed, or
    compared as a real number; a recorded price of zero is a KNOWN price and
    must be treated as one.**

Both halves of that sentence were broken before this module, and in opposite
directions, because every reader spelled the guard with ``or``:

* ``reorder_queue.views``'s ``optimal_qty * (best_supplier.unit_cost or 0)``
  and ``item_supplier.unit_cost or Decimal("0.00")`` turned "nobody recorded a
  price" into a confident ``$0.00`` line, which an order total then summed. The
  order read as cheaper than it was, and nothing on the payload said a line had
  been costed at nothing.
* ``ReorderRequest.estimated_cost``'s ``if unit_cost:`` and
  ``PriceHistory.price_change_percentage``'s
  ``if previous.unit_cost and self.unit_cost`` did the reverse: a supplier that
  charges **nothing** — donated stock, a free sample, an internal transfer, all
  ordinary in a makerspace — was read as a supplier with no price on file.

**A price of 0.00 is a price.** That is the load-bearing judgement here and the
reason ``or`` is banned on these columns: ``or`` cannot tell a recorded ``0.00``
from a ``NULL``, so every guard spelled that way gets one of the two cases
wrong. :func:`unit_price_of` is written as ``is None`` throughout for exactly
the reason :func:`inventory.services.pack_size.pack_size_of` is.

**Four states, kept distinct.** As in ``pack_size``, the unknowns are carried
apart rather than collapsed, because an operator acts differently on each:

* :data:`PRICE_KNOWN` — a price is recorded. The number is real; use it.
  ``0.00`` lands here.
* :data:`PRICE_NOT_RECORDED` — a supplier link was consulted and its price
  column is ``NULL``. Nobody has told us what this vendor charges. The
  operator's action is to record a unit or package cost on that link.
* :data:`PRICE_NO_SUPPLIER_LINK` — there was no link to consult: the item has
  no supplier rows at all. The operator's action is to add a supplier, not to
  price one that does not exist.
* :data:`PRICE_NO_ORDERABLE_LINK` — links exist and one may well carry a
  perfectly good price, but every one is inactive or discontinued, so nothing
  we can BUY quotes the next order. The operator's action is to revive a link
  or add a vendor. Only :func:`order_unit_price` / :func:`order_package_price`
  return this, and they get it from ``supplier_selection``'s own
  ``NONE_ORDERABLE`` rather than re-counting the rows, so the
  ``NO_SUPPLIERS`` / ``NONE_ORDERABLE`` split has one owner.

The last three are all **unknown**: :attr:`Price.amount` is ``None`` and
:meth:`Price.__bool__` is ``False`` for each, so no downstream figure branches
on which one it is and no money moves between them.

**Unlike ``PackSize.state``, these states reach the wire.** ``reorder_data``
and ``create_optimized_order`` serialise the *cause* beside each unpriced line
(``unit_cost_state`` / ``unit_cost_detail``), because the pad's whole job is to
tell a purchaser what to do next and "no price on file" and "no supplier at
all" send them to different screens. :func:`explain` is the one place that
sentence is written.

**Totals stay honest by counting what they left out.** :class:`PriceRollup` is
the sum: it adds the extensions it can compute and counts the lines it could
not, so a payload reports ``estimated_total`` *and* ``unpriced_item_count``
rather than a confident number that silently omits a line. A total that was
silently wrong becoming visibly incomplete is the point.

**Two item-level questions, and they differ in WHICH link they ask** — the same
shape as ``pack_size``'s ``shelf_pack_size`` / ``order_pack_size``:

* :func:`order_unit_price` / :func:`order_package_price` — "what will this cost
  if we buy it now?" The link ``supplier_selection`` chose, so an inactive or
  discontinued vendor never prices an order.
* :func:`lowest_unit_price` — "what is the cheapest anyone records for this?"
  EVERY link, orderable or not, because valuing the stock already on the shelf
  is not the same question as choosing who to buy the next box from. This is
  what ``InventoryItem.total_value`` is worth, and routing it through the
  orderability filter would revalue a shelf on a vendor's status change.

``inventory/tests/test_price_single_owner.py`` pins the reader set with the AST,
exactly as ``test_pack_size_single_owner.py`` does for its column: a new read of
``unit_cost`` or ``package_cost`` anywhere under ``backend/`` fails the build
until it either goes through this module or is added to that snapshot
deliberately. **That gate walks ``backend/`` only** — a frontend reader is not
covered by it.

**Where this meets the supplier scoring.**
``supplier_selection.score_candidate``'s cost term used to be guarded
``if link.unit_cost and average_unit_cost``, so a free link earned nothing for
being free while ``average_orderable_unit_cost`` went on counting its ``0.00``
in the yardstick its rivals were measured against — the same falsy-zero mistake
this module exists to stop, left in place because repairing it changes which
supplier the system picks and that was the captain's call rather than a defect
fix. **The captain has since decided it** (``oms-supplier-scoring-weight-flaws``)
and that term now reads its price through :func:`unit_price_of` like every other
reader, so a donated link is PRICED AT ZERO in the ranking and wins on it.
``inventory/tests/test_supplier_scoring.py`` pins the new behaviour, and
``test_price_single_owner.py``'s allowlist entry for that module dropped from 5
direct column reads to 3 in the same change.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Union

from inventory.models import ItemSupplier, PriceHistory

#: A row that records what something costs: a live supplier link, or the
#: :class:`~inventory.models.PriceHistory` snapshot of one. Both carry the same
#: two nullable columns and need the same reading, which is why
#: :func:`unit_price_of` takes either.
PricedRow = Union[ItemSupplier, PriceHistory]

#: A price is recorded — :attr:`Price.amount` holds it. ``0.00`` lands here: a
#: vendor that charges nothing has told us what it charges.
PRICE_KNOWN = "known"

#: A supplier link was consulted and its price column is ``NULL``. Nobody has
#: recorded what this vendor charges. The operator adds a price to that link.
PRICE_NOT_RECORDED = "not_recorded"

#: There was no supplier link to read a price from — the item has none. A
#: DIFFERENT fact from :data:`PRICE_NOT_RECORDED`, pointing the operator at a
#: different screen (add a vendor, not price one).
PRICE_NO_SUPPLIER_LINK = "no_supplier_link"

#: Supplier links exist but none is orderable, so nothing we can BUY quotes a
#: price for the next order. Only the ``order_*`` entry points return it — the
#: price face of ``supplier_selection``'s ``NONE_ORDERABLE`` versus
#: ``NO_SUPPLIERS``.
PRICE_NO_ORDERABLE_LINK = "no_orderable_link"

#: What an operator does about each unknown, in one sentence. Keyed by state so
#: a payload can carry the cause AND the remedy without every endpoint writing
#: its own wording. :data:`PRICE_KNOWN` is absent: there is nothing to do.
_REMEDIES = {
    PRICE_NOT_RECORDED: (
        "No price is recorded for {item} from {supplier}. Add a unit or package "
        "cost to that supplier link, or enter the price on this line."
    ),
    PRICE_NO_SUPPLIER_LINK: (
        "No supplier is linked to {item}, so nothing quotes a price for it. Add "
        "a supplier on the item, with a cost."
    ),
    PRICE_NO_ORDERABLE_LINK: (
        "Every supplier link for {item} is inactive or discontinued, so nothing "
        "you can buy from quotes a price. Reactivate one, or add a supplier that "
        "still carries it."
    ),
}


@dataclass(frozen=True)
class Price:
    """What something costs, and how well we know it.

    ``amount`` is the money when it is known — **including ``Decimal("0.00")``,
    which is a price and not an absence** — and ``None`` when it is not;
    ``state`` says which case produced that, so a caller can tell an operator
    "this vendor never told us" apart from "you have no vendor for this" apart
    from "every vendor you had is dead". ``link`` is the row consulted (a
    supplier link, or the ``PriceHistory`` snapshot of one), or ``None`` when
    there was none to consult.

    Truthiness follows ``amount``, so ``if price:`` reads as "do we know?" — and
    NOT as "is it more than nothing?", which is the confusion this whole module
    exists to remove. Read :attr:`amount` when you need the number.
    """

    amount: Optional[Decimal] = None
    state: str = PRICE_NO_SUPPLIER_LINK
    link: Optional[PricedRow] = None

    def __bool__(self) -> bool:
        return self.amount is not None

    @property
    def is_known(self) -> bool:
        """``True`` when :attr:`amount` is a usable number, ``0.00`` included."""
        return self.amount is not None


#: The answer when there was no link to consult. Shared so the common case
#: allocates nothing and identity comparisons in tests are stable.
NO_SUPPLIER_LINK = Price()

#: The answer the ``order_*`` entry points give when links exist but none is
#: orderable. Shared for the same reason as :data:`NO_SUPPLIER_LINK`.
NO_ORDERABLE_LINK = Price(state=PRICE_NO_ORDERABLE_LINK)


def price_float(price: Price) -> Optional[float]:
    """A :class:`Price` as a JSON number, or ``None`` when it is not known.

    The one rendering of a price onto a payload, so "a price as JSON" has an
    owner like every other fact here. ``None`` only where the price is
    genuinely unknown; a recorded ``0.00`` comes through as ``0.0``, which is
    what the supplier charges — the distinction ``float(x) if x else None``
    could not make (op-9m2v).
    """
    return None if not price.is_known else float(price.amount)


def _price_of(link: Optional[PricedRow], column: str) -> Price:
    """Interpret ONE priced row's price column.

    Written as ``is None`` rather than truthiness on purpose: ``or 0`` and
    ``if cost:`` on these columns are exactly the collapse this module exists to
    stop, and a guard spelled that way cannot tell a recorded ``0.00`` from a
    ``NULL``. A negative price is not treated as a fourth state — the write
    paths reject one (``_coerce_unit_cost``), and inventing a state for a value
    the database should not hold would put a branch in front of every reader
    for a population of zero.
    """
    if link is None:
        return NO_SUPPLIER_LINK
    amount = getattr(link, column)
    if amount is None:
        return Price(state=PRICE_NOT_RECORDED, link=link)
    return Price(amount=amount, state=PRICE_KNOWN, link=link)


def unit_price_of(link: Optional[PricedRow]) -> Price:
    """What ONE base unit costs on this row — the only reading of ``unit_cost``.

    Accepts a :class:`~inventory.models.PriceHistory` snapshot as well as a
    live link: a snapshot records the same two columns and needs the same
    reading, and ``PriceHistory.price_change_percentage`` is a caller that had
    the falsy guard in its own right.
    """
    return _price_of(link, "unit_cost")


def package_price_of(link: Optional[PricedRow]) -> Price:
    """What ONE package costs on this row — the only reading of ``package_cost``."""
    return _price_of(link, "package_cost")


#: ``latest`` costs MORE than ``previous``.
PRICE_INCREASING = "increasing"

#: ``latest`` costs LESS than ``previous``.
PRICE_DECREASING = "decreasing"

#: The two prices are the same number — including two recorded ``0.00``s.
PRICE_STABLE = "stable"


def direction_between(previous: Price, latest: Price) -> Optional[str]:
    """Which way two KNOWN prices moved, or ``None`` if either is unknown.

    A different question from "by what percentage?", and it still has an answer
    when that one does not: a rise from a recorded ``0.00`` has no percentage —
    there is no baseline to divide by — but it is unambiguously an INCREASE,
    and an operator looking at the item detail is owed that rather than a blank.
    :meth:`~inventory.serializers.InventoryItemDetailSerializer.get_price_trend_summary`
    is the caller, and reports it beside ``change_percentage: null``.

    Deliberately NOT used to re-derive the ordinary ``trend``, which is the sign
    of the ROUNDED percentage: the two agree on every realistic input but not on
    a change small enough to round to ``0.00``, and merging them would move a
    label for a reason that is not "base presented an unknown price as a real
    number".
    """
    if previous.amount is None or latest.amount is None:
        return None
    if latest.amount > previous.amount:
        return PRICE_INCREASING
    if latest.amount < previous.amount:
        return PRICE_DECREASING
    return PRICE_STABLE


def extended(price: Price, quantity) -> Optional[Decimal]:
    """``price × quantity``, or ``None`` when the price is unknown.

    The one multiplication in the codebase that a caller may make with a
    :class:`Price`. An unknown price yields an unknown line total rather than a
    zero one, which is the whole difference between an order that reads as
    cheaper than it is and one that says a line is uncosted.

    A **known** price of ``0.00`` extends to ``0.00`` — a real line total for a
    free item, and the value base already produced for it.
    """
    if price.amount is None:
        return None
    return Decimal(quantity) * price.amount


def explain(price: Price, *, item_name: str, supplier_name: Optional[str] = None) -> Optional[str]:
    """The sentence an operator is owed about an unknown price, or ``None``.

    ``None`` for :data:`PRICE_KNOWN`: there is nothing to act on. Every other
    state names both the cause and the remedy, because a blank an operator
    cannot act on is not a fix — the rule ``reorder_data``'s
    ``unorderable_items`` already follows for the supplier half of the same
    problem.
    """
    template = _REMEDIES.get(price.state)
    if template is None:
        return None
    return template.format(item=item_name, supplier=supplier_name or "this supplier")


class PriceRollup:
    """A money total that counts what it could not price.

    Every order-shaped payload in this codebase used to report one number for a
    group of lines, built by summing ``qty * (unit_cost or 0)``. A line nobody
    had priced contributed nothing and said nothing, so the total was wrong in
    the one direction that matters — low — and looked complete.

    This carries both halves: :attr:`amount` is the extension of the lines it
    COULD price (so a free line adds its honest ``0.00``), and
    :attr:`unpriced_count` is how many it could not. :attr:`is_complete` is the
    claim a caller is allowed to make about the total.
    """

    def __init__(self) -> None:
        self.amount = Decimal("0.00")
        self.unpriced_count = 0

    def add(self, price: Price, quantity) -> Optional[Decimal]:
        """Fold one line in, and return that line's total (``None`` if unpriced)."""
        line = extended(price, quantity)
        if line is None:
            self.unpriced_count += 1
            return None
        self.amount += line
        return line

    def absorb(self, other: "PriceRollup") -> None:
        """Fold another rollup in — a per-supplier group into an order-wide total."""
        self.amount += other.amount
        self.unpriced_count += other.unpriced_count

    @property
    def is_complete(self) -> bool:
        """``True`` when every line folded in carried a known price."""
        return self.unpriced_count == 0


def _order_link_price(item, column: str) -> Price:
    """The price on the link we would BUY through, keeping the two empties apart.

    Resolved through ``InventoryItem.primary_item_supplier`` — the memoised,
    prefetch-riding face of :mod:`inventory.services.supplier_selection` — so an
    inactive or discontinued vendor never prices an order, exactly as
    :func:`inventory.services.pack_size.order_pack_size` does for its own
    column, and for the same query-budget reason: the reason lookup runs ONLY on
    the empty path.
    """
    link = item.primary_item_supplier
    if link is not None:
        return _price_of(link, column)

    from inventory.services.supplier_selection import NONE_ORDERABLE, select_supplier

    if select_supplier(item).reason == NONE_ORDERABLE:
        return NO_ORDERABLE_LINK
    return NO_SUPPLIER_LINK


def order_unit_price(item) -> Price:
    """What one base unit costs FROM THE VENDOR WE WOULD BUY THROUGH."""
    return _order_link_price(item, "unit_cost")


def order_package_price(item) -> Price:
    """What one package costs FROM THE VENDOR WE WOULD BUY THROUGH."""
    return _order_link_price(item, "package_cost")


def lowest_unit_price(item) -> Price:
    """The cheapest unit price ANY link records for ``item`` — orderable or not.

    A different question from :func:`order_unit_price`, and deliberately not
    filtered for orderability: this values stock that is already on the shelf,
    which was bought from somebody who may since have been marked inactive or
    discontinued. Filtering here would revalue a shelf when a vendor's status
    changed, which is the shape of mistake ``pack_size.shelf_pack_size``
    documents from op-2rsp round 1.

    Rides ``item_suppliers.all()`` so a caller that prefetched (every list and
    detail read path does) pays no query. ``None`` amount with
    :data:`PRICE_NOT_RECORDED` when links exist but not one of them records a
    price, and with :data:`PRICE_NO_SUPPLIER_LINK` when there are no links at
    all — the same two empties, kept apart.
    """
    links = list(item.item_suppliers.all())
    if not links:
        return NO_SUPPLIER_LINK
    priced = [(link, link.unit_cost) for link in links if link.unit_cost is not None]
    if not priced:
        return Price(state=PRICE_NOT_RECORDED, link=links[0])
    link, amount = min(priced, key=lambda pair: pair[1])
    return Price(amount=amount, state=PRICE_KNOWN, link=link)
