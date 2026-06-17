"""
Celery tasks for the notifications app.

Hosts the new-device sign-in *email* alert (oms-1crmp, FP2) — the email
companion to the in-app alert raised inline by ``notifications.device_login``
(oms-bwcdb, FP1). FP1's login hook records a :class:`KnownDevice` and schedules
:func:`send_new_device_alert_email` via ``transaction.on_commit`` so the email
is only dispatched once the device row is durably committed.

The send path mirrors ``reorder_queue.tasks.send_reorder_request_notification_email``
+ ``reorder_queue.notifications``: a thin ``@shared_task`` that resolves the row
ids and delegates to a helper which renders the templated email and sends it via
``EmailMultiAlternatives`` (Postmark via django-anymail in prod).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone

from celery import shared_task

logger = logging.getLogger(__name__)

# A new-device sign-in is a security signal, so by default we email the alert
# even to users who have turned off routine email notifications
# (NotificationPreference.email_enabled=False). Flip to False to honor that
# opt-out for new-device alerts too.
NEW_DEVICE_ALERT_BYPASS_EMAIL_OPT_OUT = True

# Defensive cap so a hostile/oversized User-Agent can't bloat the email body.
_UA_MESSAGE_MAX = 256


def _email_opted_out(user) -> bool:
    """
    Return ``True`` when ``user`` has disabled email notifications *and* the
    security-alert exemption is off. With the exemption on (the default) this
    always returns ``False`` so the alert is sent regardless of preference.
    """
    if NEW_DEVICE_ALERT_BYPASS_EMAIL_OPT_OUT:
        return False

    from .models import NotificationPreference

    pref = NotificationPreference.objects.filter(user=user).first()
    return pref is not None and not pref.email_enabled


def _build_context(user, device) -> dict:
    """Template context for the new-device alert email."""
    frontend_url = getattr(settings, "FRONTEND_URL", "").rstrip("/")
    seen_dt = device.first_seen or timezone.now()
    return {
        "username": user.get_username(),
        # UA / IP / time mirror the in-app alert in device_login._notify_new_device.
        "user_agent": (device.user_agent or "an unrecognized browser")[:_UA_MESSAGE_MAX],
        "ip_address": device.ip_address or "an unknown location",
        "seen_at": seen_dt.strftime("%Y-%m-%d %H:%M UTC"),
        "devices_url": (f"{frontend_url}/account/devices" if frontend_url else "/account/devices"),
    }


def send_new_device_alert(user, device) -> Optional[int]:
    """
    Render and send the new-device alert email to ``user``.

    Returns the count of messages sent (Django's ``EmailMessage.send()`` return
    value), or ``None`` when there is nothing to send (no email address on the
    account, opted out, or the email backend errored).

    Email-backend exceptions are logged but never raised — a provider hiccup
    must not fail the Celery task (and so trigger a misleading retry/error).
    """
    if not user.email:
        logger.info("new-device alert: user %s has no email address; skipping", user.pk)
        return None

    if _email_opted_out(user):
        logger.info("new-device alert: user %s opted out of email; skipping", user.pk)
        return None

    ctx = _build_context(user, device)
    subject = "New device sign-in to your account"
    text_body = render_to_string("emails/new_device_alert.txt", ctx)
    html_body = render_to_string("emails/new_device_alert.html", ctx)

    try:
        message = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            to=[user.email],
        )
        message.attach_alternative(html_body, "text/html")
        sent = message.send(fail_silently=False)
        logger.info("Sent new-device alert email to user %s", user.pk)
        return sent
    except Exception:  # noqa: BLE001 — email failure must not fail the task
        logger.exception("Failed to send new-device alert email to user %s", user.pk)
        return None


@shared_task
def send_new_device_alert_email(user_id: int, device_id: int) -> Dict[str, Any]:
    """
    Celery task: email a staff/privileged user that a new device signed in.

    Looks up the ``User`` and ``KnownDevice`` by id and delegates to
    :func:`send_new_device_alert`. A missing row (user or device deleted before
    the task runs) is swallowed with a logged warning and a ``{"sent": 0, ...}``
    result rather than raising, so a benign race never errors the task.
    """
    from .models import KnownDevice

    User = get_user_model()

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.warning("send_new_device_alert_email: user %s not found", user_id)
        return {"sent": 0, "reason": "user-not-found"}

    try:
        device = KnownDevice.objects.get(pk=device_id)
    except KnownDevice.DoesNotExist:
        logger.warning("send_new_device_alert_email: device %s not found", device_id)
        return {"sent": 0, "reason": "device-not-found"}

    sent = send_new_device_alert(user, device)
    return {"sent": sent or 0, "user_id": user_id, "device_id": device_id}
