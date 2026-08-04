"""Email fan-out for high/urgent LocationProblem reports (oms-0yz).

Reports of leaks, broken doors, lighting outages, and similar urgent
maintenance issues need to reach the logistics distribution list as soon
as they're filed — well before the problem makes it through the promote
flow into a WorkOrder. The address is configured via
``settings.LOGISTICS_ALERT_EMAIL``; an empty value disables the email.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives

from inventory.models import LocationProblem
from resilience.email import send_breakered_email

logger = logging.getLogger(__name__)


def email_logistics_alert(problem: LocationProblem) -> bool:
    """Send a logistics alert email for a LocationProblem.

    Returns True when an email was dispatched, False when no recipient is
    configured. Raises whatever the email backend raises so the caller can
    decide whether to swallow the failure.
    """
    recipient = getattr(settings, "LOGISTICS_ALERT_EMAIL", "") or ""
    recipient = recipient.strip()
    if not recipient:
        logger.info("LOGISTICS_ALERT_EMAIL not set; skipping LocationProblem alert email")
        return False

    location_name = problem.location.name if problem.location_id else "(unknown location)"
    severity_label = problem.get_severity_display()
    reporter = problem.reported_by or "anonymous"

    subject = f"[{severity_label.upper()}] Location problem at {location_name}"
    body_lines = [
        f"Severity: {severity_label}",
        f"Location: {location_name}",
        f"Reported by: {reporter}",
        f"Reported at: {problem.reported_at.isoformat()}",
        "",
        "Description:",
        problem.description,
        "",
        f"Detail: /maintenance/location-problems/{problem.id}",
        f"Admin: /admin/inventory/locationproblem/{problem.id}/",
    ]
    body = "\n".join(body_lines)

    message = EmailMultiAlternatives(
        subject=subject,
        body=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
    )
    send_breakered_email(message, fail_silently=False)
    return True
