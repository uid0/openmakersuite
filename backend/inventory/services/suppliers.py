"""Write-path services for :class:`inventory.models.ItemSupplier`.

Holds the two workflow side effects that used to live inline in
``ItemSupplier.save()`` — single-primary enforcement and price-history
recording — so the override becomes a thin, transactional delegator (gh #887,
AC-2). ``save()`` still calls these, so the many bare
``ItemSupplier.objects.create(is_primary=True, ...)`` callers keep demoting
sibling primaries and writing a :class:`PriceHistory` row exactly as before.

Cost derivation (``unit_cost``/``package_cost``) is expressed here as the pure
:func:`derive_costs` and called from ``save()``. It is deliberately NOT routed
from the callers: the two columns are derived from each other, so a caller cannot
say "this is the price now" without implying something about the twin, and three
successive attempts to express that at the write sites each fixed one case by
reopening another (``docs/oms-falsy-zero-money-guards-record.md``). ``save()`` is
the one place that can see both what the caller supplied and what is stored, so
it is the one place the rule can be stated once.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from inventory.models.core import ItemSupplier, PriceHistory

#: The scale both cost columns are stored at (``DecimalField(decimal_places=2)``).
COST_SCALE = Decimal("0.01")


def quantize_cost(value):
    """Round a cost to the scale the column stores, or return it untouched.

    ``save()`` used to assign ``package_cost / quantity_per_package`` unrounded,
    so the in-memory row carried 3.333333333333333333333333333 while the row on
    disk held 3.33. Every reader between ``save()`` and a refresh saw a number the
    database does not have — including :func:`pricing_changed`, which then
    reported a price change on a save that moved no price, and
    :func:`record_price_history`, which filed one.

    The rounding is ``ROUND_HALF_EVEN`` because that is what the COLUMN does:
    ``DecimalField.get_db_prep_save`` quantizes through
    ``django.db.backends.utils.format_number``, which uses the decimal context
    default. Rounding half away from zero here instead would have silently moved
    stored money — ``package_cost 0.25`` at pack 2 would begin storing ``0.13``
    where it has always stored ``0.12`` — which is the same class of quiet
    corruption this function exists to end. The point is to hold exactly what the
    column will hold, not to pick a nicer rule.

    A value that cannot be quantized (an overflow of the column's ``max_digits``,
    say) is returned unchanged so Django's own field validation raises on it
    rather than this helper turning it into something else.
    """
    if value is None:
        return None
    try:
        return Decimal(value).quantize(COST_SCALE, rounding=ROUND_HALF_EVEN)
    except (InvalidOperation, TypeError, ValueError):
        return value


def derive_costs(*, unit_cost, package_cost, quantity_per_package, stored=None):
    """Return the ``(unit_cost, package_cost)`` pair to store. ONE rule, every site.

    The two columns are derived from each other, so a write that names only one
    of them makes the model re-derive the other. Because ``package -> unit`` is
    LOSSY at two decimal places — ``10.00 / 3`` stores as ``3.33``, and ``3.33 *
    3`` is ``9.99`` — re-deriving from a value nobody edited silently moves money.

    The fix is not another rule about which KEYS a caller sent: a form that echoes
    an unchanged box and a form that omits it are indistinguishable by key, which
    is why three successive caller-side rules each reopened the previous one's
    defect (see ``docs/oms-falsy-zero-money-guards-record.md``). It is to compare
    against what is STORED. A delta is well defined however the caller phrased
    itself, so intent no longer has to be recovered from the submitted values.

    ``stored`` is the pre-save row as a mapping (``None`` for a create).

    On a CREATE, the operator's values are all there is:

    * neither cost given — both stay NULL. Absent is not zero; "no price on
      file" has to stay reachable and must not read as a free item.
    * ``package_cost`` given — it governs, ``unit_cost`` is derived from it.
      This covers "both given": where the pair disagrees the case price wins.
    * only ``unit_cost`` given — ``package_cost`` is derived from it.

    On an UPDATE, what MOVED against the stored row is the operator's intent:

    * nothing moved — derive nothing. Both stored prices stay byte-identical,
      because editing a SKU or a flag is not a price edit.
    * ``package_cost`` moved to a value — it governs; ``unit_cost`` re-derives.
    * ``package_cost`` cleared and nothing else moved — the authoritative cost is
      gone, so both clear. That is how an operator says "I no longer know what
      this costs", and it has to stay sayable.
    * ``package_cost`` cleared but ``unit_cost`` MOVED — the changed VALUE wins.
      They emptied one box and typed in the other; discarding what they typed
      because of the box they emptied would be the same silent loss by a
      different route. An ECHOED unit cost has not moved and does not qualify:
      every form sends both boxes, so emptying the case price on its own leaves
      the unit box unchanged and falls to the bullet above.
    * only ``unit_cost`` moved to a value — the operator named a unit price and
      nothing contradicts it, so it governs and the case price re-derives. This
      is the ordinary case on every form: they all send both boxes and the
      operator edits one.
    * only ``unit_cost`` cleared — it is a derived figure and cannot be cleared
      on its own, so IT IS NEVER STORED AS NULL. It comes back re-derived from
      the case price when there is one and the pack size can divide; otherwise
      the stored pair is held exactly as it was. There is no pack size and no
      link shape where clearing it alone destroys the recorded figure, so there
      is no qualifying clause to state. That is decided in ONE place, above the
      divide-by-zero guard — three earlier statements of this rule each answered
      one combination and left a neighbouring one storing NULL, so a second site
      deciding a cleared unit cost is that mistake repeating. The write response
      carries the value it came back as. It follows that a link holding only a
      unit cost cannot have its price cleared through this path at all, not even
      by sending both as null, because an explicitly-null ``package_cost`` is
      indistinguishable from one that was already null; the remedy is to set a
      case price and then clear THAT, which clears both. On presenting the box as
      derived, the surfaces disagree: see the open list in
      ``docs/oms-supplier-cost-write-path-record.md``.
    * only the pack size moved — hold ``package_cost`` and re-derive
      ``unit_cost``. "The case holds 6, not 3" is a statement about packing, not
      about price; holding the unit price instead would silently multiply a
      recorded case price the supplier never re-quoted.
    """
    unit_cost = quantize_cost(unit_cost)
    package_cost = quantize_cost(package_cost)

    def from_package(package):
        return quantize_cost(package / quantity_per_package), package

    def from_unit(unit):
        return unit, quantize_cost(unit * quantity_per_package)

    stored_unit = quantize_cost(stored.get("unit_cost")) if stored is not None else None
    stored_package = quantize_cost(stored.get("package_cost")) if stored is not None else None
    divides = bool(quantity_per_package) and quantity_per_package >= 1

    # THE ONE PLACE a cleared ``unit_cost`` is decided. It sits above the
    # divide-by-zero guard on purpose: holding performs no arithmetic, so the
    # guard has no claim on it, and every combination of pack size and surviving
    # case price is answered here rather than in an arm somewhere below.
    if (
        stored is not None
        and unit_cost is None
        and stored_unit is not None
        and package_cost == stored_package
    ):
        if stored_package is not None and divides:
            return from_package(stored_package)
        return stored_unit, stored_package

    # The model validates >= 1, but a bypassed validator must not divide by zero.
    if not divides:
        return unit_cost, package_cost

    if stored is None:
        if package_cost is not None:
            return from_package(package_cost)
        if unit_cost is not None:
            return from_unit(unit_cost)
        return None, None

    package_moved = package_cost != stored_package
    unit_moved = unit_cost != stored_unit
    pack_moved = quantity_per_package != stored.get("quantity_per_package")

    # A VALUE THAT MOVED beats a clear, whichever box it came from. Taking the
    # clears first would mean an operator who empties the case-price box AND
    # types a NEW unit price gets NULL/NULL — their typed figure discarded
    # without a word, which is the defect class this whole path is being fixed
    # for. An echoed unit cost has NOT moved, so it never reaches here: it falls
    # to the package_moved clear below, and emptying the case price alone still
    # clears both.
    if package_moved and package_cost is not None:
        return from_package(package_cost)

    if unit_moved and unit_cost is not None:
        return from_unit(unit_cost)

    if package_moved:
        # The authoritative cost was emptied and nothing else was named: that is
        # how "I no longer know what this costs" is said, and it has to be sayable.
        return None, None

    if pack_moved:
        if package_cost is not None:
            return from_package(package_cost)
        if unit_cost is not None:
            return from_unit(unit_cost)
        return None, None

    return unit_cost, package_cost


def stored_pricing(item_supplier: "ItemSupplier") -> Optional[dict]:
    """The pre-save cost columns of a persisted row, or ``None`` for a create.

    Read once per save and shared by :func:`derive_costs` and
    :func:`pricing_changed` so the two cannot disagree about what is on disk.
    """
    if item_supplier.pk is None:
        return None

    from inventory.models.core import ItemSupplier

    return (
        ItemSupplier.objects.filter(pk=item_supplier.pk)
        .values("unit_cost", "package_cost", "quantity_per_package")
        .first()
    )


def enforce_single_primary(item_supplier: "ItemSupplier") -> None:
    """Ensure at most one primary supplier per item.

    When ``item_supplier`` is primary, clear the flag on every *other* supplier
    of the same item. A no-op when this row is not primary. On a new (unsaved)
    row ``pk`` is ``None`` and ``.exclude(pk=None)`` excludes nothing, so all
    existing primaries for the item are demoted — the intended behaviour.
    """
    if not item_supplier.is_primary:
        return

    from inventory.models.core import ItemSupplier

    ItemSupplier.objects.filter(item=item_supplier.item, is_primary=True).exclude(
        pk=item_supplier.pk
    ).update(is_primary=False)


def pricing_changed(item_supplier: "ItemSupplier", stored: Optional[dict] = None) -> bool:
    """Return ``True`` when a persisted supplier's cost/quantity differs from its DB row.

    Always ``False`` for an unsaved (new) instance — a create is reported via
    the ``is_new`` flag in :func:`record_price_history` instead. Reads the
    pre-save row, so it must be called *before* ``super().save()``.

    ``stored`` is that pre-save row when the caller has already read it (``save()``
    has, for :func:`derive_costs`), so one save does not query it twice. It must
    be the SAME read: this decides whether a price-history row is filed, and the
    captain reads that history, so it may not disagree with the values the
    derivation ran against.
    """
    if item_supplier.pk is None:
        return False

    if stored is None:
        stored = stored_pricing(item_supplier)
        if stored is None:
            return False

    return (
        stored["unit_cost"] != item_supplier.unit_cost
        or stored["package_cost"] != item_supplier.package_cost
        or stored["quantity_per_package"] != item_supplier.quantity_per_package
    )


def record_price_history(
    item_supplier: "ItemSupplier", *, is_new: bool, price_changed: bool
) -> Optional["PriceHistory"]:
    """Write a :class:`PriceHistory` row when the supplier is new or its pricing changed.

    Returns the created row, or ``None`` when nothing changed. Call *after*
    ``super().save()`` so the ``item_supplier`` FK target exists. The stored
    ``change_type`` is ``created`` on first save and ``updated`` on a price
    change, matching the historical values.
    """
    if not (is_new or price_changed):
        return None

    from inventory.models.core import PriceHistory

    change_type = PriceHistory.ChangeType.CREATED if is_new else PriceHistory.ChangeType.UPDATED
    return PriceHistory.objects.create(
        item_supplier=item_supplier,
        unit_cost=item_supplier.unit_cost,
        package_cost=item_supplier.package_cost,
        quantity_per_package=item_supplier.quantity_per_package,
        change_type=change_type,
    )
