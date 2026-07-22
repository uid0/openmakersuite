"""OMR (scan-to-complete) work-order form: version + snapshot persistence.

bead-1 of the OMR feature. ``generate_work_order_omr_pdf`` (in
``inventory.utils.work_order_pdf``) renders the form and its region map; this
module owns the *persistence* side — computing the template-drift signature and
upserting exactly one :class:`~inventory.models.WorkOrderOmrTemplate` per work
order.

The reader in bead-2 consumes the persisted snapshot: detect the 4 corner
fiducials in a scan, warp into template space, threshold each ``regions_json``
region, and refuse the scan if :func:`compute_template_version` recomputed from
the WO's *current* tasks no longer matches the stored ``template_version``.
"""

from __future__ import annotations

import hashlib
import io
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from django.db import transaction

if TYPE_CHECKING:
    from inventory.models import WorkOrder, WorkOrderOmrTemplate

logger = logging.getLogger(__name__)

# cv2/numpy back the reader. They are a hard dependency of the OMR feature
# (opencv-python-headless is already pinned and also generates the fiducials),
# but we import defensively so ``compute_template_version`` and the persistence
# helpers above stay importable — and a scan degrades to "route to review" —
# even in a stripped environment where the native libs are unavailable.
try:  # pragma: no cover - exercised implicitly; the except path needs a broken env
    import cv2
    import numpy as np

    _CV_IMPORT_ERROR: Optional[Exception] = None
except Exception as exc:  # noqa: BLE001 - any import failure means "no reader"
    cv2 = None  # type: ignore[assignment]
    np = None  # type: ignore[assignment]
    _CV_IMPORT_ERROR = exc


def dynamic_target_ids(work_order: "WorkOrder") -> list[str]:
    """The mark target-ids that vary per work order (tasks + materials + LOTO).

    Mirrors the checkbox field names ``generate_work_order_pdf`` draws exactly:
    one ``task_<uuid>`` per task completion, either ``material_<uuid>`` per usage
    row or — when a legacy WO has no usage rows — ``materialspec_<id>`` per
    maintenance-item material, and one ``loto_<uuid>`` per LOTO completion
    (energy source). The fixed completion marks
    (``work_complete``/``result_pass``/``result_fail``/``tech_initials``/
    ``tech_date``) are constant across every form and so are excluded from the
    drift signature. Including the ``loto_`` ids makes the signature sensitive to
    a changed energy-source set, so a scan of a sheet printed before the change
    is refused rather than mis-applied.
    """
    ids = [f"task_{tc.id}" for tc in work_order.task_completions.all()]

    material_usage = list(work_order.material_usage.all())
    if material_usage:
        ids += [f"material_{mu.id}" for mu in material_usage]
    elif work_order.maintenance_item_id:
        # Legacy fallback only: a work order with no usage rows borrows its PM
        # template's material spec. Corrective work orders have no template,
        # so they simply print no material boxes.
        ids += [f"materialspec_{mat.id}" for mat in work_order.maintenance_item.materials.all()]

    ids += [f"loto_{lc.id}" for lc in work_order.loto_completions.all()]
    return ids


def compute_template_version(work_order: "WorkOrder") -> int:
    """Stable content signature of a WO's current task/material set.

    Deterministic and order-independent: the same set of marks always hashes to
    the same 31-bit non-negative int (fits ``PositiveIntegerField``). bead-2
    recomputes this from the WO's live tasks and refuses a scan whose stored
    signature differs — the checklist changed since the sheet was printed.
    """
    payload = "\n".join(sorted(dynamic_target_ids(work_order)))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) & 0x7FFFFFFF


