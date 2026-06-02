"""Wave advancement for EpaperFirmwareRollout.

The ePaper OTA model is HTTPS-pull, not MQTT-push: the rollout doesn't
dispatch outbound, it stages a target on the ``EPaperDisplay`` row that
the next firmware-check call from the panel will pick up. A wave is
therefore a *promotion* of the next batch of panels into the eligible
pool, not an outbound publish.

Kept in a service module (not on the model) so the Celery beat task,
the admin "advance now" action, and tests all go through the same code
path.
"""

from __future__ import annotations

import logging
import math

from django.utils import timezone

from ..models import EPaperDisplay, EpaperFirmwareRollout

logger = logging.getLogger(__name__)


def _remaining_displays(rollout: EpaperFirmwareRollout):
    """Active panels that haven't been promoted to this rollout's target yet.

    "Not promoted" means either ``target_firmware_version`` is null, or
    points at a different version. Panels already on the target are
    excluded — picking them up again would just be a no-op since the
    check endpoint compares ``firmware_version`` (reported) to the
    target's version.
    """
    fv = rollout.firmware_version
    return rollout.target_displays().exclude(target_firmware_version=fv).order_by("id")


def advance_rollout(rollout: EpaperFirmwareRollout) -> int:
    """Promote the next wave of an ACTIVE rollout. Returns the number of
    panels promoted.

    A wave is ``ceil(target_total * batch_size_percent / 100)`` panels (at
    least 1). The rollout flips to COMPLETED when no untargeted panels
    remain (or when the entire fleet was already on the target before
    the rollout's first advance, the rare "noop rollout" case).
    """
    if rollout.status != EpaperFirmwareRollout.STATUS_ACTIVE:
        return 0

    target_total = rollout.target_displays().count()
    remaining = list(_remaining_displays(rollout))
    if not remaining:
        rollout.status = EpaperFirmwareRollout.STATUS_COMPLETED
        rollout.completed_at = timezone.now()
        rollout.save(update_fields=["status", "completed_at", "updated_at"])
        return 0

    batch_count = max(1, math.ceil(target_total * rollout.batch_size_percent / 100))
    wave = remaining[:batch_count]

    EPaperDisplay.objects.filter(pk__in=[d.pk for d in wave]).update(
        target_firmware_version=rollout.firmware_version,
        updated_at=timezone.now(),
    )

    rollout.last_advanced_at = timezone.now()
    update_fields = ["last_advanced_at", "updated_at"]
    if len(wave) >= len(remaining):
        rollout.status = EpaperFirmwareRollout.STATUS_COMPLETED
        rollout.completed_at = timezone.now()
        update_fields += ["status", "completed_at"]
    rollout.save(update_fields=update_fields)

    logger.info(
        "epaper rollout %s: promoted %d/%d panel(s) to %s (remaining=%d)",
        rollout.id,
        len(wave),
        target_total,
        rollout.firmware_version.version,
        max(0, len(remaining) - len(wave)),
    )
    return len(wave)
