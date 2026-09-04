"""Unit-of-measure conversion + packaging-chain validation (op-hzji, phase 1).

Pure functions over :class:`inventory.models.PackagingLevel` rungs and an
item's base-unit stock. Nothing here writes: ``InventoryItem.current_stock``
remains the canonical BASE-unit quantity that every reorder / purchase /
usage flow reads, and these helpers only convert it between rungs or format it
for display.

Phase 2a (op-es7c) adds the *reorder* half: :func:`count_at_level` (the whole
count in the unit an item is counted in), :func:`base_reorder_quantity` (how
much to suggest ordering, still stored in base units) and :func:`low_stock_q`
(the SQL twin of ``needs_reorder``). ``each`` items — including legacy
``use_case_based_reorder`` ones — route through the same helpers and come out
byte-for-byte where they were; only an item opted into a pack-counting
``count_mode`` takes the new path.

Phase 2b (op-ev14) adds the *write* complement — :func:`resolve_base_quantity`,
the one seam every stock-moving path (reconcile, cycle count, usage, PO order,
receipt) puts a caller-supplied number through to get BASE units. One rule
covers all of them:

* by default a quantity is BASE units, so every existing caller — and every
  ``each`` item, which is all of them today — is untouched;
* a caller that has a pack count says so with ``at_level=True``, and it is
  converted through the item's ``count_level``;
* ``at_level=True`` on an item that is *not* counted in packs is an error, never
  a silent base-unit reading, so a mis-sent flag cannot corrupt stock.

Deliberately opt-in rather than inferred from ``count_mode``: unlike
``minimum_stock``/``reorder_quantity`` (which phase 2a reinterprets for
pack-counting items, because they are *configuration* a human sets), a
transaction quantity often arrives from a base-unit-canonical source — a PO
line's pending quantity, a work-order material template — and those callers must
keep meaning base units after an item switches mode.

:func:`order_level` names the rung an item is *bought* in, which is how the
purchase-order paths reconcile the item's own chain against a supplier's case
size (see :mod:`reorder_queue.services.purchase_orders`).

⚠️ For the pack-counting modes, ``InventoryItem.minimum_stock`` and
``reorder_quantity`` are read as thresholds/amounts in the item's COUNT unit
(cases, reams, sealed packs), not base units. That reinterpretation is the
point of the unification: one pair of columns drives reordering at whatever
granularity the item is counted at. ``each`` keeps their base-unit meaning.

The validators are shared deliberately: ``PackagingLevel.clean()`` calls
:func:`validate_packaging_chain` for single-row saves, and
``InventoryItemSerializer.validate`` calls it for the nested (bulk) write,
where ``clean()`` never runs.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Iterable, Optional, Sequence

from django.core.exceptions import ValidationError
from django.db.models import ExpressionWrapper, F, IntegerField, Q

if TYPE_CHECKING:
    from inventory.models.core import InventoryItem, PackagingLevel


def _attr(level: Any, name: str) -> Any:
    """Read ``name`` off a rung given either as a model instance or a dict.

    The serializer validates its incoming ``packaging_levels`` payload (a list
    of dicts) with the same rules the model applies to instances, so the chain
    validator accepts both shapes.
    """
    if isinstance(level, Mapping):
        return level.get(name)
    return getattr(level, name, None)


def to_base(level: "PackagingLevel", count: int) -> int:
    """Base units held by ``count`` whole rungs of ``level``."""
    return count * level.base_units


def to_level_count(base_units_qty: int, level: "PackagingLevel") -> tuple[int, int]:
    """Split a base-unit quantity into ``(whole rungs, leftover base units)``.

    Raises ``ValueError`` for a rung that holds fewer than one base unit — an
    impossible chain that :func:`validate_packaging_chain` already rejects, so
    reaching it means the row was written around the validators.
    """
    if level.base_units < 1:
        raise ValueError(f"Packaging level '{level.name}' must hold at least one base unit.")
    whole, remainder = divmod(base_units_qty, level.base_units)
    return whole, remainder


def _each_display(item: "InventoryItem") -> dict:
    """Base-unit display — today's behaviour, and the fallback for a broken chain."""
    return {
        "mode": "each",
        "base_units": item.current_stock,
        "unit": item.base_unit,
        "text": f"{item.current_stock} {item.base_unit}",
    }


