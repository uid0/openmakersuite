"""Audit-event recording for webhook configuration + lifecycle
(gh #357 / #334).

Kept in a sibling module to ``reorder_queue.audit`` (purchase-order
audit) so the two concerns can evolve independently while sharing
the reorder_queue app.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from django.contrib.auth import get_user_model

from .models import WebHook, WebhookAuditEvent

User = get_user_model()


def record_event(
    *,
    action: str,
    webhook: WebHook,
    actor: Optional[User] = None,
    notes: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> WebhookAuditEvent:
    """Record a webhook audit event. ``webhook`` is required."""
    return WebhookAuditEvent.objects.create(
        action=action,
        actor=actor if (actor is not None and getattr(actor, "is_authenticated", False)) else None,
        webhook=webhook,
        notes=notes,
        metadata=metadata or {},
    )


def diff_audited_fields(before: WebHook, after: WebHook) -> Dict[str, Dict[str, Any]]:
    """Return ``{field: {before, after}}`` for changed audited fields.

    The ``secret`` field is intentionally excluded — its value never
    appears in audit metadata; only the fact that it changed is recorded
    via the ``webhook_secret_rotate`` action.
    """
    changes: Dict[str, Dict[str, Any]] = {}
    for name in WebhookAuditEvent.AUDITED_FIELDS:
        old = getattr(before, name)
        new = getattr(after, name)
        if old != new:
            changes[name] = {"before": old, "after": new}
    return changes
