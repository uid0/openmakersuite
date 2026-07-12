"""Tests for the OMR scan reader + scan-ingestion pipeline (op-6pc8, bead-2).

bead-1 printed the form + fiducials and persisted a region map; this reader
aligns a *scan*, thresholds each mark, and — per Ian's 0.999 confidence bar —
routes almost everything to human review and NEVER auto-closes a work order.

The synthetic harness renders fiducials + checkboxes + a QR directly into
canonical template space (there is no PDF rasterizer in the venv), stamps known
marks, and asserts the reader recovers exactly the stamped set. Ground-truth
positions are computed from the *documented* normalization contract
(``rect_norm`` against the fiducial centers), independent of reader internals.
"""

from __future__ import annotations

import io

from django.core.files.base import ContentFile

import cv2
import numpy as np
import pytest
import qrcode
from PIL import Image, ImageDraw
from rest_framework import status

from fiducials.services.apriltag_render import build_form_fiducials
from inventory.models import (
    MaintenanceLog,
    MaintenanceTask,
    WorkOrder,
    WorkOrderSubmission,
    WorkOrderTaskCompletion,
)
from inventory.services import work_order_omr as omr
from inventory.services.work_order_ingest import apply_submission
from inventory.services.work_order_omr import (
    OMR_AUTO_APPLY_THRESHOLD,
    build_and_persist_omr_template,
    read_omr_scan,
)
from inventory.tests.test_work_order_omr import (
    _make_wo_with_materials,
    _make_work_order,
    _staff_client,
)

pytestmark = pytest.mark.django_db

DPI = omr.CANON_DPI
FID_SIZE_PT = 30.0


# ---------------------------------------------------------------------------
# synthetic-scan generator (documented coordinate contract, not reader internals)
# ---------------------------------------------------------------------------
def _region_rect_px(region, fiducials, page_h, dpi=DPI):
    fx0 = fiducials["bl"]["cx"]
    span_x = fiducials["br"]["cx"] - fx0
    fy0 = fiducials["bl"]["cy"]
    span_y = fiducials["tl"]["cy"] - fy0
    nx0, ny0, nx1, ny1 = region["rect_norm"]

    def _x(nx):
        return (fx0 + nx * span_x) / 72.0 * dpi

    def _y(ny):  # y-up point → y-down pixel
        return (page_h - (fy0 + ny * span_y)) / 72.0 * dpi

    xs = sorted([_x(nx0), _x(nx1)])
    ys = sorted([_y(ny0), _y(ny1)])
    return xs[0], ys[0], xs[1], ys[1]