def on_hand_display(item: "InventoryItem") -> dict:
    """Express ``item.current_stock`` at the item's counting granularity.

    Read-only presentation of the canonical base-unit count; the shape depends
    on ``item.count_mode``:

    * ``each`` → ``{mode, base_units, unit, text}``
    * ``by_level`` → ``{mode, level, level_count, remainder_base, text}`` — whole
      packs, with the leftover base units reported but deliberately not
      presented as countable (the point of the mode is that partials are not
      counted).
    * ``open_closed`` → ``{mode, level, sealed, open, text}``

    A pack-counting mode with no usable ``count_level`` falls back to the
    ``each`` shape rather than raising, so a half-configured item still renders.
    """
    level = item.count_level
    if item.count_mode == item.CountMode.EACH or level is None or level.base_units < 1:
        return _each_display(item)

    whole, remainder = to_level_count(item.current_stock, level)

    if item.count_mode == item.CountMode.OPEN_CLOSED:
        return {
            "mode": "open_closed",
            "level": level.name,
            "sealed": whole,
            "open": item.open_container_count,
            "text": f"{whole} sealed + {item.open_container_count} open",
        }

    return {
        "mode": "by_level",
        "level": level.name,
        "level_count": whole,
        "remainder_base": remainder,
        "text": f"{whole} {level.name}(s)",
    }


def counts_in_packs(item: "InventoryItem") -> bool:
    """True when ``item`` is counted in whole packs of a usable ``count_level``.

    The single predicate every mode-aware caller branches on (op-es7c). It is
    deliberately conservative: a half-configured item — a pack-counting
    ``count_mode`` with no ``count_level``, or one pointing at a rung that holds
    less than a base unit — reads as ``False`` and keeps today's base-unit
    behaviour instead of raising, mirroring :func:`on_hand_display`'s fallback.
    """
    if item.count_mode == item.CountMode.EACH:
        return False
    level = item.count_level
    return level is not None and level.base_units >= 1


def count_at_level(item: "InventoryItem") -> int:
    """The item's whole count expressed in the unit it is *counted* in.

    * ``each`` → ``current_stock`` (base units), i.e. today's number.
    * ``by_level`` / ``open_closed`` → whole packs of ``count_level``. A partial
      pack is not a countable pack, and under ``open_closed`` that is exactly
      the sealed count — the open container is not counted.

    Falls back to ``current_stock`` for a pack-counting item with no usable
    ``count_level`` (see :func:`counts_in_packs`).
    """
    if not counts_in_packs(item):
        return item.current_stock
    whole, _ = to_level_count(item.current_stock, item.count_level)
    return whole


def order_level(item: "InventoryItem") -> Optional["PackagingLevel"]:
    """The rung an item is *bought* in: the outermost rung of its chain (op-ev14).

    ``sort_order`` 0 is the largest rung, so the outermost one is how the item
    arrives from a supplier — a case of paper, a carton of gloves. Returns
    ``None`` for an item that is not counted in packs (see
    :func:`counts_in_packs`), which keeps ``each`` items — every item that
    exists today — on the supplier's ``quantity_per_package`` for ordering.

    Distinct from ``ItemSupplier.quantity_per_package`` on purpose: this is the
    item's *own* packaging, and a supplier may sell it in a different case. The
    purchase-order paths prefer the supplier's case size when one is declared
    (you buy what the vendor ships) and fall back to this rung when it is not.
    """
    if not counts_in_packs(item):
        return None
    return item.packaging_levels.order_by("sort_order").first()


