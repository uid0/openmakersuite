"""Celery beat tasks for locker lifecycle (Phase 5).

Three jobs:

1. ``invalidate_expired_otps`` — periodic janitor that closes out OTPs
   whose `expires_at` is in the past. Idempotent: re-running is a
   no-op once the row is marked.
2. ``flag_propped_doors`` — scans for OPEN access events that haven't
   been followed by a SECURED within the configured threshold. Writes
   an INCOMPLETE row so the operator dashboard can surface "Locker
   X has been propped open for >N minutes" without re-walking the
   reed-state history every render.
3. ``clear_locker_lockout`` — admin-only entry point that restores a
   locked-out locker. Verifies authority at the call site (caller is
   responsible for routing through Django admin or a permission-checked
   DRF action) and publishes a ``clear_lockout`` MQTT command.

The tasks deliberately use the same `redact()` already configured for
TaskResult.traceback so failure tracebacks for these don't leak the
device MAC + JWT payload through to the admin TaskResult view.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from celery import shared_task

from .models import (
    Locker,
    LockerAccessEvent,
    LockerAccessEventKind,
    LockerOtp,
)

logger = logging.getLogger(__name__)


# Default thresholds. Operator can override via Django settings without
# editing this file (matches the FORGEKEY_PHOTO_RETENTION_DAYS pattern).
DEFAULT_PROPPED_DOOR_THRESHOLD = timedelta(minutes=5)
PROPPED_DOOR_SETTING = "LOCKERS_PROPPED_DOOR_THRESHOLD_SECONDS"


def _propped_threshold() -> timedelta:
    secs = getattr(settings, PROPPED_DOOR_SETTING, None)
    if not secs:
        return DEFAULT_PROPPED_DOOR_THRESHOLD
    return timedelta(seconds=int(secs))


@shared_task(name="lockers.tasks.invalidate_expired_otps")
def invalidate_expired_otps() -> dict[str, Any]:
    """Mark every active OTP whose `expires_at` is in the past as expired.

    The state-machine is implicit (`expires_at < now()` is enough to
    make `is_redeemable` return False), but persisting an explicit
    `revoked_at` keeps the partial-unique-index small and lets
    `LockerOtp.state` differentiate "expired" from "revoked" cleanly.

    Returns a small dict for the TaskResult view: number of rows
    flipped + a sample of locker slugs.
    """
    cutoff = timezone.now()
    qs = LockerOtp.objects.filter(
        expires_at__lt=cutoff,
        used_at__isnull=True,
        revoked_at__isnull=True,
    )
    sample = list(qs.values_list("locker__slug", flat=True)[:5])
    count = qs.update(revoked_at=cutoff)
    if count:
        logger.info(
            "Locker OTP janitor invalidated %d expired OTPs (sample: %s)",
            count,
            sample,
        )
    return {"invalidated": count, "sample": sample}


@shared_task(name="lockers.tasks.flag_propped_doors")
def flag_propped_doors() -> dict[str, Any]:
    """Find OPEN events that haven't been followed by a SECURED within
    the propped-door threshold and persist an INCOMPLETE row.

    The job iterates over each Locker that has had recent activity to
    bound the cost — we intentionally do not scan the entire
    LockerAccessEvent table on every tick.
    """
    cutoff = timezone.now() - _propped_threshold()
    flagged = 0
    affected: list[str] = []

    # Lockers with at least one OPEN event before the cutoff are the
    # only ones that could possibly be propped.
    candidate_ids = (
        LockerAccessEvent.objects.filter(
            kind=LockerAccessEventKind.OPEN,
            occurred_at__lt=cutoff,
        )
        .values_list("locker_id", flat=True)
        .distinct()
    )

    for locker_id in candidate_ids:
        with transaction.atomic():
            last_open = (
                LockerAccessEvent.objects.filter(
                    locker_id=locker_id,
                    kind=LockerAccessEventKind.OPEN,
                )
                .order_by("-occurred_at")
                .first()
            )
            if last_open is None or last_open.occurred_at >= cutoff:
                continue
            # Already had SECURED, INCOMPLETE, or LOCKOUT_CLEARED since
            # the open?  Then no action needed.
            resolution = (
                LockerAccessEvent.objects.filter(
                    locker_id=locker_id,
                    occurred_at__gt=last_open.occurred_at,
                    kind__in=(
                        LockerAccessEventKind.SECURED,
                        LockerAccessEventKind.INCOMPLETE,
                        LockerAccessEventKind.LOCKOUT_CLEARED,
                    ),
                )
                .order_by("-occurred_at")
                .first()
            )
            if resolution is not None:
                continue
            duration = (timezone.now() - last_open.occurred_at).total_seconds()
            LockerAccessEvent.objects.create(
                locker_id=locker_id,
                kind=LockerAccessEventKind.INCOMPLETE,
                actor=last_open.actor,
                asset=last_open.asset,
                metadata={
                    "open_event_id": str(last_open.id),
                    "duration_seconds": duration,
                },
            )
            flagged += 1
            affected.append(str(locker_id))

    if flagged:
        logger.warning(
            "Propped-door janitor flagged %d lockers as INCOMPLETE: %s",
            flagged,
            affected,
        )
    return {"flagged": flagged, "lockers": affected}


@shared_task(name="lockers.tasks.clear_locker_lockout")
def clear_locker_lockout(*, locker_id: str, actor_id: int | None = None) -> dict[str, Any]:
    """Publish a `clear_lockout` MQTT command for the given locker.

    Authorisation is enforced at the call site — this task assumes the
    caller (admin action / DRF view) has already verified the user is
    permitted to clear lockouts. Logged via the same audit channel as
    every other locker command.

    Returns the topic + the LockerAccessEvent.id for the LOCKOUT_CLEARED
    audit row written on success.
    """
    from .services.commands import NoSuchLockerDevice, publish_clear_lockout

    locker = Locker.objects.get(pk=locker_id)
    actor = None
    if actor_id is not None:
        from django.contrib.auth import get_user_model

        actor = get_user_model().objects.filter(pk=actor_id).first()

    try:
        topic = publish_clear_lockout(locker=locker, actor=actor)
    except NoSuchLockerDevice as exc:
        logger.error("clear_locker_lockout: %s", exc)
        raise

    event = LockerAccessEvent.objects.create(
        locker=locker,
        kind=LockerAccessEventKind.LOCKOUT_CLEARED,
        actor=actor,
        asset=locker.current_asset,
        metadata={"task": "clear_locker_lockout"},
    )
    return {"topic": topic, "event_id": str(event.id)}