def build_and_persist_omr_template(
    work_order: "WorkOrder",
    base_url: str = "",
) -> "tuple[bytes, WorkOrderOmrTemplate]":
    """Render the OMR form and upsert its persisted region map.

    Returns ``(pdf_bytes, template)``. Exactly one template row is kept per work
    order: a reprint (same or edited tasks) replaces the snapshot in place.
    """
    from inventory.models import WorkOrderOmrTemplate
    from inventory.utils.work_order_pdf import generate_work_order_omr_pdf

    pdf_bytes, template_map = generate_work_order_omr_pdf(work_order, base_url)
    version = compute_template_version(work_order)

    with transaction.atomic():
        template, _created = WorkOrderOmrTemplate.objects.update_or_create(
            work_order=work_order,
            defaults={
                "template_version": version,
                "page_w_pt": template_map["page_w_pt"],
                "page_h_pt": template_map["page_h_pt"],
                "fiducial_dict": template_map["fiducial_dict"],
                "fiducials_json": template_map["fiducials"],
                "regions_json": template_map["regions"],
            },
        )
    return pdf_bytes, template


# ---------------------------------------------------------------------------
# bead-2: the scan reader
# ---------------------------------------------------------------------------
#
# Given a scan of a printed OMR form and the WO's persisted
# :class:`WorkOrderOmrTemplate` snapshot, recover the 4 corner fiducials, warp
# the scan into template space, and threshold every ``regions_json`` region so
# each mark becomes a ``(marked, confidence, fill_ratio)`` read. The scan-level
# ``registration_confidence`` and each mark's fill are then weighed as two
# independent axes at auto-apply time (``work_order_cv.auto_apply_or_queue``):
# an inadequately-registered scan routes wholesale to review, while a
# well-registered one auto-applies only its confidently-filled marks.

# Canonical warp resolution (dpi). 200dpi keeps a 10pt checkbox ~28px wide —
# comfortably above the threshold floor — without ballooning the warp buffer.
CANON_DPI = 200

# A near-perfect fiducial quad snaps its registration confidence to exactly 1.0.
# A quad this rectangular is geometrically aligned; subpixel marker-center
# jitter on a genuinely-square scan should not drag ``registration_confidence``
# toward the ``OMR_REG_MIN`` auto-apply floor, nor pull the per-region displayed
# confidence (which still scales by this score) off a clean 1.0.
_REG_SNAP_TO_ONE = 0.997

# Fraction of each region rect trimmed on every side before thresholding, so
# the *printed* box outline is excluded and only ADDED ink is measured.
_REGION_INSET = 0.18

# Checkbox fill-fraction decision bands (fraction of interior pixels read as
# ink). Tuned against the synthetic harness: a solid/scribbled mark clears
# ``_CHECK_ON`` with confidence 1.0; a faint partial mark lands in the
# ambiguous band and routes to review; a blank box sits under ``_CHECK_OFF``.
_CHECK_ON = 0.28
_CHECK_OFF = 0.06
# Ink write-ins (initials/date) are sparse strokes, so a much lower on-band.
_INK_ON = 0.05
_INK_OFF = 0.008


@dataclass
class OmrRegionRead:
    """One thresholded mark region recovered from a warped scan."""

    target_id: str
    kind: str
    marked: bool
    confidence: float  # already multiplied by the registration confidence
    fill_ratio: float


@dataclass
class OmrReadResult:
    """Outcome of reading one scanned OMR page against a template."""

    ok: bool
    error: Optional[str] = None
    registration_confidence: float = 0.0
    recovered_work_order_id: Optional[str] = None
    reads: list = field(default_factory=list)  # list[OmrRegionRead]
    canonical_size: Optional[tuple] = None  # (w_px, h_px)


def _family_cv2_dict(fiducial_dict: str):
    """Map a persisted ``fiducial_dict`` name onto a cv2 aruco dictionary."""
    from fiducials.services.apriltag_render import FAMILY_ARUCO_4X4_50

    mapping = {FAMILY_ARUCO_4X4_50: cv2.aruco.DICT_4X4_50}
    if fiducial_dict not in mapping:
        raise ValueError(f"Unsupported OMR fiducial dictionary {fiducial_dict!r}.")
    return cv2.aruco.getPredefinedDictionary(mapping[fiducial_dict])


