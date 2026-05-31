"""Staged firmware rollout advancement.

A :class:`~forgekey.models.FirmwareRollout` targets a ``FirmwareVersion`` at
every active device of its device type and dispatches the OTA in waves of
``batch_size_percent`` of the target fleet, no more often than
``interval_minutes``. The advancement here is reused by both the Celery-beat
driver and the operator "advance now" action.
"""

from __future__ import annotations

import logging
import math

from django.utils import timezone

from ..models import DeviceFirmwareUpdate, FirmwareRollout
from .ota_dispatch import OTADispatchError, publish_ota_trigger

logger = logging.getLogger(__name__)


def rollout_progress(rollout: FirmwareRollout) -> dict:
    """Return device counts for a rollout: total / on-target / in-flight / remaining."""
    target = rollout.target_devices()
    total = target.count()
    version = rollout.firmware_version.version
    on_target = target.filter(firmware_version=version).count()

    updates = DeviceFirmwareUpdate.objects.filter(rollout=rollout)
    pending = updates.filter(status=DeviceFirmwareUpdate.STATUS_PENDING).count()
    in_progress = updates.filter(status=DeviceFirmwareUpdate.STATUS_IN_PROGRESS).count()
    failed = updates.filter(status=DeviceFirmwareUpdate.STATUS_FAILED).count()

    dispatched_ids = set(updates.values_list("device_id", flat=True))
    remaining = target.exclude(firmware_version=version).exclude(id__in=dispatched_ids).count()

    return {
        "total": total,
        "on_target": on_target,
        "pending": pending,
        "in_progress": in_progress,
        "failed": failed,
        "remaining": remaining,
    }


def _remaining_devices(rollout: FirmwareRollout) -> list:
    """Target devices not yet on the version and not yet dispatched by this rollout."""
    version = rollout.firmware_version.version
    dispatched_ids = set(
        DeviceFirmwareUpdate.objects.filter(rollout=rollout).values_list("device_id", flat=True)
    )
    return list(
        rollout.target_devices()
        .exclude(firmware_version=version)
        .exclude(id__in=dispatched_ids)
        .order_by("mac_address")
    )


def advance_rollout(rollout: FirmwareRollout, *, actor=None, client=None) -> int:
    """Dispatch the next wave of an ACTIVE rollout. Returns the device count dispatched.

    A wave is ``ceil(target_total * batch_size_percent / 100)`` devices (at
    least 1). Broker failures on a single device are logged but still count —
    ``publish_ota_trigger`` writes the ``DeviceFirmwareUpdate`` row before
    publishing, so the device won't be re-picked next wave. The rollout flips to
    COMPLETED when no target devices remain.
    """
    if rollout.status != FirmwareRollout.STATUS_ACTIVE:
        return 0

    target_total = rollout.target_devices().count()
    remaining = _remaining_devices(rollout)
    if not remaining:
        rollout.status = FirmwareRollout.STATUS_COMPLETED
        rollout.completed_at = timezone.now()
        rollout.save(update_fields=["status", "completed_at", "updated_at"])
        return 0

    batch_count = max(1, math.ceil(target_total * rollout.batch_size_percent / 100))
    wave = remaining[:batch_count]
    firmware = rollout.firmware_version

    dispatched = 0
    for device in wave:
        try:
            publish_ota_trigger(
                device, firmware, requested_by=actor, client=client, rollout=rollout
            )
        except OTADispatchError as exc:
            logger.warning(
                "Rollout %s: OTA dispatch failed for %s: %s", rollout.id, device.mac_address, exc
            )
        # Either way the update row exists (rollout-linked), so the device is
        # dispatched as far as the wave accounting is concerned.
        dispatched += 1

    rollout.last_advanced_at = timezone.now()
    update_fields = ["last_advanced_at", "updated_at"]
    if len(wave) >= len(remaining):
        rollout.status = FirmwareRollout.STATUS_COMPLETED
        rollout.completed_at = timezone.now()
        update_fields += ["status", "completed_at"]
    rollout.save(update_fields=update_fields)
    return dispatched
