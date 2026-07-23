"""
Ingestion of completed work-order PDFs (Postmark inbound webhook + manual
staff upload).

Pipeline:
  1. The Postmark webhook view (see `inventory.views.postmark_inbound_work_order`)
     or the staff manual-upload endpoint (`WorkOrderViewSet.upload_pdf`) stores
     the raw PDF attachment on a `WorkOrderSubmission` row.
  2. `apply_submission` is invoked on that submission. It:
       a. extracts the Work Order UUID using a defense-in-depth fallback chain
          (AcroForm field → embedded QR code → plain-text regex)
       b. reads the AcroForm checkbox field values via pypdf
       c. for each `task_<uuid>` / `material_<uuid>` field that is checked,
          marks the corresponding WorkOrderTaskCompletion / WorkOrderMaterialUsage
       d. attaches the PDF to WorkOrder.completed_scan
       e. if all required task steps are complete, transitions the WorkOrder to
          COMPLETED, stamps MaintenanceItem.last_completed_at, and appends a
          MaintenanceLog entry for the maintenance history.

Why the fallback chain matters: the original parser only read PDF text, which
fails silently when a user prints the form, fills it by hand, then scans the
result back to PDF — every page becomes a single embedded image with no
extractable text. We try the most reliable source first (AcroForm field, set
by the generator and preserved across re-saves), then the QR code on the
page (works for image-based PDFs), then a text regex on the footer (works for
unmodified born-digital PDFs).

Checkbox field value convention (reportlab + pypdf): unchecked fields have a
value of NameObject("/Off") (or no "/V" entry at all); checked fields are
NameObject("/Yes").
"""

import io
import logging
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import List, Optional, Tuple

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from pypdf import PdfReader

from ..models import MaintenanceLog, WorkOrder, WorkOrderMaterialUsage, WorkOrderSubmission
from .problem_auto_resolve import resolve_problems_for_work_order
from .work_order_material_usage import apply_material_usage

logger = logging.getLogger(__name__)

UUID_RE = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-" r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)
WORK_ORDER_ID_TEXT_RE = re.compile(r"Work Order ID:\s*" + UUID_RE.pattern)
THIRD_PARTY_WO_ID_TEXT_RE = re.compile(r"Third-Party Work Order ID:\s*" + UUID_RE.pattern)
THIRD_PARTY_HEADER_RE = re.compile(r"Third[- ]Party Work Order", re.IGNORECASE)
THIRD_PARTY_SUBJECT_PREFIX = "[3PWO]"
TPWO_QR_PREFIX = "tpwo:"

LOCATION_PROBLEM_HEADER_RE = re.compile(r"Location Problem Report", re.IGNORECASE)
LOCATION_PROBLEM_SUBJECT_PREFIX = "[LOCPROB]"
LOCATION_QR_PREFIX = "location:"
LOCATION_ID_TEXT_RE = re.compile(r"Location ID:\s*(\d+)")
SEVERITY_TEXT_RE = re.compile(r"Severity:\s*(low|medium|high|urgent)", re.IGNORECASE)


def _field_is_checked(value) -> bool:
    """Return True if a pypdf AcroForm field value represents a checked state."""
    if value is None:
        return False
    name = getattr(value, "name", None)
    if name is not None:
        return name.lower() == "yes"
    return str(value).lstrip("/").lower() == "yes"


def _field_value(field) -> Optional[str]:
    """Pull the string-ish "/V" value off a pypdf field, or None."""
    try:
        raw = field.get("/V") if hasattr(field, "get") else None
    except Exception:  # noqa: BLE001
        return None
    if raw is None:
        return None
    name = getattr(raw, "name", None)
    if name is not None:
        return name
    return str(raw)


def _extract_id_from_acroform(reader: PdfReader) -> Tuple[Optional[str], Optional[str]]:
    """Return (wo_id, error). The generator emits a hidden 'work_order_id' field."""
    try:
        fields = reader.get_fields() or {}
    except Exception as exc:  # noqa: BLE001
        return None, f"acroform: failed to read fields ({exc})"
    field = fields.get("work_order_id")
    if field is None:
        return None, "acroform: 'work_order_id' field not present"
    value = _field_value(field)
    if not value:
        return None, "acroform: 'work_order_id' field is empty"
    match = UUID_RE.search(value)
    if not match:
        return None, f"acroform: value '{value[:60]}' is not a UUID"
    return match.group(1), None


def _iter_pdf_images(reader: PdfReader):
    """Yield every embedded image's raw bytes across all pages."""
    for page in reader.pages:
        try:
            images = list(page.images)
        except Exception:  # noqa: BLE001  # nosec B112 - pypdf raises many types on odd PDFs
            continue
        for img in images:
            try:
                yield img.data
            except Exception:  # noqa: BLE001  # nosec B112
                continue


def _decode_qr_payloads(image_bytes: bytes) -> List[str]:
    """Try every available QR decoder, return all decoded payloads."""
    payloads: List[str] = []

    # cv2 — pip-only, no system deps. Primary path.
    try:
        import cv2  # type: ignore
        import numpy as np
        from PIL import Image

        pil = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        arr = np.array(pil)

        # OpenCV ships two independent QR backends. The legacy QRCodeDetector
        # silently mis-decodes ~1% of otherwise-clean QR images (the outcome is
        # bit-pattern dependent, not a quality/resolution issue). Because the
        # work-order QR carries a *random* UUID, that ~1% turned into a random
        # CI flake — extraction returned None for the unlucky ids and the QR
        # fallback test failed with "assert None == <uuid>" (op-6t2). The
        # newer ArUco-based detector finds the finder pattern differently and
        # decodes the cases the legacy detector drops (0 misses over 1500
        # samples vs the legacy detector's ~17). So try the legacy detector
        # first (fast, succeeds ~99% of the time) and fall back to the ArUco
        # detector before giving up. getattr-guarded for OpenCV < 4.7.
        detectors = [cv2.QRCodeDetector()]
        aruco_qr_cls = getattr(cv2, "QRCodeDetectorAruco", None)
        if aruco_qr_cls is not None:
            detectors.append(aruco_qr_cls())

        for detector in detectors:
            # Try multi-detect first (PDFs may carry multiple QRs); fall back to
            # single-detect since multi can return False for clean single-QR pages.
            try:
                ok, decoded, _, _ = detector.detectAndDecodeMulti(arr)
                if ok and decoded:
                    payloads.extend([d for d in decoded if d])
            except Exception:  # noqa: BLE001  # nosec B110 - cv2 errors on bad images
                pass
            if not payloads:
                try:
                    data, _, _ = detector.detectAndDecode(arr)
                    if data:
                        payloads.append(data)
                except Exception:  # noqa: BLE001  # nosec B110
                    pass
            if payloads:
                break
    except Exception as exc:  # noqa: BLE001 - cv2 missing or image unreadable
        logger.debug("cv2 QR decode skipped: %s", exc)

    # pyzbar as a secondary attempt — only if libzbar0 is installed.
    if not payloads:
        try:
            from PIL import Image
            from pyzbar.pyzbar import decode as pyzbar_decode

            pil = Image.open(io.BytesIO(image_bytes))
            for sym in pyzbar_decode(pil):
                try:
                    payloads.append(sym.data.decode("utf-8", errors="replace"))
                except Exception:  # noqa: BLE001  # nosec B112
                    continue
        except Exception as exc:  # noqa: BLE001
            logger.debug("pyzbar QR decode skipped: %s", exc)

    return payloads


