"""Storage Vision data model — phase 1.

Five tables that together cover the .criteria/storage-vision-supply-reorder.md
spec's data needs. Views/serializers/URLs and the Celery pipeline land in
follow-up PRs; this PR establishes the shape + constraints so the rest of
the work can be reviewed in slices without one big-bang database migration.

Lineage:

    VisionArea ───┬── VisionSlot (1 area : N marker-backed slots)
                  ├── VisionCamera (1 area : 0..N fixed cameras)
                  └── VisionCapture ── VisionObservation (1 capture : 0..N obs)
                                       (each obs ties back to a Slot)

Notes:

- ``VisionCamera.token`` is stored hashed; the raw bearer is returned in
  the create / rotate API response exactly once (AC-7). The plaintext
  fingerprint (sha256 truncated) is safe to expose in list/retrieve.
- ``VisionCapture.markers_detected`` is a JSON list of every QR marker
  the processor read, including ones that don't map to a Slot (AC-14
  + AC-15). Unmatched markers stay here rather than getting their own
  table — they're inspection-only data, not actionable rows.
- ``VisionObservation.status`` is the single source of truth for the
  review queue. Transitions are guarded in the viewset (AC-23 idempotent
  approve) so the model layer stays simple.
"""

from __future__ import annotations

import hashlib
import secrets
from decimal import Decimal
from typing import Optional

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


def _generate_camera_token() -> str:
    """Raw bearer used by a fixed camera to authenticate uploads.

    32 url-safe bytes = ~43 chars. The matching helper
    :func:`_fingerprint_token` hashes the bytes for storage.
    """
    return secrets.token_urlsafe(32)


def _fingerprint_token(raw: str) -> str:
    """Stable, public-safe fingerprint of a token.

    Returns the first 16 hex chars of sha256(raw). Long enough that a
    fingerprint can be quoted in operator chat / dashboards without
    colliding in practice, short enough that nobody mistakes it for the
    full secret.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _hash_token(raw: str) -> str:
    """Constant-time-safe storage hash for the bearer token.

    Tokens are entropy-bearing already (32 random bytes), so a single
    sha256 is sufficient — bcrypt/argon2 would just add CPU without
    raising the attack cost.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class VisionArea(models.Model):
    """A monitored supply area in the makerspace (AC-3).

    Each area is anchored to an existing ``inventory.Location`` so the
    operator sees consistent place names across the inventory map and
    the storage-vision setup. The vision layer doesn't redefine
    geography; it adds review state on top of it.
    """

    name = models.CharField(max_length=120)
    location = models.ForeignKey(
        "inventory.Location",
        on_delete=models.PROTECT,
        related_name="vision_areas",
    )
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["is_active", "name"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.location.name})"


