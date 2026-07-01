"""Access-control interlock: badge scan → authorize → power + identified session.

op-vj9. ForgeKey firmware publishes a credential access-request to
``forgekey/<mac>/access/request``; this module is the OMS decision point. It
resolves the credential to a user and the scanning device to an asset, decides
whether that user may use that asset, and on a grant opens an identified
:class:`~forgekey.models.DeviceUsage` session, powers the relay, and drives
indicator feedback. On a refusal it powers nothing and audits the denial.

**Single source of truth.** :func:`is_authorized` is the one place that answers
"may this user use this asset right now"; the relay ``enable``/``disable``
endpoints reuse it to close the long-standing authorization bypass.

**Fail safe.** Every ambiguity — unknown card, unknown device, no asset
binding, an active lockout, maintenance mode, or a broker failure while
powering on — resolves to DENY. A relay is never enabled without a passing
authorization check, and an identified session is never left open if the relay
could not actually be powered.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any, Optional

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from ..audit import record_event
from ..models import (
    AssetAuthorization,
    AssetDevice,
    DeviceLockout,
    DeviceUsage,
    ESP32Device,
    ForgeKeyAuditEvent,
    OperationalMode,
    PowerMeterReading,
)
from . import badge_enrollment
from .device_commands import DeviceCommandError, dispatch_command
from .indicator import flash_deny_for_asset, sync_bindings_for_asset

logger = logging.getLogger(__name__)
User = get_user_model()

# Contract (matches firmware): forgekey/<mac>/access/request payload schema.
SCHEMA_VERSION = "forgekey.access_request.v1"

CREDENTIAL_BADGE = "badge"
CREDENTIAL_OTP = "otp"

# Decision outcomes returned by handle_access_request.
DECISION_GRANT = "grant"
DECISION_DENY = "deny"
DECISION_END = "end"
DECISION_ENROLLED = "enrolled"

# Deny reasons (also recorded in the audit row metadata).
REASON_UNKNOWN_DEVICE = "unknown_device"
REASON_NO_ASSET = "no_asset"
REASON_UNKNOWN_CARD = "unknown_card"
REASON_NOT_AUTHORIZED = "not_authorized"
REASON_IN_USE = "in_use"
REASON_RELAY_ERROR = "relay_error"
REASON_MALFORMED = "malformed"
REASON_ENROLL_USER_MISSING = "enroll_user_missing"
REASON_BADGE_IN_USE = "badge_in_use"

# Relay command verbs the firmware renders on the device command topic.
RELAY_ENABLE = "enable"
RELAY_DISABLE = "disable"

# Idle-session auto-end tuning. A metered session is idle when its last
# above-threshold current reading is older than IDLE_AFTER_MINUTES_DEFAULT; a
# session with no power meter falls back to the wall-clock cap only.
IDLE_AFTER_MINUTES_DEFAULT = 30
MAX_SESSION_HOURS_DEFAULT = 12
IDLE_CURRENT_THRESHOLD_A = Decimal("0.05")


@dataclass
class AccessDecision:
    """Outcome of a single access-request, returned for tests + the consumer."""

    decision: str
    reason: Optional[str] = None
    user: Optional[Any] = None
    asset: Optional[Any] = None
    device: Optional[Any] = None
    session: Optional[Any] = None

    @property
    def granted(self) -> bool:
        return self.decision == DECISION_GRANT

    @property
    def powered(self) -> bool:
        """True for the two outcomes that change relay state (grant/end)."""
        return self.decision in (DECISION_GRANT, DECISION_END)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------
def resolve_user(credential_type: Any, credential_id: Any):
    """Resolve a presented credential to a :class:`User`, or ``None``.

    Badge UIDs go through :meth:`User.from_badge`. The OTP path is deferred for
    v1 and intentionally resolves to ``None`` so an OTP scan fails safe (deny)
    until a verifier lands.
    """
    if not credential_id:
        return None
    ctype = (str(credential_type) if credential_type is not None else "").strip().lower()
    if ctype == CREDENTIAL_BADGE:
        return User.from_badge(credential_id)
    if ctype == CREDENTIAL_OTP:
        logger.info("OTP credential path not yet implemented; denying access request.")
        return None
    logger.warning("Unknown credential_type %r on access request; denying.", credential_type)
    return None


def asset_for_device(device) -> Optional[Any]:
    """The asset a scanning/relay device controls (prefer its primary binding)."""
    if device is None:
        return None
    binding = (
        AssetDevice.objects.filter(device=device)
        .select_related("asset")
        .order_by("-is_primary", "role")
        .first()
    )
    return binding.asset if binding is not None else None


def _primary_device_for_asset(asset) -> Optional[ESP32Device]:
    """The device whose relay powers an asset (prefer the primary binding)."""
    binding = (
        AssetDevice.objects.filter(asset=asset)
        .select_related("device")
        .order_by("-is_primary", "role")
        .first()
    )
    return binding.device if binding is not None else None


def is_authorized(user, asset) -> bool:
    """Single source of truth for "can this user use this asset right now".

    Returns ``True`` only when an active, unexpired :class:`AssetAuthorization`
    exists for the pair AND the asset has no active :class:`DeviceLockout` AND
    its :class:`OperationalMode` is neither maintenance nor locked-out. Any
    missing input or ambiguity returns ``False`` (fail safe).
    """
    if user is None or asset is None:
        return False
    if not getattr(user, "is_authenticated", False):
        return False
    auth = (
        AssetAuthorization.objects.filter(asset=asset, user=user, is_active=True)
        .only("is_active", "expires_at")
        .first()
    )
    if auth is None or not auth.is_currently_valid():
        return False
    if DeviceLockout.objects.filter(asset=asset, is_active=True).exists():
        return False
    mode = OperationalMode.objects.filter(asset=asset).only("mode").first()
    if mode is not None and mode.mode in (
        OperationalMode.MODE_MAINTENANCE,
        OperationalMode.MODE_LOCKED_OUT,
    ):
        return False
    return True


# ---------------------------------------------------------------------------
# Relay + indicator side effects
# ---------------------------------------------------------------------------
def _enable_relay(device, *, actor=None) -> None:
    """Power the relay. Raises :class:`DeviceCommandError` on broker failure."""
    dispatch_command(device, {"cmd": RELAY_ENABLE}, actor=actor, audit_action=RELAY_ENABLE)


def _disable_relay(device, *, actor=None) -> None:
    """Cut power. Raises :class:`DeviceCommandError` on broker failure."""
    dispatch_command(device, {"cmd": RELAY_DISABLE}, actor=actor, audit_action=RELAY_DISABLE)


def _sync_indicator(asset, *, actor=None) -> None:
    """Push the asset's freshly-derived status to its indicators (best effort)."""
    try:
        sync_bindings_for_asset(asset.id, actor=actor)
    except Exception:  # pragma: no cover - sync_bindings already swallows broker errors
        logger.exception("Indicator sync after access decision failed for asset %s", asset.id)


