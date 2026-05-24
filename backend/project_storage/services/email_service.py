"""Send the project storage violation notice email."""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

from ..models import DEFAULT_PURGATORY_GRACE_DAYS, ProjectStorageStint


def send_violation_notice(stint: ProjectStorageStint) -> bool:
    """Send the "your stint expired, you have 7 days" notice.

    Returns True if the email was queued for delivery. Returns False if
    the stint has no on-file email address — caller should surface this
    to the warden so they can reach the member another way.
    """
    if not stint.email:
        return False

    purgatory_at = (stint.notice_sent_at or stint.expires_at) + timedelta(
        days=DEFAULT_PURGATORY_GRACE_DAYS
    )

    context = {
        "stint": stint,
        "display_name": stint.display_name,
        "stint_id": stint.stint_id,
        "started_at": stint.started_at,
        "expires_at": stint.expires_at,
        "purgatory_at": purgatory_at,
        "grace_days": DEFAULT_PURGATORY_GRACE_DAYS,
        "project_title": stint.project_title,
        "storage_location_name": stint.storage_location_name,
        "purgatory_location_name": stint.purgatory_location_name,
    }

    subject = f"Project storage notice — {stint.stint_id}"
    text_body = render_to_string("emails/project_storage_violation.txt", context)
    html_body = render_to_string("emails/project_storage_violation.html", context)

    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        to=[stint.email],
    )
    message.attach_alternative(html_body, "text/html")
    message.send(fail_silently=False)
    return True
