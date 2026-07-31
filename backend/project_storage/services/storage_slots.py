"""Storage-slot lifecycle: AprilTag allocation + the bulk rack generator.

Why the tag work lives here and not in ``StorageSlot.save()``: allocation
touches another app's table and can fail (finite pool), which is exactly the
kind of side-effect this codebase keeps out of ``save()``. Every creation
path — the API viewset, the management command, the Django admin — calls
:func:`ensure_slot_tag`, so a slot never exists for long without its tag.

Tag family
----------

Slot tags come from :data:`SLOT_TAG_FAMILY` (tag36h10, 2320 IDs), *not* the
tag36h11 pool that stints and maker boxes draw from. Those two recycle IDs
constantly as items come and go; racking is permanent and would slowly eat
that pool and never give anything back. Separate family = the two lifetimes
can't starve each other, and a detected ID is unambiguous because the family
is part of the identity.

Slot tags are allocated once and **never released** — the ID printed on the
rack upright has to keep meaning the same physical place for as long as the
rack stands.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable, Optional

from django.db import transaction

from fiducials.models import FAMILY_TAG36H10, AprilTagAssignment
from fiducials.services.allocator import AprilTagPoolExhausted, allocate_tag

from ..models import StorageSlot

logger = logging.getLogger(__name__)

# Dedicated, isolated pool for permanent location markers.
SLOT_TAG_FAMILY = FAMILY_TAG36H10


def ensure_slot_tag(slot: StorageSlot) -> Optional[AprilTagAssignment]:
    """Idempotently give ``slot`` its permanent AprilTag.

    Returns the assignment, or None when the family is exhausted — a full
    pool degrades (the slot still exists and is usable by code) rather than
    failing the create, mirroring how the kiosk handles an exhausted stint
    pool. The loud log is the operator's cue to bump the family.
    """
    try:
        return allocate_tag(slot, family=SLOT_TAG_FAMILY)
    except AprilTagPoolExhausted:
        logger.error(
            "AprilTag family %s exhausted; storage slot %s created without a marker.",
            SLOT_TAG_FAMILY,
            slot.code,
        )
        return None


@dataclass
class LevelSpec:
    """One level of a rack to fill: ``positions`` slots on ``level``."""

    level: str
    positions: int
    requires_pallet_jack: bool = False


@dataclass
class GenerateResult:
    """Outcome of one :func:`generate_rack_slots` run."""

    rack: int
    created: list[StorageSlot] = field(default_factory=list)
    skipped: list[StorageSlot] = field(default_factory=list)
    # Slots that ended the run with no marker because the pool ran dry.
    without_tag: list[StorageSlot] = field(default_factory=list)

    @property
    def slots(self) -> list[StorageSlot]:
        """Every slot the run touched, in code order."""
        return sorted(
            self.created + self.skipped,
            key=lambda s: (s.rack, s.level, s.position),
        )


def generate_rack_slots(
    *,
    rack: int,
    levels: Iterable[LevelSpec],
    owning_group=None,
    notes: str = "",
) -> GenerateResult:
    """Create every slot of ``rack`` from a per-level spec. Safe to re-run.

    For each level, positions are numbered ``1..positions``. Codes that
    already exist are left exactly as they are (an operator may have edited
    a slot's group or reachability by hand) and reported as skipped.

    Re-running also re-checks the tag on *skipped* slots, so a run that hit
    an exhausted pool — or an admin-created slot that never got one — heals
    on the next pass instead of needing a manual fixup.
    """
    result = GenerateResult(rack=rack)

    for spec in levels:
        level = (spec.level or "").upper()
        for position in range(1, spec.positions + 1):
            code = StorageSlot.compose_code(rack, level, position)
            with transaction.atomic():
                slot, created = StorageSlot.objects.get_or_create(
                    rack=rack,
                    level=level,
                    position=position,
                    defaults={
                        "code": code,
                        "requires_pallet_jack": spec.requires_pallet_jack,
                        "owning_group": owning_group,
                        "notes": notes,
                    },
                )
            (result.created if created else result.skipped).append(slot)
            if ensure_slot_tag(slot) is None:
                result.without_tag.append(slot)

    return result
