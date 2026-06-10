"""Storage Vision Celery tasks — slice 3 stub.

The full marker-detection pipeline lands in slice 4 (AC-13, AC-14,
AC-16). For now ``process_capture`` exists only so the upload view has
something to call. It transitions the row through processing →
processed without doing any inference; observations get created later
when slice 4 fills in the OpenCV/Pillow body.

Slice 3 ships this stub so the upload endpoint returns 202 cleanly
(AC-9, AC-10) and the capture status state machine moves at all,
giving the frontend (slice 8) something to poll on.
"""

from __future__ import annotations

import logging

from django.utils import timezone

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="storage_vision.process_capture")
def process_capture(capture_id: int) -> dict:
    """Move the capture through processing → processed (no inference yet).

    The slice-4 body will:
      - load the original image via Pillow + decode with OpenCV
      - run marker detection (QR pyzbar / OpenCV QRCodeDetector)
      - match each detected marker payload against VisionSlot.marker_code
      - create one VisionObservation per matched slot
      - record unmatched markers in ``capture.markers_detected``

    For now we just record the timestamps so the row doesn't sit in
    ``queued`` forever and the polling frontend can stop waiting.
    """
    from .models import VisionCapture

    try:
        capture = VisionCapture.objects.get(pk=capture_id)
    except VisionCapture.DoesNotExist:
        logger.warning("process_capture: capture %s vanished before processing", capture_id)
        return {"status": "missing"}

    if capture.status != VisionCapture.STATUS_QUEUED:
        logger.info(
            "process_capture: capture %s already in status %s — no-op",
            capture_id,
            capture.status,
        )
        return {"status": capture.status}

    now = timezone.now()
    capture.status = VisionCapture.STATUS_PROCESSING
    capture.processing_at = now
    capture.save(update_fields=["status", "processing_at"])

    # No-op body until slice 4. Mark processed so the state machine
    # advances and the frontend can show "done".
    capture.status = VisionCapture.STATUS_PROCESSED
    capture.processed_at = timezone.now()
    capture.processor_version = "slice3-stub"
    capture.save(update_fields=["status", "processed_at", "processor_version"])
    return {"status": capture.status, "id": capture_id}