# The wire spellings of the ``at_level`` flag, matching
# ``rest_framework.serializers.BooleanField``'s own sets so a form-encoded
# ``at_level=false`` reads the same on the endpoints that parse ``request.data``
# by hand as on the ones with a serializer. Spelled out rather than imported to
# keep this module free of DRF; :func:`parse_at_level` is pinned against
# ``BooleanField`` by a test so the two cannot drift.
# ``True``/``False`` cover the int spellings too: ``1 == True`` and ``0 == False``
# hash equal, so ``1 in _AT_LEVEL_TRUE`` holds without listing them twice.
_AT_LEVEL_TRUE = frozenset(
    {"t", "T", "y", "Y", "yes", "Yes", "YES", "true", "True", "TRUE", "on", "On", "ON", "1", True}
)
_AT_LEVEL_FALSE = frozenset(
    {
        "f",
        "F",
        "n",
        "N",
        "no",
        "No",
        "NO",
        "false",
        "False",
        "FALSE",
        "off",
        "Off",
        "OFF",
        "0",
        False,
    }
)


def parse_at_level(value: Any) -> bool:
    """Read an ``at_level`` flag off the wire (op-ev14).

    ``bool("false")`` is ``True``, so an endpoint that reads ``request.data``
    directly cannot use it: a form-encoded client — scantty posts strings —
    saying ``at_level=false`` would silently get the pack reading and a
    multiplied quantity. Missing/empty means the default, base units.

    Raises:
        ValidationError: a value that is neither a true nor a false spelling,
            rather than guessing at a quantity's unit.
    """
    if value is None or value == "":
        return False
    try:
        if value in _AT_LEVEL_TRUE:
            return True
        if value in _AT_LEVEL_FALSE:
            return False
    except TypeError:  # pragma: no cover - unhashable payload value
        pass
    raise ValidationError(f"at_level must be a boolean; got {value!r}.")


def resolve_base_quantity(
    item: "InventoryItem",
    count: int,
    *,
    at_level: bool = False,
    level: Optional["PackagingLevel"] = None,
) -> int:
    """Convert a caller-supplied ``count`` into BASE units (op-ev14).

    THE write seam: every path that moves stock from a number a caller sent —
    reconciliation, cycle count, usage, PO order quantity, receipt — routes
    through here so "is this base units or packs?" is answered in exactly one
    place.

    Args:
        item: the item the quantity belongs to.
        count: the caller's number, in whatever unit ``at_level`` selects.
        at_level: ``False`` (the default, and what every pre-op-ev14 caller
            effectively passes) → ``count`` is base units and comes back
            untouched. ``True`` → ``count`` is whole packs, and an item that is
            not counted in packs is an error rather than a guess.
        level: the rung ``count`` is expressed in, defaulting to the item's
            ``count_level``.

    Raises:
        ValidationError: ``at_level=True`` on an item that is not counted in
            packs, or a rung that holds less than one base unit.
    """
    if not at_level:
        return count

    if not counts_in_packs(item):
        raise ValidationError(
            f"'{item.name}' is not counted in packs (count mode "
            f"'{item.count_mode}'), so a pack count cannot be converted; "
            "send the quantity in base units."
        )

    rung = level or item.count_level
    if rung is None or rung.base_units < 1:
        raise ValidationError(
            f"'{item.name}' has no usable packaging level to count packs of; "
            "send the quantity in base units."
        )
    return to_base(rung, count)


def count_unit(item: "InventoryItem", level: Optional["PackagingLevel"] = None) -> str:
    """The noun a quantity for ``item`` is entered/reported in (op-ev14).

    The counting rung's name for a pack-counting item, ``base_unit`` otherwise.
    For labelling responses and error messages, so a caller can tell which unit
    the server just interpreted its number in.
    """
    if not counts_in_packs(item):
        return item.base_unit or "unit"
    rung = level or item.count_level
    return rung.name if rung is not None else (item.base_unit or "unit")


