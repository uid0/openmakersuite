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