def _load_gray(image) -> "Optional[np.ndarray]":
    """Coerce ``image`` (encoded bytes or an ndarray) to a uint8 grey array."""
    if isinstance(image, np.ndarray):
        arr = image
        if arr.ndim == 3:
            arr = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
        return np.ascontiguousarray(arr.astype(np.uint8))
    try:
        from PIL import Image, ImageOps

        pil = Image.open(io.BytesIO(image))
        pil.load()
        pil = ImageOps.exif_transpose(pil)
        return np.ascontiguousarray(np.array(pil.convert("L"), dtype=np.uint8))
    except Exception as exc:  # noqa: BLE001 - unreadable attachment -> registration fails
        logger.debug("OMR could not decode scan image: %s", exc)
        return None


def _detect_fiducial_centers(gray) -> dict:
    """Detect the 4 corner markers, returning ``{corner: (x, y)}`` scan-px centers."""
    from fiducials.services.apriltag_render import FORM_FIDUCIAL_IDS

    id_to_corner = {v: k for k, v in FORM_FIDUCIAL_IDS.items()}
    detector = cv2.aruco.ArucoDetector(
        _family_cv2_dict("aruco_4x4_50"), cv2.aruco.DetectorParameters()
    )
    corners, ids, _rejected = detector.detectMarkers(gray)
    centers: dict = {}
    if ids is None:
        return centers
    for quad, marker_id in zip(corners, ids.ravel().tolist()):
        corner = id_to_corner.get(int(marker_id))
        if corner is not None and corner not in centers:
            centers[corner] = quad.reshape(-1, 2).mean(axis=0).astype(np.float64)
    return centers


def _rectangularity(centers: dict) -> float:
    """Score how close the 4 detected marker centers are to a true rectangle.

    1.0 for an axis-aligned rectangle (a flat, square-on scan); it falls off
    with shear (skew) and keystoning (perspective), and collapses toward 0 for
    a grossly misdetected / non-convex quad. It is the scan's
    ``registration_confidence`` — the registration axis of the auto-apply
    decision: a read below ``OMR_REG_MIN`` (in particular any 3-corner affine
    read, hard-capped at 0.7) routes wholesale to review, independent of how
    confidently any individual box is filled.
    """
    try:
        p = {c: np.asarray(centers[c], dtype=np.float64) for c in ("tl", "tr", "br", "bl")}
    except KeyError:
        return 0.0

    def _norm(v):
        return float(np.hypot(v[0], v[1]))

    top = _norm(p["tr"] - p["tl"])
    bottom = _norm(p["br"] - p["bl"])
    left = _norm(p["bl"] - p["tl"])
    right = _norm(p["br"] - p["tr"])
    if min(top, bottom, left, right) < 1e-6:
        return 0.0
    side = (min(top, bottom) / max(top, bottom)) * (min(left, right) / max(left, right))

    d1 = _norm(p["br"] - p["tl"])
    d2 = _norm(p["tr"] - p["bl"])
    diag = min(d1, d2) / max(d1, d2)

    def _cos_at(a, b, c):
        u = p[a] - p[b]
        w = p[c] - p[b]
        denom = (_norm(u) * _norm(w)) or 1e-6
        return abs(float(np.dot(u, w)) / denom)

    right_angle = (
        1.0
        - (
            _cos_at("bl", "tl", "tr")
            + _cos_at("tl", "tr", "br")
            + _cos_at("tr", "br", "bl")
            + _cos_at("br", "bl", "tl")
        )
        / 4.0
    )

    score = max(0.0, min(1.0, side * diag * right_angle))
    return 1.0 if score >= _REG_SNAP_TO_ONE else score


def _canonical_dst(fiducials: dict, page_h_pt: float) -> dict:
    """Canonical (warped) pixel centre of each corner fiducial (y-down image px)."""
    scale = CANON_DPI / 72.0
    return {
        corner: (
            float(spec["cx"]) * scale,
            (page_h_pt - float(spec["cy"])) * scale,
        )
        for corner, spec in fiducials.items()
    }


