"""Unified audit-event feed across the per-domain audit tables (gh #359).

This module is the consolidation layer for the #334 audit-trail effort.
Each domain (forgekey #352, purchase orders #353, donations #354,
maintenance work orders #355, vendor compliance #356, webhooks #357,
site settings #358) maintains its own append-only audit table with
a per-domain shape. The feed normalizes a row from any of those
tables into a uniform dict so a single staff review surface can
answer "who did what, when, on this entity" without joining 8
tables by hand.

## Defensive imports

Every per-domain collector wraps its model import in a guarded
``try/except ImportError``. This protects the feed against:

1. Domain audit tables that haven't merged yet (migration ordering
   during parallel PR development).
2. Future deployments that disable an app — the feed gracefully
   omits its rows rather than 500-ing.

It also keeps this module's import cost bounded: ``ImportError`` on a
missing domain is the only failure mode that returns ``[]`` here. Any
real bug (typo in a model field, etc.) still surfaces normally.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

# Sentinels for entity_type so callers can filter without per-app
# knowledge. Keep the strings stable — they appear in API responses.
ENTITY_DEVICE = "device"
ENTITY_AUTHORIZATION = "authorization"
ENTITY_LOCKOUT = "lockout"
ENTITY_FIRMWARE_UPDATE = "firmware_update"
ENTITY_PURCHASE_ORDER = "purchase_order"
ENTITY_PO_LINE_ITEM = "purchase_order_item"
ENTITY_PO_ATTACHMENT = "purchase_order_attachment"
ENTITY_DONATION = "donation"
ENTITY_DONATION_ITEM = "donation_item"
ENTITY_TAX_RECEIPT = "tax_receipt"
ENTITY_DISPOSITION = "disposition"
ENTITY_WORK_ORDER = "work_order"
ENTITY_LOCATION_PROBLEM = "location_problem"
ENTITY_VENDOR = "vendor"
ENTITY_WEBHOOK = "webhook"
ENTITY_SITE_SETTINGS = "site_settings"


def _row(
    *,
    domain: str,
    action: str,
    actor_id: Optional[Any],
    actor_username: Optional[str],
    created_at: datetime,
    entity_type: Optional[str],
    entity_id: Optional[Any],
    notes: str,
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    """Build one normalized row. The shape is the response contract."""
    return {
        "domain": domain,
        "action": action,
        "actor_id": actor_id,
        "actor_username": actor_username,
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
        "entity_type": entity_type,
        "entity_id": str(entity_id) if entity_id is not None else None,
        "notes": notes or "",
        "metadata": metadata or {},
    }


def _entity_for_forgekey(event) -> tuple[Optional[str], Optional[Any]]:
    """Pick the most-specific entity reference on a ForgeKeyAuditEvent."""
    if event.firmware_update_id:
        return ENTITY_FIRMWARE_UPDATE, event.firmware_update_id
    if event.lockout_id:
        return ENTITY_LOCKOUT, event.lockout_id
    if event.authorization_id:
        return ENTITY_AUTHORIZATION, event.authorization_id
    if event.device_id:
        return ENTITY_DEVICE, event.device_id
    return None, None


def _collect_forgekey(filters: Dict[str, Any]) -> List[Dict[str, Any]]:
    try:
        from forgekey.models import ForgeKeyAuditEvent
    except ImportError:
        return []
    qs = ForgeKeyAuditEvent.objects.select_related("actor")
    qs = _apply_common_filters(qs, filters)
    out: List[Dict[str, Any]] = []
    for event in qs:
        entity_type, entity_id = _entity_for_forgekey(event)
        out.append(
            _row(
                domain="forgekey",
                action=event.action,
                actor_id=event.actor_id,
                actor_username=getattr(event.actor, "username", None),
                created_at=event.created_at,
                entity_type=entity_type,
                entity_id=entity_id,
                notes=event.notes,
                metadata=event.metadata,
            )
        )
    return out


def _entity_for_po(event) -> tuple[Optional[str], Optional[Any]]:
    if event.attachment_id:
        return ENTITY_PO_ATTACHMENT, event.attachment_id
    if event.line_item_id:
        return ENTITY_PO_LINE_ITEM, event.line_item_id
    if event.purchase_order_id:
        return ENTITY_PURCHASE_ORDER, event.purchase_order_id
    return None, None


def _collect_purchase_orders(filters: Dict[str, Any]) -> List[Dict[str, Any]]:
    try:
        from reorder_queue.models import PurchaseOrderAuditEvent
    except ImportError:
        return []
    qs = PurchaseOrderAuditEvent.objects.select_related("actor")
    qs = _apply_common_filters(qs, filters)
    out: List[Dict[str, Any]] = []
    for event in qs:
        entity_type, entity_id = _entity_for_po(event)
        out.append(
            _row(
                domain="purchase_orders",
                action=event.action,
                actor_id=event.actor_id,
                actor_username=getattr(event.actor, "username", None),
                created_at=event.created_at,
                entity_type=entity_type,
                entity_id=entity_id,
                notes=event.notes,
                metadata=event.metadata,
            )
        )
    return out


def _collect_webhooks(filters: Dict[str, Any]) -> List[Dict[str, Any]]:
    try:
        from reorder_queue.models import WebhookAuditEvent
    except ImportError:
        return []
    qs = WebhookAuditEvent.objects.select_related("actor")
    qs = _apply_common_filters(qs, filters)
    out: List[Dict[str, Any]] = []
    for event in qs:
        out.append(
            _row(
                domain="webhooks",
                action=event.action,
                actor_id=event.actor_id,
                actor_username=getattr(event.actor, "username", None),
                created_at=event.created_at,
                entity_type=ENTITY_WEBHOOK if event.webhook_id else None,
                entity_id=event.webhook_id,
                notes=event.notes,
                metadata=event.metadata,
            )
        )
    return out


def _entity_for_donation(event) -> tuple[Optional[str], Optional[Any]]:
    if event.disposition_id:
        return ENTITY_DISPOSITION, event.disposition_id
    if event.tax_receipt_id:
        return ENTITY_TAX_RECEIPT, event.tax_receipt_id
    if event.donation_item_id:
        return ENTITY_DONATION_ITEM, event.donation_item_id
    if event.donation_id:
        return ENTITY_DONATION, event.donation_id
    return None, None


def _collect_donations(filters: Dict[str, Any]) -> List[Dict[str, Any]]:
    try:
        from donations.models import DonationAuditEvent
    except ImportError:
        return []
    qs = DonationAuditEvent.objects.select_related("actor")
    qs = _apply_common_filters(qs, filters)
    out: List[Dict[str, Any]] = []
    for event in qs:
        entity_type, entity_id = _entity_for_donation(event)
        out.append(
            _row(
                domain="donations",
                action=event.action,
                actor_id=event.actor_id,
                actor_username=getattr(event.actor, "username", None),
                created_at=event.created_at,
                entity_type=entity_type,
                entity_id=entity_id,
                notes=event.notes,
                metadata=event.metadata,
            )
        )
    return out


def _collect_maintenance(filters: Dict[str, Any]) -> List[Dict[str, Any]]:
    try:
        from inventory.models import MaintenanceAuditEvent
    except ImportError:
        return []
    qs = MaintenanceAuditEvent.objects.select_related("actor")
    qs = _apply_common_filters(qs, filters)
    out: List[Dict[str, Any]] = []
    for event in qs:
        if event.work_order_id:
            entity_type, entity_id = ENTITY_WORK_ORDER, event.work_order_id
        elif event.location_problem_id:
            entity_type, entity_id = ENTITY_LOCATION_PROBLEM, event.location_problem_id
        else:
            entity_type, entity_id = None, None
        out.append(
            _row(
                domain="maintenance",
                action=event.action,
                actor_id=event.actor_id,
                actor_username=getattr(event.actor, "username", None),
                created_at=event.created_at,
                entity_type=entity_type,
                entity_id=entity_id,
                notes=event.notes,
                metadata=event.metadata,
            )
        )
    return out


def _collect_vendors(filters: Dict[str, Any]) -> List[Dict[str, Any]]:
    try:
        from vendors.models import VendorAuditEvent
    except ImportError:
        return []
    qs = VendorAuditEvent.objects.select_related("actor")
    qs = _apply_common_filters(qs, filters)
    out: List[Dict[str, Any]] = []
    for event in qs:
        out.append(
            _row(
                domain="vendors",
                action=event.action,
                actor_id=event.actor_id,
                actor_username=getattr(event.actor, "username", None),
                created_at=event.created_at,
                entity_type=ENTITY_VENDOR if event.vendor_id else None,
                entity_id=event.vendor_id,
                notes=event.notes,
                metadata=event.metadata,
            )
        )
    return out


def _collect_settings(filters: Dict[str, Any]) -> List[Dict[str, Any]]:
    try:
        from customization.models import SiteSettingsAuditEvent
    except ImportError:
        return []
    qs = SiteSettingsAuditEvent.objects.select_related("actor")
    qs = _apply_common_filters(qs, filters)
    out: List[Dict[str, Any]] = []
    for event in qs:
        out.append(
            _row(
                domain="customization",
                action=event.action,
                actor_id=event.actor_id,
                actor_username=getattr(event.actor, "username", None),
                created_at=event.created_at,
                entity_type=ENTITY_SITE_SETTINGS if event.site_settings_id else None,
                entity_id=event.site_settings_id,
                notes=event.notes,
                metadata=event.metadata,
            )
        )
    return out


def _collect_third_party_wo(filters: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Includes the pre-existing maintenance_orders.ThirdPartyWorkOrderAuditLog
    so the unified feed covers ALL audit-relevant events town-wide, not just
    the per-domain audits added in #352–#358."""
    try:
        from maintenance_orders.models import ThirdPartyWorkOrderAuditLog
    except ImportError:
        return []
    qs = ThirdPartyWorkOrderAuditLog.objects.select_related("actor", "work_order")
    qs = _apply_common_filters(qs, filters)
    out: List[Dict[str, Any]] = []
    for event in qs:
        out.append(
            _row(
                domain="third_party_work_orders",
                action=event.action,
                actor_id=event.actor_id,
                actor_username=getattr(event.actor, "username", None),
                created_at=event.created_at,
                entity_type="third_party_work_order" if event.work_order_id else None,
                entity_id=event.work_order_id,
                notes=event.notes,
                metadata=event.metadata,
            )
        )
    return out