def _extract_id_from_qr(reader: PdfReader) -> Tuple[Optional[str], Optional[str]]:
    """Decode every embedded image, scan each payload for a UUID."""
    image_count = 0
    decoded_count = 0
    for image_bytes in _iter_pdf_images(reader):
        image_count += 1
        payloads = _decode_qr_payloads(image_bytes)
        for payload in payloads:
            decoded_count += 1
            match = UUID_RE.search(payload)
            if match:
                return match.group(1), None
    if image_count == 0:
        return None, "qr: no embedded images found in PDF"
    if decoded_count == 0:
        return None, f"qr: {image_count} image(s) found but none decoded as a QR code"
    return None, f"qr: {decoded_count} QR payload(s) decoded but none contained a UUID"


def _extract_id_from_text(reader: PdfReader) -> Tuple[Optional[str], Optional[str]]:
    """Regex 'Work Order ID: <uuid>' over concatenated page text."""
    text_chunks: List[str] = []
    for page in reader.pages:
        try:
            text_chunks.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001  # nosec B112
            continue
    text = "\n".join(text_chunks)
    if not text.strip():
        return None, "text: pypdf extracted no text from PDF (likely image-only)"
    match = WORK_ORDER_ID_TEXT_RE.search(text)
    if match:
        return match.group(1), None
    return None, "text: 'Work Order ID: <uuid>' marker not present in extracted text"


def extract_work_order_id(reader: PdfReader) -> Tuple[Optional[str], List[str]]:
    """
    Try AcroForm → QR → text in order. First match wins. Returns the UUID
    and a list of human-readable failure messages from the methods that did
    not produce the answer (useful for surfacing to the user).
    """
    errors: List[str] = []
    for extractor in (_extract_id_from_acroform, _extract_id_from_qr, _extract_id_from_text):
        wo_id, err = extractor(reader)
        if wo_id:
            return wo_id, errors
        if err:
            errors.append(err)
    return None, errors


