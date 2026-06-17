"""
Known-device login tracking + new-device alerting (oms-bwcdb, FP1).

Hooks into the unified login/register chokepoint
(``auth_views._issue_session_and_tokens``) to:

* identify the signing-in browser via an opaque, server-minted device token
  persisted in a Django-signed, httpOnly cookie (:data:`DEVICE_COOKIE_NAME`);
* record the device against the user
  (:class:`notifications.models.KnownDevice`);
* raise an informational in-app alert the first time a *staff/privileged*
  account signs in from a device we have not seen before.

Scope (FP1): staff/superuser accounts only, in-app notification only. Email
(FP2), revoke / "this wasn't me" (FP3), and the JS fingerprint + device UI
(FP4) build on top of this.

Every public entry point is defensive: device tracking must NEVER break a
login. :func:`track_device_login` swallows and reports all exceptions so the
caller's auth flow is unaffected.
"""

from __future__ import annotations

import logging
import secrets

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.utils import timezone

from .models import KnownDevice
from .services import create_notification
from .tasks import send_new_device_alert_email

logger = logging.getLogger(__name__)

# Signed, httpOnly cookie carrying the opaque device token. Long-lived so a
# returning device stays "known" across sessions; SameSite=Lax + Secure (when
# not DEBUG) mirror the project's session-cookie posture.
DEVICE_COOKIE_NAME = "oms_device_id"
DEVICE_COOKIE_SALT = "notifications.device_login"
DEVICE_COOKIE_MAX_AGE = 365 * 24 * 60 * 60  # ~1 year, in seconds

# Defensive cap so a hostile/oversized User-Agent header can't bloat the alert
# message. The full value is still stored on the KnownDevice row (TextField).
_UA_MESSAGE_MAX = 256


def get_client_ip(request):
    """
    Best-effort client IP: first hop of ``X-Forwarded-For`` if present, else
    ``REMOTE_ADDR``. Mirrors the extractor in ``inventory.views`` so the alert
    reports the same address operators see elsewhere.
    """
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _mint_device_token():
    """Return a fresh opaque, URL-safe device token."""
    return secrets.token_urlsafe(32)


def _read_device_token(request):
    """
    Return the validated device token from the signed cookie, or ``None`` if
    absent, tampered, or expired. Never raises.
    """
    http_request = getattr(request, "_request", request)
    try:
        return http_request.get_signed_cookie(
            DEVICE_COOKIE_NAME,
            default=None,
            salt=DEVICE_COOKIE_SALT,
            max_age=DEVICE_COOKIE_MAX_AGE,
        )
    except signing.BadSignature:
        # Covers SignatureExpired (a BadSignature subclass) too. A bad/expired
        # cookie is treated as no cookie: a new device.
        return None


def _notify_new_device(user, ip_address, user_agent, device_token):
    """Raise the informational in-app new-device alert for ``user``."""
    seen_at = timezone.now().strftime("%Y-%m-%d %H:%M UTC")
    ua_summary = (user_agent or "an unrecognized browser")[:_UA_MESSAGE_MAX]
    where = ip_address or "an unknown location"
    message = (
        f"A new sign-in to your account was detected from {ua_summary} "
        f"at approximately {where} on {seen_at}. If this was you, no action "
        f"is needed. If you do not recognize this sign-in, secure your account."
    )
    create_notification(
        user=user,
        type="warning",
        title="New device sign-in",
        message=message,
        action_url="/account/devices",
        metadata={
            "ip": ip_address,
            "ua": user_agent,
            "device_token": device_token,
        },
    )


def track_device_login(request, user):
    """
    Record the signing-in device for staff/privileged ``user`` and alert on a
    new device. Returns the device token to (re)persist on the response cookie,
    or ``None`` when no cookie should be set (non-privileged user, or tracking
    failed).

    Defensive by contract: any error is logged and swallowed so a failure here
    can never block the login it is observing.
    """
    try:
        if not (user.is_staff or user.is_superuser):
            # FP1 is scoped to staff/privileged accounts only.
            return None

        token = _read_device_token(request)
        ip_address = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if token:
            device = KnownDevice.objects.filter(user=user, device_token=token).first()
            if device is not None:
                # Known device: refresh last-seen telemetry, no alert.
                device.ip_address = ip_address
                device.user_agent = user_agent
                device.save(update_fields=["ip_address", "user_agent", "last_seen"])
                return token

        # No cookie, or a cookie whose token we have never seen for this user:
        # treat as a NEW device. Reuse the presented token when available so a
        # shared browser is not issued a fresh cookie on every account.
        device_token = token or _mint_device_token()
        device = KnownDevice.objects.create(
            user=user,
            device_token=device_token,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        _notify_new_device(user, ip_address, user_agent, device_token)
        # Email companion to the in-app alert (oms-1crmp, FP2). Defer the
        # dispatch to on_commit so the worker never races the device row it
        # looks up; in request autocommit (no ATOMIC_REQUESTS) it fires at once.
        transaction.on_commit(lambda: send_new_device_alert_email.delay(user.id, device.id))
        return device_token
    except Exception:  # never let device tracking break a login
        logger.exception(
            "Known-device tracking failed for user_id=%s",
            getattr(user, "pk", None),
        )
        return None


def set_device_cookie(response, token):
    """
    Persist ``token`` as the signed, httpOnly device cookie on ``response``.
    No-op when ``token`` is falsy. Returns ``response`` for chaining.
    """
    if not token:
        return response
    response.set_signed_cookie(
        DEVICE_COOKIE_NAME,
        token,
        salt=DEVICE_COOKIE_SALT,
        max_age=DEVICE_COOKIE_MAX_AGE,
        httponly=True,
        secure=not settings.DEBUG,
        samesite="Lax",
    )
    return response
