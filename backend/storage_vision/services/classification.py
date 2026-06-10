"""Slot classification heuristic for storage_vision (AC-16).

The processor crops an evidence rectangle directly BELOW each detected
marker (the marker labels a bin; the bin contents sit below the
label) and runs :func:`classify_slot_crop` to decide whether the
slot is empty / low / full / unknown.

The first-generation heuristic is brightness-mean: an exposed bin
bottom (no stock) returns a bright crop, a partially-stocked bin
returns a mid crop, a full bin returns a dark/textured crop. This is
deliberately naive — slice 5 swaps in something better once we have a
labeled set; the review queue and the data model don't change when
that happens because every observation already records its
``model_version`` for traceability.

Confidence is the normalized distance from the nearest classification
boundary, clipped to [0, 1]. A crop whose mean sits right on a
boundary returns confidence ~0; one that's deep inside a region
returns ~1.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

import cv2
import numpy as np
from PIL import Image

MODEL_VERSION = "heuristic-v1"

# Grayscale-mean breakpoints. Tuned so:
#   bright (no stock) → empty
#   mid                → low
#   dark/textured     → full
# Calibrated against the seed fixtures; the labeled-set work in slice
# 5 will replace this with something more honest.
EMPTY_MEAN_THRESHOLD = 200
LOW_MEAN_THRESHOLD = 150


@dataclass(frozen=True)
class ClassificationResult:
    classification: str  # one of CLASS_EMPTY / CLASS_LOW / CLASS_FULL / CLASS_UNKNOWN
    confidence: float  # [0.0, 1.0]
    crop_jpeg: bytes  # thumbnail bytes (JPEG) for VisionObservation.evidence_crop
    model_version: str


def _crop_below(image_bytes: bytes, marker_bbox: tuple[int, int, int, int]) -> np.ndarray | None:
    """Return the BGR rectangle just below the marker bbox.

    The crop is the same width as the marker, 1.5x the marker height,
    starting at the bottom edge of the marker. Clipped to the image
    bounds. Returns None if there's nothing below the marker (the
    marker sits at the bottom of the frame), or if the image can't
    be decoded.
    """
    try:
        pil = Image.open(BytesIO(image_bytes))
        pil.load()
    except Exception:  # noqa: BLE001
        return None
    rgb = np.array(pil.convert("RGB"))
    h_img, w_img = rgb.shape[:2]

    x, y, w, h = marker_bbox
    top = max(0, min(h_img, y + h))
    bottom = max(0, min(h_img, top + int(round(h * 1.5))))
    left = max(0, min(w_img, x))
    right = max(0, min(w_img, x + w))
    if bottom <= top or right <= left:
        return None
    return rgb[top:bottom, left:right][:, :, ::-1].copy()  # BGR


def _confidence_for_mean(mean: float) -> float:
    """Normalize distance from the nearer breakpoint into [0, 1].

    Right on a boundary → 0; deep in a class → 1. Uses a 60-grey-level
    half-window because below that the bands collapse into noise.
    """
    if mean >= EMPTY_MEAN_THRESHOLD:
        nearest = mean - EMPTY_MEAN_THRESHOLD
    elif mean >= LOW_MEAN_THRESHOLD:
        nearest = min(EMPTY_MEAN_THRESHOLD - mean, mean - LOW_MEAN_THRESHOLD)
    else:
        nearest = LOW_MEAN_THRESHOLD - mean
    return max(0.0, min(1.0, abs(nearest) / 60.0))


def classify_slot_crop(
    image_bytes: bytes, marker_bbox: tuple[int, int, int, int]
) -> ClassificationResult | None:
    """Run the heuristic over the crop below ``marker_bbox``.

    Returns None when the crop region is empty (marker against the
    bottom edge) — the processor records that as
    classification=unknown and confidence=0 so AC-17 routes the
    observation to review-only.
    """
    bgr = _crop_below(image_bytes, marker_bbox)
    if bgr is None or bgr.size == 0:
        return None

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    mean = float(gray.mean())
    if mean >= EMPTY_MEAN_THRESHOLD:
        classification = "empty"
    elif mean >= LOW_MEAN_THRESHOLD:
        classification = "low"
    else:
        classification = "full"
    confidence = _confidence_for_mean(mean)

    # JPEG-encode the crop for VisionObservation.evidence_crop. Use
    # PIL so the encoding step doesn't depend on OpenCV's quality
    # quirks. Cap the long side at 320 px — the review UI only ever
    # shows a thumbnail, no point storing more.
    rgb = bgr[:, :, ::-1]
    pil = Image.fromarray(rgb)
    pil.thumbnail((320, 320))
    buf = BytesIO()
    pil.save(buf, format="JPEG", quality=70)

    return ClassificationResult(
        classification=classification,
        confidence=confidence,
        crop_jpeg=buf.getvalue(),
        model_version=MODEL_VERSION,
    )
