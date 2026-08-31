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


def _coerce_lead_time(value):
    """A lead time as a whole number of days, or NULL for "not known".

    Coerced here for the same reason the costs and the pack size are: the kit
    form's ``supplier_terms`` is a pass-through ``DictField``, so an integer
    column can be handed a string and only fail at the INSERT, as a 500.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise DjangoValidationError(
            {"average_lead_time": f"Must be a whole number of days, got {value!r}."}
        )
    try:
        days = int(value)
    except (ValueError, TypeError):
        raise DjangoValidationError(
            {"average_lead_time": f"Must be a whole number of days, got {value!r}."}
        )
    if days < 0:
        raise DjangoValidationError({"average_lead_time": "Cannot be negative."})
    return days


def _coerce_terms(terms):
    """Coerce every value that reaches the model's cost derivation."""
    coerced = dict(terms)
    for field in _DERIVED_COSTS:
        if coerced.get(field, UNCHANGED) is not UNCHANGED:
            coerced[field] = _coerce_cost(coerced[field], field)
    if coerced.get("quantity_per_package", UNCHANGED) is not UNCHANGED:
        coerced["quantity_per_package"] = _coerce_pack_size(coerced["quantity_per_package"])
    if coerced.get("average_lead_time", UNCHANGED) is not UNCHANGED:
        coerced["average_lead_time"] = _coerce_lead_time(coerced["average_lead_time"])
    return coerced


def _reject_unknown(terms):
    unknown = set(terms) - set(_TERM_FIELDS)
    if unknown:
        raise TypeError(f"supplier terms got unexpected fields: {sorted(unknown)}")


def _apply(link, terms):
    """Set the supplied terms on ``link``, keeping the two costs one fact.

    "The caller did not mention ``package_cost``" and "the caller SENT
    ``package_cost`` with its current value" are TWO DIFFERENT FACTS and must
    not be collapsed. Intent is KEY PRESENCE and key presence ALONE: a key in
    ``terms`` means the caller supplied it, absent means leave it alone.

    * supplied exactly ONE of the pair — "this is the price now", so the other
      is cleared and ``save()`` re-derives it from the value the caller gave.
      Whether that value happens to EQUAL what is stored is irrelevant: an
      operator restating the price they meant is still naming it.
    * supplied BOTH — the item form always sends both boxes, so they are the
      caller's own values and neither is cleared. Where the two disagree
      ``save()``'s derivation decides, exactly as it did before this owner
      existed; that derivation is not this module's to second-guess.

    Value equality decides something else entirely, one step out in
    :func:`_is_echo`: whether the request as a WHOLE is a no-op. Deciding the
    twin-clear on it as well made behaviour turn on a single cent — restating
    ``unit_cost`` as ``5.00`` beside a new pack size left the stale package
    price to overwrite it, while ``5.01`` in the same request was honoured.
    """
    supplied = [name for name in _DERIVED_COSTS if terms.get(name, UNCHANGED) is not UNCHANGED]
    if len(supplied) == 1:
        stale = _DERIVED_COSTS[0] if supplied[0] == _DERIVED_COSTS[1] else _DERIVED_COSTS[1]
        terms = {**terms, stale: None}

    for name in _TERM_FIELDS:
        value = terms.get(name, UNCHANGED)
        if value is not UNCHANGED:
            setattr(link, name, value)
    return link


def _is_echo(link, terms):
    """True when EVERY supplied term already equals what the row stores.

    The whole request, not one field: if anything the derivation depends on is
    moving — a cost, a pack size, a flag — the request is not an echo and the
    write proceeds. The kit form seeds its cost box from the stored price and
    sends it back on every save, which is the case this exists to make free.
    """
    return all(
        value == getattr(link, name) for name, value in terms.items() if value is not UNCHANGED
    )


def _dependents_of(link):
    """What already points at this link, counted by relation.

    Derived from the model's own reverse relations rather than a remembered
    list, so a table added later is covered without editing this.
    """
    counts = {}
    for rel in link._meta.related_objects:
        found = rel.related_model._base_manager.filter(**{rel.field.name: link}).count()
        if found:
            counts[str(rel.related_model._meta.verbose_name_plural)] = found
    return counts


