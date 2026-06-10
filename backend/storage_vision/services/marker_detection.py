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


def detect_markers(image_bytes: bytes) -> list[DetectedMarker]:
    """Return every QR payload found in the image, with bbox + confidence.

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

    detector = cv2.QRCodeDetector()
    retval, decoded_info, points, _straight_qrcode = detector.detectAndDecodeMulti(bgr)
    if not retval or points is None or decoded_info is None:
        return []

    out: list[DetectedMarker] = []
    for payload, quad in zip(decoded_info, points):
        if not payload:
            # OpenCV occasionally locates a QR shape but can't
            # decode it (damaged, partial, occluded). Skip — there's
            # no marker_code to match.
            continue
        # quad is shape (4, 2) of float corner coords. Replacement
        # characters in the payload mean a partial decode; downgrade
        # confidence to flag it for review.
        confidence = 1.0 if "�" not in payload else 0.4
        out.append(
            DetectedMarker(
                payload=payload,
                bbox=_bbox_from_points(np.array(quad, dtype=float)),
                confidence=confidence,
            )
        )
    return out
