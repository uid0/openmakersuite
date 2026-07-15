"""Tests for the OMR scan reader + scan-ingestion pipeline (op-6pc8, bead-2;
two-axis auto-apply revised in op-f034).

bead-1 printed the form + fiducials and persisted a region map; this reader
aligns a *scan*, thresholds each mark, and applies the two-axis auto-apply
policy — a mark records automatically only when the scan registered adequately
(``registration_confidence >= OMR_REG_MIN``) AND the box is confidently filled,
otherwise it routes to human review — and NEVER auto-closes a work order.

The synthetic harness renders fiducials + checkboxes + a QR directly into
canonical template space (there is no PDF rasterizer in the venv), stamps known
marks, and asserts the reader recovers exactly the stamped set. Ground-truth
positions are computed from the *documented* normalization contract
(``rect_norm`` against the fiducial centers), independent of reader internals.
"""

from __future__ import annotations

import io

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils.crypto import get_random_string

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
    WorkOrderMaterialUsage,
    WorkOrderSubmission,
    WorkOrderTaskCompletion,
)
from inventory.services import work_order_omr as omr
from inventory.services.work_order_cv import Detection, auto_apply_or_queue
from inventory.services.work_order_ingest import apply_submission
from inventory.services.work_order_omr import (
    OMR_REG_MIN,
    OmrReadResult,
    OmrRegionRead,
    build_and_persist_omr_template,
    detections_from_result,
    read_omr_scan,
)
from inventory.tests.test_work_order_omr import (
    _make_wo_with_materials,
    _make_work_order,
    _staff_client,
)
from notifications.models import Notification

pytestmark = pytest.mark.django_db

