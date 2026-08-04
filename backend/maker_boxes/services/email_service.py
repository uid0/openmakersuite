"""Email notifications for the maker box flow."""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import EmailMessage

from resilience.email import send_breakered_email

logger = logging.getLogger(__name__)


def send_pickup_notification(*, recipient: str, first_name: str, bin_id: str) -> bool:
    """Notify a former member that their bin contents are available for pickup.

    Returns ``True`` when Django reports the message was queued successfully.
    """
    if not recipient:
        logger.warning("send_pickup_notification: no recipient email")
        return False

    site_name = getattr(settings, "SITE_NAME", "Dallas Makerspace")
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@openmakersuite.example")
    greeting = f"Hi {first_name}," if first_name else "Hello,"

    subject = f"Your personal storage bin at {site_name}"
    body = (
        f"{greeting}\n\n"
        f"Our records show your membership has lapsed and we are reclaiming your "
        f"personal storage bin ({bin_id}). The contents will be available for "
        f"pickup any night that there is a tour. If you would like to renew "
        f"your membership and keep the bin, please reply to this email and we "
        f"will hold the contents.\n\n"
        f"Thanks,\nLogistics — {site_name}"
    )

    try:
        message = EmailMessage(
            subject=subject,
            body=body,
            from_email=from_email,
            to=[recipient],
        )
        sent = send_breakered_email(message)
        return bool(sent)
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("send_pickup_notification failed for %s: %s", recipient, exc)
        return False


__all__ = ["send_pickup_notification"]
