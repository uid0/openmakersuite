"""Tests for the paper work-order LOTO flow (WO-OMR PR4, op-xw4x).

Covers the structured "LOTO = both" half: one ``WorkOrderLotoCompletion`` per
``loto.AssetEnergySource`` on the WO's asset, an OMR-readable ``loto_<id>``
checkbox on the printed sheet, scan read-back through the SAME two-axis
auto-apply gate as tasks/materials, and the serializer/API surface. The free-text
half (``WorkOrder.loto_completion_note`` + the printed lockout paragraph) is
exercised via the serializer round-trip.

Reuses the synthetic-scan harness from ``test_work_order_omr_reader`` (there is no
PDF rasterizer in the venv): fiducials + boxes are rendered directly into
canonical template space and specific ``loto_<id>`` boxes are stamped.
"""

from __future__ import annotations

import uuid

from django.utils.crypto import get_random_string

import pytest
from rest_framework import status

from inventory.models import WorkOrder, WorkOrderLotoCompletion
from inventory.services import work_order_omr as omr
from inventory.services.work_order_ingest import apply_submission, parse_work_order_pdf
from inventory.services.work_order_loto import create_loto_completions
from inventory.services.work_order_omr import OMR_REG_MIN, dynamic_target_ids
from inventory.tests.test_work_order_ingest import _make_work_order
from inventory.tests.test_work_order_omr import _staff_client
from inventory.tests.test_work_order_omr_reader import _persisted, _submission_for, _synth_scan
from inventory.utils.work_order_pdf import generate_work_order_omr_pdf

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _add_energy_source(asset, source_type, *, magnitude="", isolation_point="", devices=()):
    """Attach an AssetEnergySource (+ optional required LOTODevices) to an asset."""
    from loto.models import AssetEnergySource, LOTODevice

    es = AssetEnergySource.objects.create(
        asset=asset,
        source_type=source_type,
        magnitude=magnitude,
        isolation_point=isolation_point,
    )
    for label in devices:
        dev = LOTODevice.objects.create(
            device_type=LOTODevice.DEVICE_PADLOCK,
            label=f"{label}-{get_random_string(4)}",
        )
        es.required_devices.add(dev)
    return es


def _wo_with_energy_sources(num_tasks=2):
    """A work order whose asset has two energy sources + materialized LOTO rows."""
    from loto.models import AssetEnergySource

    wo = _make_work_order(num_tasks=num_tasks)
    asset = wo.maintenance_item.asset
    _add_energy_source(
        asset,
        AssetEnergySource.SOURCE_ELECTRICAL,
        magnitude="240V",
        isolation_point="Panel A breaker 12",
        devices=["PAD"],
    )
    _add_energy_source(
        asset,
        AssetEnergySource.SOURCE_PNEUMATIC,
        magnitude="80psi",
        isolation_point="Red valve",
    )
    create_loto_completions(wo)
    return wo


def _loto_target_ids(template):
    return [r["target_id"] for r in template.regions_json if r["target_id"].startswith("loto_")]