class VisionSlot(models.Model):
    """A marker-backed slot inside an area, tied to one inventory item (AC-5).

    The marker code is the payload the printed QR encodes. Two active
    slots can never share a marker code — the unique-when-active
    constraint is below. Inactive slots may share with active ones (an
    operator who retires a marker and reuses the printable label later
    is the obvious case).
    """

    MARKER_CODE_MAX = 64

    area = models.ForeignKey(VisionArea, on_delete=models.PROTECT, related_name="slots")
    item = models.ForeignKey(
        "inventory.InventoryItem",
        on_delete=models.PROTECT,
        related_name="vision_slots",
    )
    marker_code = models.CharField(max_length=MARKER_CODE_MAX)
    # When a classification's confidence is below this number, the
    # observation is pending review-only (AC-17). Default 0.5 picks the
    # natural midpoint; operators can tighten per-slot for noisy
    # camera angles.
    empty_low_confidence_threshold = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default=Decimal("0.50"),
        validators=[MinValueValidator(0), MaxValueValidator(1)],
    )
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["area__name", "marker_code"]
        indexes = [
            models.Index(fields=["marker_code"]),
            models.Index(fields=["is_active", "area"]),
        ]
        constraints = [
            # Partial unique index: only enforced for active rows so
            # an inactive (retired) slot doesn't block reusing its
            # marker on a different item later.
            models.UniqueConstraint(
                fields=["marker_code"],
                condition=models.Q(is_active=True),
                name="unique_active_vision_slot_marker",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.marker_code} → {self.item.name}"


class VisionCamera(models.Model):
    """A fixed camera that uploads captures with a scoped bearer token (AC-7, AC-8).

    The raw token is returned exactly once at create / rotate time; it's
    stored hashed (see ``_hash_token``) so a database leak does not
    leak the bearers. ``token_fingerprint`` is a 16-hex-char public
    identifier — safe to display, useful in operator logs.
    """

    name = models.CharField(max_length=120)
    area = models.ForeignKey(
        VisionArea,
        on_delete=models.PROTECT,
        related_name="cameras",
        null=True,
        blank=True,
    )
    token_hash = models.CharField(max_length=64, unique=True)
    token_fingerprint = models.CharField(max_length=16, db_index=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    last_seen_status = models.JSONField(blank=True, default=dict)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["is_active", "name"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} (fp={self.token_fingerprint})"

    def issue_token(self) -> str:
        """Generate a fresh raw token, persist its hash + fingerprint, return raw.

        Caller is responsible for ``save()`` — this method just mutates the
        in-memory instance so a single ``viewset.perform_create`` call can
        do the whole "create row + emit raw token in response" atomically.
        """
        raw = _generate_camera_token()
        self.token_hash = _hash_token(raw)
        self.token_fingerprint = _fingerprint_token(raw)
        return raw

    @classmethod
    def find_by_token(cls, raw: str) -> Optional["VisionCamera"]:
        """Resolve a raw bearer to its camera, or None if no match.

        Uses the stored hash, not the fingerprint, so a published
        fingerprint cannot be used as an auth credential.
        """
        if not raw:
            return None
        return cls.objects.filter(token_hash=_hash_token(raw), is_active=True).first()


class VisionCapture(models.Model):
    """One uploaded image awaiting / past inference (AC-9, AC-10, AC-13)."""

    SOURCE_PHONE = "phone"
    SOURCE_CAMERA = "camera"
    SOURCE_CHOICES = [
        (SOURCE_PHONE, "Phone"),
        (SOURCE_CAMERA, "Fixed camera"),
    ]

    STATUS_QUEUED = "queued"
    STATUS_PROCESSING = "processing"
    STATUS_PROCESSED = "processed"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_QUEUED, "Queued"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_PROCESSED, "Processed"),
        (STATUS_FAILED, "Failed"),
    ]

    area = models.ForeignKey(VisionArea, on_delete=models.PROTECT, related_name="captures")
    source = models.CharField(max_length=16, choices=SOURCE_CHOICES)
    camera = models.ForeignKey(
        VisionCamera,
        on_delete=models.PROTECT,
        related_name="captures",
        null=True,
        blank=True,
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="+",
        null=True,
        blank=True,
    )
    original_image = models.ImageField(upload_to="storage_vision/originals/")
    # When the source said the image was taken (phone metadata or
    # camera-side timestamp). May lag received_at by minutes if the
    # camera buffered.
    captured_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_QUEUED)
    processor_version = models.CharField(max_length=64, blank=True)
    # Every marker the processor read out of the image — matched OR not.
    # Shape: [{"marker_code": "...", "bbox": [x,y,w,h], "confidence": 0.9}].
    # The matched ones spawn VisionObservation rows; the unmatched ones
    # stay here for operator inspection (AC-14, AC-15).
    markers_detected = models.JSONField(default=list, blank=True)
    # Sanitized human-readable failure reason. Stack traces and file
    # paths must never land here (AC-12).
    failure_reason = models.TextField(blank=True)
    failure_code = models.CharField(max_length=64, blank=True)
    queued_at = models.DateTimeField(default=timezone.now)
    processing_at = models.DateTimeField(null=True, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-received_at"]
        indexes = [
            models.Index(fields=["status", "-received_at"]),
            models.Index(fields=["area", "-received_at"]),
        ]

    def __str__(self) -> str:
        return f"capture-{self.pk} {self.area.name} {self.status}"


class VisionObservation(models.Model):
    """A per-slot classification produced by processing a capture (AC-16+).

    Created by the Celery processor when a known marker is detected.
    Carries its own review status (AC-20+) — the capture's status reports
    the inference job, the observation's status reports the human review.
    """

    CLASS_EMPTY = "empty"
    CLASS_LOW = "low"
    CLASS_FULL = "full"
    CLASS_UNKNOWN = "unknown"
    CLASS_CHOICES = [
        (CLASS_EMPTY, "Empty"),
        (CLASS_LOW, "Low"),
        (CLASS_FULL, "Full"),
        (CLASS_UNKNOWN, "Unknown"),
    ]

    ACTION_REVIEW_ONLY = "review_only"
    ACTION_RECONCILE_EMPTY = "reconcile_empty"
    ACTION_CHOICES = [
        (ACTION_REVIEW_ONLY, "Review only"),
        (ACTION_RECONCILE_EMPTY, "Reconcile empty"),
    ]

    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_SUPERSEDED = "superseded"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending review"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_SUPERSEDED, "Superseded"),
    ]

    capture = models.ForeignKey(
        VisionCapture, on_delete=models.CASCADE, related_name="observations"
    )
    slot = models.ForeignKey(VisionSlot, on_delete=models.PROTECT, related_name="observations")
    classification = models.CharField(max_length=16, choices=CLASS_CHOICES)
    confidence = models.DecimalField(
        max_digits=4,
        decimal_places=3,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
    )
    evidence_crop = models.ImageField(upload_to="storage_vision/crops/", null=True, blank=True)
    model_version = models.CharField(max_length=64, blank=True)
    suggested_action = models.CharField(max_length=32, choices=ACTION_CHOICES)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    # Bumped by the duplicate-suppression path (AC-19) instead of
    # creating a second pending row. Counts how many subsequent
    # captures produced the same finding before the operator
    # resolved this one.
    duplicate_count = models.PositiveIntegerField(default=0)
    last_duplicate_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["slot", "status"]),
            models.Index(fields=["suggested_action", "status"]),
        ]
        constraints = [
            # AC-19: at most one pending observation per (slot,
            # suggested_action) pair at any time. Subsequent
            # processings flow into duplicate_count instead.
            models.UniqueConstraint(
                fields=["slot", "suggested_action"],
                condition=models.Q(status="pending"),
                name="unique_pending_vision_obs_per_slot_action",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.slot.marker_code} {self.classification} ({self.status})"


class VisionReviewAction(models.Model):
    """Append-only audit row for a staff approve/reject decision (AC-21, AC-24).

    Each call to the observation approve/reject endpoint writes exactly one
    of these. The observation itself moves to ``approved`` or ``rejected`` —
    this row carries the reviewer identity, the reason text, and (for
    approvals that triggered a reconcile) a back-link to the resulting
    ``inventory.StockReconciliation`` row so the staff review history can
    show "Ian approved → stock zeroed → reorder #142 created".

    AC-23 idempotence sits on the observation status, not here: a second
    approve call short-circuits before this row would be written.
    """

    ACTION_APPROVE = "approve"
    ACTION_REJECT = "reject"
    ACTION_CHOICES = [
        (ACTION_APPROVE, "Approve"),
        (ACTION_REJECT, "Reject"),
    ]

    observation = models.ForeignKey(
        VisionObservation,
        on_delete=models.CASCADE,
        related_name="review_actions",
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="+",
    )
    action = models.CharField(max_length=16, choices=ACTION_CHOICES)
    reason = models.TextField(blank=True)
    # Only set for approve-paths that ran reconciliation. Null for
    # review-only approves (no inventory mutation) and for rejects.
    stock_reconciliation = models.ForeignKey(
        "inventory.StockReconciliation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["observation", "-created_at"]),
            models.Index(fields=["reviewer", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.action} obs={self.observation_id} by={self.reviewer_id}"
