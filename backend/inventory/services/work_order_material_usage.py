"""Shared apply/reverse logic for work-order material usage → inventory stock.

PR3 of the WO-OMR epic (op-uh8z). When a work order records a material as
*used*, the linked inventory item's stock is decremented and a
:class:`~inventory.models.UsageLog` row is written; un-marking the material
reverses both. Every path that flips ``WorkOrderMaterialUsage.was_used`` — the
manual toggle endpoint, the emailed AcroForm ingest, and the OMR scan apply —
routes through :func:`apply_material_usage` so the decrement can **never**
double-count (idempotency) and is always reversible.

Design notes
------------
* ``InventoryItem.current_stock`` and ``UsageLog.quantity_used`` are integer
  (whole stock units), while material quantities are ``Decimal``; we round the
  used quantity to whole units for the stock side (see :func:`_whole_units`).
* ``WorkOrderMaterialUsage.applied_quantity`` is the single source of truth for
  "is a decrement currently applied" (``None`` → no) and, when applied, the
  exact whole-unit amount to restore on reversal. This makes re-applying the
  same OMR scan or toggling through the review panel a no-op past the first
  application.
* A material with no ``inventory_item`` link is flag-only: ``was_used`` /
  ``quantity_used`` are recorded but nothing is decremented and no error is
  raised.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.db import transaction

from ..models import UsageLog


def _whole_units(qty) -> int:
    """Round a material quantity to whole stock units (integer), floored at 0.

    Stock and UsageLog are integer-valued; a fractional used quantity is rounded
    half-up to the nearest whole unit. Never returns a negative value.
    """
    if qty is None:
        return 0
    if not isinstance(qty, Decimal):
        try:
            qty = Decimal(str(qty))
        except (InvalidOperation, ValueError, TypeError):
            return 0
    units = int(qty.to_integral_value(rounding=ROUND_HALF_UP))
    return max(0, units)


def _actor_label(actor) -> str:
    """Best-effort human label for the acting user (or a passed-in string)."""
    if actor is None:
        return ""
    if isinstance(actor, str):
        return actor.strip()
    if not getattr(actor, "is_authenticated", False):
        return ""
    getter = getattr(actor, "get_full_name", None)
    name = getter().strip() if callable(getter) else ""
    return name or getattr(actor, "username", "") or ""


def _usage_note(usage, actor, source_note: str) -> str:
    """Compose the UsageLog note: work order id, material, actor, source."""
    parts = [f"Work order {usage.work_order.short_id}"]
    if usage.material_name:
        parts.append(usage.material_name)
    who = _actor_label(actor)
    if who:
        parts.append(f"recorded by {who}")
    if source_note:
        parts.append(source_note)
    return " — ".join(parts)


@transaction.atomic
def apply_material_usage(
    usage,
    *,
    was_used: bool,
    actor=None,
    source_note: str = "",
) -> bool:
    """Set ``usage.was_used`` and keep the inventory decrement in sync.

    Idempotent and reversible:

    * **False → True** (and not already applied, and the material links to an
      inventory item): decrement the item's stock by the rounded
      ``quantity_used`` and write a UsageLog row. Records the exact whole-unit
      amount removed in ``applied_quantity``.
    * **True → False** (and currently applied): restore the exact
      ``applied_quantity`` back to stock and void (delete) the UsageLog row.
    * Re-applying an already-applied row, or reversing an already-reversed row,
      is a no-op on the inventory side (``applied_quantity`` guards both).
    * Flag-only material (no ``inventory_item``): ``was_used`` is recorded, no
      stock change, no error.

    Returns ``True`` if any state changed.
    """
    target = bool(was_used)
    changed = False
    update_fields: list[str] = []
    log_to_delete = None

    material = usage.material
    item = material.inventory_item if material is not None else None

    if target and usage.applied_quantity is None and item is not None:
        # APPLY — guarded by ``applied_quantity is None`` so a double-apply
        # (re-run scan, toggle race) can never decrement twice.
        units = _whole_units(usage.quantity_used)
        removed = min(units, item.current_stock)
        if removed:
            item.current_stock -= removed
            item.save(update_fields=["current_stock", "updated_at"])
        usage.usage_log = (
            UsageLog.objects.create(
                item=item,
                quantity_used=units,
                notes=_usage_note(usage, actor, source_note),
            )
            if units >= 1
            else None
        )
        usage.applied_quantity = removed
        update_fields += ["applied_quantity", "usage_log"]
        changed = True
    elif not target and usage.applied_quantity is not None:
        # REVERSE — restore the exact amount removed and void the log row.
        restore_item = usage.usage_log.item if usage.usage_log_id else item
        if restore_item is not None and usage.applied_quantity:
            restore_item.current_stock += usage.applied_quantity
            restore_item.save(update_fields=["current_stock", "updated_at"])
        log_to_delete = usage.usage_log
        usage.usage_log = None
        usage.applied_quantity = None
        update_fields += ["applied_quantity", "usage_log"]
        changed = True

    if usage.was_used != target:
        usage.was_used = target
        update_fields.append("was_used")
        changed = True

    if update_fields:
        usage.save(update_fields=update_fields)

    # Delete the log only after the usage row no longer references it, so the
    # ``SET_NULL`` cascade can't race our own save.
    if log_to_delete is not None:
        log_to_delete.delete()

    return changed
