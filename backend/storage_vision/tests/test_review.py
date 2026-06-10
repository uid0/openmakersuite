"""Storage Vision slice-5 review-queue tests.

Covers AC-20 (filterable review list), AC-21 (approve creates a
vision_supply_check reconciliation linked to the observation), AC-22
(reconcile at-or-below minimum_stock auto-creates a ReorderRequest
through the existing stock-reconciliation behavior), AC-23 (approve
is idempotent — a second call returns 409 and does not double-write),
AC-24 (reject records the review action without mutating inventory),
AC-25 (bulk-approve handles partial results with per-row reasons).
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

import pytest
from rest_framework.test import APIClient

from inventory.models import Category, InventoryItem, Location, StockReconciliation
from reorder_queue.models import ReorderRequest
from storage_vision.models import (
    VisionArea,
    VisionCapture,
    VisionObservation,
    VisionReviewAction,
    VisionSlot,
)

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def staff_user():
    return get_user_model().objects.create_user(username="warden", password="x", is_staff=True)


@pytest.fixture
def staff_client(staff_user):
    api = APIClient()
    api.force_authenticate(user=staff_user)
    return api


@pytest.fixture
def logistics_client():
    user = get_user_model().objects.create_user(username="logi", password="x")
    group, _ = Group.objects.get_or_create(name="Logistics")
    user.groups.add(group)
    api = APIClient()
    api.force_authenticate(user=user)
    return api


@pytest.fixture
def member_client():
    user = get_user_model().objects.create_user(username="rando", password="x")
    api = APIClient()
    api.force_authenticate(user=user)
    return api


@pytest.fixture
def anon_client():
    return APIClient()


@pytest.fixture
def location():
    return Location.objects.create(name="Shop floor")


@pytest.fixture
def area(location):
    return VisionArea.objects.create(name="Bay 1", location=location)


@pytest.fixture
def category():
    return Category.objects.create(name="Fasteners")


@pytest.fixture
def item(category, location):
    return InventoryItem.objects.create(
        name="M3 hex bolt",
        description="",
        category=category,
        location=location,
        current_stock=8,
        minimum_stock=5,
        reorder_quantity=20,
    )


@pytest.fixture
def slot(area, item):
    return VisionSlot.objects.create(
        area=area,
        item=item,
        marker_code="VIS-BAY1-M3HEX",
        empty_low_confidence_threshold=Decimal("0.50"),
    )


def _capture(area):
    return VisionCapture.objects.create(
        area=area,
        source=VisionCapture.SOURCE_PHONE,
        status=VisionCapture.STATUS_PROCESSED,
    )


def _make_pending_obs(
    slot,
    capture,
    *,
    classification=VisionObservation.CLASS_EMPTY,
    suggested=VisionObservation.ACTION_RECONCILE_EMPTY,
    confidence="0.800",
):
    return VisionObservation.objects.create(
        capture=capture,
        slot=slot,
        classification=classification,
        confidence=Decimal(confidence),
        suggested_action=suggested,
        status=VisionObservation.STATUS_PENDING,
        model_version="heuristic-v1",
    )


@pytest.fixture
def feature_on(settings):
    settings.STORAGE_VISION_ENABLED = True


@pytest.fixture
def feature_off(settings):
    settings.STORAGE_VISION_ENABLED = False


# ---------------------------------------------------------------------------
# AC-20 — review list filtering + permissions
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("feature_on")
class TestObservationList:
    def test_member_cannot_list(self, member_client):
        resp = member_client.get("/api/storage-vision/observations/")
        assert resp.status_code == 403

    def test_anonymous_cannot_list(self, anon_client):
        resp = anon_client.get("/api/storage-vision/observations/")
        assert resp.status_code in (401, 403)

    def test_staff_can_list_with_filter(self, staff_client, area, slot):
        cap = _capture(area)
        pending = _make_pending_obs(slot, cap)
        # Resolve it so the ?status=pending filter has to do work.
        resolved = _make_pending_obs(slot, cap, suggested=VisionObservation.ACTION_REVIEW_ONLY)
        resolved.status = VisionObservation.STATUS_REJECTED
        resolved.save(update_fields=["status"])

        resp = staff_client.get("/api/storage-vision/observations/?status=pending")
        assert resp.status_code == 200
        body = resp.json()
        rows = body.get("results", body)  # pagination optional
        ids = {r["id"] for r in rows}
        assert pending.id in ids
        assert resolved.id not in ids

    def test_list_includes_denormalized_metadata(self, staff_client, area, slot):
        cap = _capture(area)
        obs = _make_pending_obs(slot, cap)
        resp = staff_client.get("/api/storage-vision/observations/")
        rows = resp.json().get("results", resp.json())
        row = next(r for r in rows if r["id"] == obs.id)
        assert row["area_name"] == area.name
        assert row["slot_marker_code"] == slot.marker_code
        assert row["item_name"] == slot.item.name
        assert row["classification"] == VisionObservation.CLASS_EMPTY
        assert row["suggested_action"] == VisionObservation.ACTION_RECONCILE_EMPTY
        assert "age_seconds" in row
        assert row["duplicate_count"] == 0


# ---------------------------------------------------------------------------
# AC-21 + AC-22 — approve creates reconciliation + reorder
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("feature_on")
class TestApprove:
    def test_approve_reconcile_empty_creates_reconciliation(
        self, staff_client, staff_user, area, slot, item
    ):
        cap = _capture(area)
        obs = _make_pending_obs(slot, cap)

        resp = staff_client.post(
            f"/api/storage-vision/observations/{obs.id}/approve/", {}, format="json"
        )
        assert resp.status_code == 200, resp.content
        obs.refresh_from_db()
        assert obs.status == VisionObservation.STATUS_APPROVED

        # AC-21: reconciliation with reason=vision_supply_check, item set to 0.
        recon = StockReconciliation.objects.get(item=item)
        assert recon.actual_count == 0
        assert recon.reason == StockReconciliation.REASON_VISION_SUPPLY_CHECK
        assert f"VisionObservation #{obs.id}" in recon.notes
        assert recon.reconciled_by == staff_user

        # AC-22: item dropped to 0 ≤ minimum_stock=5 → reorder auto-created.
        reorder = ReorderRequest.objects.get(item=item)
        assert reorder.quantity == item.reorder_quantity
        assert recon.triggered_reorder_id == reorder.id

        # Review action audit row.
        review = VisionReviewAction.objects.get(observation=obs)
        assert review.action == VisionReviewAction.ACTION_APPROVE
        assert review.reviewer == staff_user
        assert review.stock_reconciliation_id == recon.id

        body = resp.json()
        assert body["reconciliation_id"] == recon.id
        assert body["reorder_created"] is True

    def test_review_only_approve_does_not_reconcile(self, staff_client, area, slot):
        cap = _capture(area)
        obs = _make_pending_obs(slot, cap, suggested=VisionObservation.ACTION_REVIEW_ONLY)

        resp = staff_client.post(
            f"/api/storage-vision/observations/{obs.id}/approve/", {}, format="json"
        )
        assert resp.status_code == 200, resp.content
        obs.refresh_from_db()
        assert obs.status == VisionObservation.STATUS_APPROVED
        assert not StockReconciliation.objects.exists()
        assert not ReorderRequest.objects.exists()
        assert VisionReviewAction.objects.filter(observation=obs).count() == 1

    def test_approve_carries_reason_text_into_notes(self, staff_client, area, slot):
        # Reviewer note should land in the StockReconciliation.notes
        # so the audit trail says WHY the approval went through.
        cap = _capture(area)
        obs = _make_pending_obs(slot, cap)

        resp = staff_client.post(
            f"/api/storage-vision/observations/{obs.id}/approve/",
            {"reason": "verified by hand at standup"},
            format="json",
        )
        assert resp.status_code == 200
        recon = StockReconciliation.objects.get()
        assert "verified by hand at standup" in recon.notes


# ---------------------------------------------------------------------------
# AC-23 — approve idempotence
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("feature_on")
class TestApproveIdempotent:
    def test_second_approve_returns_conflict(self, staff_client, area, slot, item):
        cap = _capture(area)
        obs = _make_pending_obs(slot, cap)

        first = staff_client.post(f"/api/storage-vision/observations/{obs.id}/approve/")
        assert first.status_code == 200

        second = staff_client.post(f"/api/storage-vision/observations/{obs.id}/approve/")
        assert second.status_code == 409
        assert second.json()["code"] == "already_resolved"

        # No second reconciliation, no second reorder, no second review row.
        assert StockReconciliation.objects.filter(item=item).count() == 1
        assert ReorderRequest.objects.filter(item=item).count() == 1
        assert VisionReviewAction.objects.filter(observation=obs).count() == 1

    def test_approve_after_reject_returns_conflict(self, staff_client, area, slot):
        cap = _capture(area)
        obs = _make_pending_obs(slot, cap)
        staff_client.post(
            f"/api/storage-vision/observations/{obs.id}/reject/",
            {"reason": "marker fell off"},
            format="json",
        )
        resp = staff_client.post(f"/api/storage-vision/observations/{obs.id}/approve/")
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# AC-24 — reject paths
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("feature_on")
class TestReject:
    def test_reject_records_action_no_inventory_mutation(
        self, staff_client, staff_user, area, slot, item
    ):
        cap = _capture(area)
        obs = _make_pending_obs(slot, cap)
        before_stock = item.current_stock

        resp = staff_client.post(
            f"/api/storage-vision/observations/{obs.id}/reject/",
            {"reason": "lighting confused the heuristic"},
            format="json",
        )
        assert resp.status_code == 200, resp.content
        obs.refresh_from_db()
        item.refresh_from_db()

        assert obs.status == VisionObservation.STATUS_REJECTED
        assert item.current_stock == before_stock
        assert not StockReconciliation.objects.exists()
        assert not ReorderRequest.objects.exists()

        review = VisionReviewAction.objects.get(observation=obs)
        assert review.action == VisionReviewAction.ACTION_REJECT
        assert review.reviewer == staff_user
        assert review.reason == "lighting confused the heuristic"
        assert review.stock_reconciliation is None

    def test_reject_requires_reason(self, staff_client, area, slot):
        cap = _capture(area)
        obs = _make_pending_obs(slot, cap)
        resp = staff_client.post(
            f"/api/storage-vision/observations/{obs.id}/reject/", {}, format="json"
        )
        assert resp.status_code == 400
        obs.refresh_from_db()
        assert obs.status == VisionObservation.STATUS_PENDING


# ---------------------------------------------------------------------------
# AC-25 — bulk approve partial results
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("feature_on")
class TestBulkApprove:
    def test_bulk_approve_processes_valid_skips_invalid(self, staff_client, area, item):
        # Three observations spanning the partial-result modes:
        #   1) pending reconcile_empty — should approve.
        #   2) already-rejected — should skip with reason already_rejected.
        #   3) missing id — should skip with reason not_found.
        cap = _capture(area)

        # Need a second slot since the unique constraint blocks two
        # pending rows on the same (slot, action).
        slot_a = VisionSlot.objects.create(
            area=area,
            item=item,
            marker_code="VIS-A",
            empty_low_confidence_threshold=Decimal("0.50"),
        )
        slot_b = VisionSlot.objects.create(
            area=area,
            item=item,
            marker_code="VIS-B",
            empty_low_confidence_threshold=Decimal("0.50"),
        )
        obs_good = _make_pending_obs(slot_a, cap)
        obs_rejected = _make_pending_obs(slot_b, cap)
        obs_rejected.status = VisionObservation.STATUS_REJECTED
        obs_rejected.save(update_fields=["status"])

        ghost_id = obs_good.id + 99999

        resp = staff_client.post(
            "/api/storage-vision/observations/bulk-approve/",
            {
                "observation_ids": [obs_good.id, obs_rejected.id, ghost_id],
                "reason": "approved together at sprint review",
            },
            format="json",
        )
        assert resp.status_code == 200, resp.content
        body = resp.json()

        assert body["counts"]["requested"] == 3
        assert body["counts"]["approved"] == 1
        assert body["counts"]["skipped"] == 2

        approved_ids = {a["id"] for a in body["approved"]}
        assert approved_ids == {obs_good.id}

        skipped = {s["id"]: s["reason"] for s in body["skipped"]}
        assert skipped[obs_rejected.id] == "already_rejected"
        assert skipped[ghost_id] == "not_found"

        # Single reconciliation / reorder, only for the good one.
        assert StockReconciliation.objects.count() == 1
        obs_good.refresh_from_db()
        assert obs_good.status == VisionObservation.STATUS_APPROVED

    def test_bulk_approve_dedups_duplicate_ids(self, staff_client, area, slot):
        cap = _capture(area)
        obs = _make_pending_obs(slot, cap)
        resp = staff_client.post(
            "/api/storage-vision/observations/bulk-approve/",
            {"observation_ids": [obs.id, obs.id, obs.id]},
            format="json",
        )
        assert resp.status_code == 200
        body = resp.json()
        # The ID was deduped — only one approved, zero skipped.
        assert body["counts"]["requested"] == 1
        assert body["counts"]["approved"] == 1
        assert body["counts"]["skipped"] == 0


# ---------------------------------------------------------------------------
# AC-2 — feature flag gates writes
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("feature_off")
class TestFeatureFlagBlocksWrites:
    def test_approve_blocked_when_disabled(self, staff_client, area, slot):
        cap = _capture(area)
        obs = _make_pending_obs(slot, cap)
        resp = staff_client.post(f"/api/storage-vision/observations/{obs.id}/approve/")
        assert resp.status_code == 503
        assert resp.json()["code"] == "feature_disabled"
        obs.refresh_from_db()
        assert obs.status == VisionObservation.STATUS_PENDING

    def test_reject_blocked_when_disabled(self, staff_client, area, slot):
        cap = _capture(area)
        obs = _make_pending_obs(slot, cap)
        resp = staff_client.post(
            f"/api/storage-vision/observations/{obs.id}/reject/",
            {"reason": "x"},
            format="json",
        )
        assert resp.status_code == 503

    def test_bulk_approve_blocked_when_disabled(self, staff_client, area, slot):
        cap = _capture(area)
        obs = _make_pending_obs(slot, cap)
        resp = staff_client.post(
            "/api/storage-vision/observations/bulk-approve/",
            {"observation_ids": [obs.id]},
            format="json",
        )
        assert resp.status_code == 503

    def test_list_still_readable_when_disabled(self, staff_client):
        # Reads stay available so an operator who flipped the flag
        # off can still audit historical findings.
        resp = staff_client.get("/api/storage-vision/observations/")
        assert resp.status_code == 200
