"""Fiducial-marker (AprilTag) registry.

This app owns the *global* allocation of AprilTag IDs to records anywhere
in the system. The two printed artifacts that carry an AprilTag today —
project-storage claim receipts and personal-storage business cards — each
allocate from the *same* pool, because a single camera scanning the space
will see both kinds of label and a detected tag ID must resolve to exactly
one physical record.

Why a generic registry instead of a column on each model
--------------------------------------------------------

A detected tag ID is meaningless without a back-reference to *which* record
it labels, and that record can live in any app. Rather than add a
per-model ``april_tag_id`` column (which would force the ID space to be
partitioned per model and make a global "is this ID free?" check impossible)
we keep one table that points at the subject record via a
:class:`~django.contrib.contenttypes.fields.GenericForeignKey`. This mirrors
how the rest of this codebase avoids hard cross-app FKs (e.g. project_storage
stores a location *name*, not an ``inventory.Location`` FK).

ID reuse / retirement
---------------------

AprilTag families have a *finite* ID space (tag36h11 = 587 IDs). A makerspace
cycles many items through storage, so IDs must recycle: when a record reaches
a terminal state (a stint is removed, a maker box is destroyed) the caller
calls :func:`fiducials.services.allocator.release_tag`, which stamps
``released_at`` and returns the ID to the pool. Uniqueness is therefore
enforced only over *active* (un-released) allocations — exactly the
partial-unique-when-active pattern already used by
``VisionSlot.unique_active_vision_slot_marker`` and
``MakerBox.maker_box_bin_id_unique_when_set``.
"""

from __future__ import annotations

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone

# AprilTag family identifiers. These are config values (stored per row) so
# the system can run more than one family at once and so a future migration
# to a larger family doesn't require a schema change.
FAMILY_TAG36H11 = "tag36h11"
FAMILY_TAG36H10 = "tag36h10"

# Number of distinct marker IDs each family encodes. tag36h11 is the
# confirmed default. tag36h10 is kept as a drop-in *larger* fallback — it
# is the SAME cv2.aruco call (DICT_APRILTAG_36h10) with ~4x the headroom,
# trading a little Hamming distance / detection robustness for capacity.
# (For reference, tag25h9 = 35 and tagStandard41h12 = 2115; only the two
# below are wired into the renderer.)
FAMILY_CAPACITY = {
    FAMILY_TAG36H11: 587,
    FAMILY_TAG36H10: 2320,
}

FAMILY_CHOICES = [
    (FAMILY_TAG36H11, "AprilTag 36h11 (587 IDs)"),
    (FAMILY_TAG36H10, "AprilTag 36h10 (2320 IDs)"),
]

# The family every allocation uses unless a caller overrides it. Ian
# confirmed tag36h11 for op-e9w; switching the default to tag36h10 is a
# one-line change here once the long-lived maker-box population grows
# enough to threaten the 587-ID ceiling (see the PR description).
DEFAULT_FAMILY = FAMILY_TAG36H11


class AprilTagAssignment(models.Model):
    """One allocation of an AprilTag ID to a record (a "subject").

    ``released_at is None`` means the allocation is *active* — the tag is
    physically on a label out in the space and the ID is taken. Stamping
    ``released_at`` retires the allocation and frees the ID for reuse.
    """

    family = models.CharField(
        max_length=32,
        choices=FAMILY_CHOICES,
        default=DEFAULT_FAMILY,
        help_text="AprilTag dictionary this ID is drawn from.",
    )
    # 0 .. FAMILY_CAPACITY[family] - 1. SmallInteger is plenty (max family
    # capacity wired here is 2320, well under 32767).
    tag_id = models.PositiveSmallIntegerField()

    # Generic link to the subject record (a ProjectStorageStint, a MakerBox,
    # or any future record type). object_id is BigInteger to match the
    # project's BigAutoField default PK.
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()
    subject = GenericForeignKey("content_type", "object_id")

    allocated_at = models.DateTimeField(default=timezone.now)
    # NULL == active. Stamped by release_tag() when the subject reaches a
    # terminal state, which returns the ID to the pool.
    released_at = models.DateTimeField(null=True, blank=True)

    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["family", "tag_id"]
        verbose_name = "AprilTag assignment"
        verbose_name_plural = "AprilTag assignments"
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
            # Drives the "which IDs are currently taken in this family?"
            # query the allocator runs on every allocation.
            models.Index(fields=["family", "released_at"]),
        ]
        constraints = [
            # An ID is unique only among *active* allocations of its family;
            # once released it can be handed out again.
            models.UniqueConstraint(
                fields=["family", "tag_id"],
                condition=models.Q(released_at__isnull=True),
                name="unique_active_apriltag_per_family",
            ),
            # A record carries at most one *active* tag at a time.
            models.UniqueConstraint(
                fields=["content_type", "object_id"],
                condition=models.Q(released_at__isnull=True),
                name="unique_active_apriltag_per_subject",
            ),
        ]

    def __str__(self) -> str:
        state = "active" if self.released_at is None else "released"
        return f"{self.family}#{self.tag_id} -> {self.content_type}:{self.object_id} ({state})"

    @property
    def is_active(self) -> bool:
        return self.released_at is None