def reorder_threshold(item: "InventoryItem") -> tuple[int, str]:
    """``(threshold, unit noun)`` naming the item's reorder point as a human reads it.

    Pack-counting items read ``minimum_stock`` in their ``count_level``'s unit;
    legacy ``use_case_based_reorder`` items keep their ``minimum_cases``/"case"
    wording; everything else is ``minimum_stock`` base units. Callers own
    pluralisation.

    A case-based item whose case size is NOT KNOWN is named in base units
    instead (op-c1ke). "Reorder at: 1 case" is a threshold nothing can evaluate
    when nobody has recorded how many units a case holds, and this line is
    printed on the kanban card that outlives the screen it came from.

    For that shape the number is ``max(minimum_stock, minimum_cases)``, which is
    EXACTLY the boundary of the disjunction ``needs_reorder`` judges such an item
    on (``current_stock <= minimum_stock or current_stock <= minimum_cases``).
    Reporting the bare ``minimum_stock`` instead let one payload contradict
    itself: with ``minimum_stock`` 0, ``minimum_cases`` 1 and one unit on hand
    the badge said LOW while the card beside it printed "reorder at 0 units" — a
    threshold the stock was above. A badge and its own threshold line must name
    the same rule.
    """
    if counts_in_packs(item):
        return item.minimum_stock, item.count_level.name
    if item.use_case_based_reorder:
        if item.current_cases is not None:
            return item.minimum_cases, "case"
        return max(item.minimum_stock, item.minimum_cases), item.base_unit or "unit"
    return item.minimum_stock, item.base_unit or "unit"


def base_reorder_quantity(item: "InventoryItem") -> int:
    """How much to suggest ordering, in BASE units, before any supplier rounding.

    The stored ``ReorderRequest``/``PurchaseOrderItem`` quantity stays in base
    units — only the derivation is mode-aware:

    * ``each`` → UNCHANGED: ``max(minimum_stock - current_stock, reorder_quantity)``
      base units, which callers may still round up to a whole supplier package.
    * pack-counting → the same arithmetic one rung up, in the item's count unit,
      then converted through ``count_level``. With stock at the reorder point
      that is exactly ``reorder_quantity × count_level.base_units``; the
      shortage clause only bites for an item that fell far below its minimum,
      and is the count-level twin of the ``each`` behaviour rather than a new
      rule. Supplier packaging plays no part — a pack-counting item already
      orders whole packs of its OWN chain.
    """
    if not counts_in_packs(item):
        shortage = max(0, item.minimum_stock - item.current_stock)
        return max(shortage, item.reorder_quantity)

    shortage_levels = max(0, item.minimum_stock - count_at_level(item))
    return max(shortage_levels, item.reorder_quantity) * item.count_level.base_units


def _plural(unit: str, count) -> str:
    """``unit`` for exactly one, ``unit`` + "s" otherwise.

    Deliberately naive: it only ever appends "s". Hoisted out of
    :func:`reorder_display` UNCHANGED so :func:`order_quantity_text` words a
    quantity the same way its neighbours do, and left naive so no string this
    module already renders moves. The web's ``pluralizeUnit`` handles sibilant
    endings and is a shade better on purpose; nothing compares the two.
    """
    return unit if count == 1 else f"{unit}s"


def order_quantity_text(item: "InventoryItem", quantity: int) -> str:
    """``quantity`` BASE units, worded for a human who is about to have it ordered.

    The wording twin of :func:`base_reorder_quantity`, so a surface that FILES a
    reorder can name the number it is filing rather than a differently-derived
    one beside it. Base units are what a ``ReorderRequest.quantity`` is stored
    in — ``mark-received`` adds it straight to ``current_stock`` — so the base
    count is always spelled out; for an item counted in whole packs the pack
    reading leads, because that is the noun the shelf label and every other
    screen use for it.

    The pack reading is offered ONLY when the base count divides exactly into
    whole packs. A quantity that does not (the shortage clause can produce one
    for a half-configured item) would need "2.5 cases", and a member cannot act
    on a number of boxes that does not exist.
    """
    base_unit = item.base_unit or "unit"
    if counts_in_packs(item):
        per_pack = item.count_level.base_units
        if per_pack >= 1 and quantity % per_pack == 0:
            packs = quantity // per_pack
            pack_unit = item.count_level.name
            return (
                f"{packs} {_plural(pack_unit, packs)} "
                f"({quantity} {_plural(base_unit, quantity)})"
            )
    return f"{quantity} {_plural(base_unit, quantity)}"


