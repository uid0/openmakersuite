"""Storage Vision slice-4 processing tests.

Covers AC-13 (state machine), AC-14 (marker detection + slot
matching), AC-15 (no markers → reviewable failure data), AC-16
(classification + evidence crop), AC-17 (low-confidence → review-only),
AC-18 (empty/low ≥ threshold → reconcile_empty), and AC-19 (duplicate
suppression).

The detector and classifier are exercised through their public
helpers + via the full ``process_capture`` task so the routing logic
is covered end-to-end.
"""

from __future__ import annotations

from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile

import pytest
import qrcode
from PIL import Image

from inventory.models import Category, InventoryItem, Location
from storage_vision.models import VisionArea, VisionCapture, VisionObservation, VisionSlot
from storage_vision.services.classification import (
    EMPTY_MEAN_THRESHOLD,
    LOW_MEAN_THRESHOLD,
    MODEL_VERSION,
    classify_slot_crop,
)
from storage_vision.services.marker_detection import detect_markers
from storage_vision.tasks import process_capture

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def location():
    return Location.objects.create(name="Shop floor")


@pytest.fixture
def area(location):
    return VisionArea.objects.create(name="Bay 1", location=location)


@pytest.fixture
def category():
    return Category.objects.create(name="Fasteners")


@pytest.fixture
def bin_loc():
    return Location.objects.create(name="Bin shelf A")


@pytest.fixture
def item(category, bin_loc):
    return InventoryItem.objects.create(
        name="M3 hex bolt",
        description="",
        category=category,
        location=bin_loc,
        current_stock=0,
        minimum_stock=5,
        reorder_quantity=10,
    )


@pytest.fixture
def slot(area, item):
    return VisionSlot.objects.create(
        area=area,
        item=item,
        marker_code="VIS-BAY1-M3HEX",
        empty_low_confidence_threshold=Decimal("0.50"),
    )


def _qr_png(payload: str, box_size: int = 8) -> bytes:
    """Render a QR PNG with the given payload."""
    qr = qrcode.QRCode(box_size=box_size, border=4)
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _composite_marker_above(payload: str, bin_color: tuple[int, int, int]) -> bytes:
    """Build a 600x800 RGB JPEG: marker QR at the top, ``bin_color`` rectangle
    underneath. The classifier crops the rectangle below the marker, so this
    pattern lets us drive the heuristic deterministically."""
    qr_bytes = _qr_png(payload, box_size=6)
    qr_img = Image.open(BytesIO(qr_bytes)).convert("RGB")
    qr_img = qr_img.resize((300, 300))

    canvas = Image.new("RGB", (600, 800), bin_color)
    # Paste the QR in the top center so the area BELOW it is the
    # ``bin_color`` region.
    canvas.paste(qr_img, (150, 50))
    buf = BytesIO()
    canvas.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _capture_from_bytes(area, image_bytes: bytes, source=VisionCapture.SOURCE_PHONE):
    upload = SimpleUploadedFile("frame.jpg", image_bytes, content_type="image/jpeg")
    return VisionCapture.objects.create(
        area=area,
        source=source,
        original_image=upload,
        status=VisionCapture.STATUS_QUEUED,
    )


# ---------------------------------------------------------------------------
# Pure detector / classifier units
# ---------------------------------------------------------------------------


class TestDetectMarkers:
    def test_no_qr_returns_empty(self):
        # A plain grey rectangle has no QR.
        buf = BytesIO()
        Image.new("RGB", (200, 200), (150, 150, 150)).save(buf, format="JPEG")
        assert detect_markers(buf.getvalue()) == []

    def test_decodes_qr_payload(self):
        png = _qr_png("VIS-HELLO")
        out = detect_markers(png)
        assert len(out) == 1
        assert out[0].payload == "VIS-HELLO"
        # bbox is in image-pixel coords; for a QR PNG the box is
        # nearly the full image after the white border.
        x, y, w, h = out[0].bbox
        assert w > 50 and h > 50
        assert out[0].confidence == pytest.approx(1.0)

    def test_undecodable_input_returns_empty(self):
        # Garbage bytes — Pillow will reject. Should not raise.
        assert detect_markers(b"not an image") == []