def _region_px_rect(rect_norm, dst: dict) -> "Optional[tuple]":
    """Map a ``rect_norm`` (y-up, fiducial-centre normalised) to canonical px."""
    try:
        bl, br, tl = dst["bl"], dst["br"], dst["tl"]
    except KeyError:
        return None
    nx0, ny0, nx1, ny1 = rect_norm

    def _px_x(nx):
        return bl[0] + nx * (br[0] - bl[0])

    def _px_y(ny):  # ny is y-up; tl is higher on the page (smaller image y)
        return bl[1] + ny * (tl[1] - bl[1])

    xs = (_px_x(nx0), _px_x(nx1))
    ys = (_px_y(ny0), _px_y(ny1))
    return (min(xs), min(ys), max(xs), max(ys))


def _crop_region(warp, rect_px, inset: float = _REGION_INSET):
    """Crop the inset interior of a region from the warped page (excludes border)."""
    h, w = warp.shape[:2]
    x0, y0, x1, y1 = rect_px
    bw = x1 - x0
    bh = y1 - y0
    ix0 = int(round(x0 + bw * inset))
    iy0 = int(round(y0 + bh * inset))
    ix1 = int(round(x1 - bw * inset))
    iy1 = int(round(y1 - bh * inset))
    ix0 = max(0, min(w, ix0))
    ix1 = max(0, min(w, ix1))
    iy0 = max(0, min(h, iy0))
    iy1 = max(0, min(h, iy1))
    if ix1 <= ix0 or iy1 <= iy0:
        return None
    return warp[iy0:iy1, ix0:ix1]


