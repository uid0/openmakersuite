"""Storage Vision Celery tasks.

slice 3 shipped the upload + state-machine stub.
slice 4 ships the actual marker detection + classification pipeline
(AC-13 through AC-19).
slice 6 adds the retention task (AC-26, AC-27): originals are pruned
after STORAGE_VISION_RETENTION_DAYS, but the observation rows,
review actions, evidence crops, classifications, and audit metadata
all outlive the original.

The processor is split so the heavy work happens in the worker:

    process_capture(capture_id)
      ├── load capture row, transition queued → processing
      ├── decode image bytes
      ├── detect_markers() — QR payloads + bboxes (AC-14)
      ├── for each detected marker:
      │     match to an active VisionSlot by marker_code
      │     classify the crop below the marker (AC-16)
      │     create / dedupe a pending VisionObservation (AC-17/18/19)
      │     record marker bookkeeping on the capture (AC-14)
      ├── transition processed (no markers → processed with
      │     failure_code=no_markers_detected per AC-15)
      └── any unexpected exception → failed + SANITIZED reason (AC-12)

Slice 5 plugs the approval/review flow into the VisionObservation
rows this task creates.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.utils import timezone

from celery import shared_task

logger = logging.getLogger(__name__)

# How long after creation a pending observation is still considered
# "the same finding" for AC-19 duplicate suppression. Re-processings
# inside this window bump duplicate_count; older ones get a fresh row.
DUPLICATE_WINDOW = timedelta(days=14)


@shared_task(name="storage_vision.process_capture")
def process_capture(capture_id: int) -> dict:
    """Run marker detection + classification for one VisionCapture.

    Always returns a small dict (status string + counts) so a
    Celery result backend can keep something useful, but the source
    of truth for everything downstream is the VisionCapture +
    VisionObservation rows the task writes.
    """
    from .models import VisionCapture, VisionObservation, VisionSlot
    from .services.classification import classify_slot_crop
    from .services.marker_detection import detect_markers

    try:
        capture = VisionCapture.objects.select_related("area").get(pk=capture_id)
    except VisionCapture.DoesNotExist:
        logger.warning("process_capture: capture %s vanished before processing", capture_id)
        return {"status": "missing"}

    if capture.status != VisionCapture.STATUS_QUEUED:
        # Re-enqueued or already finished. Don't re-stamp anything.
        return {"status": capture.status}

    capture.status = VisionCapture.STATUS_PROCESSING
    capture.processing_at = timezone.now()
    capture.save(update_fields=["status", "processing_at"])

    try:
        image_bytes = capture.original_image.open("rb").read()
    except Exception:  # noqa: BLE001
        _mark_failed(capture, "image_open_error", "Could not open the uploaded image.")
        return {"status": capture.status, "id": capture_id}
    finally:
        try:
            capture.original_image.close()
        except Exception:  # noqa: BLE001
            # Best-effort close — already-closed handles, missing
            # storage backends, and gone-away tempfiles all surface
            # here. None of them block the inference body.
            logger.debug("process_capture: ignoring close error on capture %s", capture_id)

    try:
        markers = detect_markers(image_bytes)
    except Exception:  # noqa: BLE001
        # AC-12: sanitize. Never let an OpenCV/Pillow trace land in
        # failure_reason; the operator-visible message has to be
        # safe to render in the review UI verbatim.
        _mark_failed(capture, "detection_error", "Marker detection failed.")
        return {"status": capture.status, "id": capture_id}

    if not markers:
        capture.markers_detected = []
        capture.failure_code = "no_markers_detected"
        capture.failure_reason = "No storage-vision markers were readable in this image."
        capture.status = VisionCapture.STATUS_PROCESSED
        capture.processed_at = timezone.now()
        capture.processor_version = "slice4"
        capture.save(
            update_fields=[
                "markers_detected",
                "failure_code",
                "failure_reason",
                "status",
                "processed_at",
                "processor_version",
            ]
        )
        return {"status": capture.status, "id": capture_id, "markers": 0, "observations": 0}

    # Index the active slots once — captures are usually 1-10 markers
    # against a couple hundred slots tops, so a single queryset beats
    # per-marker lookups.
    slot_by_code = {
        s.marker_code: s
        for s in VisionSlot.objects.select_related("item").filter(area=capture.area, is_active=True)
    }

    markers_log: list[dict] = []
    observations_created = 0
    duplicates_bumped = 0

    for detected in markers:
        slot = slot_by_code.get(detected.payload)
        marker_log = {
            "marker_code": detected.payload,
            "bbox": list(detected.bbox),
            "confidence": detected.confidence,
            "matched_slot_id": slot.id if slot else None,
        }
        markers_log.append(marker_log)
        if slot is None:
            # AC-14: unknown markers are recorded but never create
            # observations — they wouldn't have an inventory item to
            # link to.
            continue

        classification = classify_slot_crop(image_bytes, detected.bbox)
        if classification is None:
            # The marker hugged the bottom edge — nothing to crop.
            # Record an unknown / review-only observation so the
            # operator can recompose the shot.
            cls = VisionObservation.CLASS_UNKNOWN
            conf = Decimal("0.000")
            suggested = VisionObservation.ACTION_REVIEW_ONLY
            crop_jpeg = None
            model_version = "heuristic-v1"
        else:
            cls = classification.classification
            conf = Decimal(str(round(classification.confidence, 3)))
            model_version = classification.model_version
            crop_jpeg = classification.crop_jpeg
            # AC-17 / AC-18 routing.
            suggested = _suggested_action_for(slot, cls, classification.confidence)

        created_or_updated = _create_or_dedupe_observation(
            capture=capture,
            slot=slot,
            classification=cls,
            confidence=conf,
            crop_jpeg=crop_jpeg,
            model_version=model_version,
            suggested_action=suggested,
        )
        if created_or_updated == "created":
            observations_created += 1
        elif created_or_updated == "bumped":
            duplicates_bumped += 1

    capture.markers_detected = markers_log
    capture.status = VisionCapture.STATUS_PROCESSED
    capture.processed_at = timezone.now()
    capture.processor_version = "slice4"
    capture.failure_code = ""
    capture.failure_reason = ""
    capture.save(
        update_fields=[
            "markers_detected",
            "status",
            "processed_at",
            "processor_version",
            "failure_code",
            "failure_reason",
        ]
    )

    return {
        "status": capture.status,
        "id": capture_id,
        "markers": len(markers),
        "observations": observations_created,
        "duplicates_bumped": duplicates_bumped,
    }


def _mark_failed(capture, code: str, reason: str) -> None:
    """Sanitized failure stamp (AC-12)."""
    capture.status = capture.STATUS_FAILED
    capture.failed_at = timezone.now()
    capture.failure_code = code
    capture.failure_reason = reason
    capture.save(update_fields=["status", "failed_at", "failure_code", "failure_reason"])


def _suggested_action_for(slot, classification: str, confidence: float) -> str:
    """AC-17 + AC-18: pick suggested_action from class + confidence.

    - empty/low + confidence >= slot.empty_low_confidence_threshold
      → reconcile_empty (will let staff one-click stock-zero on approve).
    - empty/low + confidence below threshold → review_only.
    - full or unknown → review_only (no automated action ever).
    """
    from .models import VisionObservation

    if classification in (VisionObservation.CLASS_EMPTY, VisionObservation.CLASS_LOW):
        threshold = float(slot.empty_low_confidence_threshold)
        if confidence >= threshold:
            return VisionObservation.ACTION_RECONCILE_EMPTY
    return VisionObservation.ACTION_REVIEW_ONLY


def _create_or_dedupe_observation(
    *,
    capture,
    slot,
    classification: str,
    confidence,
    crop_jpeg: bytes | None,
    model_version: str,
    suggested_action: str,
) -> str:
    """AC-19: create a fresh pending row, or bump the existing one.

    Returns "created", "bumped", or "skipped" so the caller can log
    how a capture decomposed.
    """
    from .models import VisionObservation

    try:
        with transaction.atomic():
            obs = VisionObservation(
                capture=capture,
                slot=slot,
                classification=classification,
                confidence=confidence,
                model_version=model_version,
                suggested_action=suggested_action,
                status=VisionObservation.STATUS_PENDING,
            )
            if crop_jpeg:
                obs.evidence_crop.save(
                    f"capture-{capture.pk}-slot-{slot.pk}.jpg",
                    ContentFile(crop_jpeg),
                    save=False,
                )
            obs.save()
            return "created"
    except IntegrityError:
        # The partial unique constraint
        # unique_pending_vision_obs_per_slot_action fired. Bump the
        # existing pending row instead.
        existing = (
            VisionObservation.objects.filter(
                slot=slot,
                suggested_action=suggested_action,
                status=VisionObservation.STATUS_PENDING,
            )
            .order_by("-created_at")
            .first()
        )
        if existing is None:
            # The constraint says one exists; if we can't find it,
            # something else is wrong — log and move on rather than
            # spinning.
            logger.warning(
                "process_capture: IntegrityError on slot=%s action=%s but no pending row found",
                slot.pk,
                suggested_action,
            )
            return "skipped"

        # Only bump if the existing pending row is still fresh — old
        # findings shouldn't suppress new ones (AC-19 + the 14-day
        # window above). If it's stale, supersede it and write a
        # new pending row.
        if existing.created_at < timezone.now() - DUPLICATE_WINDOW:
            existing.status = VisionObservation.STATUS_SUPERSEDED
            existing.save(update_fields=["status"])
            return _create_or_dedupe_observation(
                capture=capture,
                slot=slot,
                classification=classification,
                confidence=confidence,
                crop_jpeg=crop_jpeg,
                model_version=model_version,
                suggested_action=suggested_action,
            )

        existing.duplicate_count = (existing.duplicate_count or 0) + 1
        existing.last_duplicate_at = timezone.now()
        # Carry the freshest classification + confidence forward so the
        # review UI shows the latest evidence, not the original
        # finding. Crop only replaced if we got one this round.
        existing.classification = classification
        existing.confidence = confidence
        existing.model_version = model_version
        update_fields = [
            "duplicate_count",
            "last_duplicate_at",
            "classification",
            "confidence",
            "model_version",
            "updated_at",
        ]
        if crop_jpeg:
            existing.evidence_crop.save(
                f"capture-{capture.pk}-slot-{slot.pk}.jpg",
                ContentFile(crop_jpeg),
                save=False,
            )
            update_fields.append("evidence_crop")
        existing.save(update_fields=update_fields)
        return "bumped"


@shared_task(name="storage_vision.prune_original_captures")
def prune_original_captures() -> dict:
    """AC-26 — delete original capture images older than the retention window.

    What this DOES delete:
      - VisionCapture.original_image (the bytes on disk + the FieldFile
        reference)

    What this preserves (the AC-26 contract):
      - The VisionCapture row itself (kept for audit + the
        markers_detected / failure_reason history)
      - Every derived VisionObservation row
      - VisionObservation.evidence_crop (the small JPEG thumbnail
        the operator approved against)
      - Every VisionReviewAction (the approve/reject audit trail)
      - Linked StockReconciliation + ReorderRequest rows (untouched)

    A retention window of 0 days means "keep originals forever" — an
    operator opt-in that skips the prune entirely. Anything else is
    interpreted as a day count.

    The task is safe to run multiple times per window: captures whose
    original_image has already been cleared are skipped without
    touching storage.
    """
    days = int(getattr(settings, "STORAGE_VISION_RETENTION_DAYS", 30) or 0)
    if days <= 0:
        logger.info("prune_original_captures: STORAGE_VISION_RETENTION_DAYS=0, skipping")
        return {"status": "disabled", "deleted": 0, "freed_bytes": 0}

    from .models import VisionCapture

    cutoff = timezone.now() - timedelta(days=days)
    # Pull only rows that still have an original on disk. ImageField
    # records the relative path even after delete(); checking
    # `original_image` (truthy) excludes the empty string sentinel.
    candidates = (
        VisionCapture.objects.filter(received_at__lt=cutoff)
        .exclude(original_image="")
        .only("id", "original_image", "received_at")
    )

    deleted = 0
    freed_bytes = 0
    errors = 0
    for capture in candidates.iterator(chunk_size=200):
        field = capture.original_image
        try:
            try:
                freed_bytes += int(field.size or 0)
            except Exception:  # noqa: BLE001
                # Underlying file might be already gone (manually
                # cleared, or a backing store that doesn't expose
                # size). Don't let a counter glitch block the prune.
                logger.debug("prune_original_captures: size() failed for capture %s", capture.pk)
            # delete(save=False) clears bytes AND the FieldFile
            # reference. We follow up with a single UPDATE so we
            # don't touch `received_at` or any other columns.
            field.delete(save=False)
            VisionCapture.objects.filter(pk=capture.pk).update(original_image="")
            deleted += 1
        except Exception:  # noqa: BLE001
            errors += 1
            logger.warning(
                "prune_original_captures: failed to prune capture %s — continuing",
                capture.pk,
            )

    logger.info(
        "prune_original_captures: deleted=%s freed_bytes=%s errors=%s cutoff=%s",
        deleted,
        freed_bytes,
        errors,
        cutoff.isoformat(),
    )
    return {
        "status": "ok",
        "deleted": deleted,
        "freed_bytes": freed_bytes,
        "errors": errors,
        "cutoff": cutoff.isoformat(),
        "retention_days": days,
    }