def parse_work_order_pdf(pdf_bytes: bytes) -> dict:
    """
    Parse a work-order PDF and return identifiers + checkbox state.

    Returns a dict with keys:
        work_order_id:       the UUID string, or None if not found
        extraction_errors:   list of per-method failure messages (empty when
                             a method succeeded on its first try)
        task_checks:         {task_completion_id: bool}
        material_checks:     {material_usage_id: bool}
        loto_checks:         {loto_completion_id: bool}
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))

    work_order_id, extraction_errors = extract_work_order_id(reader)

    task_checks: dict[str, bool] = {}
    material_checks: dict[str, bool] = {}
    loto_checks: dict[str, bool] = {}

    fields = reader.get_fields() or {}
    for name, field in fields.items():
        try:
            value = field.get("/V") if hasattr(field, "get") else None
        except Exception:  # noqa: BLE001
            value = None
        checked = _field_is_checked(value)
        if name.startswith("task_"):
            task_checks[name[len("task_") :]] = checked
        elif name.startswith("material_"):
            material_checks[name[len("material_") :]] = checked
        elif name.startswith("loto_"):
            loto_checks[name[len("loto_") :]] = checked

    return {
        "work_order_id": work_order_id,
        "extraction_errors": extraction_errors,
        "task_checks": task_checks,
        "material_checks": material_checks,
        "loto_checks": loto_checks,
    }


def _resolve_work_order(
    submission: WorkOrderSubmission, parsed: dict
) -> Tuple[Optional[WorkOrder], Optional[str]]:
    wo_id = parsed.get("work_order_id")
    if not wo_id:
        errors = parsed.get("extraction_errors") or []
        if errors:
            tried = "; ".join(errors)
            return None, f"Could not find a Work Order ID marker in the PDF. Tried: {tried}"
        return None, "Could not find a Work Order ID marker in the PDF."
    try:
        return WorkOrder.objects.get(id=wo_id), None
    except WorkOrder.DoesNotExist:
        return None, f"Work order {wo_id} not found."


def apply_submission(submission: WorkOrderSubmission) -> WorkOrderSubmission:
    """
    Parse the submission's PDF and dispatch to the right downstream handler
    based on ``submission.kind``.

      - ``KIND_PM_COMPLETION`` → updates a PM ``WorkOrder`` in place.
      - ``KIND_THIRD_PARTY_WO`` → updates a ``ThirdPartyWorkOrder`` and
        advances it to ``validated``.

    Both branches are safe to call multiple times for the same submission.
    """
    submission.attachment.open("rb")
    try:
        pdf_bytes = submission.attachment.read()
    finally:
        submission.attachment.close()

    # OMR scan path (bead-2): a scan of a printed PM work-order form. The
    # interactive AcroForm layer is gone, so marks are recovered by computer
    # vision and split by the two-axis auto-apply policy (registration adequacy
    # × fill confidence): a confidently-filled mark on an adequately-registered
    # scan applies, everything else routes to human review. Only PM-completion
    # scans use OMR; 3PWO/LOCPROB keep their existing QR/text handlers.
    if (
        submission.source == WorkOrderSubmission.Source.SCAN
        and submission.kind == WorkOrderSubmission.Kind.PM_COMPLETION
    ):
        return _apply_omr_submission(submission, pdf_bytes)

    if submission.kind == WorkOrderSubmission.Kind.THIRD_PARTY_WO:
        return _apply_third_party_submission(submission, pdf_bytes)
    if submission.kind == WorkOrderSubmission.Kind.LOCATION_PROBLEM:
        return _apply_location_problem_submission(submission, pdf_bytes)
    return _apply_pm_submission(submission, pdf_bytes)


@transaction.atomic
def _apply_pm_submission(submission: WorkOrderSubmission, pdf_bytes: bytes) -> WorkOrderSubmission:
    """PM-completion path: marks task completions on a ``WorkOrder``."""
    try:
        parsed = parse_work_order_pdf(pdf_bytes)
    except Exception as exc:  # noqa: BLE001 - defensive: malformed PDF
        logger.exception("Failed to parse work order submission %s", submission.id)
        submission.status = WorkOrderSubmission.Status.FAILED
        submission.parse_error = f"Failed to parse PDF: {exc}"
        submission.save(update_fields=["status", "parse_error"])
        return submission

    submission.parsed_fields = parsed

    work_order, err = _resolve_work_order(submission, parsed)
    if err:
        submission.status = WorkOrderSubmission.Status.FAILED
        submission.parse_error = err
        submission.save(update_fields=["status", "parse_error", "parsed_fields"])
        return submission

    submission.work_order = work_order

    # AC-4 (oms-2da): run image-based CV detections (signatures, OCR'd
    # handwritten notes) and split them into auto-apply vs human-review by
    # confidence threshold. AcroForm checkbox values are already binary 1.0
    # confidence and remain on the auto-apply path.
    try:
        cv_detections = _cv_detect_completion(io.BytesIO(pdf_bytes))
    except Exception:  # noqa: BLE001 - CV failures must not block ingest
        logger.exception("CV detection failed for submission %s", submission.id)
        cv_detections = []

    from .work_order_cv import auto_apply_or_queue

    auto_cv, queue_cv = auto_apply_or_queue(cv_detections)

    now = timezone.now()
    completion_note = "Completed via emailed paper work order."

    # Apply task checkbox transitions (only uncompleted → completed).
    applied_tasks = 0
    task_qs = work_order.task_completions.filter(id__in=parsed["task_checks"].keys())
    for tc in task_qs:
        if parsed["task_checks"].get(str(tc.id)) and not tc.is_completed:
            tc.is_completed = True
            tc.completed_at = now
            tc.notes = (tc.notes + "\n" + completion_note).strip() if tc.notes else completion_note
            tc.save(update_fields=["is_completed", "completed_at", "notes"])
            applied_tasks += 1

    # Apply material usage (only false → true). Routes through the shared
    # helper so a used material decrements its linked inventory item and logs
    # usage (idempotent + reversible), exactly like the manual/OMR paths.
    material_qs = WorkOrderMaterialUsage.objects.select_related("material__inventory_item").filter(
        work_order=work_order,
        id__in=parsed["material_checks"].keys(),
    )
    for mu in material_qs:
        if parsed["material_checks"].get(str(mu.id)) and not mu.was_used:
            mu.work_order = work_order  # prime FK cache for the usage note
            apply_material_usage(mu, was_used=True, source_note="emailed paper work order")

    # Apply LOTO energy-source completions (only false → true). Structured safety
    # data only — deliberately NOT part of the WO-completion gate below (that
    # stays required-tasks-based). Mirrors the task/material paths for a
    # digitally-filled form emailed in (the scan path uses omr_apply_mark).
    loto_checks = parsed.get("loto_checks") or {}
    loto_qs = work_order.loto_completions.filter(id__in=loto_checks.keys())
    for lc in loto_qs:
        if loto_checks.get(str(lc.id)) and not lc.is_completed:
            lc.is_completed = True
            lc.completed_at = now
            lc.notes = (lc.notes + "\n" + completion_note).strip() if lc.notes else completion_note
            lc.save(update_fields=["is_completed", "completed_at", "notes"])

    # Attach PDF to the work order's maintenance history.
    if not work_order.completed_scan:
        work_order.completed_scan.save(
            f"wo-{work_order.short_id}-completed.pdf",
            ContentFile(pdf_bytes),
            save=False,
        )

    # Decide overall WO status.
    required_total = work_order.task_completions.filter(is_required=True).count()
    required_done = work_order.task_completions.filter(is_required=True, is_completed=True).count()

    wo_became_complete = False
    if required_total > 0 and required_done >= required_total:
        if work_order.status != WorkOrder.Status.COMPLETED:
            work_order.status = WorkOrder.Status.COMPLETED
            work_order.completed_at = now
            wo_became_complete = True
    elif applied_tasks > 0 and work_order.status == WorkOrder.Status.OPEN:
        work_order.status = WorkOrder.Status.IN_PROGRESS

    work_order.save()

    if wo_became_complete:
        # Whatever problem report this work order was promoted from closes with
        # it — a form mailed in completes the work just as the screen does.
        resolve_problems_for_work_order(work_order, notes=work_order.notes or "")
        # Corrective work orders have no PM template to advance, and
        # ``MaintenanceLog.maintenance_item`` is non-nullable — there is no log
        # to write for them. The work order's own completion stamp is the record.
        item = work_order.maintenance_item
        if item is not None:
            item.last_completed_at = now
            item.save(update_fields=["last_completed_at"])
            MaintenanceLog.objects.create(
                maintenance_item=item,
                completed_by=None,
                notes=(
                    f"Completed via emailed paper work order "
                    f"(submission {submission.id}, WO {work_order.short_id})."
                ),
            )

    # Apply auto-confidence CV detections inline; queue the rest.
    cv_notes_to_append: list[str] = []
    for det in auto_cv:
        if det.kind == "signature":
            cv_notes_to_append.append(
                f"Signature detected on paper form (confidence {det.confidence:.2f})."
            )
        elif det.kind == "handwritten" and isinstance(det.value, str) and det.value:
            cv_notes_to_append.append(
                f'Handwritten note (OCR, confidence {det.confidence:.2f}): "{det.value}"'
            )
    if cv_notes_to_append:
        existing = work_order.notes or ""
        combined = existing + ("\n" if existing else "") + "\n".join(cv_notes_to_append)
        work_order.notes = combined.strip()

    submission.pending_changes = [d.to_dict() for d in queue_cv]

    # If we have any low-confidence detections, the submission is held for
    # human review rather than marked applied — the digital UI surfaces the
    # pending list for accept/reject.
    if queue_cv:
        submission.status = WorkOrderSubmission.Status.PENDING_REVIEW
    else:
        submission.status = WorkOrderSubmission.Status.APPLIED
    submission.parse_error = ""
    submission.save(
        update_fields=[
            "status",
            "work_order",
            "parsed_fields",
            "parse_error",
            "pending_changes",
        ]
    )
    return submission


# ---------------------------------------------------------------------------
# OMR scan ingestion (bead-2)
# ---------------------------------------------------------------------------

# Human-readable labels for the fixed completion marks every OMR form carries.
OMR_FIXED_MARK_LABELS = {
    "work_complete": "Work complete",
    "result_pass": "Result: pass",
    "result_fail": "Result: fail",
    "tech_initials": "Technician initials",
    "tech_date": "Date signed",
}


def looks_like_scan(raw_bytes: bytes, *, is_image: bool = False) -> bool:
    """Decide whether an inbound attachment is a scan (vs born-digital).

    An image attachment is always a scan. A PDF is a scan when its interactive
    AcroForm layer is gone (no ``work_order_id`` field) but it still carries an
    embedded page raster — i.e. a printed form that was scanned back. A
    born-digital OMR form keeps its ``work_order_id`` field and stays on the
    fast AcroForm path.
    """
    if is_image:
        return True
    try:
        reader = PdfReader(io.BytesIO(raw_bytes))
        fields = reader.get_fields() or {}
        if "work_order_id" in fields:
            return False
        return any(True for _ in _iter_pdf_images(reader))
    except Exception:  # noqa: BLE001  # nosec B110 - unreadable => not a scan we can OMR
        return False


def _extract_id_from_image(image_bytes: bytes) -> Tuple[Optional[str], Optional[str]]:
    """Recover the WO UUID from a scanned image's QR code (no PDF wrapper)."""
    try:
        payloads = _decode_qr_payloads(image_bytes)
    except Exception as exc:  # noqa: BLE001
        return None, f"qr: decode failed ({exc})"
    for payload in payloads:
        match = UUID_RE.search(payload)
        if match:
            return match.group(0), None
    if payloads:
        return None, "qr: QR payload(s) decoded but none contained a UUID"
    return None, "qr: no QR code decoded from the scanned image"


