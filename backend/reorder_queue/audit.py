"""Audit-event recording for purchase-order actions (gh #353 / #334).

Centralized helper so emission sites (views, services) all go through
one path. Mirrors the shape of ``forgekey.audit.record_event`` (gh #352)
so the eventual unified review surface (gh #359) can join across domains
without per-domain quirks.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from django.contrib.auth import get_user_model

from .models import (
    PurchaseOrder,
    PurchaseOrderAttachment,
    PurchaseOrderAuditEvent,
    PurchaseOrderItem,
)

User = get_user_model()


def record_event(
    *,
    action: str,
    actor: Optional[User] = None,
    purchase_order: Optional[PurchaseOrder] = None,
    line_item: Optional[PurchaseOrderItem] = None,
    attachment: Optional[PurchaseOrderAttachment] = None,
    notes: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> PurchaseOrderAuditEvent:
    """Record a purchase-order audit event.

    At least one of ``purchase_order``, ``line_item``, or ``attachment``
    must be supplied so the row is queryable by entity. The ``purchase_order``
    FK is auto-derived from the supplied line item or attachment when one is
    passed without an explicit PO so audit rows always have an entity-level
    pointer for review-surface queries.

    Anonymous / system-initiated actions are allowed (``actor=None``); the
    audit row simply records "no known user." Caller is expected to pass the
    request user when one exists.
    """
    if purchase_order is None:
        if line_item is not None:
            purchase_order = line_item.purchase_order
        elif attachment is not None:
            purchase_order = attachment.purchase_order

    return PurchaseOrderAuditEvent.objects.create(
        action=action,
        actor=actor if (actor is not None and getattr(actor, "is_authenticated", False)) else None,
        purchase_order=purchase_order,
        line_item=line_item,
        attachment=attachment,
        notes=notes,
        metadata=metadata or {},
    )


def record_line_reprice(
    *,
    line_item: PurchaseOrderItem,
    previous_unit_cost: Any,
    actor: Optional[User] = None,
) -> PurchaseOrderAuditEvent:
    """Record that an existing line's ORDERED price changed, naming both figures.

    The price-trace invariant: a line's ``unit_cost_ordered`` never changes
    without one of these rows behind it. Every route that can rewrite the field
    on an existing line — the deliberate PATCH reprice, an add that grows a line
    while overriding its price, and the Django admin change form — emits it
    from here, so "show me every time a price on this order changed" is ONE
    query against ONE action rather than a hunt through several shapes of row.
    The person asking that question is asking because the money looks wrong,
    which is exactly when a missed second place matters.

    An add that both grows a line and reprices it therefore writes two rows.
    That is not noise: the request genuinely did both, and recording both is the
    more truthful record.
    """
    return record_event(
        action=PurchaseOrderAuditEvent.Action.PO_LINE_REPRICE,
        actor=actor,
        line_item=line_item,
        metadata={
            "line_shape": line_item.target_type,
            "item_supplier": line_item.item_supplier_id,
            "asset_id": str(line_item.asset_id) if line_item.asset_id else None,
            "description": line_item.description or "",
            "quantity_ordered": line_item.quantity_ordered,
            "previous_unit_cost_ordered": str(previous_unit_cost),
            "unit_cost_ordered": str(line_item.unit_cost_ordered),
        },
    )
