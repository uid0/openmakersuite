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
from typing import List, Optional, Tuple

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from pypdf import PdfReader

from ..models import MaintenanceLog, WorkOrder, WorkOrderMaterialUsage, WorkOrderSubmission

logger = logging.getLogger(__name__)

UUID_RE = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-" r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)
WORK_ORDER_ID_TEXT_RE = re.compile(r"Work Order ID:\s*" + UUID_RE.pattern)


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
        except Exception:  # noqa: BLE001 - pypdf is permissive about odd PDFs
            continue
        for img in images:
            try:
                yield img.data
            except Exception:  # noqa: BLE001
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
        detector = cv2.QRCodeDetector()
        # Try multi-detect first (PDFs may carry multiple QRs); fall back to
        # single-detect since multi can return False for clean single-QR pages.
        try:
            ok, decoded, _, _ = detector.detectAndDecodeMulti(arr)
            if ok and decoded:
                payloads.extend([d for d in decoded if d])
        except Exception:  # noqa: BLE001
            pass
        if not payloads:
            try:
                data, _, _ = detector.detectAndDecode(arr)
                if data:
                    payloads.append(data)
            except Exception:  # noqa: BLE001
                pass
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
                except Exception:  # noqa: BLE001
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
        except Exception:  # noqa: BLE001 - nosec B112
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
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))

    work_order_id, extraction_errors = extract_work_order_id(reader)

    task_checks: dict[str, bool] = {}
    material_checks: dict[str, bool] = {}

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

    return {
        "work_order_id": work_order_id,
        "extraction_errors": extraction_errors,
        "task_checks": task_checks,
        "material_checks": material_checks,
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


@transaction.atomic
def apply_submission(submission: WorkOrderSubmission) -> WorkOrderSubmission:
    """
    Parse the submission's PDF and update the referenced WorkOrder in place.

    Safe to call multiple times for the same submission: task completions are
    only transitioned from unchecked → checked, so re-applying is a no-op for
    any step that was already completed digitally in the app.
    """
    submission.attachment.open("rb")
    try:
        pdf_bytes = submission.attachment.read()
    finally:
        submission.attachment.close()

    try:
        parsed = parse_work_order_pdf(pdf_bytes)
    except Exception as exc:  # noqa: BLE001 - defensive: malformed PDF
        logger.exception("Failed to parse work order submission %s", submission.id)
        submission.status = WorkOrderSubmission.STATUS_FAILED
        submission.parse_error = f"Failed to parse PDF: {exc}"
        submission.save(update_fields=["status", "parse_error"])
        return submission

    submission.parsed_fields = parsed

    work_order, err = _resolve_work_order(submission, parsed)
    if err:
        submission.status = WorkOrderSubmission.STATUS_FAILED
        submission.parse_error = err
        submission.save(update_fields=["status", "parse_error", "parsed_fields"])
        return submission

    submission.work_order = work_order

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

    # Apply material usage (only false → true).
    material_qs = WorkOrderMaterialUsage.objects.filter(
        work_order=work_order,
        id__in=parsed["material_checks"].keys(),
    )
    for mu in material_qs:
        if parsed["material_checks"].get(str(mu.id)) and not mu.was_used:
            mu.was_used = True
            mu.save(update_fields=["was_used"])

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
        if work_order.status != WorkOrder.STATUS_COMPLETED:
            work_order.status = WorkOrder.STATUS_COMPLETED
            work_order.completed_at = now
            wo_became_complete = True
    elif applied_tasks > 0 and work_order.status == WorkOrder.STATUS_OPEN:
        work_order.status = WorkOrder.STATUS_IN_PROGRESS

    work_order.save()

    if wo_became_complete:
        item = work_order.maintenance_item
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

    submission.status = WorkOrderSubmission.STATUS_APPLIED
    submission.parse_error = ""
    submission.save(update_fields=["status", "work_order", "parsed_fields", "parse_error"])
    return submission
