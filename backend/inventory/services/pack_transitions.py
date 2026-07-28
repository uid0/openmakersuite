"""Open / finish transitions for ``open_closed`` items (op-ev14, phase 2b).

The two moves an ``open_closed`` item makes that no other stock path expresses:

* **open a sealed pack** — a sealed pack is broken into. ``current_stock`` drops
  by the pack's base units and ``open_container_count`` goes up by one.
* **finish the open pack** — the pack that was open is empty.
  ``open_container_count`` drops by one and stock does **not** move.

The asymmetry is the mode's definition, not an oversight: under
``open_closed`` the countable stock is the SEALED packs, and whatever is left
inside an open pack is deliberately untracked (see
:func:`inventory.services.packaging.on_hand_display`). So the base units leave
``current_stock`` at the moment the pack is opened — that is when they stop
being countable — and finishing it is pure container bookkeeping with nothing
left to deduct.

Opening therefore *is* consumption and writes a :class:`~inventory.models.UsageLog`
for the pack's base units, so usage history and stock movements agree. Finishing
writes none: a zero-quantity usage row would both misstate history and violate
``UsageLog.quantity_used``'s ``MinValueValidator(1)``.

``each`` and ``by_level`` items have no open container, so both transitions
reject them rather than inventing one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from django.core.exceptions import ValidationError
from django.db import transaction

from inventory.models import InventoryItem, UsageLog

from .packaging import counts_in_packs

if TYPE_CHECKING:  # pragma: no cover
    from django.contrib.auth.models import AbstractBaseUser


def _locked_open_closed_item(item: "InventoryItem") -> "InventoryItem":
    """Re-read ``item`` under ``select_for_update``, rejecting the wrong mode.

    Both transitions are read-modify-write on two columns, so the row is locked
    for the life of the caller's transaction: two members opening a pack at the
    same moment must not both read the same sealed count.
    """
    locked = InventoryItem.objects.select_for_update().get(pk=item.pk)
    if locked.count_mode != InventoryItem.CountMode.OPEN_CLOSED or not counts_in_packs(locked):
        raise ValidationError(
            f"'{locked.name}' does not track open containers (count mode "
            f"'{locked.count_mode}'); only 'open_closed' items can open or "
            "finish a pack."
        )
    return locked


def open_pack(
    item: "InventoryItem",
    *,
    user: Optional["AbstractBaseUser"] = None,
    notes: str = "",
) -> tuple["InventoryItem", "UsageLog"]:
    """Break into a sealed pack: stock down one pack, open containers up one.

    Returns the refreshed item and the :class:`~inventory.models.UsageLog`
    written for the pack's base units. Raises ``ValidationError`` when the item
    does not track open containers or has no sealed pack left to open.
    """
    with transaction.atomic():
        locked = _locked_open_closed_item(item)
        level = locked.count_level
        pack_base_units = level.base_units

        if locked.current_stock < pack_base_units:
            raise ValidationError(
                f"No sealed {level.name} of '{locked.name}' left to open "
                f"({locked.current_stock} {locked.base_unit} on hand, a "
                f"{level.name} holds {pack_base_units})."
            )

        # Cost snapshot mirrors ``log_usage``: recorded for history, and never
        # rewritten by a later price change. No committee is charged here — a
        # chargeable consumption goes through ``log_usage``.
        unit_cost = locked.unit_cost
        total_cost = unit_cost * pack_base_units if unit_cost is not None else None

        locked.current_stock -= pack_base_units
        locked.open_container_count += 1
        locked.save(update_fields=["current_stock", "open_container_count", "updated_at"])

        usage_log = UsageLog.objects.create(
            item=locked,
            quantity_used=pack_base_units,
            notes=(notes or f"Opened a {level.name}.").strip(),
            unit_cost=unit_cost,
            total_cost=total_cost,
            charged_by=user if (user is not None and user.is_authenticated) else None,
        )

    return locked, usage_log


def finish_open_pack(
    item: "InventoryItem",
    *,
    user: Optional["AbstractBaseUser"] = None,
) -> "InventoryItem":
    """Retire an emptied open pack: open containers down one, stock unchanged.

    Returns the refreshed item. Raises ``ValidationError`` when the item does
    not track open containers or has none open. ``user`` is accepted for symmetry
    with :func:`open_pack` (and so callers need not branch); nothing is logged
    because nothing is consumed — the pack's contents left ``current_stock``
    when it was opened.
    """
    with transaction.atomic():
        locked = _locked_open_closed_item(item)
        if locked.open_container_count < 1:
            raise ValidationError(
                f"'{locked.name}' has no open {locked.count_level.name} to finish."
            )

        locked.open_container_count -= 1
        locked.save(update_fields=["open_container_count", "updated_at"])

    return locked