# ---------------------------------------------------------------------------
# WO generation → LOTO completion rows
# ---------------------------------------------------------------------------
class TestLotoCompletionCreation:
    def test_create_loto_completions_one_row_per_energy_source(self):
        wo = _wo_with_energy_sources()
        rows = list(wo.loto_completions.all())
        assert len(rows) == 2
        # denormalized descriptive fields (mirrors task_title on task completions)
        by_label = {r.source_label: r for r in rows}
        assert "Electrical (240V)" in by_label
        assert by_label["Electrical (240V)"].isolation_point == "Panel A breaker 12"
        assert by_label["Electrical (240V)"].required_devices.startswith("PAD-")
        assert "Pneumatic (80psi)" in by_label
        assert all(r.is_completed is False for r in rows)

    def test_no_energy_sources_creates_no_rows_and_no_error(self):
        wo = _make_work_order(num_tasks=1)  # AssetFactory asset, no energy sources
        created = create_loto_completions(wo)
        assert created == []
        assert wo.loto_completions.count() == 0

    def test_create_loto_completions_is_idempotent(self):
        wo = _wo_with_energy_sources()
        assert wo.loto_completions.count() == 2
        # a reprint / bundle path may call it again — must not duplicate rows
        again = create_loto_completions(wo)
        assert again == []
        assert wo.loto_completions.count() == 2

    def test_generate_work_order_endpoint_materializes_loto_rows(self):
        from loto.models import AssetEnergySource

        wo = _make_work_order(num_tasks=1)
        item = wo.maintenance_item
        _add_energy_source(item.asset, AssetEnergySource.SOURCE_ELECTRICAL, magnitude="120V")

        client, _user = _staff_client()
        resp = client.post(
            f"/api/inventory/maintenance-items/{item.id}/generate_work_order/",
            {},
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED
        new_wo = WorkOrder.objects.get(id=resp.data["id"])
        assert new_wo.loto_completions.count() == 1
        assert new_wo.loto_completions.first().source_label == "Electrical (120V)"


# ---------------------------------------------------------------------------
# PDF + OMR template targets
# ---------------------------------------------------------------------------
class TestLotoPdfAndTemplate:
    def test_pdf_template_has_a_loto_box_per_energy_source(self):
        wo = _wo_with_energy_sources()
        _pdf, template_map = generate_work_order_omr_pdf(wo, base_url="http://example.com")
        mapped = {r["target_id"] for r in template_map["regions"]}
        for lc in wo.loto_completions.all():
            assert f"loto_{lc.id}" in mapped

    def test_dynamic_target_ids_includes_loto(self):
        wo = _wo_with_energy_sources()
        ids = dynamic_target_ids(wo)
        expected = {f"loto_{lc.id}" for lc in wo.loto_completions.all()}
        assert expected <= set(ids)

    def test_template_version_is_sensitive_to_energy_source_changes(self):
        # Adding an energy source after printing changes the drift signature, so a
        # scan of the old sheet is refused rather than mis-applied.
        from loto.models import AssetEnergySource

        wo = _wo_with_energy_sources()
        before = omr.compute_template_version(wo)
        _add_energy_source(wo.maintenance_item.asset, AssetEnergySource.SOURCE_THERMAL)
        create_loto_completions(wo)
        after = omr.compute_template_version(wo)
        assert before != after

    def test_no_energy_sources_means_no_loto_boxes_no_error(self):
        wo = _make_work_order(num_tasks=2)  # no energy sources
        pdf, template_map = generate_work_order_omr_pdf(wo, base_url="http://example.com")
        assert pdf[:5] == b"%PDF-"
        loto_regions = [r for r in template_map["regions"] if r["target_id"].startswith("loto_")]
        assert loto_regions == []


# ---------------------------------------------------------------------------
# OMR scan read-back (through the two-axis auto-apply gate)
# ---------------------------------------------------------------------------
class TestLotoOmrReadback:
    def test_solid_loto_mark_auto_applies_completion(self):
        wo = _wo_with_energy_sources()
        template = _persisted(wo)
        loto_ids = _loto_target_ids(template)
        assert loto_ids, "expected loto_ boxes in the persisted template"
        target = loto_ids[0]

        scan = _synth_scan(template, wo_id=wo.id, marks={target: "full"})
        sub = _submission_for(wo, scan)
        apply_submission(sub)
        sub.refresh_from_db()
        wo.refresh_from_db()

        lc = WorkOrderLotoCompletion.objects.get(id=target[len("loto_") :])
        assert lc.is_completed is True  # auto-applied (adequate reg + solid fill)
        assert lc.completed_at is not None
        by_id = {c["target_id"]: c for c in sub.pending_changes}
        assert by_id[target]["auto_applied"] is True
        assert by_id[target]["label"].startswith("LOTO: ")
        # a scan NEVER auto-closes; it may progress OPEN → IN_PROGRESS
        assert wo.status != WorkOrder.Status.COMPLETED
        assert sub.status == sub.Status.PENDING_REVIEW

    def test_three_corner_scan_queues_loto_mark_not_applied(self):
        # LOTO is safety-critical: an inadequately-registered (3-corner, capped at
        # 0.7 < OMR_REG_MIN) scan routes even a solid box to review — "in doubt,
        # queue". Same gate as tasks; nothing auto-applies.
        wo = _wo_with_energy_sources()
        template = _persisted(wo)
        target = _loto_target_ids(template)[0]

        scan = _synth_scan(template, wo_id=wo.id, marks={target: "full"}, drop_fiducials=("tl",))
        sub = _submission_for(wo, scan)
        apply_submission(sub)
        sub.refresh_from_db()

        lc = WorkOrderLotoCompletion.objects.get(id=target[len("loto_") :])
        assert lc.is_completed is False  # queued, not applied
        assert sub.parsed_fields["registration_confidence"] < OMR_REG_MIN
        by_id = {c["target_id"]: c for c in sub.pending_changes}
        assert by_id[target]["auto_applied"] is False

    def test_scan_marking_only_loto_never_closes_work_order(self):
        wo = _wo_with_energy_sources(num_tasks=2)
        template = _persisted(wo)
        # mark BOTH loto boxes solidly but no task boxes
        marks = {tid: "full" for tid in _loto_target_ids(template)}
        scan = _synth_scan(template, wo_id=wo.id, marks=marks)
        sub = _submission_for(wo, scan)
        apply_submission(sub)
        wo.refresh_from_db()
        sub.refresh_from_db()

        assert wo.status != WorkOrder.Status.COMPLETED
        assert wo.completed_at is None
        # both energy sources pre-checked for the reviewer
        assert all(lc.is_completed for lc in wo.loto_completions.all())


# ---------------------------------------------------------------------------
# serializer + API surface
# ---------------------------------------------------------------------------
class TestLotoSerializerAndApi:
    def test_detail_serializer_exposes_loto_completions_and_note(self):
        wo = _wo_with_energy_sources()
        wo.loto_completion_note = "Verified de-energized with meter."
        wo.save(update_fields=["loto_completion_note"])

        client, _user = _staff_client()
        resp = client.get(f"/api/inventory/work-orders/{wo.id}/")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["loto_completion_note"] == "Verified de-energized with meter."
        labels = {row["source_label"] for row in resp.data["loto_completions"]}
        assert labels == {"Electrical (240V)", "Pneumatic (80psi)"}
        row = resp.data["loto_completions"][0]
        for field in ("source_type", "isolation_point", "required_devices", "is_completed"):
            assert field in row

    def test_patch_loto_completion_note_round_trips(self):
        wo = _wo_with_energy_sources()
        client, _user = _staff_client()
        resp = client.patch(
            f"/api/inventory/work-orders/{wo.id}/",
            {"loto_completion_note": "Locked out at panel A."},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        wo.refresh_from_db()
        assert wo.loto_completion_note == "Locked out at panel A."

    def test_complete_loto_endpoint_toggles_and_attributes_user(self):
        wo = _wo_with_energy_sources()
        lc = wo.loto_completions.first()
        client, user = _staff_client()

        resp = client.patch(
            f"/api/inventory/work-orders/{wo.id}/loto/{lc.id}/complete/",
            {"is_completed": True},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        lc.refresh_from_db()
        assert lc.is_completed is True
        assert lc.completed_by_id == user.id
        assert lc.completed_at is not None
        wo.refresh_from_db()
        assert wo.status == WorkOrder.Status.IN_PROGRESS  # progressed, never COMPLETED

        # un-toggling clears attribution
        resp = client.patch(
            f"/api/inventory/work-orders/{wo.id}/loto/{lc.id}/complete/",
            {"is_completed": False},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        lc.refresh_from_db()
        assert lc.is_completed is False
        assert lc.completed_by_id is None
        assert lc.completed_at is None

    def test_complete_loto_unknown_id_404s(self):
        wo = _wo_with_energy_sources()
        client, _user = _staff_client()
        resp = client.patch(
            f"/api/inventory/work-orders/{wo.id}/loto/{uuid.uuid4()}/complete/",
            {"is_completed": True},
            format="json",
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_complete_loto_requires_is_completed(self):
        wo = _wo_with_energy_sources()
        lc = wo.loto_completions.first()
        client, _user = _staff_client()
        resp = client.patch(
            f"/api/inventory/work-orders/{wo.id}/loto/{lc.id}/complete/",
            {},
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# born-digital (AcroForm) parse path also understands loto_
# ---------------------------------------------------------------------------
class TestLotoBornDigitalParse:
    def test_parse_work_order_pdf_reads_loto_checks(self):
        # A digitally-filled OMR PDF (AcroForm intact) carries loto_<id> fields;
        # the born-digital parser surfaces them alongside task/material checks.
        wo = _wo_with_energy_sources()
        pdf = generate_work_order_omr_pdf(wo, base_url="http://example.com")[0]
        parsed = parse_work_order_pdf(pdf)
        assert "loto_checks" in parsed
        expected = {str(lc.id) for lc in wo.loto_completions.all()}
        assert set(parsed["loto_checks"].keys()) == expected
        # unfilled form → every loto box reads False
        assert all(v is False for v in parsed["loto_checks"].values())