def _synth_scan(
    template,
    *,
    wo_id=None,
    marks=None,
    dpi=DPI,
    warp_dst=None,
    gradient=False,
    drop_fiducials=(),
):
    """Render a fake flatbed scan of the form → PNG bytes.

    ``marks``: {target_id: "full"|"scribble"|"marginal"|None}. ``warp_dst``: a
    4-point float32 array to perspective-warp the finished page (skew). If
    ``wo_id`` is given, a decodable QR carrying it is drawn.
    """
    marks = marks or {}
    fiducials = template.fiducials_json
    page_w = template.page_w_pt
    page_h = template.page_h_pt
    w = int(round(page_w / 72.0 * dpi))
    h = int(round(page_h / 72.0 * dpi))
    canvas = Image.new("L", (w, h), 255)
    draw = ImageDraw.Draw(canvas)

    fid_px = int(round(FID_SIZE_PT / 72.0 * dpi))
    markers = build_form_fiducials(fid_px)
    for corner, spec in fiducials.items():
        if corner in drop_fiducials:
            continue
        cx = spec["cx"] / 72.0 * dpi
        cy = (page_h - spec["cy"]) / 72.0 * dpi
        canvas.paste(
            markers[corner].convert("L"),
            (int(round(cx - fid_px / 2)), int(round(cy - fid_px / 2))),
        )

    if wo_id is not None:
        qr = qrcode.make(f"https://x/inventory/work-orders/{wo_id}").convert("L")
        qr = qr.resize((dpi, dpi), Image.NEAREST)
        canvas.paste(qr, (w // 2 - dpi // 2, int(0.42 * h)))

    for region in template.regions_json:
        x0, y0, x1, y1 = _region_rect_px(region, fiducials, page_h, dpi)
        draw.rectangle([x0, y0, x1, y1], outline=0, width=2)
        style = marks.get(region["target_id"])
        if style == "full":
            draw.rectangle([x0 + 2, y0 + 2, x1 - 2, y1 - 2], fill=0)
        elif style == "scribble":
            draw.line([x0 + 3, y0 + 3, x1 - 3, y1 - 3], fill=0, width=3)
            draw.line([x0 + 3, y1 - 3, x1 - 3, y0 + 3], fill=0, width=3)
        elif style == "marginal":
            draw.line([x0 + 4, (y0 + y1) / 2, (x0 + x1) / 2, y1 - 4], fill=0, width=2)

    arr = np.array(canvas)
    if warp_dst is not None:
        src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
        matrix = cv2.getPerspectiveTransform(src, warp_dst)
        arr = cv2.warpPerspective(arr, matrix, (w, h), borderValue=255)
    if gradient:
        grad = np.tile(np.linspace(1.0, 0.55, w), (h, 1))
        arr = (arr.astype(np.float32) * grad).clip(0, 255).astype(np.uint8)

    buf = io.BytesIO()
    Image.fromarray(arr).convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def _persisted(wo):
    _pdf, template = build_and_persist_omr_template(wo, base_url="https://x")
    return template


def _task_target_ids(template):
    return [r["target_id"] for r in template.regions_json if r["target_id"].startswith("task_")]


def _submission_for(wo, scan_bytes, *, source=WorkOrderSubmission.Source.SCAN):
    sub = WorkOrderSubmission(
        kind=WorkOrderSubmission.Kind.PM_COMPLETION,
        source=source,
        status=WorkOrderSubmission.Status.RECEIVED,
    )
    sub.attachment.save("scan.png", ContentFile(scan_bytes), save=False)
    sub.save()
    return sub


# ---------------------------------------------------------------------------
# reader-level
# ---------------------------------------------------------------------------
class TestReader:
    def test_registration_identity_is_near_perfect(self):
        wo = _make_work_order(num_tasks=3)
        template = _persisted(wo)
        scan = _synth_scan(template)
        result = read_omr_scan(scan, template)
        assert result.ok
        assert result.registration_confidence >= 0.999

    def test_recovers_exact_marked_set(self):
        wo = _make_work_order(num_tasks=3)
        template = _persisted(wo)
        tasks = _task_target_ids(template)
        marks = {tasks[0]: "full", tasks[1]: "scribble"}  # tasks[2] blank
        result = read_omr_scan(_synth_scan(template, marks=marks), template)
        assert result.ok
        by_id = {r.target_id: r for r in result.reads}
        assert by_id[tasks[0]].marked is True
        assert by_id[tasks[1]].marked is True
        assert by_id[tasks[2]].marked is False
        # the two clear marks clear the auto-apply bar; the blank does not read.
        assert by_id[tasks[0]].confidence >= OMR_AUTO_APPLY_THRESHOLD
        assert by_id[tasks[1]].confidence >= OMR_AUTO_APPLY_THRESHOLD

    def test_marginal_mark_is_uncertain(self):
        wo = _make_work_order(num_tasks=2)
        template = _persisted(wo)
        tasks = _task_target_ids(template)
        result = read_omr_scan(_synth_scan(template, marks={tasks[0]: "marginal"}), template)
        by_id = {r.target_id: r for r in result.reads}
        assert by_id[tasks[0]].confidence < OMR_AUTO_APPLY_THRESHOLD

    def test_skew_drags_all_reads_below_bar(self):
        wo = _make_work_order(num_tasks=3)
        template = _persisted(wo)
        tasks = _task_target_ids(template)
        w = int(round(template.page_w_pt / 72.0 * DPI))
        h = int(round(template.page_h_pt / 72.0 * DPI))
        warp_dst = np.float32([[40, 30], [w - 20, 70], [w - 60, h - 40], [30, h - 25]])
        result = read_omr_scan(
            _synth_scan(template, marks={t: "full" for t in tasks}, warp_dst=warp_dst),
            template,
        )
        assert result.ok  # still aligned (4 fiducials) — just not confidently
        assert result.registration_confidence < OMR_AUTO_APPLY_THRESHOLD
        assert all(r.confidence < OMR_AUTO_APPLY_THRESHOLD for r in result.reads)

    def test_brightness_gradient_still_reads_marks(self):
        wo = _make_work_order(num_tasks=2)
        template = _persisted(wo)
        tasks = _task_target_ids(template)
        result = read_omr_scan(
            _synth_scan(template, marks={tasks[0]: "full"}, gradient=True), template
        )
        by_id = {r.target_id: r for r in result.reads}
        assert by_id[tasks[0]].marked is True
        assert by_id[tasks[0]].confidence >= OMR_AUTO_APPLY_THRESHOLD

    def test_missing_fiducials_fail_registration(self):
        wo = _make_work_order(num_tasks=2)
        template = _persisted(wo)
        scan = _synth_scan(template, drop_fiducials=("tl", "tr"))
        result = read_omr_scan(scan, template)
        assert result.ok is False
        assert "align" in (result.error or "")


# ---------------------------------------------------------------------------
# ingestion pipeline
# ---------------------------------------------------------------------------
class TestScanIngestion:
    def test_high_confidence_marks_are_prechecked_and_queued(self):
        wo = _make_wo_with_materials(num_tasks=3, num_materials=1)
        template = _persisted(wo)
        tasks = _task_target_ids(template)
        scan = _synth_scan(template, wo_id=wo.id, marks={tasks[0]: "full"})
        sub = _submission_for(wo, scan)

        apply_submission(sub)
        sub.refresh_from_db()

        assert sub.status == WorkOrderSubmission.Status.PENDING_REVIEW
        assert sub.work_order_id == wo.id
        # the solid mark pre-checked its task box
        tc0 = WorkOrderTaskCompletion.objects.get(id=tasks[0][len("task_") :])
        assert tc0.is_completed is True
        # every emitted read carries a crop_url + auto_applied for the reviewer
        by_id = {c["target_id"]: c for c in sub.pending_changes}
        assert tasks[0] in by_id
        assert by_id[tasks[0]]["auto_applied"] is True
        assert by_id[tasks[0]]["crop_url"].endswith(f"/mark-crop/{tasks[0]}/")
        assert by_id[tasks[0]]["confidence"] >= OMR_AUTO_APPLY_THRESHOLD

    def test_scan_never_auto_closes_work_order(self):
        wo = _make_work_order(num_tasks=2, all_required=True)
        template = _persisted(wo)
        tasks = _task_target_ids(template)
        # mark BOTH required tasks solidly — the born-digital path would close.
        scan = _synth_scan(template, wo_id=wo.id, marks={t: "full" for t in tasks})
        sub = _submission_for(wo, scan)

        apply_submission(sub)
        wo.refresh_from_db()
        sub.refresh_from_db()

        assert wo.status != WorkOrder.Status.COMPLETED
        assert wo.completed_at is None
        assert not MaintenanceLog.objects.filter(maintenance_item=wo.maintenance_item).exists()
        assert sub.status == WorkOrderSubmission.Status.PENDING_REVIEW
        # tasks are pre-checked though (visible on the WO for the reviewer)
        assert all(tc.is_completed for tc in wo.task_completions.all())

    def test_human_confirm_completes_the_work_order(self):
        wo = _make_work_order(num_tasks=2, all_required=True)
        template = _persisted(wo)
        tasks = _task_target_ids(template)
        scan = _synth_scan(template, wo_id=wo.id, marks={t: "full" for t in tasks})
        sub = _submission_for(wo, scan)
        apply_submission(sub)

        client, _user = _staff_client()
        resp = client.post(
            f"/api/inventory/work-orders/{wo.id}/submissions/{sub.id}/apply-pending/",
            {"confirm_complete": True},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["work_order_completed"] is True

        wo.refresh_from_db()
        assert wo.status == WorkOrder.Status.COMPLETED
        assert wo.completed_at is not None
        wo.maintenance_item.refresh_from_db()
        assert wo.maintenance_item.last_completed_at is not None
        assert MaintenanceLog.objects.filter(maintenance_item=wo.maintenance_item).count() == 1

    def test_marginal_mark_is_queued_not_applied(self):
        wo = _make_work_order(num_tasks=2)
        template = _persisted(wo)
        tasks = _task_target_ids(template)
        scan = _synth_scan(template, wo_id=wo.id, marks={tasks[0]: "marginal"})
        sub = _submission_for(wo, scan)
        apply_submission(sub)
        sub.refresh_from_db()

        tc0 = WorkOrderTaskCompletion.objects.get(id=tasks[0][len("task_") :])
        assert tc0.is_completed is False  # not auto-applied
        by_id = {c["target_id"]: c for c in sub.pending_changes}
        assert tasks[0] in by_id
        assert by_id[tasks[0]]["auto_applied"] is False
        assert by_id[tasks[0]]["confidence"] < OMR_AUTO_APPLY_THRESHOLD

    def test_template_version_mismatch_routes_to_review(self):
        wo = _make_work_order(num_tasks=2)
        template = _persisted(wo)
        tasks = _task_target_ids(template)
        scan = _synth_scan(template, wo_id=wo.id, marks={tasks[0]: "full"})
        # drift: the checklist changed after the sheet printed
        new_task = MaintenanceTask.objects.create(
            maintenance_item=wo.maintenance_item, order=99, title="Added later", is_required=False
        )
        WorkOrderTaskCompletion.objects.create(
            work_order=wo, task=new_task, task_title=new_task.title, task_order=99
        )
        sub = _submission_for(wo, scan)
        apply_submission(sub)
        sub.refresh_from_db()

        assert sub.status == WorkOrderSubmission.Status.PENDING_REVIEW
        assert "reprint" in sub.parse_error.lower()
        assert sub.pending_changes == []
        assert not wo.task_completions.filter(
            id=tasks[0][len("task_") :], is_completed=True
        ).exists()

    def test_no_template_routes_to_review(self):
        wo = _make_work_order(num_tasks=2)
        template = _persisted(wo)
        scan = _synth_scan(template, wo_id=wo.id, marks={_task_target_ids(template)[0]: "full"})
        wo.omr_templates.all().delete()  # no template on file
        sub = _submission_for(wo, scan)
        apply_submission(sub)
        sub.refresh_from_db()
        assert sub.status == WorkOrderSubmission.Status.PENDING_REVIEW
        assert "template" in sub.parse_error.lower()

    def test_unresolvable_wo_id_fails(self):
        wo = _make_work_order(num_tasks=2)
        template = _persisted(wo)
        import uuid

        scan = _synth_scan(template, wo_id=uuid.uuid4(), marks={})
        sub = _submission_for(wo, scan)
        apply_submission(sub)
        sub.refresh_from_db()
        assert sub.status == WorkOrderSubmission.Status.FAILED

    def test_registration_failure_holds_for_review(self):
        wo = _make_work_order(num_tasks=2)
        template = _persisted(wo)
        scan = _synth_scan(template, wo_id=wo.id, drop_fiducials=("tl", "tr", "br"))
        sub = _submission_for(wo, scan)
        apply_submission(sub)
        sub.refresh_from_db()
        assert sub.status == WorkOrderSubmission.Status.PENDING_REVIEW
        assert "align" in sub.parse_error.lower()


# ---------------------------------------------------------------------------
# review flow (API)
# ---------------------------------------------------------------------------
class TestReviewFlow:
    def _staged(self, num_tasks=3, mark_style="full", mark_count=1):
        wo = _make_work_order(num_tasks=num_tasks)
        template = _persisted(wo)
        tasks = _task_target_ids(template)
        marks = {tasks[i]: mark_style for i in range(mark_count)}
        sub = _submission_for(wo, _synth_scan(template, wo_id=wo.id, marks=marks))
        apply_submission(sub)
        sub.refresh_from_db()
        return wo, sub, tasks

    def test_mark_crop_endpoint_returns_png(self):
        wo, sub, tasks = self._staged()
        client, _user = _staff_client()
        resp = client.get(
            f"/api/inventory/work-orders/{wo.id}/submissions/{sub.id}/mark-crop/{tasks[0]}/"
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp["Content-Type"] == "image/png"
        assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"

    def test_per_row_reject_undoes_autoapply(self):
        wo, sub, tasks = self._staged(mark_count=1)
        tc0 = WorkOrderTaskCompletion.objects.get(id=tasks[0][len("task_") :])
        assert tc0.is_completed is True  # auto-pre-checked

        client, _user = _staff_client()
        resp = client.post(
            f"/api/inventory/work-orders/{wo.id}/submissions/{sub.id}/discard-pending/",
            {"target_ids": [tasks[0]]},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        tc0.refresh_from_db()
        assert tc0.is_completed is False  # rejection undid the pre-check

    def test_per_row_accept_applies_only_selected(self):
        wo = _make_work_order(num_tasks=3)
        template = _persisted(wo)
        tasks = _task_target_ids(template)
        # two marginal marks (both queued, neither pre-applied)
        sub = _submission_for(
            wo,
            _synth_scan(template, wo_id=wo.id, marks={tasks[0]: "marginal", tasks[1]: "marginal"}),
        )
        apply_submission(sub)

        client, _user = _staff_client()
        resp = client.post(
            f"/api/inventory/work-orders/{wo.id}/submissions/{sub.id}/apply-pending/",
            {"target_ids": [tasks[0]]},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        assert WorkOrderTaskCompletion.objects.get(id=tasks[0][len("task_") :]).is_completed is True
        assert (
            WorkOrderTaskCompletion.objects.get(id=tasks[1][len("task_") :]).is_completed is False
        )
        sub.refresh_from_db()
        # one row applied, one still queued → stays in review
        assert sub.status == WorkOrderSubmission.Status.PENDING_REVIEW
        assert len(sub.pending_changes) == 1