def _omr_scan_inputs(raw_bytes: bytes) -> "Tuple[Optional[str], Optional[str], Optional[bytes]]":
    """Return ``(work_order_id, id_error, image_bytes)`` for a scan attachment.

    Handles both a scanned PDF (recover id via the AcroForm→QR→text chain, page
    raster via the first embedded image) and a directly-attached JPG/PNG.
    """
    if raw_bytes[:5] == b"%PDF-":
        try:
            reader = PdfReader(io.BytesIO(raw_bytes))
        except Exception as exc:  # noqa: BLE001
            return None, f"Failed to read scanned PDF: {exc}", None
        wo_id, errors = extract_work_order_id(reader)
        id_err = None if wo_id else "; ".join(errors or []) or "no id marker in scan"
        image_bytes = next(_iter_pdf_images(reader), None)
        return wo_id, id_err, image_bytes
    wo_id, id_err = _extract_id_from_image(raw_bytes)
    return wo_id, id_err, raw_bytes


def _omr_label(
    target_id: str, task_titles: dict, material_names: dict, loto_labels: Optional[dict] = None
) -> str:
    loto_labels = loto_labels or {}
    if target_id in OMR_FIXED_MARK_LABELS:
        return OMR_FIXED_MARK_LABELS[target_id]
    if target_id.startswith("task_"):
        return task_titles.get(target_id[len("task_") :], target_id)
    if target_id.startswith("material_"):
        return material_names.get(target_id[len("material_") :], target_id)
    if target_id.startswith("materialspec_"):
        return f"Material {target_id[len('materialspec_') :]}"
    if target_id.startswith("loto_"):
        label = loto_labels.get(target_id[len("loto_") :])
        return f"LOTO: {label}" if label else target_id
    return target_id


def omr_apply_mark(
    work_order: WorkOrder, target_id: str, *, marked: bool, now=None, note: str = "", actor=None
) -> int:
    """Apply (or undo) a single task/material mark. Returns 1 if state changed.

    Fixed completion marks (``work_complete`` etc.) carry no task/material
    state and are intentionally a no-op here — WO closure is a separate,
    human-confirmed step.

    A material mark routes through :func:`apply_material_usage`, so accepting a
    scanned "used" mark decrements the linked inventory item and logs usage, and
    rejecting/undoing it reverses both. ``actor`` (the reviewing user, when
    present) is attributed on the usage log.
    """
    now = now or timezone.now()
    if target_id.startswith("task_"):
        tc = work_order.task_completions.filter(id=target_id[len("task_") :]).first()
        if tc is None or tc.is_completed == marked:
            return 0
        tc.is_completed = marked
        tc.completed_at = now if marked else None
        if marked and note:
            tc.notes = (tc.notes + "\n" + note).strip() if tc.notes else note
        tc.save(update_fields=["is_completed", "completed_at", "notes"])
        return 1
    if target_id.startswith("material_"):
        mu = (
            WorkOrderMaterialUsage.objects.select_related("material__inventory_item")
            .filter(work_order=work_order, id=target_id[len("material_") :])
            .first()
        )
        if mu is None or mu.was_used == marked:
            return 0
        mu.work_order = work_order  # prime FK cache for the usage note
        apply_material_usage(mu, was_used=marked, actor=actor, source_note=note)
        return 1
    if target_id.startswith("loto_"):
        # LOTO energy-source completion. Rides the SAME two-axis auto-apply gate
        # as tasks (a confidently-filled box on a well-registered scan; anything
        # in doubt is queued upstream in ``auto_apply_or_queue``) — this only
        # flips the structured checkbox datum, never closes the WO. Mirrors the
        # task branch exactly, including leaving ``completed_by`` unattributed
        # (a scan can't know who did the work).
        lc = work_order.loto_completions.filter(id=target_id[len("loto_") :]).first()
        if lc is None or lc.is_completed == marked:
            return 0
        lc.is_completed = marked
        lc.completed_at = now if marked else None
        if marked and note:
            lc.notes = (lc.notes + "\n" + note).strip() if lc.notes else note
        lc.save(update_fields=["is_completed", "completed_at", "notes"])
        return 1
    return 0


def omr_confirm_completion(
    work_order: WorkOrder, submission: WorkOrderSubmission, user=None
) -> bool:
    """Human-confirmed WO completion from a reviewed scan.

    This is the ONLY place a scanned submission may advance a work order to
    COMPLETED (design rule: never auto-close from a scan). Closes the WO — and
    runs the same maintenance-history side effects as the born-digital path —
    only when every required task is complete. Returns True if it closed.
    """
    required_total = work_order.task_completions.filter(is_required=True).count()
    required_done = work_order.task_completions.filter(is_required=True, is_completed=True).count()
    if not (required_total > 0 and required_done >= required_total):
        return False
    if work_order.status == WorkOrder.Status.COMPLETED:
        return False
    from inventory.services.work_order_timer import (
        apply_elapsed_to_log,
        finalize_work_order_timers,
    )

    now = timezone.now()
    work_order.status = WorkOrder.Status.COMPLETED
    work_order.completed_at = now
    work_order.save(update_fields=["status", "completed_at", "updated_at"])
    # A WO started on-screen and closed off a scanned sheet must not be left
    # with a clock running — same finalize the digital completion path uses.
    finalize_work_order_timers(work_order, now=now)
    # Same as the digital path: the report this work order came from closes too.
    resolve_problems_for_work_order(work_order, actor=user, notes=work_order.notes or "")
    # No PM template on a corrective work order, and no MaintenanceLog either:
    # the log's ``maintenance_item`` is non-nullable, so there is nothing to
    # write. The elapsed time stays on the work order itself.
    item = work_order.maintenance_item
    if item is not None:
        item.last_completed_at = now
        item.save(update_fields=["last_completed_at"])
        log = MaintenanceLog.objects.create(
            maintenance_item=item,
            completed_by=user if (user and getattr(user, "is_authenticated", False)) else None,
            notes=(
                f"Completed via reviewed scan "
                f"(submission {submission.id}, WO {work_order.short_id})."
            ),
        )
        apply_elapsed_to_log(log, work_order)
    return True


