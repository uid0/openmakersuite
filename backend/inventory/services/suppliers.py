"""Write-path services for :class:`inventory.models.ItemSupplier`.

Holds the two workflow side effects that used to live inline in
``ItemSupplier.save()`` — single-primary enforcement and price-history
recording — so the override becomes a thin, transactional delegator (gh #887,
AC-2). ``save()`` still calls these, so the many bare
``ItemSupplier.objects.create(is_primary=True, ...)`` callers keep demoting
sibling primaries and writing a :class:`PriceHistory` row exactly as before.

Cost derivation (``unit_cost``/``package_cost``) stays in ``save()`` — it is a
pure local invariant, not a workflow side effect.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from inventory.models.core import ItemSupplier, PriceHistory


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


def pricing_changed(item_supplier: "ItemSupplier") -> bool:
    """Return ``True`` when a persisted supplier's cost/quantity differs from its DB row.

    Always ``False`` for an unsaved (new) instance — a create is reported via
    the ``is_new`` flag in :func:`record_price_history` instead. Reads the
    pre-save row, so it must be called *before* ``super().save()``.
    """
    if item_supplier.pk is None:
        return False

    from inventory.models.core import ItemSupplier

    try:
        old = ItemSupplier.objects.get(pk=item_supplier.pk)
    except ItemSupplier.DoesNotExist:
        return False

    return (
        old.unit_cost != item_supplier.unit_cost
        or old.package_cost != item_supplier.package_cost
        or old.quantity_per_package != item_supplier.quantity_per_package
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


#: Sentinel for "the caller did not mention this field", distinct from an
#: explicitly supplied ``None`` meaning "clear it". Same convention, and for the
#: same reason, as :data:`reorder_queue.services.purchase_orders.UNCHANGED`.
UNCHANGED = object()

#: The cost columns whose values ``ItemSupplier.save`` derives from each other.
_DERIVED_COSTS = ("unit_cost", "package_cost")

_TERM_FIELDS = (
    "supplier_sku",
    "supplier_url",
    "unit_cost",
    "package_cost",
    "quantity_per_package",
    "average_lead_time",
    "package_upc",
    "unit_upc",
    "is_primary",
)


def write_supplier_terms(*, item, supplier=None, supplier_id=None, **terms):
    """Write purchase terms onto an item's supplier link — the ONE way to do it.

    Every caller used to hand-roll
    ``ItemSupplier.objects.update_or_create(defaults=<partial dict>)`` against a
    model whose ``save()`` DERIVES ``unit_cost`` and ``package_cost`` from each
    other, and a partial write always loses that fight in one of two ways:

    * the stale sibling column wins. ``save()`` prefers ``package_cost``, so
      setting only ``unit_cost`` on a link that already has a package price
      recomputed the OLD price straight back over the operator's typed one.
    * the derived column is dropped. ``update_or_create`` restricts
      ``update_fields`` to the ``defaults`` keys, so a ``package_cost`` that
      ``save()`` had just derived never reached the database.

    So the two costs are ONE fact here: naming either one without the other
    means "this is the price now", and the unnamed twin is cleared so ``save()``
    re-derives it. The derivation itself is untouched and still lives in
    ``save()`` — this only stops callers fighting it.

    Every field defaults to :data:`UNCHANGED`, so an omitted field keeps what is
    on the row and an explicitly supplied ``None`` clears it. On a CREATE the
    model's own field defaults fill the gaps; on an UPDATE nothing the caller did
    not name is touched — notably ``quantity_per_package``, whose forced default
    used to reset a recorded pack size and, through the derivation, the price
    with it.

    Saves with a full ``save()`` rather than a restricted ``update_fields``, so
    the single-primary enforcement and the :class:`PriceHistory` snapshot that
    ``save()`` already performs are preserved exactly.
    """
    from inventory.models.core import ItemSupplier

    unknown = set(terms) - set(_TERM_FIELDS)
    if unknown:
        raise TypeError(f"write_supplier_terms got unexpected terms: {sorted(unknown)}")

    if supplier_id is None:
        supplier_id = supplier.pk

    link = ItemSupplier.objects.filter(item=item, supplier_id=supplier_id).first()
    if link is None:
        link = ItemSupplier(item=item, supplier_id=supplier_id)

    named_costs = [name for name in _DERIVED_COSTS if terms.get(name, UNCHANGED) is not UNCHANGED]
    if len(named_costs) == 1:
        stale = _DERIVED_COSTS[0] if named_costs[0] == _DERIVED_COSTS[1] else _DERIVED_COSTS[1]
        terms[stale] = None

    for name in _TERM_FIELDS:
        value = terms.get(name, UNCHANGED)
        if value is not UNCHANGED:
            setattr(link, name, value)

    link.save()
    return link
