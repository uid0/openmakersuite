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

from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Optional

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.backends.utils import format_number

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


def _as_stored(item_supplier: "ItemSupplier", field_name: str, value):
    """``value`` rounded the way the column will store it.

    ``save()``'s derivation divides, so ``package_cost 10.00`` over a pack of 3
    yields ``3.3333…`` in memory while the ``numeric(10,2)`` column holds
    ``3.33``. Comparing the two raw made :func:`pricing_changed` answer True on
    every save of such a link, writing a phantom "Price Update" into the
    supplier price-trend chart. Uses Django's own write-path rounding so this
    comparison cannot drift from what the database actually keeps.
    """
    if value is None:
        return None
    field = item_supplier._meta.get_field(field_name)
    return format_number(Decimal(str(value)), field.max_digits, field.decimal_places)


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
        _as_stored(item_supplier, "unit_cost", old.unit_cost)
        != _as_stored(item_supplier, "unit_cost", item_supplier.unit_cost)
        or _as_stored(item_supplier, "package_cost", old.package_cost)
        != _as_stored(item_supplier, "package_cost", item_supplier.package_cost)
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

#: Every writable column of the relationship. Wider than "the costs" on
#: purpose: the generic ``/item-suppliers/`` endpoint writes the dimensional and
#: bookkeeping columns in the SAME request as the costs, so splitting the write
#: would put half of it back outside the owner.
_TERM_FIELDS = (
    "supplier_sku",
    "supplier_url",
    "unit_cost",
    "package_cost",
    "quantity_per_package",
    "average_lead_time",
    "package_upc",
    "unit_upc",
    "package_height",
    "package_width",
    "package_length",
    "package_weight",
    "notes",
    "is_primary",
    "is_active",
    "is_discontinued",
)


def _coerce_cost(value, field):
    """A cost as a ``Decimal``, or ``None`` for "no price".

    The boundary this owner needs. ``supplier_terms`` is a plain ``DictField``
    whose ``_UnvalidatedField`` child passes JSON through untouched, so a cost
    arrives as whatever the caller sent — the kit form sends ``String(unitCost)``.
    An uncoerced string reaches ``ItemSupplier.save``'s derivation and
    ``"5.00" * 6`` is string repetition, not arithmetic. Same shape, and the same
    remedy, as :func:`reorder_queue.services.line_entry._coerce_unit_cost`.

    An explicit ``None`` stays ``None``: "no price" is not ``Decimal("0")``.
    """
    if value is None:
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise DjangoValidationError({field: f"Must be a number, got {value!r}."})
    if not amount.is_finite():
        raise DjangoValidationError({field: f"Must be a finite number, got {value!r}."})
    if amount < 0:
        raise DjangoValidationError({field: "Cannot be negative."})
    return amount


def _coerce_pack_size(value):
    """A pack size as a positive ``int``. NULL is not one of its answers."""
    try:
        size = int(value)
    except (ValueError, TypeError):
        raise DjangoValidationError(
            {"quantity_per_package": f"Must be a whole number, got {value!r}."}
        )
    if size < 1:
        raise DjangoValidationError({"quantity_per_package": "Must be at least 1."})
    return size


def _coerce_terms(terms):
    """Coerce every value that reaches the model's cost derivation."""
    coerced = dict(terms)
    for field in _DERIVED_COSTS:
        if coerced.get(field, UNCHANGED) is not UNCHANGED:
            coerced[field] = _coerce_cost(coerced[field], field)
    if coerced.get("quantity_per_package", UNCHANGED) is not UNCHANGED:
        coerced["quantity_per_package"] = _coerce_pack_size(coerced["quantity_per_package"])
    return coerced


def _apply(link, terms):
    """Set the named terms on ``link``, keeping the two costs one fact.

    Naming ONE cost means "this is the price now", so its twin is cleared and
    ``save()`` re-derives it — but ONLY when the named value actually differs
    from what is stored. The kit form seeds its cost box from the stored price
    and echoes it back on every save, so clearing unconditionally re-derived a
    package price the operator never touched (a link at ``package_cost 10.00``
    over a pack of 3 stores ``unit_cost 3.33``, and ``3.33 * 3`` is ``9.99``).
    An echoed value is a no-op: it moves nothing and records no price history.
    """
    named_costs = [
        name
        for name in _DERIVED_COSTS
        if terms.get(name, UNCHANGED) is not UNCHANGED and terms[name] != getattr(link, name)
    ]
    if len(named_costs) == 1:
        stale = _DERIVED_COSTS[0] if named_costs[0] == _DERIVED_COSTS[1] else _DERIVED_COSTS[1]
        terms = {**terms, stale: None}

    for name in _TERM_FIELDS:
        value = terms.get(name, UNCHANGED)
        if value is not UNCHANGED:
            setattr(link, name, value)
    return link


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

    terms = _coerce_terms(terms)

    with transaction.atomic():
        link = (
            ItemSupplier.objects.select_for_update()
            .filter(item=item, supplier_id=supplier_id)
            .first()
        )
        if link is not None:
            _apply(link, terms).save()
            return link

        try:
            with transaction.atomic():
                link = _apply(ItemSupplier(item=item, supplier_id=supplier_id), terms)
                link.save()
                return link
        except IntegrityError:
            pass

        link = (
            ItemSupplier.objects.select_for_update()
            .filter(item=item, supplier_id=supplier_id)
            .get()
        )
        _apply(link, terms).save()
        return link