def _notify_wo_scanned(
    submission: WorkOrderSubmission, work_order: Optional[WorkOrder], *, degraded: bool
) -> None:
    """Fire an app-wide "a scan needs review" notification for a scanned-in WO.

    Registered via ``transaction.on_commit`` at each terminal PENDING_REVIEW
    save (the clean read at the end of ``_apply_omr_submission`` and the
    degraded ``_omr_review`` path) so it fires exactly once per ingest and only
    after the ingest actually commits — never on a rolled-back atomic block, and
    never from ``_omr_fail`` (a hard failure is out of scope). Wrapped so a
    notification failure can never break ingest (precedent: ``inventory/views``
    ``add_photo``). Consumers: the existing header bell / notification centre,
    which polls; the ``action_url`` deep-links to the WO review screen.
    """
    if work_order is None:
        return
    try:
        from notifications.services import notify_admins

        count = len(submission.pending_changes or [])
        if degraded:
            message = (
                f"{work_order.short_id} scanned in but could not be read cleanly "
                "— open it to review by hand."
            )
        else:
            noun = "mark" if count == 1 else "marks"
            message = f"{work_order.short_id} scanned in — {count} {noun} to confirm."
        notify_admins(
            type="warning",
            title="Work order scanned — needs review",
            message=message,
            action_url=f"/maintenance/work-orders/{work_order.id}",
            metadata={
                "work_order_id": str(work_order.id),
                "submission_id": str(submission.id),
                "kind": "work_order_scanned",
            },
        )
    except Exception:  # noqa: BLE001 - a notification must never break ingest
        logger.exception("Failed to emit scan-in notification for submission %s", submission.id)


def _omr_fail(submission: WorkOrderSubmission, message: str) -> WorkOrderSubmission:
    """Unrecoverable scan (no WO id / WO missing): mark FAILED."""
    submission.status = WorkOrderSubmission.Status.FAILED
    submission.parse_error = message
    submission.save(update_fields=["status", "parse_error", "work_order"])
    return submission


def _omr_review(
    submission: WorkOrderSubmission, work_order: Optional[WorkOrder], message: str
) -> WorkOrderSubmission:
    """Recoverable scan problem (bad alignment / template drift / no template):
    hold for human review with an explanatory message and apply nothing."""
    submission.work_order = work_order
    submission.status = WorkOrderSubmission.Status.PENDING_REVIEW
    submission.parse_error = message
    submission.pending_changes = []
    submission.save(update_fields=["status", "parse_error", "work_order", "pending_changes"])
    # Degraded scans still need a human — surface them app-wide too (op-o6rs).
    transaction.on_commit(lambda: _notify_wo_scanned(submission, work_order, degraded=True))
    return submission


@transaction.atomic
def _apply_omr_submission(submission: WorkOrderSubmission, raw_bytes: bytes) -> WorkOrderSubmission:
    """Read marks off an OMR scan and stage them for human review.

    Auto-apply is two-axis (``auto_apply_or_queue``): a mark pre-checks its
    task/material box only when the scan registered adequately
    (``registration_confidence >= OMR_REG_MIN``) AND the box is confidently
    filled; every ambiguous fill, and every mark on an inadequately-registered
    scan, queues to ``pending_changes``. The work order is NEVER auto-advanced
    to COMPLETED here — that transition is gated behind an explicit human
    confirm on the review screen (design rule: never auto-close from a scan).
    """
    from inventory.services.work_order_cv import auto_apply_or_queue
    from inventory.services.work_order_omr import (
        OMR_REG_MIN,
        compute_template_version,
        detections_from_result,
        read_omr_scan,
    )

    wo_id, id_err, image_bytes = _omr_scan_inputs(raw_bytes)
    if not wo_id:
        return _omr_fail(submission, f"Could not recover the work order id from the scan: {id_err}")
    try:
        work_order = WorkOrder.objects.get(id=wo_id)
    except WorkOrder.DoesNotExist:
        return _omr_fail(submission, f"Work order {wo_id} not found.")

    template = work_order.omr_templates.order_by("-created_at").first()
    if template is None:
        return _omr_review(
            submission,
            work_order,
            "No OMR template on file for this work order — reprint the OMR form before scanning.",
        )

    # Drift guard: refuse a scan whose checklist changed since the sheet printed.
    if compute_template_version(work_order) != template.template_version:
        return _omr_review(
            submission,
            work_order,
            "The work order's tasks changed after this form was printed. "
            "Reprint the OMR form and scan again.",
        )

    if image_bytes is None:
        return _omr_review(
            submission, work_order, "The scanned PDF contained no page image to read."
        )

    result = read_omr_scan(image_bytes, template, recovered_work_order_id=str(wo_id))

    # Keep the raw scan on the WO for history + on-demand crop rendering.
    if not work_order.completed_scan:
        ext = "pdf" if raw_bytes[:5] == b"%PDF-" else "png"
        work_order.completed_scan.save(
            f"wo-{work_order.short_id}-scan.{ext}", ContentFile(raw_bytes), save=False
        )
        work_order.save(update_fields=["completed_scan"])

    if not result.ok:
        return _omr_review(
            submission,
            work_order,
            f"Could not align the scanned form: {result.error}. Please review the scan by hand.",
        )

    detections = detections_from_result(result)
    auto, queue = auto_apply_or_queue(
        detections,
        registration_confidence=result.registration_confidence,
        reg_min=OMR_REG_MIN,
    )

    now = timezone.now()
    note = "Pre-checked from scan (OMR, pending confirmation)."
    task_titles = {str(tc.id): tc.task_title for tc in work_order.task_completions.all()}
    material_names = {str(mu.id): mu.material_name for mu in work_order.material_usage.all()}
    loto_labels = {str(lc.id): lc.source_label for lc in work_order.loto_completions.all()}

    applied = 0
    for det in auto:
        applied += omr_apply_mark(
            work_order, det.target_id, marked=bool(det.value), now=now, note=note
        )

    # Progress is allowed (OPEN → IN_PROGRESS); COMPLETED is NOT — never here.
    if applied and work_order.status == WorkOrder.Status.OPEN:
        work_order.status = WorkOrder.Status.IN_PROGRESS
        work_order.save(update_fields=["status"])

    base = f"/api/inventory/work-orders/{work_order.id}/submissions/{submission.id}/mark-crop/"
    pending: list = []
    for det, auto_applied in [(d, True) for d in auto] + [(d, False) for d in queue]:
        pending.append(
            {
                "kind": det.kind,
                "target_id": det.target_id,
                "value": bool(det.value),
                "confidence": round(float(det.confidence), 4),
                "label": _omr_label(det.target_id, task_titles, material_names, loto_labels),
                "crop_url": f"{base}{det.target_id}/",
                "auto_applied": auto_applied,
            }
        )

    submission.work_order = work_order
    submission.pending_changes = pending
    submission.parsed_fields = {
        "work_order_id": str(wo_id),
        "source": "scan",
        "registration_confidence": round(float(result.registration_confidence), 4),
        "auto_applied_count": len(auto),
        "queued_count": len(queue),
    }
    # A scan ALWAYS ends in review — the human confirms the marks (and any WO
    # completion) on screen. Never auto-applied, never auto-closed.
    submission.status = WorkOrderSubmission.Status.PENDING_REVIEW
    submission.parse_error = ""
    submission.save(
        update_fields=["status", "work_order", "parsed_fields", "parse_error", "pending_changes"]
    )
    # A scanned WO is back and awaiting a human confirm — notify app-wide once
    # the atomic ingest commits (op-o6rs). on_commit avoids notifying on rollback.
    transaction.on_commit(lambda: _notify_wo_scanned(submission, work_order, degraded=False))
    return submission