def reorder_display(item: "InventoryItem") -> dict:
    """Reorder point + current count in one unit, ready to label a UI (op-es7c).

    ``{mode, unit, threshold, current, reorder_quantity, needs_reorder, text,
    order_quantity, order_text}``.

    Every quantity is expressed in ``unit`` EXCEPT ``order_quantity``, which is
    BASE units — the unit a filed ``ReorderRequest.quantity`` is stored in — and
    ``order_text``, which words it. The pair is here rather than in a block of
    its own because a surface that both shows a reorder quantity and files one
    must read a single answer: ``reorder_quantity`` is the item's CONFIGURED
    amount in its own counting unit, ``order_quantity`` is what filing a reorder
    right now would actually order, and a page that files must show the second.
    ``ScanPage`` is that page — it POSTs ``order_quantity`` and prints
    ``order_text``, so the two cannot name different numbers again
    (``test_reorder_filing.py``, ``ScanPage.test.tsx``).

    The two agree for an ``each`` item, differ by the pack size for a
    pack-counting one (3 cases ↔ 36 bottles), and differ outright for a legacy
    ``use_case_based_reorder`` item, whose ordering path reads
    ``reorder_quantity`` while its display reads ``reorder_cases`` — see
    :func:`base_reorder_quantity`, which owns that behaviour.
    """
    threshold, unit = reorder_threshold(item)
    current_cases = None if counts_in_packs(item) else item.current_cases
    if counts_in_packs(item):
        current: float = count_at_level(item)
        quantity = item.reorder_quantity
    elif item.use_case_based_reorder and current_cases is not None:
        current = current_cases
        quantity = item.reorder_cases
    else:
        # Either a plain base-unit item, or a case-based one whose case size is
        # not known (op-c1ke) — ``reorder_threshold`` has already named this
        # pair in base units, and "10 cases on hand" for ten loose units is a
        # wrong number on a printed card.
        current = item.current_stock
        quantity = item.reorder_quantity

    order_quantity = base_reorder_quantity(item)

    return {
        "mode": item.count_mode,
        "unit": unit,
        "threshold": threshold,
        "current": current,
        "reorder_quantity": quantity,
        "order_quantity": order_quantity,
        "order_text": order_quantity_text(item, order_quantity),
        "needs_reorder": item.needs_reorder,
        "text": (
            f"{current} {_plural(unit, current)} on hand · "
            f"reorder at {threshold} {_plural(unit, threshold)}"
        ),
    }


def low_stock_q() -> Q:
    """``Q`` selecting items at/below their reorder point — the SQL twin of ``needs_reorder``.

    For the reorder-queue surfaces that pre-filter in the database rather than
    walking the property. ``each`` items keep EXACTLY today's predicate,
    ``current_stock <= minimum_stock`` — including ``use_case_based_reorder``
    ones, whose SQL filter has always been that base-unit comparison even though
    the property compares cases (a pre-existing divergence this deliberately
    preserves *where the case size is known*). Where the case size is NOT known,
    ``needs_reorder`` adds this same comparison as one disjunct, so the two agree
    wherever ``minimum_cases <= minimum_stock`` — the shape where they used to
    disagree visibly (op-c1ke). Above that the property still flags an item this
    query does not match, through its second disjunct; that is the pre-existing
    divergence direction, kept deliberately because closing it the other way
    would REMOVE an alert base raised. Pack-counting items compare WHOLE packs
    instead::

        floor(current_stock / base_units) <= minimum_stock
        ⟺ current_stock < (minimum_stock + 1) × base_units

    The two branches are exact complements — the second is De Morgan's of
    :func:`counts_in_packs`, spelled positively so the nullable ``count_level``
    join is never negated.
    """
    from inventory.models.core import InventoryItem

    pack_modes = [InventoryItem.CountMode.BY_LEVEL, InventoryItem.CountMode.OPEN_CLOSED]

    counted_in_packs = Q(
        count_mode__in=pack_modes,
        count_level__isnull=False,
        count_level__base_units__gte=1,
    )
    counted_in_base = (
        ~Q(count_mode__in=pack_modes)
        | Q(count_level__isnull=True)
        | Q(count_level__base_units__lt=1)
    )
    pack_threshold = ExpressionWrapper(
        (F("minimum_stock") + 1) * F("count_level__base_units"),
        output_field=IntegerField(),
    )

    return (counted_in_base & Q(current_stock__lte=F("minimum_stock"))) | (
        counted_in_packs & Q(current_stock__lt=pack_threshold)
    )


