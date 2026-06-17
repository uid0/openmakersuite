"""Account-security actions for the device-management API (FP3, oms-ltqs3).

The "this wasn't me" / revoke-all action invalidates **all** of a user's
sessions and tokens so the Django admin, the DRF browsable API, and the REST
API every force a fresh sign-in:

* **Django sessions** (db-backed; covers admin + browsable + session-auth REST)
  are decoded and deleted for the user.
* **JWTs** (access *and* refresh) are invalidated in one stroke by advancing
  ``User.tokens_valid_after``; the token classes in ``config.tokens`` reject any
  token issued before that cutoff. See that module for why this is preferred
  over the simple-JWT blacklist app.

Every action is recorded as an :class:`~notifications.models.AccountSecurityAuditEvent`.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from django.contrib.sessions.models import Session
from django.utils import timezone

from .models import AccountSecurityAuditEvent, KnownDevice


def _delete_sessions_for_user(user) -> int:
    """Delete every non-expired Django session that authenticates ``user``.

    Django stores no user index on the session table, so we scan the live
    (non-expired) rows and decode each. Returns the number deleted.
    """
    deleted = 0
    user_pk = str(user.pk)
    now = timezone.now()
    for session in Session.objects.filter(expire_date__gte=now):
        if session.get_decoded().get("_auth_user_id") == user_pk:
            session.delete()
            deleted += 1
    return deleted


def record_event(
    *,
    action: str,
    actor=None,
    ip_address: Optional[str] = None,
    user_agent: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> AccountSecurityAuditEvent:
    """Record an account-security audit event.

    ``actor`` is dropped to ``None`` (not anonymous) when it is not an
    authenticated user, mirroring the other audit helpers in the project.
    """
    return AccountSecurityAuditEvent.objects.create(
        action=action,
        actor=actor if (actor is not None and getattr(actor, "is_authenticated", False)) else None,
        ip_address=ip_address,
        user_agent=user_agent or "",
        metadata=metadata or {},
    )


def revoke_all_sessions_and_tokens(
    user,
    *,
    actor=None,
    ip_address: Optional[str] = None,
    user_agent: str = "",
) -> Dict[str, Any]:
    """Force a full re-auth for ``user`` everywhere and audit it.

    Deletes the user's Django sessions, advances ``tokens_valid_after`` so all
    outstanding JWTs are rejected, and records a ``revoke_all`` audit event.
    Returns a summary dict (``sessions_deleted``, ``tokens_valid_after``).
    """
    cutoff = timezone.now()
    sessions_deleted = _delete_sessions_for_user(user)

    user.tokens_valid_after = cutoff
    user.save(update_fields=["tokens_valid_after"])

    record_event(
        action=AccountSecurityAuditEvent.ACTION_REVOKE_ALL,
        actor=actor if actor is not None else user,
        ip_address=ip_address,
        user_agent=user_agent,
        metadata={
            "sessions_deleted": sessions_deleted,
            "tokens_valid_after": cutoff.isoformat(),
        },
    )
    return {"sessions_deleted": sessions_deleted, "tokens_valid_after": cutoff}


def record_device_forgotten(
    device: KnownDevice,
    *,
    actor=None,
    ip_address: Optional[str] = None,
    user_agent: str = "",
) -> AccountSecurityAuditEvent:
    """Audit the removal of a single known device (recorded before delete)."""
    return record_event(
        action=AccountSecurityAuditEvent.ACTION_FORGET_DEVICE,
        actor=actor,
        ip_address=ip_address,
        user_agent=user_agent,
        metadata={
            "device_id": device.pk,
            "device_user_agent": device.user_agent,
            "device_ip": device.ip_address,
        },
    )