def _cv_detect_completion(pdf_stream) -> list:
    """Run image-based CV over a PDF's embedded page images.

    Returns a list of ``Detection`` objects from
    ``inventory.services.work_order_cv``. Skips images that look like QR
    codes (those are the WO-ID payloads, not signatures or notes).
    """
    from .work_order_cv import Detection, detect_signature, ocr_handwritten

    reader = PdfReader(pdf_stream)
    detections: list = []
    for image_bytes in _iter_pdf_images(reader):
        if not image_bytes:
            continue
        if _looks_like_qr_payload(image_bytes):
            continue
        # Signature detection: look for ink mass — a sparse handwritten
        # signature scribble in the signoff block.
        is_signed, sig_conf = detect_signature(image_bytes)
        if sig_conf > 0:
            detections.append(
                Detection(
                    kind="signature",
                    target_id=None,
                    value=is_signed,
                    confidence=sig_conf,
                    label="Signature block",
                )
            )
        # OCR for handwritten notes (best-effort — needs tesseract).
        text, ocr_conf = ocr_handwritten(image_bytes)
        if text:
            detections.append(
                Detection(
                    kind="handwritten",
                    target_id=None,
                    value=text,
                    confidence=ocr_conf,
                    label="Handwritten note",
                )
            )
    return detections


# ─────────────────────────────────────────────────────────────────────────────
# Third-party paper work-order form ingestion
#
# Vendors receive a printable PDF generated by
# `inventory.utils.third_party_wo_pdf.generate_third_party_wo_form_pdf` with
# the TPWO id stamped into a hidden AcroForm field and a `tpwo:<uuid>` QR
# code. They fill out vendor name, invoice total, downtime window, asset ids,
# and keyfob id either directly in a PDF reader or on paper + scan; the
# parser pulls back whatever AcroForm values survived. Any embedded image
# beyond the QR code is captured as a KIND_PHOTO attachment.
# ─────────────────────────────────────────────────────────────────────────────

THIRD_PARTY_TEXT_FIELD_NAMES = (
    "vendor_name",
    "invoice_total",
    "downtime_start",
    "downtime_end",
    "keyfob_id",
)


def _coerce_decimal(raw: Optional[str]) -> Optional[Decimal]:
    if raw is None:
        return None
    cleaned = re.sub(r"[^\d.\-]", "", str(raw))
    if not cleaned or cleaned in ("-", "."):
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


_DATETIME_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%m/%d/%Y %H:%M",
    "%m/%d/%Y %I:%M %p",
    "%m/%d/%Y",
)


def _coerce_datetime(raw: Optional[str]):
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None
    for fmt in _DATETIME_FORMATS:
        try:
            naive = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if timezone.is_naive(naive):
            return timezone.make_aware(naive, timezone.get_current_timezone())
        return naive
    return None


def _extract_tpwo_id_from_acroform(reader: PdfReader) -> Tuple[Optional[str], Optional[str]]:
    try:
        fields = reader.get_fields() or {}
    except Exception as exc:  # noqa: BLE001
        return None, f"acroform: failed to read fields ({exc})"
    field = fields.get("third_party_work_order_id")
    if field is None:
        return None, "acroform: 'third_party_work_order_id' field not present"
    value = _field_value(field)
    if not value:
        return None, "acroform: 'third_party_work_order_id' field is empty"
    match = UUID_RE.search(value)
    if not match:
        return None, f"acroform: value '{value[:60]}' is not a UUID"
    return match.group(1), None


def _extract_tpwo_id_from_qr(reader: PdfReader) -> Tuple[Optional[str], Optional[str]]:
    """Like _extract_id_from_qr but accepts ``tpwo:<uuid>`` payloads explicitly."""
    image_count = 0
    for image_bytes in _iter_pdf_images(reader):
        image_count += 1
        for payload in _decode_qr_payloads(image_bytes):
            if payload.lower().startswith(TPWO_QR_PREFIX):
                match = UUID_RE.search(payload)
                if match:
                    return match.group(1), None
            match = UUID_RE.search(payload)
            if match:
                return match.group(1), None
    if image_count == 0:
        return None, "qr: no embedded images found in PDF"
    return None, "qr: no QR payload contained a third-party WO UUID"


def _extract_tpwo_id_from_text(reader: PdfReader) -> Tuple[Optional[str], Optional[str]]:
    text_chunks: List[str] = []
    for page in reader.pages:
        try:
            text_chunks.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001  # nosec B112
            continue
    text = "\n".join(text_chunks)
    if not text.strip():
        return None, "text: pypdf extracted no text from PDF (likely image-only)"
    match = THIRD_PARTY_WO_ID_TEXT_RE.search(text)
    if match:
        return match.group(1), None
    return None, "text: 'Third-Party Work Order ID: <uuid>' marker not present"


def _extract_tpwo_id(reader: PdfReader) -> Tuple[Optional[str], List[str]]:
    errors: List[str] = []
    for extractor in (
        _extract_tpwo_id_from_acroform,
        _extract_tpwo_id_from_qr,
        _extract_tpwo_id_from_text,
    ):
        wo_id, err = extractor(reader)
        if wo_id:
            return wo_id, errors
        if err:
            errors.append(err)
    return None, errors


