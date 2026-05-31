"""Access-decision + OTP lifecycle service for lockers (Phase 1+2).

The access decision is the heart of the locker pipeline: when a user
tries to retrieve an asset from a SIG-owned locker, we have to answer
two questions:

1. Are they *allowed* (staff bypass, SIG bypass, certified user, or
   none of the above → denied)?
2. If allowed, mint a short-TTL OTP that the keypad can verify
   without round-tripping every guess back to Django.

Both questions live here so callers (DRF endpoint, admin action,
shell tools) get one canonical decision path.

This module intentionally does NOT implement the *publish-MQTT-command*
side; that's Phase 3 in `lockers/services/commands.py`. Keeping access
decisions free of MQTT side effects means we can unit-test them
without a broker.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from django.contrib.auth.models import Group
from django.utils import timezone

from membership.models import SIGAdmin
from membership.utils import is_certified, is_logistics_member

from ..models import Locker, LockerOtp, _generate_otp_code


@dataclass(frozen=True)
class AccessDecision:
    """Outcome of `decide_locker_access`. Always carries a reason so the
    audit trail can record *why* a user was allowed or denied — not
    just the boolean.
    """

    allowed: bool
    reason: str  # "staff_bypass" | "sig_admin_bypass" | "logistics_bypass"
    # | "certified" | "missing_certification" | "locker_inactive"
    # | "anonymous" | "no_required_certs"
    missing_certifications: tuple[str, ...] = ()


def _is_staff_or_super(user) -> bool:
    return bool(user.is_staff or user.is_superuser)


def _is_sig_admin_for(user, sig: Group) -> bool:
    """True if `user` is a SIG admin of the locker's owning SIG."""
    if not user or not user.is_authenticated:
        return False
    return SIGAdmin.objects.filter(user=user, group=sig, is_active=True).exists()


def decide_locker_access(user, locker: Locker) -> AccessDecision:
    """Pure decision: should `user` be allowed to open `locker`?

    Order of bypass:
      1. Staff / superuser
      2. Logistics group member
      3. SIG admin of the locker's owning SIG
      4. Certified user (holds *every* required cert)
      5. No required certs configured → denied (locker not yet
         provisioned for self-serve access)

    Inactive lockers are denied regardless of role; an inactive locker
    isn't a security failure, but minting an OTP for one would just
    produce a spurious "keypad rejected" report.
    """
    if not user or not user.is_authenticated:
        return AccessDecision(allowed=False, reason="anonymous")

    if not locker.is_active:
        return AccessDecision(allowed=False, reason="locker_inactive")

    if _is_staff_or_super(user):
        return AccessDecision(allowed=True, reason="staff_bypass")

    if is_logistics_member(user):
        return AccessDecision(allowed=True, reason="logistics_bypass")

    if _is_sig_admin_for(user, locker.owning_sig):
        return AccessDecision(allowed=True, reason="sig_admin_bypass")

    required = list(locker.required_certifications.filter(is_active=True))
    if not required:
        # No SIG bypass + no required certs configured = no self-serve
        # access path. Operator must either grant SIG-admin status or
        # add at least one required cert.
        return AccessDecision(allowed=False, reason="no_required_certs")

    missing = tuple(c.name for c in required if not is_certified(user, c))
    if missing:
        return AccessDecision(
            allowed=False,
            reason="missing_certification",
            missing_certifications=missing,
        )

    return AccessDecision(allowed=True, reason="certified")


def can_user_access_locker(user, locker: Locker) -> bool:
    """Boolean shortcut over `decide_locker_access` for callers that
    don't need the structured reason.
    """
    return decide_locker_access(user, locker).allowed


def can_user_manage_locker(user, locker: Locker) -> bool:
    """True if the user is a *manager* of the locker — staff / superuser,
    a logistics member, or an admin of the owning SIG — as opposed to a
    member who merely has access. Gates OTP administration (list / revoke)
    and other operator-only surfaces.
    """
    if not user or not user.is_authenticated:
        return False
    if _is_staff_or_super(user):
        return True
    if is_logistics_member(user):
        return True
    return _is_sig_admin_for(user, locker.owning_sig)


# ---------------------------------------------------------------------------
# OTP lifecycle
# ---------------------------------------------------------------------------


class OtpDenied(RuntimeError):
    """Raised by `generate_otp` when the user isn't allowed access.

    Carries the structured `AccessDecision` so callers can surface the
    same reason to the user / audit log without re-running the check.
    """

    def __init__(self, decision: AccessDecision) -> None:
        self.decision = decision
        super().__init__(f"Access denied: {decision.reason}")


def generate_otp(
    *,
    user,
    locker: Locker,
    digits: int = 6,
    ttl: Optional[timedelta] = None,
) -> LockerOtp:
    """Run the access check + mint a fresh OTP.

    Raises ``OtpDenied`` when the user isn't allowed. Otherwise returns
    a saved ``LockerOtp`` with a unique-per-locker code that expires at
    ``now + ttl`` (default 60 min per spec).

    Re-runs code generation on collision (the unique partial-index on
    `(locker, code)` for active OTPs may reject a duplicate). Bounded
    retry — after 8 attempts the digit space is too small for the
    locker's pending-OTP load and we surface the conflict.
    """
    decision = decide_locker_access(user, locker)
    if not decision.allowed:
        raise OtpDenied(decision)

    expires_at = timezone.now() + (ttl or LockerOtp.DEFAULT_TTL)

    last_exc: Optional[Exception] = None
    for _ in range(8):
        try:
            return LockerOtp.objects.create(
                locker=locker,
                requesting_user=user,
                code=_generate_otp_code(digits),
                expires_at=expires_at,
            )
        except Exception as exc:  # noqa: BLE001 — IntegrityError is the target
            last_exc = exc
            continue
    # If we got here, the digit space is too narrow. Caller should
    # bump `digits` or revoke stale codes.
    raise RuntimeError(
        "Could not mint a unique OTP for this locker after 8 attempts; "
        "increase digit count or revoke stale codes."
    ) from last_exc


def redeem_otp(*, locker: Locker, code: str) -> Optional[LockerOtp]:
    """Find a redeemable OTP for `(locker, code)`, mark it used, and
    return it. Returns None if no active OTP matches — callers should
    treat that as a denied attempt and log accordingly.

    Race: two concurrent redeems would both pass `is_redeemable` then
    both call `mark_used`. The second update is a no-op due to the
    `if used_at is not None: return` guard, so both callers think they
    succeeded. For locker hardware this is actually the safe outcome
    (the user already typed the right code; they get one unlock).
    Phase 4's audit layer logs both attempts.
    """
    candidate = (
        LockerOtp.objects.filter(
            locker=locker,
            code=code,
            used_at__isnull=True,
            revoked_at__isnull=True,
            expires_at__gt=timezone.now(),
        )
        .order_by("-created_at")
        .first()
    )
    if candidate is None:
        return None
    candidate.mark_used()
    return candidate
