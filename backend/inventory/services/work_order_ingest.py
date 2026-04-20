"""
Ingestion of completed work-order PDFs emailed in via the Postmark inbound
webhook.

Pipeline:
  1. The Postmark webhook view (see `inventory.views.postmark_inbound_work_order`)
     stores the raw PDF attachment on a `WorkOrderSubmission` row.
  2. `apply_submission` is invoked on that submission. It:
       a. extracts the embedded "Work Order ID: <uuid>" marker from the PDF text
       b. reads the AcroForm checkbox field values via pypdf
       c. for each `task_<uuid>` / `material_<uuid>` field that is checked,
          marks the corresponding WorkOrderTaskCompletion / WorkOrderMaterialUsage
       d. attaches the PDF to WorkOrder.completed_scan
       e. if all required task steps are complete, transitions the WorkOrder to
          COMPLETED, stamps MaintenanceItem.last_completed_at, and appends a
          MaintenanceLog entry for the maintenance history.

Checkbox field value convention (reportlab + pypdf): unchecked fields have a
value of NameObject("/Off") (or no "/V" entry at all); checked fields are
NameObject("/Yes").
"""

import io
import logging
import re
from typing import Optional, Tuple

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from pypdf import PdfReader

from ..models import MaintenanceLog, WorkOrder, WorkOrderMaterialUsage, WorkOrderSubmission

logger = logging.getLogger(__name__)

# Matches the UUID printed in the PDF footer: "Work Order ID: <uuid>"
WORK_ORDER_ID_RE = re.compile(
    r"Work Order ID:\s*([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)


def _field_is_checked(value) -> bool:
    """Return True if a pypdf AcroForm field value represents a checked state."""
    if value is None:
        return False
    # pypdf returns NameObject instances; stringifying gives "/Yes" or "/Off".
    name = getattr(value, "name", None)
    if name is not None:
        return name.lower() == "yes"
    return str(value).lstrip("/").lower() == "yes"


def parse_work_order_pdf(pdf_bytes: bytes) -> dict:
    """
    Parse a work-order PDF and return identifiers + checkbox state.

    Returns a dict with keys:
        work_order_id:    the UUID string, or None if not found
        task_checks:      {task_completion_id: bool}
        material_checks:  {material_usage_id: bool}  (keyed by usage UUID
                          when the PDF was generated from a WO with usage rows,
                          otherwise keyed by maintenance-material UUID via the
                          "materialspec_" prefix — not applied to DB state)
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))

    text_chunks = []
    for page in reader.pages:
        try:
            text_chunks.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 - pypdf raises various errors on odd PDFs  # nosec B112
            continue
    text = "\n".join(text_chunks)
    match = WORK_ORDER_ID_RE.search(text)
    work_order_id = match.group(1) if match else None

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
        "task_checks": task_checks,
        "material_checks": material_checks,
    }


def _resolve_work_order(
    submission: WorkOrderSubmission, parsed: dict
) -> Tuple[Optional[WorkOrder], Optional[str]]:
    wo_id = parsed.get("work_order_id")
    if not wo_id:
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
