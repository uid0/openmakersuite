"""Kit explosion: turning one received kit line into component stock (op-8n0).

A kit is bought as a single purchase-order line and *received* as N credits to
its component items. The three functions here are deliberately split:

* :func:`build_kit_snapshot` freezes a kit's bill of materials at order time.
* :func:`kit_component_credits` is PURE — it computes what a receipt *would*
  credit and writes nothing. The purchase-order serializer renders its preview
  from this, and :func:`explode_kit_receipt` applies exactly what it returns, so
  the number the operator reads on the line and the number the receipt posts
  cannot drift apart.
* :func:`explode_kit_receipt` performs the writes, under row locks.

**Ordered, not current.** A kit's BOM is editable and weeks can pass between
ordering and receiving, so both the preview and the receipt read the line's
order-time snapshot when it has one. The live BOM is consulted only when a line
carries no snapshot at all — legacy rows written before the field existed. Both
sides take the same ``snapshot`` argument through the same function, so there is
no second code path in which they could disagree.

Neither knows anything about idempotency. Both are driven by *this receipt's*
quantity, never by ``po_item.quantity_received`` — that is what makes partial
receipts additive, and it means over-receipt has to be rejected upstream (the
pending-quantity guard in ``reorder_queue.views``) rather than half-detected
here.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Iterator, NamedTuple

if TYPE_CHECKING:  # pragma: no cover - typing only
    from inventory.models import InventoryItem

logger = logging.getLogger(__name__)


class KitCredit(NamedTuple):
    """One component's share of a kit receipt.

    ``quantity_per_kit`` is the bill-of-materials number; ``quantity`` is what
    this particular receipt credits (``quantity_per_kit × kits``).
    """

    component: "InventoryItem"
    quantity_per_kit: int
    quantity: int


def build_kit_snapshot(kit: "InventoryItem") -> dict | None:
    """Freeze ``kit``'s bill of materials for storage on a purchase-order line.

    Called once, when a kit line is created. Returns ``None`` for a non-kit so
    the caller can assign the result unconditionally and leave ordinary lines
    NULL.

    Quantities are stored PER KIT, never multiplied by the ordered quantity: the
    same snapshot has to serve the ordered-quantity preview and each partial
    receipt, which credit different multiples of it.

    Names and SKUs ride along for the record of what was bought, but the credit
    path keys on ``component`` (the PK) alone — a renamed cartridge is still the
    same cartridge, and resolving live rows keeps the display current.
    """
    if not getattr(kit, "is_kit", False):
        return None

    rows = kit.kit_components.select_related("component").order_by("component_id")
    return {
        "components": [
            {
                # Stringified: item PKs are UUIDs, which JSON cannot hold.
                "component": str(row.component_id),
                "component_name": row.component.name,
                "component_sku": row.component.sku,
                "quantity_per_kit": row.quantity,
            }
            for row in rows
        ]
    }


def _snapshot_quantities(snapshot: dict | None) -> list[tuple[str, int]] | None:
    """Parse ``snapshot`` into ``[(component_pk, quantity_per_kit), ...]``.

    ``None`` means "this line has no snapshot, fall back to the live BOM". An
    empty list does NOT — a kit ordered with components and emptied afterwards
    must credit what was ordered, and falling back there would resurrect the
    very live-BOM read the snapshot exists to prevent.
    """
    if snapshot is None:
        return None

    rows = snapshot.get("components") if isinstance(snapshot, dict) else snapshot
    if not isinstance(rows, list):
        return None

    parsed: list[tuple[str, int]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        component_id, quantity = row.get("component"), row.get("quantity_per_kit")
        if component_id and isinstance(quantity, int) and quantity > 0:
            parsed.append((str(component_id), quantity))
    return parsed


def kit_component_credits(
    kit: "InventoryItem",
    kit_quantity: int,
    *,
    snapshot: dict | None = None,
) -> Iterator[KitCredit]:
    """Yield the stock credits receiving ``kit_quantity`` of ``kit`` would apply.

    Pure: reads a bill of materials and writes nothing. Yields nothing for a
    non-kit item, for a non-positive quantity, or for an empty bill of
    materials, so callers can render or apply the result unconditionally.

    ``snapshot`` is the line's order-time breakdown. When given, IT is the bill
    of materials — the kit's current components are not read at all, which is
    what makes a receipt credit what was ordered rather than what the kit
    contains today. Only a line with no snapshot falls back to the live BOM.

    Ordered by component primary key so the preview, the receipt, and the lock
    order in :func:`explode_kit_receipt` all agree on a single sequence.
    """
    if not getattr(kit, "is_kit", False) or kit_quantity <= 0:
        return

    ordered = _snapshot_quantities(snapshot)
    if ordered is None:
        components = kit.kit_components.select_related("component").order_by("component_id")
        for row in components:
            yield KitCredit(
                component=row.component,
                quantity_per_kit=row.quantity,
                quantity=row.quantity * kit_quantity,
            )
        return

    # Resolve the snapshot's PKs in ONE query. Keyed by ``str(pk)`` rather than
    # with ``in_bulk``, whose dict comes back keyed by UUID objects and would
    # miss every one of the strings the snapshot stores.
    #
    # A component deleted since the order is skipped with a warning rather than
    # crashing a receipt for goods that physically arrived.
    from inventory.models import InventoryItem

    items = {
        str(item.pk): item
        for item in InventoryItem.objects.filter(pk__in=[pk for pk, _ in ordered])
    }
    for component_id, quantity_per_kit in sorted(ordered):
        component = items.get(component_id)
        if component is None:
            logger.warning(
                "Kit %s (id=%s) was ordered with component id=%s, which no "
                "longer exists; it cannot be credited.",
                kit.name,
                kit.pk,
                component_id,
            )
            continue
        yield KitCredit(
            component=component,
            quantity_per_kit=quantity_per_kit,
            quantity=quantity_per_kit * kit_quantity,
        )


def explode_kit_receipt(
    kit: "InventoryItem",
    kit_quantity: int,
    *,
    snapshot: dict | None = None,
) -> list[KitCredit]:
    """Credit each component's stock for a receipt of ``kit_quantity`` kits.

    Returns the credits applied, so the caller can close the components' reorder
    requests and report the effect. Must be called inside the receipt's
    ``transaction.atomic()`` block — ``receive_delivery`` owns that transaction.

    ``snapshot`` is the line's order-time breakdown, and is what gets credited
    when present; see :func:`kit_component_credits`, which this delegates the
    entire question of "how much of what" to. Going through that one function is
    what makes the preview on the line and the stock this posts the same numbers
    by construction rather than by two implementations agreeing.

    Rows are locked with ``select_for_update`` **in primary-key order**. Two
    receipts landing at once on kits that share components (an ink kit and a
    bundle both containing black cartridges) would otherwise be able to grab the
    same two rows in opposite orders and deadlock.

    An empty bill of materials logs a warning and returns ``[]`` rather than
    raising. The goods physically arrived; blowing up mid-receipt would roll
    back a delivery that really happened and leave the operator with no way to
    record it. The kit's own stock is never touched.
    """
    if not kit.is_kit or kit_quantity <= 0:
        return []

    from inventory.models import InventoryItem

    # Ask the preview what this receipt owes, then pin those rows for the write.
    per_kit = {
        credit.component.pk: credit.quantity_per_kit
        for credit in kit_component_credits(kit, kit_quantity, snapshot=snapshot)
    }
    if not per_kit:
        logger.warning(
            "Kit %s (id=%s) was received with an empty bill of materials; "
            "no component stock was credited.",
            kit.name,
            kit.pk,
        )
        return []

    locked = InventoryItem.objects.select_for_update().filter(pk__in=per_kit).order_by("pk")

    credits: list[KitCredit] = []
    for component in locked:
        quantity_per_kit = per_kit[component.pk]
        credited = quantity_per_kit * kit_quantity
        component.current_stock += credited
        component.save(update_fields=["current_stock", "updated_at"])
        credits.append(
            KitCredit(
                component=component,
                quantity_per_kit=quantity_per_kit,
                quantity=credited,
            )
        )
    return credits
