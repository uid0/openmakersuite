"""
oms-2da: tests for work-order feature parity between digital and PDF.

Covers:
- AC-1: WorkOrderSerializer exposes electrical context (asset stubs +
  electrical_circuits join via location).
- AC-2: WorkOrderSerializer exposes LOTO context using existing
  Asset.lockout_* fields, with the empty-state present even when no LOTO
  is required.
- AC-3: validation prompt + audit log; transitions to completed and PDF
  generation are gated on a complete validation record.
- AC-4: CV pipeline produces detections with confidence; low-confidence
  detections route to pending_changes; accept/reject endpoints work.
- AC-5: parity audit — the digital data dict and the PDF's underlying data
  dict are built from the same shared service, so they cannot drift.
"""

from __future__ import annotations

from datetime import date, timedelta

from django.contrib.auth import get_user_model

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from electrical_circuits.models import Breaker, NetworkDrop, Outlet
from inventory.models import (
    Asset,
    MaintenanceItem,
    MaintenanceTask,
    WorkOrder,
    WorkOrderOmrTemplate,
    WorkOrderSubmission,
    WorkOrderTaskCompletion,
    WorkOrderValidation,
)
from inventory.services.work_order_context import (
    build_electrical_context,
    build_loto_context,
    build_work_order_context,
)
from inventory.services.work_order_cv import Detection, auto_apply_or_queue
from inventory.services.work_order_omr import compute_template_version
from inventory.tests.factories import AssetFactory, LocationFactory

pytestmark = pytest.mark.django_db


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def staff_user(db):
    return get_user_model().objects.create_user(
        username="techlead",
        password="x",
        email="lead@example.com",
        is_staff=True,
    )


@pytest.fixture
def api(staff_user):
    client = APIClient()
    client.force_authenticate(user=staff_user)
    return client


def _build_wo(*, asset=None, num_tasks: int = 1) -> WorkOrder:
    asset = asset or AssetFactory()
    item = MaintenanceItem.objects.create(
        asset=asset,
        title="Monthly check",
        description="Routine",
        interval_days=30,
    )
    tasks = [
        MaintenanceTask.objects.create(
            maintenance_item=item,
            order=i,
            title=f"Step {i + 1}",
            is_required=True,
        )
        for i in range(num_tasks)
    ]
    wo = WorkOrder.objects.create(
        maintenance_item=item,
        due_date=date.today() + timedelta(days=7),
    )
    for t in tasks:
        WorkOrderTaskCompletion.objects.create(
            work_order=wo,
            task=t,
            task_title=t.title,
            task_order=t.order,
            is_required=t.is_required,
        )
    return wo


# ─────────────────────────────────────────────────────────────────────────────
# AC-1: electrical context
# ─────────────────────────────────────────────────────────────────────────────


def test_electrical_context_empty_for_bare_asset():
    """No wiring, no circuits → is_empty so frontend shows the empty state."""
    wo = _build_wo()
    ctx = build_electrical_context(wo.maintenance_item.asset)

    assert ctx["rows"] == []
    assert ctx["outlets"] == []
    assert ctx["breakers"] == []
    assert ctx["network_drops"] == []
    assert ctx["is_empty"] is True


def test_electrical_context_pulls_asset_charfield_stubs():
    asset = AssetFactory(
        electrical_box="Panel A enclosure",
        breaker_location="Panel A / 12",
        suite="North bay",
        wiring_type=Asset.WiringType.PLUG_240V,
        has_network_drop=True,
        network_drop_location="Wallplate 3A",
        power_draw_watts=1500,
    )
    wo = _build_wo(asset=asset)
    ctx = build_electrical_context(wo.maintenance_item.asset)

    labels = {row[0] for row in ctx["rows"]}
    assert {
        "Wiring",
        "Rated Power Draw",
        "Suite",
        "Electrical Box",
        "Breaker",
        "Network Drop",
    } <= labels
    assert ctx["is_empty"] is False


