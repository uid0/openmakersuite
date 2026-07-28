"""Unit-of-measure conversion + packaging-chain validation (op-hzji, phase 1).

Pure functions over :class:`inventory.models.PackagingLevel` rungs and an
item's base-unit stock. Nothing here writes: ``InventoryItem.current_stock``
remains the canonical BASE-unit quantity that every reorder / purchase /
usage flow reads, and these helpers only convert it between rungs or format it
for display.

The validators are shared deliberately: ``PackagingLevel.clean()`` calls
:func:`validate_packaging_chain` for single-row saves, and
``InventoryItemSerializer.validate`` calls it for the nested (bulk) write,
where ``clean()`` never runs.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Iterable, Optional, Sequence

from django.core.exceptions import ValidationError

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
    if len(set(sort_orders)) != len(sort_orders):
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
