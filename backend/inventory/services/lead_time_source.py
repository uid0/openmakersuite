"""Where a supplier's lead time CAME FROM, decided once, where the value is decided.

``ItemSupplier.average_lead_time`` is NOT NULL with a planning default of 7, so a
link nobody quoted a wait for stores the same ``7`` as a supplier who genuinely
promised a week — and every screen, the reorder point and supplier scoring read
the two identically. ``ItemSupplier.average_lead_time_source`` says which it is:

* ``default``  — nobody supplied a number; the column took the planning default.
* ``recorded`` — a caller supplied this number (an operator typing a quote, an
  API client sending one). ``0`` is recorded same-day pickup, like any number.
* ``measured`` — :func:`inventory.tasks.update_average_lead_times` computed it
  from completed deliveries.
* ``unknown``  — the row predates this column. The migration backfilled every
  existing row with it, because which of those 7s were typed was never captured
  and cannot be recovered. It is never promoted to a known provenance by a write
  that does not change the number.

This column changes NO reading of the lead time. Planning, the reorder point,
supplier scoring and alerting read ``average_lead_time`` exactly as before; the
source is information for a person, nothing computes with it.

THE RULE, AND WHY IT LIVES HERE AND NOT AT THE WRITE SITES
----------------------------------------------------------

A first attempt inferred provenance at each write site from the SHAPE of the
input (was the key present? was the box non-empty?). Every site could then
mislabel on its own, and each fix moved the defect to the next seam: the admin
form could set the marker apart from the value; ``"soon"`` fell back to 7 and was
labelled recorded; a PATCH echoing an untouched legacy 7 relabelled it recorded.
It was reverted after five rounds.

Here provenance is PRODUCED by what decides the stored value, and nothing else:

1. **The planning default carries its own mark.** The field's default is
   :func:`planning_default`, which returns ``7`` as a private ``int`` subclass. It
   IS 7 — arithmetic, comparison, JSON and the database all see a plain 7 — but
   :func:`decide_lead_time_source` can tell it was never supplied. So a write
   path that takes the default by omitting the key needs no rule of its own, and
   a number a caller supplies, including ``7``, is a plain ``int`` and cannot be
   mistaken for the default. The mark cannot be put on any other number.
2. **A measurement carries its mark the same way**, via
   :func:`measured_lead_time`, called by the one task that measures.
3. **Everything else is a DELTA against the stored row** — the same principle
   ``derive_costs`` follows for prices, for the same reason: only ``save()`` sees
   both what the caller supplied and what is on disk. A plain number equal to the
   stored one leaves the stored source alone (an echo cannot relabel, an unknown
   stays unknown); a different one is ``recorded``.

``ItemSupplier.save()`` calls :func:`settle_lead_time_source` on every save, and
the source column is ``editable=False``: no ``ModelForm`` or ``ModelSerializer``
accepts it, and ``save()`` overwrites whatever an instance holds. The known
limit, stated rather than hidden: re-sending the number a row already holds
cannot promote a ``default`` or ``unknown`` 7 to ``recorded`` — confirming "the
supplier really did quote 7" needs a different number or a future explicit
action. That errs toward saying less than is known, never more.

EVERY WRITE PATH, AND HOW IT REACHES THIS
-----------------------------------------

Derived from "what can put a number in the column", not from the field name.

* ``POST/PUT/PATCH /api/inventory/item-suppliers/`` (the web relationship
  editor, and any client) — ``ItemSupplierSerializer`` -> ``objects.create()`` or
  setattr + ``save()``. An omitted key takes the default; an echo is a delta.
* The kit form's ``supplier_terms`` — ``KitSerializer._apply_supplier_terms`` ->
  ``get_or_create`` / setattr of the SENT keys + ``save()``.
* Item create — ``InventoryItemViewSet._sync_primary_supplier`` ->
  ``update_or_create``. Absent, blank and malformed input OMIT the key there
  rather than restating the default as a plain 7.
* The Django admin (``ItemSupplierAdmin`` and the item admin's inline) —
  ``ItemSupplierAdminForm``: the box starts blank and a blank box passes the
  instance's own value through, so an untouched add form takes the marked
  default and an untouched change form writes an echo.
* ``inventory.tasks.update_average_lead_times`` -> :func:`measured_lead_time` +
  ``save(update_fields=["average_lead_time"])``; the source column is added to
  ``update_fields`` whenever the value is written.
* ``QuerySet.update``, ``bulk_update`` touching either column, and
  ``bulk_create`` bypass ``save()``, so :class:`ItemSupplierQuerySet` REFUSES
  them (also through related managers) with :class:`UndecidedLeadTimeWrite`.
* ``loaddata`` (``raw`` saves) and historical models in migrations bypass
  ``save()`` too; neither ships ItemSupplier data, and a dump carries the source
  it was saved with.

``inventory/tests/test_lead_time_source_agrees_with_how_it_was_obtained.py`` pins
the invariant over every path above: no write path stores a source that
disagrees with how its value was obtained. A new write path is a new driver
there, not a new rule here.

What is deliberately NOT here: values DERIVED from a link's lead time (the index
card's longest lead, item metrics, supplier averages, reorder-row lead times) do
not carry the source yet; they would need it propagated through each derivation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable, Optional

from django.db import models

if TYPE_CHECKING:
    from inventory.models.core import ItemSupplier

#: The planning default, in calendar days. The ONE definition of the number; the
#: model field takes it through :func:`planning_default`.
PLANNING_DEFAULT_DAYS = 7

VALUE_FIELD = "average_lead_time"
SOURCE_FIELD = "average_lead_time_source"


class LeadTimeSource(models.TextChoices):
    UNKNOWN = "unknown", "Unknown (stored before its source was kept)"
    DEFAULT = "default", "Planning default (nobody recorded one)"
    RECORDED = "recorded", "Recorded"
    MEASURED = "measured", "Measured from deliveries"


class _PlanningDefault(int):
    """The planning default as the field default produces it. Behaves as the int."""

    __slots__ = ()


class _Measured(int):
    """A lead time computed from deliveries. Behaves as the int."""

    __slots__ = ()


class LeadTimeDaysField(models.PositiveIntegerField):
    """``average_lead_time``'s column: a ``PositiveIntegerField`` that keeps the mark.

    ``Field.to_python`` normalises with ``int(value)``, and ``Model.full_clean``
    (which every ``ModelForm``, the admin's included, runs before saving) assigns
    that result back to the instance — so without this an admin add form would
    strip the "not supplied" mark off the default between the form and
    ``save()``. A marked value is already a valid int; it passes through as is.
    """

    def to_python(self, value):
        if type(value) in (_PlanningDefault, _Measured):
            return value
        return super().to_python(value)


def planning_default() -> int:
    """The model field's default: ``PLANNING_DEFAULT_DAYS``, marked as unsupplied."""
    return _PlanningDefault(PLANNING_DEFAULT_DAYS)


def measured_lead_time(days: int) -> int:
    """``days``, marked as measured from deliveries rather than quoted."""
    return _Measured(days)


def decide_lead_time_source(value, stored: Optional[tuple[int, str]]) -> str:
    """The source a row holding ``value`` must carry, given what is on disk.

    ``stored`` is the persisted ``(days, source)``, or ``None`` for a create.
    """
    if type(value) is _PlanningDefault:
        return LeadTimeSource.DEFAULT
    if type(value) is _Measured:
        return LeadTimeSource.MEASURED
    try:
        days = int(value)
    except (TypeError, ValueError):
        # Not a number at all: the save fails at the column. Claim nothing.
        return LeadTimeSource.UNKNOWN
    if stored is not None and days == stored[0]:
        return stored[1]
    return LeadTimeSource.RECORDED


def stored_lead_time(item_supplier: "ItemSupplier") -> Optional[tuple[int, str]]:
    """The persisted ``(days, source)`` of this row, or ``None`` for a create."""
    if item_supplier.pk is None:
        return None
    return (
        type(item_supplier)
        .objects.filter(pk=item_supplier.pk)
        .values_list(VALUE_FIELD, SOURCE_FIELD)
        .first()
    )


def settle_lead_time_source(item_supplier: "ItemSupplier", update_fields):
    """Set the row's source for this save; return ``update_fields`` to save with.

    Called by ``ItemSupplier.save()`` inside its transaction, before the write.
    Where the save does not write the lead time, the source is not written
    either, so a restricted save cannot set one apart from the other.
    """
    if update_fields is not None and VALUE_FIELD not in update_fields:
        return frozenset(update_fields) - {SOURCE_FIELD}
    item_supplier.average_lead_time_source = decide_lead_time_source(
        item_supplier.average_lead_time, stored_lead_time(item_supplier)
    )
    if update_fields is None:
        return None
    return frozenset(update_fields) | {SOURCE_FIELD}


class UndecidedLeadTimeWrite(TypeError):
    """A bulk write would store a lead time or source ``save()`` never decided."""


def _refuse(fields: Iterable[str], call: str) -> None:
    touched = sorted({VALUE_FIELD, SOURCE_FIELD} & set(fields))
    if touched:
        raise UndecidedLeadTimeWrite(
            f"ItemSupplier {call} would write {', '.join(touched)} without "
            "ItemSupplier.save(), which is the only place a lead time's source is "
            "decided. Save each row instead."
        )


class ItemSupplierQuerySet(models.QuerySet):
    """Refuses the writes that bypass ``save()`` for the lead-time pair."""

    def update(self, **kwargs):
        _refuse(kwargs, "QuerySet.update()")
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, *args, **kwargs):
        _refuse(fields, "bulk_update()")
        # The class is named in ``super(...)`` so the purchase-order settlement
        # scanner (``reorder_queue/settlement_sites.py``) can see statically that
        # this receiver is not an order queryset.
        return super(ItemSupplierQuerySet, self).bulk_update(objs, fields, *args, **kwargs)

    def bulk_create(self, objs, *args, **kwargs):
        # Every column is written by an INSERT, the lead-time pair included.
        _refuse((VALUE_FIELD, SOURCE_FIELD), "bulk_create()")
