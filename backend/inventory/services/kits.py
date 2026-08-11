"""Kit explosion: turning one received kit line into component stock (op-8n0).

A kit is bought as a single purchase-order line and *received* as N credits to
its component items. The two functions here are deliberately split:

* :func:`kit_component_credits` is PURE — it computes what a receipt *would*
  credit and writes nothing. The purchase-order serializer renders its preview
  from this, and :func:`explode_kit_receipt` applies exactly what it returns, so
  the number the operator reads on the line and the number the receipt posts
  cannot drift apart.
* :func:`explode_kit_receipt` performs the writes, under row locks.

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


def kit_component_credits(kit: "InventoryItem", kit_quantity: int) -> Iterator[KitCredit]:
    """Yield the stock credits receiving ``kit_quantity`` of ``kit`` would apply.

    Pure: reads the bill of materials and writes nothing. Yields nothing for a
    non-kit item, for a non-positive quantity, or for a kit with an empty bill
    of materials, so callers can render or apply the result unconditionally.

    Ordered by primary key so the preview, the receipt, and the lock order in
    :func:`explode_kit_receipt` all agree on a single sequence.
    """
    if not getattr(kit, "is_kit", False) or kit_quantity <= 0:
        return

    components = kit.kit_components.select_related("component").order_by("component_id")
    for row in components:
        yield KitCredit(
            component=row.component,
            quantity_per_kit=row.quantity,
            quantity=row.quantity * kit_quantity,
        )


def explode_kit_receipt(kit: "InventoryItem", kit_quantity: int) -> list[KitCredit]:
    """Credit each component's stock for a receipt of ``kit_quantity`` kits.

    Returns the credits applied, so the caller can close the components' reorder
    requests and report the effect. Must be called inside the receipt's
    ``transaction.atomic()`` block — ``receive_delivery`` owns that transaction.

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

    # Re-read the bill of materials under lock. ``kit_component_credits`` is the
    # shape; this is the same query with the rows pinned for the write.
    from inventory.models import InventoryItem

    component_rows = list(kit.kit_components.select_related("component").order_by("component_id"))
    if not component_rows:
        logger.warning(
            "Kit %s (id=%s) was received with an empty bill of materials; "
            "no component stock was credited.",
            kit.name,
            kit.pk,
        )
        return []

    per_kit = {row.component_id: row.quantity for row in component_rows}
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