def _ink_fill_ratio(interior, paper_white: float) -> float:
    """Fraction of interior pixels that are ADDED ink, robust to lighting.

    Combines a per-region *adaptive* threshold (catches strokes under a
    brightness gradient, where a global cutoff fails) with a global
    paper-relative absolute cutoff (``paper_white`` is the page's bright-paper
    level, catching solid fills where the local mean is itself dark and the
    adaptive pass under-reports).
    """
    interior = interior.astype(np.uint8)
    ih, iw = interior.shape[:2]

    block = max(3, (min(ih, iw) // 2) | 1)
    adaptive = cv2.adaptiveThreshold(
        interior, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, block, 10
    )
    ink = adaptive > 0

    abs_dark = interior < (0.5 * paper_white)

    return float(np.count_nonzero(ink | abs_dark)) / float(interior.size)


def _score_region(
    warp, rect_px, kind: str, reg_conf: float, paper_white: float
) -> "tuple[bool, float, float]":
    """Threshold one region → (marked, confidence, fill_ratio). Confidence is
    the decision certainty already multiplied by the registration confidence."""
    interior = _crop_region(warp, rect_px)
    if interior is None or interior.size == 0:
        return False, 0.0, 0.0

    fill = _ink_fill_ratio(interior, paper_white)
    on, off = (_INK_ON, _INK_OFF) if kind == "ink" else (_CHECK_ON, _CHECK_OFF)

    if fill >= on:
        marked = True
        raw = min(1.0, 0.9 + (fill - on) * 4.0)
    elif fill <= off:
        marked = False
        raw = min(1.0, 0.9 + (off - fill) * 10.0)
    else:
        mid = (on + off) / 2.0
        marked = fill > mid
        dist = abs(fill - mid) / ((on - off) / 2.0)
        raw = 0.5 + 0.4 * min(1.0, dist)

    return marked, max(0.0, min(1.0, raw * reg_conf)), fill


def _register_and_warp(gray, fiducials: dict, page_w_pt: float, page_h_pt: float):
    """Detect fiducials and warp ``gray`` into canonical template space.

    Returns ``(warp, reg_conf, error)``. ``warp`` is None (and ``error`` set)
    when fewer than 3 corners are found. 4 corners → exact perspective warp;
    exactly 3 → an affine fallback at reduced confidence (bead spec).
    """
    centers = _detect_fiducial_centers(gray)
    dst = _canonical_dst(fiducials, page_h_pt)
    present = [c for c in ("tl", "tr", "br", "bl") if c in centers and c in dst]
    w_px = max(1, int(round(page_w_pt / 72.0 * CANON_DPI)))
    h_px = max(1, int(round(page_h_pt / 72.0 * CANON_DPI)))

    if len(present) >= 4:
        src = np.array([centers[c] for c in ("tl", "tr", "br", "bl")], dtype=np.float32)
        dpts = np.array([dst[c] for c in ("tl", "tr", "br", "bl")], dtype=np.float32)
        matrix = cv2.getPerspectiveTransform(src, dpts)
        warp = cv2.warpPerspective(
            gray, matrix, (w_px, h_px), borderValue=255, flags=cv2.INTER_LINEAR
        )
        return warp, _rectangularity(centers), None

    if len(present) == 3:
        src = np.array([centers[c] for c in present], dtype=np.float32)
        dpts = np.array([dst[c] for c in present], dtype=np.float32)
        matrix = cv2.getAffineTransform(src, dpts)
        warp = cv2.warpAffine(gray, matrix, (w_px, h_px), borderValue=255, flags=cv2.INTER_LINEAR)
        # An affine model cannot correct keystoning; cap confidence at 0.7 so a
        # 3-corner read stays below OMR_REG_MIN and always routes to review.
        return warp, 0.7, None

    return None, 0.0, "could not align form: fewer than 3 corner fiducials detected"


def read_omr_scan(
    image, template, *, recovered_work_order_id: Optional[str] = None
) -> OmrReadResult:
    """Read a scanned OMR page against its persisted template snapshot.

    ``image`` is either encoded image bytes (JPG/PNG) or a numpy array (BGR or
    grey). ``template`` is a :class:`WorkOrderOmrTemplate` (or any object
    exposing ``fiducials_json``/``regions_json``/``page_w_pt``/``page_h_pt``/
    ``fiducial_dict``). DB-free and side-effect-free so it unit-tests directly.
    """
    if cv2 is None or np is None:  # pragma: no cover - only in a broken env
        return OmrReadResult(ok=False, error=f"opencv unavailable: {_CV_IMPORT_ERROR}")

    gray = _load_gray(image)
    if gray is None or gray.size == 0:
        return OmrReadResult(ok=False, error="could not decode scan image")

    fiducials = dict(template.fiducials_json or {})
    regions = list(template.regions_json or [])
    page_w_pt = float(template.page_w_pt)
    page_h_pt = float(template.page_h_pt)

    try:
        warp, reg_conf, error = _register_and_warp(gray, fiducials, page_w_pt, page_h_pt)
    except Exception as exc:  # noqa: BLE001 - a CV failure must route to review, never 500
        logger.warning("OMR registration failed: %s", exc)
        return OmrReadResult(ok=False, error=f"could not align form: {exc}")

    if warp is None:
        return OmrReadResult(ok=False, error=error, registration_confidence=reg_conf)

    dst = _canonical_dst(fiducials, page_h_pt)
    # Bright-paper level for the whole warped page — the absolute-darkness
    # baseline for every region (computed once, not per region).
    paper_white = float(np.percentile(warp, 92)) or 255.0
    reads: list = []
    for region in regions:
        rect_px = _region_px_rect(region["rect_norm"], dst)
        if rect_px is None:
            continue
        marked, confidence, fill = _score_region(
            warp, rect_px, region.get("kind", "checkbox"), reg_conf, paper_white
        )
        reads.append(
            OmrRegionRead(
                target_id=region["target_id"],
                kind=region.get("kind", "checkbox"),
                marked=marked,
                confidence=confidence,
                fill_ratio=fill,
            )
        )

    return OmrReadResult(
        ok=True,
        error=None,
        registration_confidence=reg_conf,
        recovered_work_order_id=recovered_work_order_id,
        reads=reads,
        canonical_size=(warp.shape[1], warp.shape[0]),
    )


def render_mark_crop(image, template, target_id: str, *, pad: float = 0.35) -> Optional[bytes]:
    """Return a small PNG of one warped mark region, for the reviewer to eyeball.

    Re-warps the scan into template space and crops the ``target_id`` region
    (with a little context padding). Returns None if the scan cannot be aligned
    or the target is unknown. Recomputed on demand — nothing is persisted.
    """
    if cv2 is None or np is None:  # pragma: no cover
        return None
    gray = _load_gray(image)
    if gray is None or gray.size == 0:
        return None

    fiducials = dict(template.fiducials_json or {})
    page_w_pt = float(template.page_w_pt)
    page_h_pt = float(template.page_h_pt)
    try:
        warp, _reg, _err = _register_and_warp(gray, fiducials, page_w_pt, page_h_pt)
    except Exception:  # noqa: BLE001
        return None
    if warp is None:
        return None

    region = next(
        (r for r in (template.regions_json or []) if r.get("target_id") == target_id), None
    )
    if region is None:
        return None
    rect_px = _region_px_rect(region["rect_norm"], _canonical_dst(fiducials, page_h_pt))
    if rect_px is None:
        return None

    h, w = warp.shape[:2]
    x0, y0, x1, y1 = rect_px
    bw, bh = x1 - x0, y1 - y0
    cx0 = max(0, int(round(x0 - bw * pad)))
    cy0 = max(0, int(round(y0 - bh * pad)))
    cx1 = min(w, int(round(x1 + bw * pad)))
    cy1 = min(h, int(round(y1 + bh * pad)))
    if cx1 <= cx0 or cy1 <= cy0:
        return None

    crop = warp[cy0:cy1, cx0:cx1]
    ok, buf = cv2.imencode(".png", crop)
    if not ok:
        return None
    return buf.tobytes()


def detections_from_result(result: OmrReadResult) -> list:
    """Convert an :class:`OmrReadResult` into ``work_order_cv.Detection`` rows.

    Only *marked* regions and *uncertain* reads are emitted — a confidently
    blank checkbox is a no-op and would only clutter the review queue. Each
    detection carries its ``fill_ratio`` and a ``confident_checked`` flag (the
    mark is present AND its fill sits cleanly in the "on" band); the caller
    pairs that per-mark fill-confidence with the scan-level
    ``registration_confidence`` in ``auto_apply_or_queue`` to decide auto-apply
    vs review.
    """
    from inventory.services.work_order_cv import Detection

    detections: list = []
    for read in result.reads:
        on = _INK_ON if read.kind == "ink" else _CHECK_ON
        off = _INK_OFF if read.kind == "ink" else _CHECK_OFF
        if not read.marked and read.fill_ratio <= off:
            continue  # clearly empty box — a no-op that would only clutter review
        detections.append(
            Detection(
                kind=read.kind,
                target_id=read.target_id,
                value=bool(read.marked),
                confidence=float(read.confidence),
                label="",
                fill_ratio=float(read.fill_ratio),
                confident_checked=bool(read.marked and read.fill_ratio >= on),
            )
        )
    return detections


# Scan auto-apply policy (Ian, 07-14 — revises the 07-08 flatbed-only 0.999
# bar). Auto-apply is two-axis (see ``work_order_cv.auto_apply_or_queue``): a
# mark commits silently only when the scan registered adequately AND its box is
# confidently filled. ``OMR_REG_MIN`` is the registration floor for the
# "adequate" half. It MUST stay above the 0.7 three-corner affine cap
# (``_register_and_warp``) so any read with fewer than 4 recovered fiducials
# always routes to human review; a clean or ordinarily-skewed 4-corner scan
# clears it, and only then do its confidently-checked marks auto-apply.
OMR_REG_MIN = 0.8