def _refuse_item_move(link, item):
    """Refuse to move a persisted link to a different item. Always refuses.

    None of the tables pointing at an ``ItemSupplier`` carries its own item —
    :class:`PriceHistory`, :class:`PurchaseOrderItem` and :class:`LeadTimeLog`
    all reach it THROUGH the link. So moving the link does not strand them, it
    silently RE-ATTRIBUTES them: measured, a purchase order line recording what
    was actually bought from one item reports the other afterwards, and one
    item's price history becomes the other's.

    Unconditional rather than "refuse only when something points at it",
    because :func:`record_price_history` writes a row on every link's first
    save whether or not it carries a price, so there is always history to
    rewrite. A conditional refusal would read as though some moves are allowed
    while refusing every one of them in practice.

    The remedy is in the message, because a refusal an operator cannot act on
    is not a fix.
    """
    recorded = _dependents_of(link)
    detail = (
        ", ".join(f"{count} {name}" for name, count in sorted(recorded.items()))
        if recorded
        else "its recorded history"
    )
    raise DjangoValidationError(
        {
            "item": (
                f"A supplier link cannot be moved to another item: {detail} "
                f"already record it against {link.item}, and moving it would "
                f"re-attribute them. Create a new supplier link on {item} "
                f"instead, and deactivate this one if it is no longer used."
            )
        }
    )


def update_supplier_terms(link, *, item=UNCHANGED, supplier_id=UNCHANGED, **terms):
    """Write terms onto a link the caller ALREADY HOLDS. Never creates.

    The entry point for a caller that addressed one row — the
    ``/item-suppliers/<pk>/`` endpoint names it in the URL. It writes THAT row,
    including when the terms move it to a different supplier.

    Split out from :func:`write_supplier_terms` because inferring the row from
    an (item, supplier) pair is wrong here: a PATCH that changes ``supplier``
    matches no existing pair, so the pair resolver CREATED a second link and
    left the addressed one behind, pointing at the old supplier with its old
    SKU and costs. The item form's supplier dropdown stays enabled on saved
    rows and ``relationshipChanged`` lists a changed supplier as a reason to
    write, so that is an ordinary edit, not a corner.

    Both halves of the link's identity are parameters here for the same reason
    the terms are: a writable field the caller supplied must either land or be
    refused out loud. ``supplier`` lands; ``item`` is refused — see
    :func:`_refuse_item_move`.
    """
    from inventory.models.core import ItemSupplier

    _reject_unknown(terms)
    terms = _coerce_terms(terms)

    with transaction.atomic():
        locked = ItemSupplier.objects.select_for_update().get(pk=link.pk)
        if item is not UNCHANGED and item.pk != locked.item_id:
            _refuse_item_move(locked, item)
        moving_supplier = supplier_id is not UNCHANGED and supplier_id != locked.supplier_id
        if not moving_supplier and _is_echo(locked, terms):
            return locked
        if supplier_id is not UNCHANGED:
            locked.supplier_id = supplier_id
        _apply(locked, terms).save()
        return locked


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

    The losing-create retry is narrowed to the race it was written for: it
    re-fetches, and re-raises the ORIGINAL ``IntegrityError`` when no row
    appeared. Retrying unconditionally turned every other constraint violation
    — an unknown supplier id reaching the insert as a foreign-key error, say —
    into a misleading ``DoesNotExist`` with the real cause gone from the
    traceback, which is what the manager method this replaced avoided.
    """
    from inventory.models.core import ItemSupplier

    _reject_unknown(terms)

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
            if _is_echo(link, terms):
                return link
            _apply(link, terms).save()
            return link

        try:
            with transaction.atomic():
                link = _apply(ItemSupplier(item=item, supplier_id=supplier_id), terms)
                link.save()
                return link
        except IntegrityError:
            link = (
                ItemSupplier.objects.select_for_update()
                .filter(item=item, supplier_id=supplier_id)
                .first()
            )
            if link is None:
                raise
            _apply(link, terms).save()
            return link
