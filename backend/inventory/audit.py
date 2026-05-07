"""Audit-event recording for preventive-maintenance actions (gh #355 / #334).

Mirrors the helpers in forgekey (#352), reorder_queue (#353), and
donations (#354) so the eventual unified review surface (#359) can
join across domains without per-domain quirks.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from django.contrib.auth import get_user_model

from .models import LocationProblem, MaintenanceAuditEvent, WorkOrder

User = get_user_model()


def record_event(
    *,
    action: str,
    actor: Optional[User] = None,
    work_order: Optional[WorkOrder] = None,
    location_problem: Optional[LocationProblem] = None,
    notes: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> MaintenanceAuditEvent:
    """Record a maintenance audit event.

    At least one of ``work_order`` or ``location_problem`` must be
    supplied so the row is queryable by entity.

    Anonymous / system-initiated actions are allowed (``actor=None``);
    the audit row simply records "no known user." Caller is expected
    to pass the request user when one exists.
    """
    return MaintenanceAuditEvent.objects.create(
        action=action,
        actor=actor if (actor is not None and getattr(actor, "is_authenticated", False)) else None,
        work_order=work_order,
        location_problem=location_problem,
        notes=notes,
        metadata=metadata or {},
    )