def validate_packaging_chain(levels: Iterable[Any]) -> None:
    """Raise ``ValidationError`` unless ``levels`` form a coherent chain.

    Accepts model instances or payload dicts (see :func:`_attr`). An empty
    chain is valid — an item with no packaging levels is simply counted in base
    units. Otherwise:

    * every rung is named and holds at least one base unit;
    * ``sort_order`` is unique within the item;
    * exactly one rung is the base (``base_units == 1``), and it is the rung
      with the largest ``sort_order``;
    * ``base_units`` strictly decreases as ``sort_order`` increases — each rung
      is genuinely smaller than the one that contains it.
    """
    chain: Sequence[Any] = list(levels)
    if not chain:
        return

    errors: list[str] = []
    for level in chain:
        name = (_attr(level, "name") or "").strip()
        if not name:
            errors.append("Every packaging level needs a name.")
            break
    for level in chain:
        base_units = _attr(level, "base_units")
        if base_units is None or base_units < 1:
            errors.append("Every packaging level must hold at least one base unit.")
            break

    sort_orders = [_attr(level, "sort_order") for level in chain]
    if any(sort_order is None for sort_order in sort_orders):
        # Bail before the sort below, which cannot order None against an int.
        errors.append("Every packaging level needs a sort order.")
    elif len(set(sort_orders)) != len(sort_orders):
        errors.append("Packaging levels must have distinct sort orders.")

    if errors:
        raise ValidationError(errors)

    ordered = sorted(chain, key=lambda level: _attr(level, "sort_order"))
    base_rungs = [level for level in ordered if _attr(level, "base_units") == 1]
    if len(base_rungs) != 1:
        errors.append(
            "Exactly one packaging level must be the base unit (holding 1 base unit); "
            f"found {len(base_rungs)}."
        )
    elif _attr(base_rungs[0], "sort_order") != _attr(ordered[-1], "sort_order"):
        errors.append("The base packaging level must be the innermost (largest sort order).")

    for outer, inner in zip(ordered, ordered[1:]):
        if _attr(inner, "base_units") >= _attr(outer, "base_units"):
            errors.append(
                f"Packaging level '{_attr(inner, 'name')}' must hold fewer base units than "
                f"'{_attr(outer, 'name')}' that contains it."
            )

    if errors:
        raise ValidationError(errors)


def resolve_count_level_error(
    count_mode: str,
    count_level: Optional["PackagingLevel"],
    item: Optional["InventoryItem"],
    level_sort_orders: Optional[set] = None,
) -> Optional[str]:
    """Return why ``count_level`` is wrong for ``count_mode``, or ``None`` if it fits.

    Shared by the serializer's ``validate`` so the API rejects the same
    combinations ``InventoryItem._clean_count_mode`` does, with the extra check
    the model cannot make: when a write also replaces the packaging chain, the
    named level has to survive that replacement. ``level_sort_orders`` is the
    set of sort orders the item will have after the write (``None`` when the
    chain is untouched).
    """
    from inventory.models.core import InventoryItem

    if count_mode == InventoryItem.CountMode.EACH:
        if count_level is not None:
            return "Count level must be empty when counting each base unit."
        return None

    if count_level is None:
        return f"Count level is required when count mode is '{count_mode}'."
    if item is None or count_level.item_id != item.pk:
        return "Count level must be one of this item's packaging levels."
    if level_sort_orders is not None and count_level.sort_order not in level_sort_orders:
        return "Count level must be one of the packaging levels being saved."
    return None