User = get_user_model()

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
        # the two clear marks are confidently CHECKED (fill in the on-band);
        # the blank does not read at all.
        assert by_id[tasks[0]].fill_ratio >= omr._CHECK_ON
        assert by_id[tasks[1]].fill_ratio >= omr._CHECK_ON

    def test_marginal_mark_is_uncertain(self):
        wo = _make_work_order(num_tasks=2)
        template = _persisted(wo)
        tasks = _task_target_ids(template)
        result = read_omr_scan(_synth_scan(template, marks={tasks[0]: "marginal"}), template)
        by_id = {r.target_id: r for r in result.reads}
        # a marginal mark lands in the ambiguous band — above "off" (so it still
        # surfaces for review) but below the "on" bar, so it is NOT confidently
        # CHECKED and will never auto-apply, regardless of registration.
        assert omr._CHECK_OFF < by_id[tasks[0]].fill_ratio < omr._CHECK_ON

    def test_ordinary_skewed_4corner_scan_is_adequate_for_auto_apply(self):
        # The decisive op-f034 behaviour change: an ordinary hand-fed skewed scan
        # is NOT flatbed-perfect (reg < 1.0) yet still registers adequately off
        # its 4 corners (reg >= OMR_REG_MIN), so its solidly-filled boxes qualify
        # for auto-apply — the retired 0.999 bar wrongly queued exactly these.
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
        assert result.ok  # aligned off 4 fiducials
        # adequate for auto-apply, but demonstrably not a flatbed-perfect scan.
        assert OMR_REG_MIN <= result.registration_confidence < 1.0
        marks = [r for r in result.reads if r.target_id in tasks]
        assert marks and all(r.fill_ratio >= omr._CHECK_ON for r in marks)

    def test_three_corner_read_is_inadequate(self):
        # Exactly one corner missing → affine fallback hard-capped at 0.7, which
        # sits below OMR_REG_MIN so the whole page routes to review even though
        # the box is solidly filled.
        wo = _make_work_order(num_tasks=2)
        template = _persisted(wo)
        tasks = _task_target_ids(template)
        result = read_omr_scan(
            _synth_scan(template, marks={tasks[0]: "full"}, drop_fiducials=("tl",)), template
        )
        assert result.ok  # 3 corners still align (affine)
        assert result.registration_confidence == pytest.approx(0.7)
        assert result.registration_confidence < OMR_REG_MIN

    def test_brightness_gradient_still_reads_marks(self):
        wo = _make_work_order(num_tasks=2)
        template = _persisted(wo)
        tasks = _task_target_ids(template)
        result = read_omr_scan(
            _synth_scan(template, marks={tasks[0]: "full"}, gradient=True), template
        )
        by_id = {r.target_id: r for r in result.reads}
        assert by_id[tasks[0]].marked is True
        assert by_id[tasks[0]].fill_ratio >= omr._CHECK_ON

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
        # auto-applied because registration was adequate AND the fill was
        # confidently in the "on" band (a clean scan → high displayed confidence).
        assert by_id[tasks[0]]["confidence"] >= 0.9
        assert sub.parsed_fields["registration_confidence"] >= OMR_REG_MIN

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
        # queued on the FILL axis: registration was adequate (a clean scan), but
        # the ambiguous fill is not confidently CHECKED, so it never auto-applies.
        assert sub.parsed_fields["registration_confidence"] >= OMR_REG_MIN

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

    def test_ordinary_skewed_scan_auto_applies_solid_marks(self):
        # End-to-end headline regression (op-f034): a solidly-ticked box on an
        # ordinary skewed (non-flatbed) 4-corner scan now records automatically,
        # advancing OPEN → IN_PROGRESS — the retired 0.999 bar queued these.
        wo = _make_work_order(num_tasks=3)
        template = _persisted(wo)
        tasks = _task_target_ids(template)
        w = int(round(template.page_w_pt / 72.0 * DPI))
        h = int(round(template.page_h_pt / 72.0 * DPI))
        warp_dst = np.float32([[40, 30], [w - 20, 70], [w - 60, h - 40], [30, h - 25]])
        scan = _synth_scan(template, wo_id=wo.id, marks={tasks[0]: "full"}, warp_dst=warp_dst)
        sub = _submission_for(wo, scan)
        apply_submission(sub)
        sub.refresh_from_db()
        wo.refresh_from_db()

        assert sub.status == WorkOrderSubmission.Status.PENDING_REVIEW
        assert OMR_REG_MIN <= sub.parsed_fields["registration_confidence"] < 1.0
        tc0 = WorkOrderTaskCompletion.objects.get(id=tasks[0][len("task_") :])
        assert tc0.is_completed is True  # auto-applied despite the skew
        by_id = {c["target_id"]: c for c in sub.pending_changes}
        assert by_id[tasks[0]]["auto_applied"] is True
        assert wo.status == WorkOrder.Status.IN_PROGRESS  # progressed, never COMPLETED

    def test_three_corner_scan_queues_all_marks(self):
        # A 3-corner (affine, reg 0.7) scan is inadequate: even a solid mark
        # routes to review, nothing is applied, and the WO stays OPEN.
        wo = _make_work_order(num_tasks=2)
        template = _persisted(wo)
        tasks = _task_target_ids(template)
        scan = _synth_scan(template, wo_id=wo.id, marks={tasks[0]: "full"}, drop_fiducials=("tl",))
        sub = _submission_for(wo, scan)
        apply_submission(sub)
        sub.refresh_from_db()
        wo.refresh_from_db()

        assert sub.status == WorkOrderSubmission.Status.PENDING_REVIEW
        assert sub.parsed_fields["registration_confidence"] < OMR_REG_MIN
        tc0 = WorkOrderTaskCompletion.objects.get(id=tasks[0][len("task_") :])
        assert tc0.is_completed is False  # inadequate registration → queued
        by_id = {c["target_id"]: c for c in sub.pending_changes}
        assert tasks[0] in by_id
        assert by_id[tasks[0]]["auto_applied"] is False
        assert wo.status == WorkOrder.Status.OPEN  # nothing applied

    def test_two_axis_matrix_through_apply(self, monkeypatch):
        # Craft reads (registration + fill controlled directly) to drive every
        # branch through _apply_omr_submission in one pass: solid task + solid
        # material auto-apply on an adequately-registered scan; an ambiguous-fill
        # task queues. WO advances OPEN → IN_PROGRESS but is NEVER COMPLETED.
        wo = _make_wo_with_materials(num_tasks=2, num_materials=1)
        template = _persisted(wo)
        tasks = _task_target_ids(template)
        mu = wo.material_usage.first()
        material_tid = f"material_{mu.id}"

        crafted = OmrReadResult(
            ok=True,
            registration_confidence=0.9,  # adequate (>= OMR_REG_MIN)
            recovered_work_order_id=str(wo.id),
            reads=[
                OmrRegionRead(tasks[0], "checkbox", True, 0.9, 1.0),  # solid → auto
                OmrRegionRead(tasks[1], "checkbox", True, 0.6, 0.15),  # ambiguous → queue
                OmrRegionRead(material_tid, "checkbox", True, 0.9, 1.0),  # solid → auto
            ],
            canonical_size=(100, 100),
        )
        monkeypatch.setattr(omr, "read_omr_scan", lambda *a, **k: crafted)

        sub = _submission_for(wo, _synth_scan(template, wo_id=wo.id))
        apply_submission(sub)
        sub.refresh_from_db()
        wo.refresh_from_db()

        assert WorkOrderTaskCompletion.objects.get(id=tasks[0][len("task_") :]).is_completed is True
        assert WorkOrderMaterialUsage.objects.get(id=mu.id).was_used is True
        assert (
            WorkOrderTaskCompletion.objects.get(id=tasks[1][len("task_") :]).is_completed is False
        )

        by_id = {c["target_id"]: c for c in sub.pending_changes}
        assert by_id[tasks[0]]["auto_applied"] is True
        assert by_id[material_tid]["auto_applied"] is True
        assert by_id[tasks[1]]["auto_applied"] is False

        assert wo.status == WorkOrder.Status.IN_PROGRESS
        assert wo.completed_at is None
        assert sub.status == WorkOrderSubmission.Status.PENDING_REVIEW


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

    def test_scan_image_endpoint_returns_full_page_png(self):
        # op-o6rs: the reviewer verifies the marks against the whole paper form,
        # so the endpoint renders the full scanned page (not a per-mark crop).
        wo, sub, _tasks = self._staged()
        client, _user = _staff_client()
        resp = client.get(f"/api/inventory/work-orders/{wo.id}/submissions/{sub.id}/scan-image/")
        assert resp.status_code == status.HTTP_200_OK
        assert resp["Content-Type"] == "image/png"
        assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"

    def test_scan_image_endpoint_404s_unknown_submission(self):
        wo, _sub, _tasks = self._staged()
        client, _user = _staff_client()
        resp = client.get(f"/api/inventory/work-orders/{wo.id}/submissions/{wo.id}/scan-image/")
        assert resp.status_code == status.HTTP_404_NOT_FOUND

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