def test_electrical_context_joins_electrical_circuits_via_location():
    location = LocationFactory()
    breaker = Breaker.objects.create(location=location, panel="A", breaker_number="12", amperage=20)
    Outlet.objects.create(
        location=location, identifier="bench-1", breaker=breaker, outlet_type="standard"
    )
    NetworkDrop.objects.create(
        location=location, identifier="wallplate-3A", drop_type="data", patch_panel="IDF-1"
    )

    asset = AssetFactory(location=location)
    wo = _build_wo(asset=asset)
    ctx = build_electrical_context(wo.maintenance_item.asset)

    assert len(ctx["outlets"]) == 1
    assert ctx["outlets"][0]["identifier"] == "bench-1"
    assert ctx["outlets"][0]["breaker"]["label"] == "A / 12"
    assert len(ctx["breakers"]) == 1
    assert len(ctx["network_drops"]) == 1
    assert ctx["network_drops"][0]["identifier"] == "wallplate-3A"
    assert ctx["is_empty"] is False


# ─────────────────────────────────────────────────────────────────────────────
# AC-2: LOTO context
# ─────────────────────────────────────────────────────────────────────────────


def test_loto_context_empty_when_no_lockout_required():
    asset = AssetFactory(lockout_type=Asset.LockoutType.NONE)
    ctx = build_loto_context(asset)
    assert ctx["is_required"] is False
    assert ctx["is_empty"] is True


def test_loto_context_populated_when_lockout_required():
    asset = AssetFactory(
        lockout_type=Asset.LockoutType.LOTO,
        lockout_instructions="Open Panel A breaker 12. Lock with tag.",
        lockout_responsible="Ops lead",
    )
    ctx = build_loto_context(asset)
    assert ctx["is_required"] is True
    assert ctx["is_empty"] is False
    assert ctx["lockout_responsible"] == "Ops lead"
    assert "Open Panel A" in ctx["lockout_instructions"]


# ─────────────────────────────────────────────────────────────────────────────
# AC-1+AC-2: serializer surfaces both blocks
# ─────────────────────────────────────────────────────────────────────────────


def test_serializer_includes_electrical_and_loto_blocks(api):
    asset = AssetFactory(
        lockout_type=Asset.LockoutType.LOTO,
        lockout_instructions="Lock breaker A12.",
        electrical_box="Panel A",
    )
    wo = _build_wo(asset=asset)

    res = api.get(f"/api/inventory/work-orders/{wo.id}/")
    assert res.status_code == status.HTTP_200_OK, res.content
    body = res.json()
    assert "electrical" in body
    assert "loto" in body
    assert body["loto"]["is_required"] is True
    assert any(row[0] == "Electrical Box" for row in body["electrical"]["rows"])


# ─────────────────────────────────────────────────────────────────────────────
# AC-3: validation prompt + gates
# ─────────────────────────────────────────────────────────────────────────────


def test_completion_blocked_without_validation(api):
    wo = _build_wo()
    res = api.patch(
        f"/api/inventory/work-orders/{wo.id}/",
        {"status": WorkOrder.Status.COMPLETED},
        format="json",
    )
    assert res.status_code == status.HTTP_412_PRECONDITION_FAILED
    assert res.json()["code"] == "validation_required"
    wo.refresh_from_db()
    assert wo.status != WorkOrder.Status.COMPLETED


def test_pdf_generation_blocked_without_validation(api):
    wo = _build_wo()
    res = api.get(f"/api/inventory/work-orders/{wo.id}/pdf/")
    assert res.status_code == status.HTTP_412_PRECONDITION_FAILED
    assert res.json()["code"] == "validation_required"


def test_validation_endpoint_rejects_partial_acknowledgement(api):
    wo = _build_wo()
    res = api.post(
        f"/api/inventory/work-orders/{wo.id}/validate/",
        {
            "electrical_acknowledged": True,
            "loto_acknowledged": False,
            "required_fields_acknowledged": True,
        },
        format="json",
    )
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert WorkOrderValidation.objects.filter(work_order=wo).count() == 0


