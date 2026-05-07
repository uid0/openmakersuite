"""Audit-event recording for vendor compliance/lifecycle actions
(gh #356 / #334).

Mirrors the helpers in #352-#355 so the eventual unified review surface
(#359) joins across domains without per-domain quirks.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from django.contrib.auth import get_user_model

from .models import Vendor, VendorAuditEvent

User = get_user_model()


def record_event(
    *,
    action: str,
    vendor: Vendor,
    actor: Optional[User] = None,
    notes: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> VendorAuditEvent:
    """Record a vendor audit event. ``vendor`` is required."""
    return VendorAuditEvent.objects.create(
        action=action,
        actor=actor if (actor is not None and getattr(actor, "is_authenticated", False)) else None,
        vendor=vendor,
        notes=notes,
        metadata=metadata or {},
    )


def diff_compliance_fields(before: Vendor, after: Vendor) -> Dict[str, Dict[str, Any]]:
    """Return ``{field: {before: ..., after: ...}}`` for changed compliance
    fields. Returns ``{}`` when nothing tracked changed.

    Date fields are serialized to ISO strings so the audit row stays
    JSON-clean for downstream review surfaces.
    """
    changes: Dict[str, Dict[str, Any]] = {}
    for name in VendorAuditEvent.COMPLIANCE_FIELDS:
        old = getattr(before, name)
        new = getattr(after, name)
        if old != new:
            changes[name] = {
                "before": _jsonable(old),
                "after": _jsonable(new),
            }
    return changes


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value