# ---------------------------------------------------------------------------
# two-axis auto-apply decision (op-f034) — pure unit, no CV/DB
# ---------------------------------------------------------------------------
class TestAutoApplyPolicy:
    """The registration × fill-confidence split in ``auto_apply_or_queue`` and
    the ``confident_checked`` flag ``detections_from_result`` derives."""

    def _det(self, *, confident_checked, value=True, fill_ratio=1.0):
        return Detection(
            kind="checkbox",
            target_id="task_x",
            value=value,
            confidence=0.5,  # irrelevant under the OMR two-axis policy
            fill_ratio=fill_ratio,
            confident_checked=confident_checked,
        )

    def test_reg_min_above_three_corner_cap(self):
        # 3-corner affine reads are hard-capped at 0.7; the floor must exceed it
        # so those reads always route to review.
        assert OMR_REG_MIN > 0.7

    def test_solid_fill_adequate_registration_auto_applies(self):
        det = self._det(confident_checked=True)
        auto, queue = auto_apply_or_queue([det], registration_confidence=0.85, reg_min=OMR_REG_MIN)
        assert auto == [det]
        assert queue == []

    def test_solid_fill_three_corner_registration_queues(self):
        det = self._det(confident_checked=True)
        auto, queue = auto_apply_or_queue([det], registration_confidence=0.7, reg_min=OMR_REG_MIN)
        assert auto == []
        assert queue == [det]

    def test_ambiguous_fill_adequate_registration_queues(self):
        det = self._det(confident_checked=False, fill_ratio=0.15)
        auto, queue = auto_apply_or_queue([det], registration_confidence=0.99, reg_min=OMR_REG_MIN)
        assert auto == []
        assert queue == [det]

    def test_reg_min_defaults_to_omr_reg_min(self):
        # Exactly at the floor is adequate (>=); reg_min falls back to OMR_REG_MIN.
        det = self._det(confident_checked=True)
        auto, _queue = auto_apply_or_queue([det], registration_confidence=OMR_REG_MIN)
        assert auto == [det]

    def test_single_axis_confidence_path_is_unchanged(self):
        # Without ``registration_confidence`` the born-digital confidence split
        # still applies and ``confident_checked`` is ignored.
        hi = Detection(kind="signature", target_id=None, value=True, confidence=0.95)
        lo = Detection(kind="handwritten", target_id=None, value="x", confidence=0.4)
        auto, queue = auto_apply_or_queue([hi, lo], threshold=0.7)
        assert auto == [hi]
        assert queue == [lo]

    def test_detections_from_result_flags_confident_checked_and_drops_empty(self):
        result = OmrReadResult(
            ok=True,
            registration_confidence=1.0,
            reads=[
                OmrRegionRead("task_solid", "checkbox", True, 1.0, 0.90),  # confident checked
                OmrRegionRead("task_amb", "checkbox", True, 0.6, 0.15),  # ambiguous
                OmrRegionRead("task_blank", "checkbox", False, 0.98, 0.01),  # confident empty
                OmrRegionRead("tech_initials", "ink", True, 1.0, 0.09),  # ink, confident
            ],
        )
        dets = {d.target_id: d for d in detections_from_result(result)}
        assert "task_blank" not in dets  # confidently-empty box dropped as a no-op
        assert dets["task_solid"].confident_checked is True
        assert dets["task_solid"].fill_ratio == pytest.approx(0.90)
        assert dets["task_amb"].confident_checked is False
        assert dets["tech_initials"].confident_checked is True  # 0.09 >= _INK_ON (0.05)