class TestClassifySlotCrop:
    def test_bright_crop_means_empty(self):
        # Marker at the top, bright (240) below.
        img = _composite_marker_above("X", (240, 240, 240))
        markers = detect_markers(img)
        assert markers, "QR should be detectable in the composite"
        result = classify_slot_crop(img, markers[0].bbox)
        assert result is not None
        assert result.classification == "empty"
        assert result.model_version == MODEL_VERSION
        # confidence must be positive — the mean (~240) sits well
        # past the EMPTY threshold so we expect ≥ ~0.5.
        assert result.confidence > 0.3

    def test_mid_crop_means_low(self):
        # Mid-gray below the marker (175).
        img = _composite_marker_above("X", (175, 175, 175))
        markers = detect_markers(img)
        assert markers
        result = classify_slot_crop(img, markers[0].bbox)
        assert result is not None
        assert result.classification == "low"

    def test_dark_crop_means_full(self):
        img = _composite_marker_above("X", (60, 60, 60))
        markers = detect_markers(img)
        assert markers
        result = classify_slot_crop(img, markers[0].bbox)
        assert result is not None
        assert result.classification == "full"

    def test_thresholds_are_sane(self):
        # Sanity: EMPTY > LOW, both inside the 0..255 grey range.
        assert 0 < LOW_MEAN_THRESHOLD < EMPTY_MEAN_THRESHOLD < 255


# ---------------------------------------------------------------------------
# process_capture — full task
# ---------------------------------------------------------------------------


class TestProcessCaptureNoMarkers:
    """AC-15: no markers detected → processed + machine-readable reason."""

    def test_records_no_markers_detected(self, area):
        # Plain image, no QR.
        buf = BytesIO()
        Image.new("RGB", (300, 300), (200, 200, 200)).save(buf, format="JPEG")
        capture = _capture_from_bytes(area, buf.getvalue())

        process_capture(capture.pk)
        capture.refresh_from_db()

        assert capture.status == VisionCapture.STATUS_PROCESSED
        assert capture.failure_code == "no_markers_detected"
        assert capture.markers_detected == []
        assert capture.processor_version == "slice4"
        assert capture.processing_at is not None
        assert capture.processed_at is not None
        assert not capture.observations.exists()


class TestProcessCaptureUnmatchedMarker:
    """AC-14: unmatched markers are logged but produce no observations."""

    def test_unknown_marker_logged_only(self, area):
        img = _composite_marker_above("UNKNOWN-MARKER", (60, 60, 60))
        capture = _capture_from_bytes(area, img)

        process_capture(capture.pk)
        capture.refresh_from_db()

        assert capture.status == VisionCapture.STATUS_PROCESSED
        assert capture.failure_code == ""
        assert len(capture.markers_detected) == 1
        assert capture.markers_detected[0]["marker_code"] == "UNKNOWN-MARKER"
        assert capture.markers_detected[0]["matched_slot_id"] is None
        assert not capture.observations.exists()


class TestProcessCaptureHappyPath:
    """AC-13, AC-14, AC-16, AC-18 — known marker, full classification."""

    def test_known_empty_creates_pending_reconcile_observation(self, area, slot):
        # Bright below marker → empty → above threshold → reconcile_empty.
        img = _composite_marker_above(slot.marker_code, (240, 240, 240))
        capture = _capture_from_bytes(area, img)

        process_capture(capture.pk)
        capture.refresh_from_db()

        assert capture.status == VisionCapture.STATUS_PROCESSED
        assert len(capture.markers_detected) == 1
        assert capture.markers_detected[0]["matched_slot_id"] == slot.id

        obs = capture.observations.get()
        assert obs.classification == VisionObservation.CLASS_EMPTY
        assert obs.suggested_action == VisionObservation.ACTION_RECONCILE_EMPTY
        assert obs.status == VisionObservation.STATUS_PENDING
        assert obs.model_version == MODEL_VERSION
        assert obs.confidence > Decimal("0.0")
        # evidence_crop is a real file — saved via .save() on the ImageField.
        assert obs.evidence_crop.name


