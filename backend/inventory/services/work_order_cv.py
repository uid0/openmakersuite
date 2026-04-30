"""
Computer-vision detections for paper work-order ingestion (AC-4, oms-2da).

Two detection sources:

1. **AcroForm checkboxes** (``parse_work_order_pdf`` in
   ``work_order_ingest``). When the PDF still has its interactive form layer
   intact, every checkbox value is read directly. Confidence is **1.0** —
   binary, no inference.

2. **Image-based** detections (this module). When the form has been printed
   and re-scanned, the AcroForm layer is gone and only embedded page images
   remain. We:

     - look for signature blocks: a region of the page with high ink density
       implies the technician signed.
     - run optional OCR (pytesseract) on dedicated handwritten-note boxes
       and append the result to the WO's notes.
     - attempt image-based checkbox detection by scanning each embedded
       image for "filled box" patterns. This is best-effort; confidences
       below ``CV_CONFIDENCE_THRESHOLD`` route to the pending-review queue
       instead of auto-applying.

The threshold is configurable via Django settings (default 0.7 — AC-4).

Tesseract is **optional**: if pytesseract / the tesseract binary aren't
installed, ``ocr_handwritten`` returns ``None`` with confidence 0 and the
caller routes the submission for human review of the notes block. This keeps
the pipeline functional in environments where the OCR system dependency
isn't available.
"""

from __future__ import annotations

import io
import logging
from dataclasses import asdict, dataclass
from typing import Iterable, Optional

from django.conf import settings

logger = logging.getLogger(__name__)


DEFAULT_CONFIDENCE_THRESHOLD = 0.7


def confidence_threshold() -> float:
    """Threshold below which detections route to pending review (AC-4)."""
    return float(getattr(settings, "CV_CONFIDENCE_THRESHOLD", DEFAULT_CONFIDENCE_THRESHOLD))


@dataclass
class Detection:
    """One CV-derived signal extracted from a paper-form scan.

    ``kind`` examples: ``"checkbox"``, ``"signature"``, ``"handwritten"``.
    ``target_id`` is the AcroForm field name (e.g. ``"task_<uuid>"``) when
    the detection maps to a known checkbox; otherwise ``None`` for free-form
    detections like signatures.
    """

    kind: str
    target_id: Optional[str]
    value: object
    confidence: float
    label: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def detect_filled_checkbox(image_bytes: bytes) -> tuple[bool, float]:
    """Estimate whether an isolated checkbox image is filled.

    Pure heuristic: rasterize, threshold, compute the fraction of dark
    pixels. Empty boxes are mostly white (only the rule lines); filled
    boxes have an X / check mark / scribble. We treat 35%+ darkness as
    filled with high confidence; 12-35% as ambiguous (medium confidence);
    <12% as empty with high confidence.

    Returns (is_filled, confidence).
    """
    try:
        import numpy as np
        from PIL import Image, ImageOps
    except Exception as exc:  # noqa: BLE001 - image deps missing
        logger.debug("checkbox detect unavailable: %s", exc)
        return False, 0.0

    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("L")
        img = ImageOps.autocontrast(img)
        arr = np.asarray(img)
    except Exception as exc:  # noqa: BLE001
        logger.debug("checkbox detect failed to load image: %s", exc)
        return False, 0.0

    if arr.size == 0:
        return False, 0.0

    # Dark = anything below 128 in the autocontrasted greyscale.
    dark_ratio = float((arr < 128).sum()) / float(arr.size)

    if dark_ratio >= 0.35:
        return True, min(1.0, 0.7 + (dark_ratio - 0.35) * 1.5)
    if dark_ratio <= 0.12:
        return False, min(1.0, 0.8 + (0.12 - dark_ratio) * 1.5)
    # Ambiguous middle band — confidence scales linearly with how filled it is.
    midpoint = (0.12 + 0.35) / 2
    distance = abs(dark_ratio - midpoint) / (0.35 - 0.12)
    return dark_ratio > midpoint, max(0.4, 0.4 + distance * 0.3)


def detect_signature(image_bytes: bytes) -> tuple[bool, float]:
    """Decide whether a signature region has ink in it.

    Same darkness heuristic as ``detect_filled_checkbox`` but with a much
    lower threshold — signatures are sparse strokes, not block fills.
    """
    try:
        import numpy as np
        from PIL import Image, ImageOps
    except Exception as exc:  # noqa: BLE001
        logger.debug("signature detect unavailable: %s", exc)
        return False, 0.0

    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("L")
        img = ImageOps.autocontrast(img)
        arr = np.asarray(img)
    except Exception as exc:  # noqa: BLE001
        logger.debug("signature detect failed to load image: %s", exc)
        return False, 0.0

    if arr.size == 0:
        return False, 0.0

    dark_ratio = float((arr < 128).sum()) / float(arr.size)
    # Signature presence is more lenient — even 5% ink is plausibly a name.
    if dark_ratio >= 0.08:
        return True, min(1.0, 0.7 + dark_ratio * 0.8)
    if dark_ratio <= 0.02:
        return False, min(1.0, 0.85 + (0.02 - dark_ratio) * 5)
    # Ambiguous band 2-8%.
    return dark_ratio > 0.05, 0.5


def ocr_handwritten(image_bytes: bytes) -> tuple[str, float]:
    """Best-effort OCR for a handwritten-note region.

    Returns ``("", 0.0)`` when pytesseract / the tesseract binary aren't
    available — the caller treats that as "needs human review", not
    "definitely empty".
    """
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        logger.debug("OCR unavailable (pytesseract not installed): %s", exc)
        return "", 0.0

    try:
        img = Image.open(io.BytesIO(image_bytes))
    except Exception as exc:  # noqa: BLE001
        logger.debug("OCR failed to load image: %s", exc)
        return "", 0.0

    try:
        # ``image_to_data`` exposes per-word confidences (0-100). We average
        # the non-empty confidences and rescale to 0-1.
        data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    except Exception as exc:  # noqa: BLE001 - tesseract binary missing or PDF unreadable
        logger.debug("OCR call failed: %s", exc)
        return "", 0.0

    words = []
    confs = []
    for word, conf in zip(data.get("text", []), data.get("conf", [])):
        if not word or not word.strip():
            continue
        try:
            conf_f = float(conf)
        except (TypeError, ValueError):
            continue
        if conf_f < 0:
            continue
        words.append(word)
        confs.append(conf_f)

    if not words:
        return "", 0.0
    text = " ".join(words).strip()
    avg_conf = sum(confs) / len(confs) / 100.0
    return text, max(0.0, min(1.0, avg_conf))


def auto_apply_or_queue(
    detections: Iterable[Detection],
    threshold: Optional[float] = None,
) -> tuple[list[Detection], list[Detection]]:
    """Split detections into (auto_apply, queue_for_review) by confidence.

    Used by the ingest pipeline to decide which CV-derived changes commit
    silently and which surface in the digital WO's pending-review panel.
    """
    if threshold is None:
        threshold = confidence_threshold()
    auto: list[Detection] = []
    queue: list[Detection] = []
    for det in detections:
        if det.confidence >= threshold:
            auto.append(det)
        else:
            queue.append(det)
    return auto, queue