# ---------------------------------------------------------------------------
# scan-in notification (op-o6rs): a scan landing in review notifies app-wide
# ---------------------------------------------------------------------------
NOTIFY_TITLE = "Work order scanned — needs review"


class TestScanNotification:
    """A SCAN → PENDING_REVIEW ingest fans out exactly one app-wide notification
    (one row per staff user, not duplicated across the pipeline's several
    saves); the degraded ``_omr_review`` path notifies too; the ``_omr_fail``
    hard-failure path does not; and a notification failure never breaks ingest.
    """

    def _staff(self, n=2):
        return [
            User.objects.create_user(
                username=f"staff_{i}_{get_random_string(5)}",
                email=f"staff{i}@example.com",
                password=get_random_string(24),
                is_staff=True,
                is_active=True,
            )
            for i in range(n)
        ]

    def test_clean_scan_notifies_admins_exactly_once(self, django_capture_on_commit_callbacks):
        staff = self._staff(2)
        wo = _make_work_order(num_tasks=3)
        template = _persisted(wo)
        tasks = _task_target_ids(template)
        scan = _synth_scan(template, wo_id=wo.id, marks={tasks[0]: "full"})
        sub = _submission_for(wo, scan)

        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            apply_submission(sub)

        # exactly one on_commit hook despite the pipeline's several saves
        assert len(callbacks) == 1
        notes = Notification.objects.filter(title=NOTIFY_TITLE)
        # one row per staff user — not duplicated
        assert notes.count() == len(staff)
        n = notes.first()
        assert n.type == "warning"
        assert n.action_url == f"/maintenance/work-orders/{wo.id}"
        assert n.metadata["work_order_id"] == str(wo.id)
        assert n.metadata["submission_id"] == str(sub.id)
        assert n.metadata["kind"] == "work_order_scanned"

    def test_notification_is_deferred_until_commit(self, django_capture_on_commit_callbacks):
        # No notification exists until the ingest transaction actually commits —
        # a rolled-back ingest must never notify (on_commit, not inline).
        self._staff(1)
        wo = _make_work_order(num_tasks=2)
        template = _persisted(wo)
        scan = _synth_scan(template, wo_id=wo.id, marks={_task_target_ids(template)[0]: "full"})
        sub = _submission_for(wo, scan)

        with django_capture_on_commit_callbacks(execute=False):
            apply_submission(sub)
            # inside the block the commit hasn't run yet
            assert not Notification.objects.filter(title=NOTIFY_TITLE).exists()

    def test_degraded_scan_notifies(self, django_capture_on_commit_callbacks):
        staff = self._staff(1)
        wo = _make_work_order(num_tasks=2)
        template = _persisted(wo)
        scan = _synth_scan(template, wo_id=wo.id, marks={_task_target_ids(template)[0]: "full"})
        wo.omr_templates.all().delete()  # no template on file → _omr_review path
        sub = _submission_for(wo, scan)

        with django_capture_on_commit_callbacks(execute=True):
            apply_submission(sub)
        sub.refresh_from_db()

        assert sub.status == WorkOrderSubmission.Status.PENDING_REVIEW
        assert Notification.objects.filter(title=NOTIFY_TITLE, user__in=staff).count() == len(staff)

    def test_failed_scan_does_not_notify(self, django_capture_on_commit_callbacks):
        import uuid

        self._staff(1)
        wo = _make_work_order(num_tasks=2)
        template = _persisted(wo)
        scan = _synth_scan(template, wo_id=uuid.uuid4(), marks={})  # unresolvable WO id
        sub = _submission_for(wo, scan)

        with django_capture_on_commit_callbacks(execute=True):
            apply_submission(sub)
        sub.refresh_from_db()

        assert sub.status == WorkOrderSubmission.Status.FAILED
        assert not Notification.objects.filter(title=NOTIFY_TITLE).exists()

    def test_notification_failure_does_not_break_ingest(
        self, monkeypatch, django_capture_on_commit_callbacks
    ):
        self._staff(1)
        wo = _make_work_order(num_tasks=2)
        template = _persisted(wo)
        scan = _synth_scan(template, wo_id=wo.id, marks={_task_target_ids(template)[0]: "full"})
        sub = _submission_for(wo, scan)

        def _boom(*a, **k):
            raise RuntimeError("notification backend down")

        # notify_admins is imported lazily inside the hook; patch it at source.
        monkeypatch.setattr("notifications.services.notify_admins", _boom)

        with django_capture_on_commit_callbacks(execute=True):
            apply_submission(sub)
        sub.refresh_from_db()

        # ingest still succeeded even though the notification blew up
        assert sub.status == WorkOrderSubmission.Status.PENDING_REVIEW
        assert not Notification.objects.filter(title=NOTIFY_TITLE).exists()