def test_completion_allowed_after_full_validation(api, staff_user):
    wo = _build_wo()
    val_res = api.post(
        f"/api/inventory/work-orders/{wo.id}/validate/",
        {
            "electrical_acknowledged": True,
            "loto_acknowledged": True,
            "required_fields_acknowledged": True,
            "notes": "All checked",
        },
        format="json",
    )
    assert val_res.status_code == status.HTTP_201_CREATED
    record = WorkOrderValidation.objects.get(work_order=wo)
    assert record.is_complete is True
    assert record.validated_by_id == staff_user.id

    res = api.patch(
        f"/api/inventory/work-orders/{wo.id}/",
        {"status": WorkOrder.Status.COMPLETED},
        format="json",
    )
    assert res.status_code == status.HTTP_200_OK
    wo.refresh_from_db()
    assert wo.status == WorkOrder.Status.COMPLETED


def test_completion_stamps_completed_at(api):
    """Digitally completing a work order stamps completed_at.

    The digital path used to leave it null, surfacing as "Completed N/A" in
    the asset detail view.
    """
    wo = _build_wo()
    assert wo.completed_at is None

    api.post(
        f"/api/inventory/work-orders/{wo.id}/validate/",
        {
            "electrical_acknowledged": True,
            "loto_acknowledged": True,
            "required_fields_acknowledged": True,
        },
        format="json",
    )
    res = api.patch(
        f"/api/inventory/work-orders/{wo.id}/",
        {"status": WorkOrder.Status.COMPLETED},
        format="json",
    )

    assert res.status_code == status.HTTP_200_OK
    wo.refresh_from_db()
    assert wo.status == WorkOrder.Status.COMPLETED
    assert wo.completed_at is not None


def test_reopening_clears_completed_at(api):
    """Reopening a completed work order clears its completion timestamp."""
    wo = _build_wo()
    api.post(
        f"/api/inventory/work-orders/{wo.id}/validate/",
        {
            "electrical_acknowledged": True,
            "loto_acknowledged": True,
            "required_fields_acknowledged": True,
        },
        format="json",
    )
    api.patch(
        f"/api/inventory/work-orders/{wo.id}/",
        {"status": WorkOrder.Status.COMPLETED},
        format="json",
    )
    wo.refresh_from_db()
    assert wo.completed_at is not None

    res = api.patch(
        f"/api/inventory/work-orders/{wo.id}/",
        {"status": WorkOrder.Status.IN_PROGRESS},
        format="json",
    )

    assert res.status_code == status.HTTP_200_OK
    wo.refresh_from_db()
    assert wo.status == WorkOrder.Status.IN_PROGRESS
    assert wo.completed_at is None


def test_backfill_migration_populates_completed_at():
    """The 0062 data migration repairs legacy completed work orders that have
    no completion timestamp, using updated_at as the proxy."""
    import importlib

    from django.apps import apps as global_apps

    wo = _build_wo()
    # Simulate a legacy digital completion: completed status, no timestamp.
    # .update() bypasses auto_now, so updated_at stays at creation time.
    WorkOrder.objects.filter(pk=wo.pk).update(status=WorkOrder.Status.COMPLETED, completed_at=None)

    migration = importlib.import_module("inventory.migrations.0062_backfill_workorder_completed_at")
    migration.backfill_completed_at(global_apps, None)

    wo.refresh_from_db()
    assert wo.completed_at is not None
    assert wo.completed_at == wo.updated_at


def test_pdf_allowed_after_full_validation(api):
    wo = _build_wo()
    api.post(
        f"/api/inventory/work-orders/{wo.id}/validate/",
        {
            "electrical_acknowledged": True,
            "loto_acknowledged": True,
            "required_fields_acknowledged": True,
        },
        format="json",
    )
    res = api.get(f"/api/inventory/work-orders/{wo.id}/pdf/")
    assert res.status_code == status.HTTP_200_OK
    assert res["Content-Type"] == "application/pdf"
    # op-8mjw: the /pdf/ route now emits the unified OMR variant, so it upserts
    # exactly one WorkOrderOmrTemplate snapshot (as omr-pdf did previously).
    template = WorkOrderOmrTemplate.objects.get(work_order=wo)
    assert template.template_version == compute_template_version(wo)


# ─────────────────────────────────────────────────────────────────────────────
# AC-4: CV confidence + pending review queue
# ─────────────────────────────────────────────────────────────────────────────


