"""QR marker detection for storage_vision captures (AC-14, AC-15).

The processor (`storage_vision.tasks.process_capture`) feeds the
uploaded image bytes through :func:`detect_markers`, which returns
zero or more :class:`DetectedMarker` records. Each record has the raw
QR payload, the bounding box in image-pixel coordinates, and a
confidence proxy.

OpenCV's ``QRCodeDetector`` is the primary decoder because it's
already pinned in requirements (opencv-python-headless), runs on CPU,
needs no model files, and handles multiple QRs in a single frame
(``detectAndDecodeMulti``).

That legacy detector alone is not enough, though: it silently misses
~1% of otherwise-clean QR images, and which images it misses is
bit-pattern dependent rather than a quality/resolution problem. op-6t2
(PR #807) hit exactly that in the work-order PDF extractor, where it
showed up as a random CI flake. OpenCV ships a second, independent
ArUco-based QR backend (``QRCodeDetectorAruco``, OpenCV >= 4.7) that
finds the finder pattern differently and reads the frames the legacy
one drops, so we run both and union the payloads.

The union matters more here than it did for op-6t2: a work-order PDF
carries a single QR, so "first backend that decodes anything wins" is
enough there. A capture frame is usually a shelf with *several* slot
markers, and a legacy miss on one of them is silent — that slot simply
never gets an observation, and the capture still looks successful
because its neighbours decoded. Running both backends over every frame
costs a few tens of milliseconds in the worker and closes that hole.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from io import BytesIO

import cv2
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DetectedMarker:
    """One QR payload detected inside a capture."""

    payload: str
    # (x, y, w, h) in image-pixel coordinates. The box is computed
    # from the QR's four corner points, not the QR's rotated
    # bounding quad — the processor uses it for cropping evidence
    # which is rotation-agnostic.
    bbox: tuple[int, int, int, int]
    # Heuristic confidence: 1.0 for a clean decode, lower if the
    # decode came back with replacement characters or a partial
    # payload. OpenCV doesn't expose its internal Reed-Solomon
    # confidence, so we approximate.
    confidence: float


def _bbox_from_points(points: np.ndarray) -> tuple[int, int, int, int]:
    """Reduce QR corner points to an axis-aligned bounding box."""
    xs = points[:, 0]
    ys = points[:, 1]
    x = int(max(0, np.floor(xs.min())))
    y = int(max(0, np.floor(ys.min())))
    w = int(np.ceil(xs.max() - xs.min()))
    h = int(np.ceil(ys.max() - ys.min()))
    return (x, y, max(1, w), max(1, h))


def _quad_to_bbox(quad) -> tuple[int, int, int, int] | None:
    """Bounding box for one detector-reported QR quad, or None if unusable.

    ``detectAndDecodeMulti`` hands back one ``(4, 2)`` quad per QR while
    ``detectAndDecode`` returns ``(1, 4, 2)`` for its single hit; the
    reshape normalizes both shapes.
    """
    try:
        points = np.asarray(quad, dtype=float).reshape(-1, 2)
    except (TypeError, ValueError):
        return None
    if points.size == 0:
        return None
    return _bbox_from_points(points)


def _decode_to_array(image_bytes: bytes) -> np.ndarray | None:
    """Pillow-decode then BGR-convert for OpenCV. Returns None on bad input."""
    try:
        pil = Image.open(BytesIO(image_bytes))
        pil.load()
    except Exception:  # noqa: BLE001 — caller wants a None signal, not a trace
        return None
    rgb = np.array(pil.convert("RGB"))
    # OpenCV wants BGR. Avoid the cvtColor allocation when the
    # underlying array is already in the layout we need.
    return rgb[:, :, ::-1].copy()


def _build_detectors() -> list:
    """The QR backends to run, cheapest/most-common first.

    ``QRCodeDetectorAruco`` landed in OpenCV 4.7, so it's getattr-guarded:
    on an older build we still detect, just with the legacy backend only.
    """
    detectors = [cv2.QRCodeDetector()]
    aruco_qr_cls = getattr(cv2, "QRCodeDetectorAruco", None)
    if aruco_qr_cls is not None:
        try:
            detectors.append(aruco_qr_cls())
        except Exception:  # noqa: BLE001 — never let the newer backend cost us the legacy one
            logger.debug("detect_markers: ArUco QR backend failed to construct")
    return detectors


def _detect_with(detector, bgr: np.ndarray) -> list[tuple[str, object]]:
    """Every ``(payload, quad)`` pair one backend can read out of the frame."""
    found: list[tuple[str, object]] = []
    try:
        retval, decoded_info, points, _straight_qrcode = detector.detectAndDecodeMulti(bgr)
    except Exception:  # noqa: BLE001 — cv2 raises several types on odd frames
        retval, decoded_info, points = False, None, None

    if retval and decoded_info is not None and points is not None:
        for payload, quad in zip(decoded_info, points):
            if not payload:
                # OpenCV occasionally locates a QR shape but can't
                # decode it (damaged, partial, occluded). Skip — there's
                # no marker_code to match.
                continue
            found.append((payload, quad))
    if found:
        return found

    # detectAndDecodeMulti returns False on some clean single-QR frames
    # that the single-shot call reads fine, so try that before giving up
    # on this backend.
    try:
        payload, quad, _straight_qrcode = detector.detectAndDecode(bgr)
    except Exception:  # noqa: BLE001
        return found
    if payload:
        found.append((payload, quad))
    return found


def detect_markers(image_bytes: bytes) -> list[DetectedMarker]:
    """Return every QR payload found in the image, with bbox + confidence.

    Payloads are unioned across the QR backends (see module docstring)
    and deduped by payload — the first backend to read a given marker
    owns its bbox.

    Returns an empty list when the image decodes but contains no
    readable QR, AND when the image itself can't be decoded. The
    caller distinguishes the two cases by separately checking that
    the image was valid (the upload serializer already does this on
    the request path, so an empty list inside the processor means
    AC-15 — `no_markers_detected`).
    """
    bgr = _decode_to_array(image_bytes)
    if bgr is None:
        return []

    out: list[DetectedMarker] = []
    seen: set[str] = set()
    for detector in _build_detectors():
        for payload, quad in _detect_with(detector, bgr):
            if payload in seen:
                continue
            bbox = _quad_to_bbox(quad)
            if bbox is None:
                # Decoded text but no usable corner points. The
                # classifier crops relative to the marker, so a marker
                # we can't place on the frame isn't actionable.
                logger.debug("detect_markers: dropping payload with no corner points")
                continue
            seen.add(payload)
            # Replacement characters in the payload mean a partial
            # decode; downgrade confidence to flag it for review.
            confidence = 1.0 if "�" not in payload else 0.4
            out.append(DetectedMarker(payload=payload, bbox=bbox, confidence=confidence))
    return out