def parse_third_party_wo_pdf(pdf_bytes: bytes) -> dict:
    """
    Parse a third-party WO paper-form PDF.

    Returns a dict with keys:
        third_party_work_order_id: UUID string or None
        extraction_errors:         per-method failure messages
        text_fields:               {field_name: raw string} for non-empty fields
        asset_ids:                 list of UUID strings collected from
                                   ``asset_id_<n>`` AcroForm fields
        invoice_total:             Decimal or None (parsed from text_fields)
        downtime_start / end:      timezone-aware datetime or None
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))

    tpwo_id, extraction_errors = _extract_tpwo_id(reader)

    text_fields: dict[str, str] = {}
    asset_ids: list[str] = []

    fields = reader.get_fields() or {}
    for name, field in fields.items():
        value = _field_value(field)
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        if name in THIRD_PARTY_TEXT_FIELD_NAMES:
            text_fields[name] = text
        elif name.startswith("asset_id_") or name == "asset_id":
            match = UUID_RE.search(text)
            if match and match.group(1) not in asset_ids:
                asset_ids.append(match.group(1))

    invoice_total = _coerce_decimal(text_fields.get("invoice_total"))
    downtime_start = _coerce_datetime(text_fields.get("downtime_start"))
    downtime_end = _coerce_datetime(text_fields.get("downtime_end"))

    return {
        "third_party_work_order_id": tpwo_id,
        "extraction_errors": extraction_errors,
        "text_fields": text_fields,
        "asset_ids": asset_ids,
        "invoice_total": str(invoice_total) if invoice_total is not None else None,
        "downtime_start": downtime_start.isoformat() if downtime_start else None,
        "downtime_end": downtime_end.isoformat() if downtime_end else None,
    }


def _pdf_text(pdf_bytes: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception:  # noqa: BLE001
        return ""
    chunks: List[str] = []
    for page in reader.pages:
        try:
            chunks.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001  # nosec B112
            continue
    return "\n".join(chunks)


def detect_submission_kind(pdf_bytes: bytes, subject: str = "") -> str:
    """
    Detect the submission kind from a PDF.

    Routing signals, in order of confidence:
      1. Email subject prefix (``[3PWO]`` or ``[LOCPROB]``).
      2. Hidden AcroForm fields (``third_party_work_order_id``,
         ``location_problem_report`` / ``location_id``).
      3. Header text (``Third-Party Work Order`` /
         ``Location Problem Report``).

    Defaults to ``KIND_PM_COMPLETION``.
    """
    subject_norm = (subject or "").lstrip().upper()
    if subject_norm.startswith(THIRD_PARTY_SUBJECT_PREFIX):
        return WorkOrderSubmission.Kind.THIRD_PARTY_WO
    if subject_norm.startswith(LOCATION_PROBLEM_SUBJECT_PREFIX):
        return WorkOrderSubmission.Kind.LOCATION_PROBLEM
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        fields = reader.get_fields() or {}
        if "third_party_work_order_id" in fields:
            return WorkOrderSubmission.Kind.THIRD_PARTY_WO
        if "location_problem_report" in fields or "location_id" in fields:
            return WorkOrderSubmission.Kind.LOCATION_PROBLEM
    except Exception:  # noqa: BLE001  # nosec B110 - malformed PDFs default to PM path
        pass
    text = _pdf_text(pdf_bytes)
    if LOCATION_PROBLEM_HEADER_RE.search(text):
        return WorkOrderSubmission.Kind.LOCATION_PROBLEM
    if THIRD_PARTY_HEADER_RE.search(text):
        return WorkOrderSubmission.Kind.THIRD_PARTY_WO
    return WorkOrderSubmission.Kind.PM_COMPLETION


def _looks_like_qr_payload(image_bytes: bytes) -> bool:
    """Best-effort: does this embedded image contain decodable QR content?"""
    try:
        return bool(_decode_qr_payloads(image_bytes))
    except Exception:  # noqa: BLE001  # nosec B110
        return False


def _attach_photo_evidence(tpwo, reader: PdfReader, *, uploaded_by=None) -> int:
    """Persist any embedded image that isn't a QR code as a KIND_PHOTO attachment.

    Returns the number of photo attachments created. Idempotent on repeated
    calls only by content size — if the same submission is re-applied, new
    rows are created. Callers gate by submission.status to avoid double-write.
    """
    from maintenance_orders.models import ThirdPartyWorkOrderAttachment

    created = 0
    for idx, image_bytes in enumerate(_iter_pdf_images(reader), start=1):
        if not image_bytes:
            continue
        if _looks_like_qr_payload(image_bytes):
            continue
        attachment = ThirdPartyWorkOrderAttachment(
            work_order=tpwo,
            kind=ThirdPartyWorkOrderAttachment.KIND_PHOTO,
            uploaded_by=uploaded_by,
            caption=f"Embedded photo {idx} from paper-form submission",
        )
        attachment.file.save(
            f"tpwo-{tpwo.short_id}-photo-{idx}.png",
            ContentFile(image_bytes),
            save=False,
        )
        attachment.save()
        created += 1
    return created


@transaction.atomic
def _apply_third_party_submission(
    submission: WorkOrderSubmission, pdf_bytes: bytes
) -> WorkOrderSubmission:
    """Third-party path: populate a ThirdPartyWorkOrder and advance to validated."""
    from maintenance_orders.models import ThirdPartyWorkOrder, ThirdPartyWorkOrderAttachment

    try:
        parsed = parse_third_party_wo_pdf(pdf_bytes)
    except Exception as exc:  # noqa: BLE001 - defensive: malformed PDF
        logger.exception("Failed to parse third-party WO submission %s", submission.id)
        submission.status = WorkOrderSubmission.Status.FAILED
        submission.parse_error = f"Failed to parse PDF: {exc}"
        submission.save(update_fields=["status", "parse_error"])
        return submission

    submission.parsed_fields = parsed

    tpwo_id = parsed.get("third_party_work_order_id")
    if not tpwo_id:
        errors = parsed.get("extraction_errors") or []
        msg = (
            "Could not find a Third-Party Work Order ID marker in the PDF. "
            f"Tried: {'; '.join(errors)}"
            if errors
            else "Could not find a Third-Party Work Order ID marker in the PDF."
        )
        submission.status = WorkOrderSubmission.Status.FAILED
        submission.parse_error = msg
        submission.save(update_fields=["status", "parse_error", "parsed_fields"])
        return submission

    try:
        tpwo = ThirdPartyWorkOrder.objects.get(id=tpwo_id)
    except ThirdPartyWorkOrder.DoesNotExist:
        submission.status = WorkOrderSubmission.Status.FAILED
        submission.parse_error = f"Third-party work order {tpwo_id} not found."
        submission.save(update_fields=["status", "parse_error", "parsed_fields"])
        return submission

    submission.third_party_work_order = tpwo

    text_fields = parsed.get("text_fields") or {}
    update_fields: list[str] = []

    keyfob_id = text_fields.get("keyfob_id", "").strip()
    if keyfob_id and keyfob_id != tpwo.keyfob_id:
        tpwo.keyfob_id = keyfob_id[:64]
        update_fields.append("keyfob_id")

    invoice_total = _coerce_decimal(text_fields.get("invoice_total"))
    if invoice_total is not None and invoice_total != tpwo.actual_invoice_total:
        tpwo.actual_invoice_total = invoice_total
        update_fields.append("actual_invoice_total")

    downtime_start = _coerce_datetime(text_fields.get("downtime_start"))
    if downtime_start and downtime_start != tpwo.downtime_start:
        tpwo.downtime_start = downtime_start
        update_fields.append("downtime_start")

    downtime_end = _coerce_datetime(text_fields.get("downtime_end"))
    if downtime_end and downtime_end != tpwo.downtime_end:
        tpwo.downtime_end = downtime_end
        update_fields.append("downtime_end")

    if tpwo.downtime_start and tpwo.downtime_end:
        total = tpwo.downtime_end - tpwo.downtime_start
        if total != tpwo.total_downtime:
            tpwo.total_downtime = total
            update_fields.append("total_downtime")

    # Multi-asset distribution: ensure a ThirdPartyWorkOrderAsset link exists for
    # every asset id the vendor reported. Cost shares are left at 0 — Phase 3
    # reconciliation will materialize allocated_cost when SIG sets share_pct.
    from inventory.models import Asset
    from maintenance_orders.models import ThirdPartyWorkOrderAsset

    valid_asset_ids = list(
        Asset.objects.filter(id__in=parsed.get("asset_ids") or []).values_list("id", flat=True)
    )
    for asset_id in valid_asset_ids:
        ThirdPartyWorkOrderAsset.objects.get_or_create(
            work_order=tpwo,
            asset_id=asset_id,
        )

    # State advance: paper form is post-work submission → validated.
    state_advanced = False
    terminal = (
        ThirdPartyWorkOrder.STATUS_VALIDATED,
        ThirdPartyWorkOrder.STATUS_FINANCIAL_REVIEW,
        ThirdPartyWorkOrder.STATUS_CLOSED,
        ThirdPartyWorkOrder.STATUS_CANCELLED,
    )
    if tpwo.status not in terminal:
        tpwo.status = ThirdPartyWorkOrder.STATUS_VALIDATED
        update_fields.append("status")
        state_advanced = True

    if update_fields:
        update_fields.append("updated_at")
        tpwo.save(update_fields=list(dict.fromkeys(update_fields)))

    # Persist the original PDF as a paper-form attachment (idempotent: skip
    # if a paper_form attachment already exists for this submission).
    paper_form_caption = f"Submission {submission.id}"
    already_attached = ThirdPartyWorkOrderAttachment.objects.filter(
        work_order=tpwo,
        kind=ThirdPartyWorkOrderAttachment.KIND_PAPER_FORM,
        caption=paper_form_caption,
    ).exists()
    if not already_attached:
        attachment = ThirdPartyWorkOrderAttachment(
            work_order=tpwo,
            kind=ThirdPartyWorkOrderAttachment.KIND_PAPER_FORM,
            uploaded_by=submission.submitted_by,
            caption=paper_form_caption,
        )
        attachment.file.save(
            f"tpwo-{tpwo.short_id}-paper-form.pdf",
            ContentFile(pdf_bytes),
            save=False,
        )
        attachment.save()

        # Attach embedded photos only on the first apply to avoid duplicates
        # if the submission is re-processed.
        try:
            reader = PdfReader(io.BytesIO(pdf_bytes))
            _attach_photo_evidence(tpwo, reader, uploaded_by=submission.submitted_by)
        except Exception:  # noqa: BLE001 - photo extraction is best-effort
            logger.debug(
                "Photo extraction failed for TPWO submission %s", submission.id, exc_info=True
            )

    if state_advanced:
        logger.info(
            "TPWO %s advanced to validated via paper-form submission %s",
            tpwo.short_id,
            submission.id,
        )

    submission.status = WorkOrderSubmission.Status.APPLIED
    submission.parse_error = ""
    submission.save(
        update_fields=["status", "third_party_work_order", "parsed_fields", "parse_error"]
    )
    return submission


def parse_location_problem_pdf(pdf_bytes: bytes) -> dict:
    """Extract location id, severity, and description from a Location
    Problem Report PDF.

    The recognised template is described in
    ``docs/location-problem-form.md``: a header line ``Location Problem
    Report``, a ``Location ID:`` field (numeric) or QR code with payload
    ``location:<id>``, an optional ``Severity:`` line, and a free-form
    ``Description:`` block that runs to the end of the page.
    """
    parsed: dict = {
        "location_id": None,
        "severity": None,
        "description": "",
        "extraction_errors": [],
    }
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception as exc:  # noqa: BLE001
        parsed["extraction_errors"].append(f"PdfReader failed: {exc}")
        return parsed

    fields = {}
    try:
        fields = reader.get_fields() or {}
    except Exception:  # noqa: BLE001  # nosec B110 - falls back to text/QR
        pass

    location_id = _field_value(fields.get("location_id")) if fields else None
    severity = _field_value(fields.get("severity")) if fields else None
    description = _field_value(fields.get("description")) if fields else None

    text = _pdf_text(pdf_bytes)
    if not location_id:
        match = LOCATION_ID_TEXT_RE.search(text)
        if match:
            location_id = match.group(1)
    if not location_id:
        for image_bytes in _iter_pdf_images(reader):
            for payload in _decode_qr_payloads(image_bytes) or []:
                if payload and payload.startswith(LOCATION_QR_PREFIX):
                    location_id = payload[len(LOCATION_QR_PREFIX) :].strip()
                    break
            if location_id:
                break

    if not severity:
        match = SEVERITY_TEXT_RE.search(text)
        if match:
            severity = match.group(1).lower()

    if not description:
        marker = "Description:"
        idx = text.find(marker)
        if idx >= 0:
            description = text[idx + len(marker) :].strip()

    try:
        parsed["location_id"] = int(location_id) if location_id else None
    except (TypeError, ValueError):
        parsed["extraction_errors"].append(f"Bad location id: {location_id!r}")
        parsed["location_id"] = None
    parsed["severity"] = (severity or "").lower() or None
    parsed["description"] = (description or "").strip()
    return parsed


@transaction.atomic
def _apply_location_problem_submission(
    submission: WorkOrderSubmission, pdf_bytes: bytes
) -> WorkOrderSubmission:
    """Location-problem path: create a LocationProblem from the PDF."""
    from ..models import Location, LocationProblem

    parsed = parse_location_problem_pdf(pdf_bytes)
    submission.parsed_fields = parsed

    location_id = parsed.get("location_id")
    if not location_id:
        submission.status = WorkOrderSubmission.Status.FAILED
        submission.parse_error = (
            "Could not extract a Location ID from the form. "
            "Tried: AcroForm location_id, Location ID text, QR location:<id>."
        )
        submission.save(update_fields=["status", "parse_error", "parsed_fields"])
        return submission

    try:
        location = Location.objects.get(id=location_id)
    except Location.DoesNotExist:
        submission.status = WorkOrderSubmission.Status.FAILED
        submission.parse_error = f"Location {location_id} not found."
        submission.save(update_fields=["status", "parse_error", "parsed_fields"])
        return submission

    severity = parsed.get("severity") or LocationProblem.Severity.MEDIUM
    valid_severities = {choice for choice, _ in LocationProblem.Severity.choices}
    if severity not in valid_severities:
        severity = LocationProblem.Severity.MEDIUM

    description = parsed.get("description") or "Reported via paper Location Problem Report"

    if submission.location_problem is None:
        problem = LocationProblem.objects.create(
            location=location,
            description=description,
            severity=severity,
            reported_by=submission.from_email or "",
            status=LocationProblem.Status.REPORTED,
        )
        problem.paper_form_attachment.save(
            f"locprob-{problem.id}-paper.pdf",
            ContentFile(pdf_bytes),
            save=True,
        )
        submission.location_problem = problem

    submission.status = WorkOrderSubmission.Status.APPLIED
    submission.parse_error = ""
    submission.save(update_fields=["status", "location_problem", "parsed_fields", "parse_error"])
    return submission