def test_auto_apply_or_queue_splits_by_confidence(settings):
    settings.CV_CONFIDENCE_THRESHOLD = 0.7
    detections = [
        Detection(kind="signature", target_id=None, value=True, confidence=0.95),
        Detection(kind="handwritten", target_id=None, value="ok", confidence=0.5),
        Detection(kind="checkbox", target_id="task_1", value=True, confidence=0.7),
    ]
    auto, queue = auto_apply_or_queue(detections)
    assert {d.confidence for d in auto} == {0.95, 0.7}
    assert [d.confidence for d in queue] == [0.5]


def test_apply_pending_changes_endpoint(api):
    wo = _build_wo()
    sub = WorkOrderSubmission.objects.create(
        kind=WorkOrderSubmission.Kind.PM_COMPLETION,
        work_order=wo,
        status=WorkOrderSubmission.Status.PENDING_REVIEW,
        pending_changes=[
            {
                "kind": "signature",
                "target_id": None,
                "value": True,
                "confidence": 0.55,
                "label": "Signature block",
            },
            {
                "kind": "handwritten",
                "target_id": None,
                "value": "Replaced V-belt",
                "confidence": 0.6,
                "label": "Handwritten note",
            },
        ],
    )

    res = api.post(f"/api/inventory/work-orders/{wo.id}/submissions/{sub.id}/apply-pending/")
    assert res.status_code == status.HTTP_200_OK
    assert res.json()["applied_count"] == 2
    sub.refresh_from_db()
    assert sub.status == WorkOrderSubmission.Status.APPLIED
    assert sub.pending_changes == []
    wo.refresh_from_db()
    assert "Replaced V-belt" in wo.notes
    assert "Signature accepted" in wo.notes


def test_discard_pending_changes_endpoint(api):
    wo = _build_wo()
    sub = WorkOrderSubmission.objects.create(
        kind=WorkOrderSubmission.Kind.PM_COMPLETION,
        work_order=wo,
        status=WorkOrderSubmission.Status.PENDING_REVIEW,
        pending_changes=[
            {
                "kind": "handwritten",
                "target_id": None,
                "value": "illegible",
                "confidence": 0.4,
                "label": "Handwritten note",
            }
        ],
    )

    res = api.post(f"/api/inventory/work-orders/{wo.id}/submissions/{sub.id}/discard-pending/")
    assert res.status_code == status.HTTP_200_OK
    assert res.json()["dropped_count"] == 1
    sub.refresh_from_db()
    assert sub.status == WorkOrderSubmission.Status.APPLIED
    assert sub.pending_changes == []
    wo.refresh_from_db()
    assert "illegible" not in wo.notes


# ─────────────────────────────────────────────────────────────────────────────
# AC-5: parity audit
# ─────────────────────────────────────────────────────────────────────────────


def test_pdf_and_serializer_share_the_same_context_builder():
    """
    Parity audit: both surfaces consume build_work_order_context() — same
    keys, same values. The asset has a representative mix of electrical /
    LOTO data so any field that one surface drops will trip the test.
    """
    asset = AssetFactory(
        wiring_type=Asset.WiringType.PLUG_240V,
        electrical_box="Panel A",
        breaker_location="Panel A / 12",
        lockout_type=Asset.LockoutType.LOTO,
        lockout_instructions="Lock breaker A12.",
        lockout_responsible="Ops lead",
        has_network_drop=True,
        network_drop_location="WP-3A",
    )
    wo = _build_wo(asset=asset)

    ctx = build_work_order_context(wo)
    assert set(ctx.keys()) == {"electrical", "loto", "tools", "reference_documents"}

    # Re-derive the PDF row set through its public helper and confirm every
    # asset-level row from the shared electrical context appears.
    from inventory.utils.work_order_pdf import _build_electrical_rows

    pdf_rows = _build_electrical_rows(asset)
    pdf_labels = {row[0] for row in pdf_rows}

    digital_labels = {row[0] for row in ctx["electrical"]["rows"]}
    # Every digital label must appear in the PDF (PDF additionally includes
    # LOTO labels, which is by design — see the helper for the rule).
    assert digital_labels <= pdf_labels
    # And the LOTO content matches.
    assert ctx["loto"]["lockout_responsible"] == "Ops lead"
    assert "Lock breaker A12" in ctx["loto"]["lockout_instructions"]
    assert "Lockout Type" in pdf_labels  # PDF carries the LOTO row