class TestProcessCaptureLowConfidence:
    """AC-17: empty/low classification but confidence below the slot
    threshold → review_only, not reconcile_empty."""

    def test_low_confidence_routes_to_review_only(self, area, slot):
        # Push the slot's threshold above what the heuristic will
        # ever return for a clean bright crop (max confidence is
        # ~1.0 at very far from boundary; 0.99 is achievable but
        # safe headroom for the test).
        slot.empty_low_confidence_threshold = Decimal("0.99")
        slot.save()

        # Mean ~210 — past EMPTY threshold but only ~10 inside it,
        # so confidence will be ~10/60 ≈ 0.17. Definitely under 0.99.
        img = _composite_marker_above(slot.marker_code, (210, 210, 210))
        capture = _capture_from_bytes(area, img)

        process_capture(capture.pk)

        obs = capture.observations.get()
        assert obs.classification == VisionObservation.CLASS_EMPTY
        assert obs.suggested_action == VisionObservation.ACTION_REVIEW_ONLY


class TestProcessCaptureFullClassification:
    """AC-18 negative: full → never reconcile_empty, always review_only."""

    def test_full_slot_routes_to_review_only(self, area, slot):
        img = _composite_marker_above(slot.marker_code, (60, 60, 60))
        capture = _capture_from_bytes(area, img)

        process_capture(capture.pk)

        obs = capture.observations.get()
        assert obs.classification == VisionObservation.CLASS_FULL
        assert obs.suggested_action == VisionObservation.ACTION_REVIEW_ONLY


class TestProcessCaptureDuplicateSuppression:
    """AC-19: a second capture producing the same (slot, suggested_action)
    pending finding bumps duplicate_count instead of creating a new row."""

    def test_second_capture_bumps_existing_observation(self, area, slot):
        img1 = _composite_marker_above(slot.marker_code, (240, 240, 240))
        capture1 = _capture_from_bytes(area, img1)
        process_capture(capture1.pk)

        first_obs = capture1.observations.get()
        assert first_obs.duplicate_count == 0
        assert first_obs.last_duplicate_at is None

        img2 = _composite_marker_above(slot.marker_code, (240, 240, 240))
        capture2 = _capture_from_bytes(area, img2)
        process_capture(capture2.pk)

        # Still exactly one pending observation across both captures.
        pending = VisionObservation.objects.filter(
            slot=slot, status=VisionObservation.STATUS_PENDING
        )
        assert pending.count() == 1

        first_obs.refresh_from_db()
        assert first_obs.duplicate_count == 1
        assert first_obs.last_duplicate_at is not None
        # The bumped row carries forward the latest classification.
        assert first_obs.classification == VisionObservation.CLASS_EMPTY


class TestProcessCaptureFailurePath:
    """AC-12 sanitization extends to the worker — a downstream OpenCV /
    Pillow exception must NEVER land its traceback in failure_reason."""

    def test_detector_exception_is_sanitized(self, area):
        # Drop a valid image so we get past the bytes read, then
        # blow up the detector body.
        buf = BytesIO()
        Image.new("RGB", (200, 200), (128, 128, 128)).save(buf, format="JPEG")
        capture = _capture_from_bytes(area, buf.getvalue())

        with patch(
            "storage_vision.services.marker_detection.detect_markers",
            side_effect=RuntimeError("oh no /tmp/secret.jpg blew up"),
        ):
            process_capture(capture.pk)

        capture.refresh_from_db()
        assert capture.status == VisionCapture.STATUS_FAILED
        assert capture.failed_at is not None
        assert capture.failure_code == "detection_error"
        # Must NOT leak the original message — that one had a path
        # in it. The sanitized message we ship is stable.
        assert "secret.jpg" not in capture.failure_reason
        assert "/tmp" not in capture.failure_reason
        assert "Traceback" not in capture.failure_reason


class TestProcessCaptureIdempotence:
    """AC-13: re-running a processed task is a no-op (slice 3 contract)."""

    def test_no_op_when_already_processed(self, area):
        buf = BytesIO()
        Image.new("RGB", (200, 200), (200, 200, 200)).save(buf, format="JPEG")
        capture = _capture_from_bytes(area, buf.getvalue())
        process_capture(capture.pk)
        capture.refresh_from_db()
        first_processed_at = capture.processed_at

        # Re-run — should not flip state nor re-stamp processed_at.
        process_capture(capture.pk)
        capture.refresh_from_db()
        assert capture.status == VisionCapture.STATUS_PROCESSED
        assert capture.processed_at == first_processed_at
