"""Purchase-order receipt → work-order material bridge (op-bu80, B4).

A purchase-order line can now be tagged with the work order it was ordered
for (``PurchaseOrderItem.work_order``). When such a line is *received*, the
parts did not just land on a shelf — they landed on a job. This module is the
one seam that threads the receipt back onto that job as an actual-cost
material line, so ``WorkOrder.actual_material_cost`` (op-768w) — and the cost
reporting and ledger charge that consume it — sees what the job really cost.

Mirrors :func:`accounting.adapters.post_po_receipt`: the write lives in the
app that owns the target model and is called from
:func:`reorder_queue.services.receiving.receive_delivery` through a lazy
import, so ``reorder_queue`` never imports ``inventory`` services at module
scope.

Two decisions worth stating out loud
------------------------------------

**Idempotency comes from an absolute quantity, not a running total.** The line
posted here is found-or-created on ``(work_order, purchase_order_item)`` and
its ``quantity_used`` is *set* to the PO line's cumulative
``quantity_received`` rather than incremented by the delivery amount. A partial
receipt of 3 followed by the remaining 7 leaves one line reading 10, and
re-driving the very same ``DeliveryItem`` recomputes the same 10 — there is no
"have I already counted this delivery?" state to keep, because nothing is
counted twice by construction.

**The bridge moves no stock.** Receiving already incremented the inventory
item; the material line is marked ``was_used`` because the units were bought
*for* this job, but ``applied_quantity`` stays ``None`` so
:func:`inventory.services.work_order_material_usage.apply_material_usage`
remains the only thing that ever decrements. Receipt behaviour is therefore
unchanged, and if the units genuinely leave stock an operator toggles the line
through that one seam exactly as for any other material.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from ..models import WorkOrderMaterialUsage

# ``WorkOrderMaterialUsage.unit_cost`` is 2dp; a PO line's cost is 4dp.
_CENTS = Decimal("0.01")


def purchase_line_name(po_item) -> str:
    """Human label for a PO line, from whichever target the line carries.

    Same priority as the rest of the PO surface (#884's typed-target
    accessor): the inventory item's name, the asset's name, then the freeform
    description. Shared with
    :func:`inventory.services.work_order_context.build_purchase_lines_context`
    so the material line and the "ordered for this WO" row read identically.
    """
    target = po_item.target
    if target is not None:
        return getattr(target, "name", "") or ""
    return po_item.description or f"Line {po_item.pk}"


def purchase_line_unit_cost(po_item) -> Optional[Decimal]:
    """The real price paid per unit: actual when recorded, else what was ordered.

    ``is None`` rather than a truthiness fallback, so a line explicitly
    receipted at zero cost (a free replacement, a warranty part) stays free
    instead of silently reverting to the price it was ordered at. Shared with
    :func:`inventory.services.work_order_context.build_purchase_lines_context`
    so the ordering row and the material line never quote different prices.
    """
    unit_cost = po_item.unit_cost_actual
    if unit_cost is None:
        unit_cost = po_item.unit_cost_ordered
    return unit_cost


def _material_unit_cost(po_item) -> Optional[Decimal]:
    """:func:`purchase_line_unit_cost` rounded to the material row's 2dp field."""
    unit_cost = purchase_line_unit_cost(po_item)
    if unit_cost is None:
        return None
    return Decimal(unit_cost).quantize(_CENTS, rounding=ROUND_HALF_UP)


def post_work_order_material(po_item) -> Optional[WorkOrderMaterialUsage]:
    """Mirror a received PO line onto the work order it was ordered for.

    No-op (returns ``None``) for a line with no ``work_order``, which is every
    ordinary purchase.

    Otherwise the work order gets exactly one ad-hoc material line per PO line,
    carrying the item's name, the cumulative received quantity, the actual (or
    ordered) unit cost and — for an inventory line — a direct
    :attr:`~inventory.models.WorkOrderMaterialUsage.inventory_item` link, so
    the job shows both what was bought and what it cost. Safe to call again for
    the same line: it recomputes the same values (see the module docstring).

    The caller owns the transaction.
    """
    if po_item.work_order_id is None:
        return None

    inventory_item = po_item.item  # None for asset / freeform lines
    quantity = Decimal(po_item.quantity_received or 0)

    usage, created = WorkOrderMaterialUsage.objects.get_or_create(
        work_order_id=po_item.work_order_id,
        purchase_order_item=po_item,
        defaults={
            "material": None,
            "is_ad_hoc": True,
            "inventory_item": inventory_item,
            "material_name": purchase_line_name(po_item),
            # Nothing "planned" this line on the work order — the purchase
            # order did — so both quantities agree, matching ``add_material``.
            "quantity_planned": quantity,
            "quantity_used": quantity,
            "unit_cost": _material_unit_cost(po_item),
            "was_used": True,
        },
    )
    if created:
        return usage

    # Existing line: re-mirror the PO. A later receipt raises the quantity and
    # a cost correction (``unit_cost_actual`` typed in at receipt time) lands
    # here too — the PO line is the source of truth for a PO-sourced material.
    usage.quantity_used = quantity
    usage.unit_cost = _material_unit_cost(po_item)
    usage.was_used = True
    if usage.inventory_item_id is None and inventory_item is not None:
        usage.inventory_item = inventory_item
    usage.save(
        update_fields=["quantity_used", "unit_cost", "was_used", "inventory_item"],
    )
    return usage