def _apply_common_filters(qs, filters: Dict[str, Any]):
    """Apply actor / action / since / until filters to a queryset.

    Each per-domain queryset has the same field names for these filters,
    so the application is generic. Entity-type and entity-id filters are
    applied AFTER normalization (in ``collect_events``) since the
    underlying field names differ across domains.
    """
    actor_id = filters.get("actor_id")
    if actor_id:
        qs = qs.filter(actor_id=actor_id)
    action = filters.get("action")
    if action:
        qs = qs.filter(action=action)
    since = filters.get("since")
    if since:
        qs = qs.filter(created_at__gte=since)
    until = filters.get("until")
    if until:
        qs = qs.filter(created_at__lte=until)
    return qs


_COLLECTORS = (
    _collect_forgekey,
    _collect_purchase_orders,
    _collect_webhooks,
    _collect_donations,
    _collect_maintenance,
    _collect_vendors,
    _collect_settings,
    _collect_third_party_wo,
)


def collect_events(
    *,
    actor_id: Optional[Any] = None,
    action: Optional[str] = None,
    domain: Optional[str] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[Any] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """Collect normalized audit events across all domains.

    Filters are applied per-domain at queryset level when possible
    (actor / action / since / until); domain / entity filters are
    applied post-normalization. Results are sorted by ``created_at``
    descending and capped at ``limit`` (default 200, max 1000).
    """
    filters = {
        "actor_id": actor_id,
        "action": action,
        "since": since,
        "until": until,
    }

    rows: List[Dict[str, Any]] = []
    for collector in _COLLECTORS:
        rows.extend(collector(filters))

    if domain:
        rows = [r for r in rows if r["domain"] == domain]
    if entity_type:
        rows = [r for r in rows if r["entity_type"] == entity_type]
    if entity_id is not None:
        target = str(entity_id)
        rows = [r for r in rows if r["entity_id"] == target]

    rows.sort(key=lambda r: r["created_at"], reverse=True)

    capped = max(1, min(int(limit), 1000))
    return rows[:capped]