def _deny_feedback(asset, *, actor=None) -> None:
    """Red-blink the asset's indicators as deny feedback (best effort)."""
    try:
        flash_deny_for_asset(asset.id, actor=actor)
    except Exception:  # pragma: no cover - flash_deny already swallows broker errors
        logger.exception("Indicator deny-flash failed for asset %s", asset.id)


def _audit(
    action: str,
    *,
    actor=None,
    asset=None,
    device=None,
    reason: Optional[str] = None,
    credential_id: Any = None,
    notes: str = "",
    extra: Optional[dict] = None,
) -> None:
    metadata: dict = {}
    if reason:
        metadata["reason"] = reason
    if credential_id is not None:
        metadata["credential_id"] = str(credential_id)
    if extra:
        metadata.update(extra)
    record_event(
        action=action,
        actor=actor if (actor is not None and getattr(actor, "is_authenticated", False)) else None,
        asset=asset,
        device=device,
        notes=notes,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def handle_access_request(mac: str, payload: dict) -> AccessDecision:
    """Resolve one access-request to a grant / denial / session-end / enrollment.

    ``mac`` is the colon-form MAC of the scanning device (topic segment 2);
    ``payload`` is the decoded ``forgekey.access_request.v1`` body. Always
    returns an :class:`AccessDecision`; never raises for an expected refusal.
    """
    credential_type = payload.get("credential_type")
    credential_id = payload.get("credential_id")
    reader_id = payload.get("reader_id")

    device = ESP32Device.objects.filter(mac_address=mac).first()
    if device is None:
        logger.warning("Access request from unknown device MAC %s; denying.", mac)
        _audit(
            ForgeKeyAuditEvent.ACTION_ACCESS_DENIED,
            reason=REASON_UNKNOWN_DEVICE,
            credential_id=credential_id,
            notes="access denied: unknown device",
            extra={"mac": mac},
        )
        return AccessDecision(DECISION_DENY, REASON_UNKNOWN_DEVICE)

    asset = asset_for_device(device)
    if asset is None:
        logger.warning("Access request on device %s with no bound asset; denying.", mac)
        _audit(
            ForgeKeyAuditEvent.ACTION_ACCESS_DENIED,
            device=device,
            reason=REASON_NO_ASSET,
            credential_id=credential_id,
            notes="access denied: device not bound to an asset",
        )
        return AccessDecision(DECISION_DENY, REASON_NO_ASSET, device=device)

    # Enrollment interception: an armed "enroll next scan" captures this UID for
    # a chosen user instead of authorizing it.
    pending = badge_enrollment.consume(reader_id=reader_id, mac=mac)
    if pending is not None:
        return _enroll_badge(pending, credential_id=credential_id, asset=asset, device=device)

    user = resolve_user(credential_type, credential_id)
    if user is None:
        _deny_feedback(asset)
        _audit(
            ForgeKeyAuditEvent.ACTION_ACCESS_DENIED,
            asset=asset,
            device=device,
            reason=REASON_UNKNOWN_CARD,
            credential_id=credential_id,
            notes="access denied: unknown credential",
        )
        return AccessDecision(DECISION_DENY, REASON_UNKNOWN_CARD, asset=asset, device=device)

    open_session = (
        DeviceUsage.objects.filter(asset=asset, ended_at__isnull=True)
        .select_related("user")
        .order_by("-started_at")
        .first()
    )
    if open_session is not None:
        if open_session.user_id == user.id:
            return _end_session(open_session, user=user, asset=asset, device=device)
        # Someone else is using the tool — never hand it over mid-session.
        _deny_feedback(asset)
        _audit(
            ForgeKeyAuditEvent.ACTION_ACCESS_DENIED,
            actor=user,
            asset=asset,
            device=device,
            reason=REASON_IN_USE,
            credential_id=credential_id,
            notes="access denied: tool already in use by another user",
            extra={"holder_user_id": open_session.user_id},
        )
        return AccessDecision(
            DECISION_DENY,
            REASON_IN_USE,
            user=user,
            asset=asset,
            device=device,
            session=open_session,
        )

    if not is_authorized(user, asset):
        _deny_feedback(asset)
        _audit(
            ForgeKeyAuditEvent.ACTION_ACCESS_DENIED,
            actor=user,
            asset=asset,
            device=device,
            reason=REASON_NOT_AUTHORIZED,
            credential_id=credential_id,
            notes="access denied: user not authorized for asset",
        )
        return AccessDecision(
            DECISION_DENY, REASON_NOT_AUTHORIZED, user=user, asset=asset, device=device
        )

    return _grant(user=user, asset=asset, device=device, credential_id=credential_id)


def _grant(*, user, asset, device, credential_id=None) -> AccessDecision:
    """Open an identified session and power the relay; deny on broker failure."""
    session = DeviceUsage.objects.create(asset=asset, user=user)
    try:
        _enable_relay(device, actor=user)
    except DeviceCommandError as exc:
        # Fail safe: a session that isn't actually powered must not linger (it
        # would derive an IN_USE indicator and block the next scan). Roll back.
        session.delete()
        logger.error("Relay enable failed during grant for asset %s: %s", asset.id, exc)
        _audit(
            ForgeKeyAuditEvent.ACTION_ACCESS_DENIED,
            actor=user,
            asset=asset,
            device=device,
            reason=REASON_RELAY_ERROR,
            credential_id=credential_id,
            notes="access denied: relay enable failed",
        )
        return AccessDecision(
            DECISION_DENY, REASON_RELAY_ERROR, user=user, asset=asset, device=device
        )

    _sync_indicator(asset, actor=user)
    _audit(
        ForgeKeyAuditEvent.ACTION_ACCESS_GRANTED,
        actor=user,
        asset=asset,
        device=device,
        credential_id=credential_id,
        notes="access granted: session opened, relay enabled",
        extra={"session_id": str(session.id)},
    )
    return AccessDecision(DECISION_GRANT, user=user, asset=asset, device=device, session=session)


def _end_session(session, *, user, asset, device) -> AccessDecision:
    """End an open session on a same-user re-scan and cut power (best effort)."""
    session.end_session()
    try:
        _disable_relay(device, actor=user)
    except DeviceCommandError:
        logger.warning("Relay disable failed ending session for asset %s", asset.id)
    _sync_indicator(asset, actor=user)
    _audit(
        ForgeKeyAuditEvent.ACTION_SESSION_ENDED,
        actor=user,
        asset=asset,
        device=device,
        notes="session ended: same-user re-scan, relay disabled",
        extra={"session_id": str(session.id), "duration_seconds": session.duration_seconds},
    )
    return AccessDecision(DECISION_END, user=user, asset=asset, device=device, session=session)


def _enroll_badge(pending: dict, *, credential_id, asset, device) -> AccessDecision:
    """Bind a captured UID to the user an enrollment was armed for."""
    if not credential_id:
        return AccessDecision(DECISION_DENY, REASON_MALFORMED, asset=asset, device=device)
    badge = str(credential_id).strip()
    target = User.objects.filter(pk=pending.get("user_id")).first()
    if target is None:
        logger.warning(
            "Enrollment armed for missing user %r; ignoring scan.", pending.get("user_id")
        )
        return AccessDecision(DECISION_DENY, REASON_ENROLL_USER_MISSING, asset=asset, device=device)

    existing = User.from_badge(badge)
    if existing is not None and existing.pk != target.pk:
        # Don't silently steal a card from another member; surface the conflict.
        logger.warning(
            "Badge %s already enrolled to user %s; enrollment refused.", badge, existing.pk
        )
        _audit(
            ForgeKeyAuditEvent.ACTION_ACCESS_DENIED,
            actor=target,
            asset=asset,
            device=device,
            reason=REASON_BADGE_IN_USE,
            credential_id=badge,
            notes="enrollment refused: badge already assigned",
            extra={"conflict_user_id": existing.pk, "target_user_id": target.pk},
        )
        return AccessDecision(
            DECISION_DENY, REASON_BADGE_IN_USE, user=target, asset=asset, device=device
        )

    with transaction.atomic():
        target.badge_number = badge
        target.save(update_fields=["badge_number"])
    badge_enrollment.record_capture(target.pk, badge)
    _audit(
        ForgeKeyAuditEvent.ACTION_BADGE_ENROLLED,
        actor=target,
        asset=asset,
        device=device,
        credential_id=badge,
        notes="badge enrolled via reader scan",
        extra={"user_id": target.pk, "badge_number": badge},
    )
    logger.info("Enrolled badge %s to user %s via reader scan.", badge, target.pk)
    return AccessDecision(DECISION_ENROLLED, user=target, asset=asset, device=device)


# ---------------------------------------------------------------------------
# Idle-session reaper (Celery beat)
# ---------------------------------------------------------------------------
def _last_metered_activity(session) -> Optional[Any]:
    """Latest timestamp this session drew meaningful current, or ``None``.

    ``None`` distinguishes "no power meter on this asset" (rely on the
    wall-clock cap) from "metered but idle" (eligible for the short idle
    window).
    """
    has_readings = PowerMeterReading.objects.filter(usage_session=session).exists()
    if not has_readings:
        return None
    return (
        PowerMeterReading.objects.filter(
            usage_session=session, current__gte=IDLE_CURRENT_THRESHOLD_A
        )
        .order_by("-timestamp")
        .values_list("timestamp", flat=True)
        .first()
    ) or session.started_at


def end_idle_sessions(
    *,
    idle_after_minutes: int = IDLE_AFTER_MINUTES_DEFAULT,
    max_session_hours: int = MAX_SESSION_HOURS_DEFAULT,
    actor=None,
) -> int:
    """Close idle / runaway usage sessions, cut their power, reset indicators.

    A metered session is idle when its last above-threshold current reading is
    older than ``idle_after_minutes``; a session with no power meter only ends
    once it exceeds the ``max_session_hours`` wall-clock cap. Returns the number
    of sessions ended.
    """
    now = timezone.now()
    idle_cutoff = now - timedelta(minutes=idle_after_minutes)
    hard_cutoff = now - timedelta(hours=max_session_hours)

    ended = 0
    open_sessions = DeviceUsage.objects.filter(ended_at__isnull=True).select_related("asset")
    for session in open_sessions:
        last_metered = _last_metered_activity(session)
        idle = last_metered is not None and last_metered <= idle_cutoff
        runaway = session.started_at is not None and session.started_at <= hard_cutoff
        if not (idle or runaway):
            continue

        session.end_session()
        device = _primary_device_for_asset(session.asset)
        if device is not None:
            try:
                _disable_relay(device, actor=actor)
            except DeviceCommandError:
                logger.warning("Relay disable failed auto-ending session %s", session.id)
        _sync_indicator(session.asset, actor=actor)
        _audit(
            ForgeKeyAuditEvent.ACTION_SESSION_ENDED,
            actor=actor,
            asset=session.asset,
            device=device,
            reason="idle" if idle else "max_duration",
            notes="session auto-ended by idle reaper",
            extra={"session_id": str(session.id), "duration_seconds": session.duration_seconds},
        )
        ended += 1

    if ended:
        logger.info("Idle-session reaper ended %d session(s).", ended)
    return ended


__all__ = [
    "SCHEMA_VERSION",
    "CREDENTIAL_BADGE",
    "CREDENTIAL_OTP",
    "DECISION_GRANT",
    "DECISION_DENY",
    "DECISION_END",
    "DECISION_ENROLLED",
    "AccessDecision",
    "resolve_user",
    "asset_for_device",
    "is_authorized",
    "handle_access_request",
    "end_idle_sessions",
]
