"""Tests for the OMR (scan-to-complete) work-order form variant (op-w4ju).

bead-1 generates a Work Order PDF a tech fills BY HAND, prints 4 corner
fiducials, and persists a resolution-independent region map so bead-2's reader
can align a *scan* and threshold each mark. There is deliberately NO CV read of
scanned marks here — only form generation, the region map, and the
fiducial-recovery contract.
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils.crypto import get_random_string

import numpy as np
import pytest
from PIL import Image
from reportlab.lib.pagesizes import letter
from rest_framework import status
from rest_framework.test import APIClient

from fiducials.services.apriltag_render import (
    FAMILY_ARUCO_4X4_50,
    FORM_FIDUCIAL_IDS,
    build_form_fiducials,
    decode_apriltag_ids,
)
from inventory.models import WorkOrderMaterialUsage, WorkOrderOmrTemplate, WorkOrderValidation
from inventory.services.work_order_omr import (
    build_and_persist_omr_template,
    compute_template_version,
    dynamic_target_ids,
)
from inventory.tests.test_work_order_ingest import _make_work_order
from inventory.utils.work_order_pdf import (
    _FIDUCIAL_SIZE_PT,
    _fiducial_layout,
    generate_work_order_omr_pdf,
    generate_work_order_pdf,
)

User = get_user_model()
pytestmark = pytest.mark.django_db

FIXED_MARKS = {"work_complete", "result_pass", "result_fail", "tech_initials", "tech_date"}


def _make_wo_with_materials(num_tasks=3, num_materials=2):
    wo = _make_work_order(num_tasks=num_tasks)
    for i in range(num_materials):
        WorkOrderMaterialUsage.objects.create(
            work_order=wo,
            material_name=f"Material {i + 1}",
            quantity_planned=Decimal("2.00"),
            unit="ea",
        )
    return wo


def _validate(wo):
    return WorkOrderValidation.objects.create(
        work_order=wo,
        electrical_acknowledged=True,
        loto_acknowledged=True,
        required_fields_acknowledged=True,
    )


def _member_client():
    user = User.objects.create_user(
        username=f"member_{get_random_string(6)}",
        email="member@example.com",
        password=get_random_string(24),
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client, user


def _staff_client():
    user = User.objects.create_user(
        username=f"staff_{get_random_string(6)}",
        email="staff@example.com",
        password=get_random_string(24),
        is_staff=True,
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client, user


def _render_fiducial_page(template_map, dpi=200):
    """Rasterize just the form's 4 corner fiducials onto a white page.

    Uses the persisted anchor layout (the same geometry reportlab draws with)
    so the round-trip exercises the reader's fiducial-recovery contract without
    pulling in a PDF-rasterization dependency (that belongs to bead-2).
    """
    page_w = template_map["page_w_pt"]
    page_h = template_map["page_h_pt"]
    w_px = int(round(page_w / 72.0 * dpi))
    h_px = int(round(page_h / 72.0 * dpi))
    page = Image.new("L", (w_px, h_px), 255)
    layout = _fiducial_layout(page_w, page_h)
    s_px = int(round(_FIDUCIAL_SIZE_PT / 72.0 * dpi))
    markers = build_form_fiducials(s_px)
    for corner, spec in layout.items():
        x_px = int(round(spec["x0"] / 72.0 * dpi))
        top_px = int(round(h_px - (spec["y0"] + _FIDUCIAL_SIZE_PT) / 72.0 * dpi))
        page.paste(markers[corner].convert("L"), (x_px, top_px))
    return page


class TestOmrRegionMap:
    def test_region_map_covers_every_checkbox_and_the_new_marks(self):
        wo = _make_wo_with_materials(num_tasks=3, num_materials=2)
        tcs = list(wo.task_completions.all())
        usages = list(wo.material_usage.all())

        _pdf, template_map = generate_work_order_omr_pdf(wo, base_url="http://example.com")
        mapped = {r["target_id"] for r in template_map["regions"]}

        for tc in tcs:
            assert f"task_{tc.id}" in mapped
        for mu in usages:
            assert f"material_{mu.id}" in mapped
        assert FIXED_MARKS <= mapped

    def test_materialspec_fallback_when_no_usage_rows(self):
        # Legacy WOs without usage rows fall back to the item's material spec —
        # the region map must still cover those materialspec_<id> boxes.
        from inventory.models import MaintenanceMaterial

        wo = _make_work_order(num_tasks=1)
        spec = MaintenanceMaterial.objects.create(
            maintenance_item=wo.maintenance_item,
            name="Filter cartridge",
            quantity=Decimal("1.00"),
            unit="ea",
        )
        _pdf, template_map = generate_work_order_omr_pdf(wo, base_url="")
        mapped = {r["target_id"] for r in template_map["regions"]}
        assert f"materialspec_{spec.id}" in mapped

    def test_regions_normalized_within_unit_square(self):
        wo = _make_wo_with_materials()
        _pdf, template_map = generate_work_order_omr_pdf(wo, base_url="")
        for r in template_map["regions"]:
            x0, y0, x1, y1 = r["rect_norm"]
            assert 0.0 <= x0 < x1 <= 1.0, r
            assert 0.0 <= y0 < y1 <= 1.0, r
            assert r["kind"] in {"checkbox", "ink"}

    def test_ink_regions_use_ink_kind(self):
        wo = _make_wo_with_materials()
        _pdf, template_map = generate_work_order_omr_pdf(wo, base_url="")
        by_id = {r["target_id"]: r for r in template_map["regions"]}
        assert by_id["tech_initials"]["kind"] == "ink"
        assert by_id["tech_date"]["kind"] == "ink"
        assert by_id["work_complete"]["kind"] == "checkbox"

    def test_map_dynamic_targets_match_version_source(self):
        # The version signature and the drawn dynamic marks must agree, or the
        # drift guard would compare against a set the form never had.
        wo = _make_wo_with_materials(num_tasks=2, num_materials=2)
        _pdf, template_map = generate_work_order_omr_pdf(wo, base_url="")
        dynamic_mapped = {
            r["target_id"] for r in template_map["regions"] if r["target_id"] not in FIXED_MARKS
        }
        assert dynamic_mapped == set(dynamic_target_ids(wo))


class TestOmrSnapshot:
    def test_persists_single_snapshot_with_signature(self):
        wo = _make_wo_with_materials()
        pdf_bytes, template = build_and_persist_omr_template(wo, base_url="http://x")

        assert pdf_bytes[:5] == b"%PDF-"
        assert WorkOrderOmrTemplate.objects.filter(work_order=wo).count() == 1
        assert template.template_version == compute_template_version(wo)
        assert template.fiducial_dict == FAMILY_ARUCO_4X4_50
        assert set(template.fiducials_json) == {"tl", "tr", "br", "bl"}
        assert sorted(a["id"] for a in template.fiducials_json.values()) == [0, 1, 2, 3]
        assert FIXED_MARKS <= {r["target_id"] for r in template.regions_json}
        assert template.page_w_pt == letter[0]
        assert template.page_h_pt == letter[1]

    def test_reprint_replaces_snapshot_in_place(self):
        wo = _make_wo_with_materials()
        _b, first = build_and_persist_omr_template(wo, base_url="")
        _b, second = build_and_persist_omr_template(wo, base_url="")
        assert WorkOrderOmrTemplate.objects.filter(work_order=wo).count() == 1
        assert first.pk == second.pk

    def test_version_changes_when_tasks_change(self):
        # Template-drift guard: editing the checklist changes the signature, so
        # bead-2 can refuse a scan of a now-stale printed sheet.
        from inventory.models import WorkOrderTaskCompletion

        wo = _make_wo_with_materials(num_tasks=2)
        _b, template = build_and_persist_omr_template(wo, base_url="")
        stored = template.template_version

        WorkOrderTaskCompletion.objects.create(
            work_order=wo, task_title="Added step", task_order=99, is_required=True
        )
        assert compute_template_version(wo) != stored


class TestFiducialRoundTrip:
    def test_four_corners_detect_and_map_back_to_anchors(self):
        import cv2

        wo = _make_wo_with_materials()
        _pdf, template_map = generate_work_order_omr_pdf(wo, base_url="")

        dpi = 200
        page = _render_fiducial_page(template_map, dpi=dpi)

        # (1) all four corner ids decode.
        assert sorted(decode_apriltag_ids(page, family=FAMILY_ARUCO_4X4_50)) == [0, 1, 2, 3]

        # (2) each detected marker center maps back to its persisted anchor —
        # the 4 corner correspondences the reader relies on to warp a scan.
        dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        detector = cv2.aruco.ArucoDetector(dictionary, cv2.aruco.DetectorParameters())
        corners, ids, _ = detector.detectMarkers(np.array(page))
        id_to_corner = {v: k for k, v in FORM_FIDUCIAL_IDS.items()}
        page_h = template_map["page_h_pt"]
        seen = set()
        for quad, marker_id in zip(corners, ids.ravel().tolist()):
            corner = id_to_corner[int(marker_id)]
            seen.add(corner)
            cx_px, cy_px = quad.reshape(-1, 2).mean(axis=0)
            cx_pt = cx_px / dpi * 72.0
            cy_pt = page_h - cy_px / dpi * 72.0
            anchor = template_map["fiducials"][corner]
            assert abs(cx_pt - anchor["cx"]) < 4.0
            assert abs(cy_pt - anchor["cy"]) < 4.0
        assert seen == {"tl", "tr", "br", "bl"}


class TestOmrVariantVsStandard:
    def test_standard_pdf_has_no_capture_side_effects(self):
        # Generating the plain PDF must not persist a template or alter output.
        wo = _make_wo_with_materials()
        standard = generate_work_order_pdf(wo, base_url="")
        assert standard[:5] == b"%PDF-"
        assert not WorkOrderOmrTemplate.objects.filter(work_order=wo).exists()

    def test_omr_pdf_differs_from_standard(self):
        wo = _make_wo_with_materials()
        standard = generate_work_order_pdf(wo, base_url="")
        omr, _map = generate_work_order_omr_pdf(wo, base_url="")
        # Fiducials + extra marks make the OMR variant materially larger.
        assert len(omr) > len(standard)


@pytest.mark.integration
class TestOmrPdfEndpoint:
    URL = "/api/inventory/work-orders/{}/omr-pdf/"

    def test_412_without_validation(self):
        client, _u = _staff_client()
        wo = _make_wo_with_materials()
        resp = client.get(self.URL.format(wo.id))
        assert resp.status_code == status.HTTP_412_PRECONDITION_FAILED
        assert resp.json()["code"] == "validation_required"
        assert not WorkOrderOmrTemplate.objects.filter(work_order=wo).exists()

    def test_200_and_persists_snapshot_after_validation(self):
        client, _u = _staff_client()
        wo = _make_wo_with_materials()
        _validate(wo)
        resp = client.get(self.URL.format(wo.id))
        assert resp.status_code == status.HTTP_200_OK
        assert resp["Content-Type"] == "application/pdf"
        assert resp.content[:5] == b"%PDF-"
        template = WorkOrderOmrTemplate.objects.get(work_order=wo)
        assert template.template_version == compute_template_version(wo)

    def test_member_can_read_after_validation(self):
        # GET is a safe method — any authenticated user may print, matching pdf.
        client, _u = _member_client()
        wo = _make_wo_with_materials()
        _validate(wo)
        resp = client.get(self.URL.format(wo.id))
        assert resp.status_code == status.HTTP_200_OK

    def test_anonymous_denied(self):
        wo = _make_wo_with_materials()
        _validate(wo)
        resp = APIClient().get(self.URL.format(wo.id))
        assert resp.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)


@pytest.mark.integration
class TestPdfEndpointUnifiedOnOmr:
    """op-8mjw: the ``/pdf/`` route unifies on the OMR variant and ``omr-pdf``
    is a thin deprecated alias — a single PDF generation route. Every printed
    sheet now carries fiducials + an upserted template (scan-to-complete) while
    keeping the interactive AcroForm layer (emailed-digital fill)."""

    PDF_URL = "/api/inventory/work-orders/{}/pdf/"
    OMR_URL = "/api/inventory/work-orders/{}/omr-pdf/"

    @staticmethod
    def _template_snapshot(wo):
        t = WorkOrderOmrTemplate.objects.get(work_order=wo)  # exactly one row (upsert)
        return {
            "template_version": t.template_version,
            "page_w_pt": t.page_w_pt,
            "page_h_pt": t.page_h_pt,
            "fiducial_dict": t.fiducial_dict,
            "fiducials_json": t.fiducials_json,
            "regions_json": t.regions_json,
        }

    def test_pdf_route_still_gated_without_validation(self):
        client, _u = _staff_client()
        wo = _make_wo_with_materials()
        resp = client.get(self.PDF_URL.format(wo.id))
        assert resp.status_code == status.HTTP_412_PRECONDITION_FAILED
        assert resp.json()["code"] == "validation_required"
        # A blocked request must not persist a template.
        assert not WorkOrderOmrTemplate.objects.filter(work_order=wo).exists()

    def test_pdf_route_emits_omr_variant_and_upserts_template(self):
        client, _u = _staff_client()
        wo = _make_wo_with_materials()
        _validate(wo)

        resp = client.get(self.PDF_URL.format(wo.id))

        assert resp.status_code == status.HTTP_200_OK
        assert resp["Content-Type"] == "application/pdf"
        assert resp.content[:5] == b"%PDF-"
        # The /pdf/ route now upserts exactly one OMR template (as omr-pdf did).
        snapshot = self._template_snapshot(wo)
        assert snapshot["template_version"] == compute_template_version(wo)
        # ...and the bytes are the OMR superset, not the plain digital form
        # (which persists nothing — see TestOmrVariantVsStandard).
        plain = generate_work_order_pdf(wo, base_url="")
        assert len(resp.content) > len(plain)

    def test_pdf_and_omr_pdf_are_equivalent_and_both_upsert(self):
        client, _u = _staff_client()
        wo = _make_wo_with_materials()
        _validate(wo)

        pdf_resp = client.get(self.PDF_URL.format(wo.id))
        assert pdf_resp.status_code == status.HTTP_200_OK
        after_pdf = self._template_snapshot(wo)

        omr_resp = client.get(self.OMR_URL.format(wo.id))
        assert omr_resp.status_code == status.HTTP_200_OK
        after_omr = self._template_snapshot(wo)

        # Same HTTP envelope, both OMR-variant bytes, and — crucially — the same
        # upserted template snapshot: the two routes are equivalent, and omr-pdf
        # upserts in place rather than creating a second row.
        assert pdf_resp["Content-Type"] == omr_resp["Content-Type"] == "application/pdf"
        assert pdf_resp.content[:5] == omr_resp.content[:5] == b"%PDF-"
        assert after_omr == after_pdf
        assert after_pdf["template_version"] == compute_template_version(wo)

        plain = generate_work_order_pdf(wo, base_url="")
        assert len(pdf_resp.content) > len(plain)
        assert len(omr_resp.content) > len(plain)
