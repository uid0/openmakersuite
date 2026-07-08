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
from typing import TYPE_CHECKING

from django.db import transaction

if TYPE_CHECKING:
    from inventory.models import WorkOrder, WorkOrderOmrTemplate


def dynamic_target_ids(work_order: "WorkOrder") -> list[str]:
    """The mark target-ids that vary per work order (tasks + materials).

    Mirrors the checkbox field names ``generate_work_order_pdf`` draws exactly:
    one ``task_<uuid>`` per task completion, and either ``material_<uuid>`` per
    usage row or — when a legacy WO has no usage rows — ``materialspec_<id>``
    per maintenance-item material. The fixed completion marks
    (``work_complete``/``result_pass``/``result_fail``/``tech_initials``/
    ``tech_date``) are constant across every form and so are excluded from the
    drift signature.
    """
    ids = [f"task_{tc.id}" for tc in work_order.task_completions.all()]

    material_usage = list(work_order.material_usage.all())
    if material_usage:
        ids += [f"material_{mu.id}" for mu in material_usage]
    else:
        ids += [f"materialspec_{mat.id}" for mat in work_order.maintenance_item.materials.all()]
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
