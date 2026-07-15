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
from dataclasses import dataclass
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

    ``fill_ratio`` / ``confident_checked`` are populated only on the OMR
    (scan-to-complete) path by ``detections_from_result``: the mark's measured
    fill fraction and whether that fill sits cleanly in the "checked" band.
    They drive the two-axis OMR auto-apply decision in ``auto_apply_or_queue``
    and stay at their inert defaults (``0.0`` / ``False``) on the born-digital
    signature/OCR path, which splits purely on ``confidence``.
    """

    kind: str
    target_id: Optional[str]
    value: object
    confidence: float
    label: str = ""
    fill_ratio: float = 0.0
    confident_checked: bool = False

    def to_dict(self) -> dict:
        # Wire shape for the born-digital CV pending-review queue
        # (``work_order_ingest`` serialises queued detections with this). The
        # OMR-only ``fill_ratio`` / ``confident_checked`` scoring fields are
        # deliberately excluded — they are an internal auto-apply signal, not
        # part of this payload.
        return {
            "kind": self.kind,
            "target_id": self.target_id,
            "value": self.value,
            "confidence": self.confidence,
            "label": self.label,
        }


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
    *,
    registration_confidence: Optional[float] = None,
    reg_min: Optional[float] = None,
) -> tuple[list[Detection], list[Detection]]:
    """Split detections into ``(auto_apply, queue_for_review)``.

    Used by the ingest pipeline to decide which CV-derived changes commit
    silently and which surface in the digital WO's pending-review panel. Two
    policies share this splitter; both keep the ``(auto, queue)`` shape.

    **OMR two-axis** — selected when ``registration_confidence`` is supplied
    (the scan-to-complete path). A mark auto-applies IFF the scan registered
    well enough (``registration_confidence >= reg_min``) AND its fill is
    confidently CHECKED (``det.confident_checked``). Fill-confidence and
    registration quality are thus independent axes: a solidly-ticked box on an
    ordinary (skewed, 4-corner) scan records automatically, while any ambiguous
    fill — or *every* mark on an inadequately-registered scan (a 3-corner affine
    read is hard-capped at 0.7, so ``reg_min > 0.7`` sends it wholesale to
    review) — routes to ``pending_changes``. ``reg_min`` defaults to
    ``OMR_REG_MIN``.

    **Single-axis confidence** — the default (born-digital signature/OCR path).
    A detection auto-applies when ``det.confidence >= threshold`` (defaults to
    ``confidence_threshold()``), a standalone certainty rather than an OMR
    fill × registration product.
    """
    auto: list[Detection] = []
    queue: list[Detection] = []

    if registration_confidence is not None:
        if reg_min is None:
            from inventory.services.work_order_omr import OMR_REG_MIN

            reg_min = OMR_REG_MIN
        registration_adequate = registration_confidence >= reg_min
        for det in detections:
            if registration_adequate and det.confident_checked:
                auto.append(det)
            else:
                queue.append(det)
        return auto, queue

    if threshold is None:
        threshold = confidence_threshold()
    for det in detections:
        if det.confidence >= threshold:
            auto.append(det)
        else:
            queue.append(det)
    return auto, queue