# ---------------------------------------------------------------------------
# per-WO pending-review badge (op-o6rs): serializer field + prefetch (no N+1)
# ---------------------------------------------------------------------------
class TestPendingReviewBadge:
    def _sub(self, wo, status_value):
        return WorkOrderSubmission.objects.create(
            work_order=wo,
            kind=WorkOrderSubmission.Kind.PM_COMPLETION,
            source=WorkOrderSubmission.Source.SCAN,
            status=status_value,
        )

    def test_list_row_exposes_pending_review_count(self):
        wo = _make_work_order(num_tasks=1)
        self._sub(wo, WorkOrderSubmission.Status.PENDING_REVIEW)
        self._sub(wo, WorkOrderSubmission.Status.APPLIED)  # not counted
        client, _user = _staff_client()
        resp = client.get("/api/inventory/work-orders/")
        assert resp.status_code == status.HTTP_200_OK
        row = next(r for r in resp.data["results"] if r["id"] == str(wo.id))
        assert row["pending_review_count"] == 1
        assert row["has_pending_review"] is True

    def test_detail_header_exposes_pending_review_count(self):
        wo = _make_work_order(num_tasks=1)
        self._sub(wo, WorkOrderSubmission.Status.PENDING_REVIEW)
        client, _user = _staff_client()
        resp = client.get(f"/api/inventory/work-orders/{wo.id}/")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["pending_review_count"] == 1
        assert resp.data["has_pending_review"] is True

    def test_no_pending_review_reads_zero(self):
        wo = _make_work_order(num_tasks=1)
        self._sub(wo, WorkOrderSubmission.Status.APPLIED)
        client, _user = _staff_client()
        resp = client.get(f"/api/inventory/work-orders/{wo.id}/")
        assert resp.data["pending_review_count"] == 0
        assert resp.data["has_pending_review"] is False

    def test_pending_review_count_has_no_nplus1(self):
        # Submissions are prefetched, so listing WOs that each carry submissions
        # costs the same number of queries as listing WOs with none — the count
        # never fires a per-row query.
        client, _user = _staff_client()

        def _list_queries(n_subs_each):
            WorkOrder.objects.all().delete()
            for _ in range(3):
                wo = _make_work_order(num_tasks=1)
                for _ in range(n_subs_each):
                    self._sub(wo, WorkOrderSubmission.Status.PENDING_REVIEW)
            with CaptureQueriesContext(connection) as ctx:
                resp = client.get("/api/inventory/work-orders/")
                assert resp.status_code == status.HTTP_200_OK
            return len(ctx.captured_queries)

        assert _list_queries(0) == _list_queries(2)
