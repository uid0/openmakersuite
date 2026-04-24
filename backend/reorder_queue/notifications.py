"""
Email notifications for reorder queue events.

When a new ReorderRequest is created, this module resolves the correct
set of recipients (SIGProfile.email > SIGAdmin emails > staff fallback),
filters out users who have opted out of email/order-update notifications,
and dispatches a templated Postmark email.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Iterable, List, Optional

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

if TYPE_CHECKING:
    from .models import ReorderRequest

logger = logging.getLogger(__name__)

User = get_user_model()


def _filter_by_notification_prefs(emails: Iterable[str]) -> List[str]:
    """
    Drop any email whose owning User has opted out of email or order-update
    notifications. Emails with no matching user record pass through unchanged
    (e.g., SIGProfile.email may be a distribution list with no Django user).
    """
    from django.db.models import Q

    from notifications.models import NotificationPreference

    emails = [e for e in emails if e]
    if not emails:
        return []

    opted_out = set(
        NotificationPreference.objects.filter(user__email__in=emails)
        .filter(Q(email_enabled=False) | Q(order_updates=False))
        .values_list("user__email", flat=True)
    )
    return [e for e in emails if e not in opted_out]


def _dedupe_preserving_order(items: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for item in items:
        if not item:
            continue
        key = item.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item.strip())
    return out


def get_reorder_recipients(reorder_request: "ReorderRequest") -> List[str]:
    """
    Resolve the list of email recipients for a new reorder-request notification.

    Fallback chain:
      1. The owning group's SIGProfile.email, if set.
      2. Otherwise, the emails of all active SIGAdmins for that group.
      3. Otherwise, the emails of all is_staff=True users as a last resort.

    Results are deduplicated and filtered through NotificationPreference
    (email_enabled and order_updates must both be True for user-linked emails).
    """
    from membership.models import SIGAdmin

    item = reorder_request.item
    group = getattr(item, "owning_group", None)

    # Tier 1: SIG-level email address
    if group is not None:
        profile = getattr(group, "sig_profile", None)
        if profile and profile.email:
            filtered = _filter_by_notification_prefs([profile.email])
            if filtered:
                return _dedupe_preserving_order(filtered)

    # Tier 2: SIGAdmin email fan-out
    if group is not None:
        admin_emails = list(
            SIGAdmin.get_sig_admins(group).exclude(email="").values_list("email", flat=True)
        )
        if admin_emails:
            filtered = _filter_by_notification_prefs(admin_emails)
            if filtered:
                return _dedupe_preserving_order(filtered)

    # Tier 3: staff fallback
    staff_emails = list(
        User.objects.filter(is_staff=True).exclude(email="").values_list("email", flat=True)
    )
    filtered = _filter_by_notification_prefs(staff_emails)
    return _dedupe_preserving_order(filtered)


def _build_context(reorder_request: "ReorderRequest") -> dict:
    item = reorder_request.item
    group = getattr(item, "owning_group", None)
    frontend_url = getattr(settings, "FRONTEND_URL", "").rstrip("/")
    return {
        "request": reorder_request,
        "item": item,
        "group": group,
        "sig_name": getattr(group, "name", None),
        "requester": reorder_request.requested_by or "Unknown",
        "quantity": reorder_request.quantity,
        "status": reorder_request.get_status_display(),
        "priority": reorder_request.get_priority_display(),
        "notes": reorder_request.request_notes,
        "admin_url": (f"{frontend_url}/reorder-queue/{reorder_request.id}" if frontend_url else ""),
    }


def send_reorder_request_notification(reorder_request: "ReorderRequest") -> Optional[int]:
    """
    Render and send a new-reorder-request notification email.

    Returns the count of messages sent (Django's EmailMessage.send() return
    value) or None if there were no recipients after filtering.

    Exceptions from the email backend are logged but never raised — the caller
    (signal handler / Celery task) should not fail the originating transaction
    because an email provider hiccupped.
    """
    recipients = get_reorder_recipients(reorder_request)
    if not recipients:
        logger.info(
            "Reorder request %s has no eligible email recipients after filtering",
            reorder_request.id,
        )
        return None

    ctx = _build_context(reorder_request)
    subject = f"New reorder request: {ctx['item'].name} (x{ctx['quantity']})"
    text_body = render_to_string("emails/reorder_request_new.txt", ctx)
    html_body = render_to_string("emails/reorder_request_new.html", ctx)

    try:
        message = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            to=recipients,
        )
        message.attach_alternative(html_body, "text/html")
        sent = message.send(fail_silently=False)
        logger.info(
            "Sent reorder-request notification for %s to %d recipient(s)",
            reorder_request.id,
            len(recipients),
        )
        return sent
    except Exception:  # noqa: BLE001 — email failures must not break the request flow
        logger.exception(
            "Failed to send reorder-request notification for request %s",
            reorder_request.id,
        )
        return None
