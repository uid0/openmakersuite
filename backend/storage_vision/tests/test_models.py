"""Storage Vision phase-1 model tests.

Phase 1 ships the data model only. These tests pin the contract the
follow-up views / Celery / UI work depends on:

  - VisionArea / VisionSlot / VisionCamera / VisionCapture / VisionObservation
    rows save and round-trip with their FKs.
  - The active-marker-code unique constraint (AC-5) catches duplicate
    active markers but allows an inactive marker to be reused.
  - The pending-observation uniqueness constraint (AC-19) catches a
    second pending row for the same (slot, suggested_action).
  - The camera-token issue / lookup helpers do what they say —
    fingerprint is publicly safe, the hashed bearer round-trips
    via find_by_token.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import IntegrityError, transaction

import pytest

from inventory.models import Category, InventoryItem, Location
from storage_vision.models import (
    VisionArea,
    VisionCamera,
    VisionCapture,
    VisionObservation,
    VisionSlot,
    _fingerprint_token,
    _hash_token,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def location():
    return Location.objects.create(name="Shop floor")


@pytest.fixture
def item():
    cat = Category.objects.create(name="Fasteners")
    loc = Location.objects.create(name="Bin shelf A")
    return InventoryItem.objects.create(
        name="M3 hex bolt",
        description="",
        category=cat,
        location=loc,
        current_stock=0,
        minimum_stock=5,
        reorder_quantity=100,
    )


@pytest.fixture
def area(location):
    return VisionArea.objects.create(name="Shop floor monitor", location=location)


# ---------------------------------------------------------------------------
# VisionArea
# ---------------------------------------------------------------------------


class TestVisionArea:
    def test_save_and_str(self, area, location):
        assert area.pk is not None
        assert area.is_active is True
        assert location.name in str(area)


# ---------------------------------------------------------------------------
# VisionSlot — unique-active-marker constraint
# ---------------------------------------------------------------------------


class TestVisionSlotConstraints:
    def test_active_marker_codes_must_be_unique(self, area, item):
        VisionSlot.objects.create(area=area, item=item, marker_code="SHELF-A-01")
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                VisionSlot.objects.create(area=area, item=item, marker_code="SHELF-A-01")

    def test_inactive_marker_code_can_be_reused(self, area, item):
        # Operator retires a slot — marker label gets repurposed on a
        # different bin later. The retired slot should not block the
        # new one.
        VisionSlot.objects.create(area=area, item=item, marker_code="SHELF-A-02", is_active=False)
        VisionSlot.objects.create(area=area, item=item, marker_code="SHELF-A-02")

    def test_default_threshold_is_half(self, area, item):
        slot = VisionSlot.objects.create(area=area, item=item, marker_code="X-1")
        assert slot.empty_low_confidence_threshold == Decimal("0.50")


# ---------------------------------------------------------------------------
# VisionCamera — token plumbing
# ---------------------------------------------------------------------------


class TestVisionCameraTokens:
    def test_issue_token_sets_hash_and_fingerprint(self, area):
        cam = VisionCamera(name="Shelf cam 1", area=area)
        raw = cam.issue_token()
        cam.save()

        # Raw token is 32 url-safe bytes — at least 40 chars.
        assert len(raw) >= 40
        # Hash and fingerprint are derived deterministically.
        assert cam.token_hash == _hash_token(raw)
        assert cam.token_fingerprint == _fingerprint_token(raw)
        # Fingerprint is 16 hex chars — safe for public display.
        assert len(cam.token_fingerprint) == 16

    def test_find_by_token_round_trip(self, area):
        cam = VisionCamera(name="Shelf cam", area=area)
        raw = cam.issue_token()
        cam.save()

        found = VisionCamera.find_by_token(raw)
        assert found is not None
        assert found.pk == cam.pk

    def test_find_by_token_misses_on_wrong_raw(self, area):
        cam = VisionCamera(name="cam", area=area)
        cam.issue_token()
        cam.save()

        assert VisionCamera.find_by_token("totally-wrong-token") is None

    def test_fingerprint_is_not_a_credential(self, area):
        # Posting the fingerprint as if it were the raw bearer must not
        # authenticate. The fingerprint is meant for display only.
        cam = VisionCamera(name="cam", area=area)
        cam.issue_token()
        cam.save()

        assert VisionCamera.find_by_token(cam.token_fingerprint) is None

    def test_inactive_camera_never_resolves(self, area):
        cam = VisionCamera(name="cam", area=area)
        raw = cam.issue_token()
        cam.save()

        cam.is_active = False
        cam.save(update_fields=["is_active"])

        assert VisionCamera.find_by_token(raw) is None


# ---------------------------------------------------------------------------
# VisionCapture — basic round-trip + state tracking
# ---------------------------------------------------------------------------


class TestVisionCapture:
    def test_capture_round_trip(self, area):
        cap = VisionCapture.objects.create(
            area=area,
            source=VisionCapture.SOURCE_PHONE,
            original_image="storage_vision/originals/test.jpg",
        )
        assert cap.status == VisionCapture.STATUS_QUEUED
        assert cap.markers_detected == []
        assert cap.failure_reason == ""

    def test_camera_link_is_nullable_for_phone_source(self, area):
        # AC-9: phone uploads don't require a camera FK.
        cap = VisionCapture.objects.create(
            area=area,
            source=VisionCapture.SOURCE_PHONE,
            original_image="storage_vision/originals/phone.jpg",
        )
        assert cap.camera is None


# ---------------------------------------------------------------------------
# VisionObservation — pending uniqueness (AC-19)
# ---------------------------------------------------------------------------


class TestVisionObservationConstraints:
    @pytest.fixture
    def capture(self, area):
        return VisionCapture.objects.create(
            area=area,
            source=VisionCapture.SOURCE_PHONE,
            original_image="storage_vision/originals/x.jpg",
        )

    @pytest.fixture
    def slot(self, area, item):
        return VisionSlot.objects.create(area=area, item=item, marker_code="M-19")

    def test_two_pending_obs_for_same_slot_action_rejected(self, capture, slot):
        VisionObservation.objects.create(
            capture=capture,
            slot=slot,
            classification=VisionObservation.CLASS_EMPTY,
            confidence=Decimal("0.900"),
            suggested_action=VisionObservation.ACTION_RECONCILE_EMPTY,
        )
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                VisionObservation.objects.create(
                    capture=capture,
                    slot=slot,
                    classification=VisionObservation.CLASS_EMPTY,
                    confidence=Decimal("0.910"),
                    suggested_action=VisionObservation.ACTION_RECONCILE_EMPTY,
                )

    def test_approved_obs_unblocks_a_new_pending_one(self, capture, slot):
        first = VisionObservation.objects.create(
            capture=capture,
            slot=slot,
            classification=VisionObservation.CLASS_EMPTY,
            confidence=Decimal("0.900"),
            suggested_action=VisionObservation.ACTION_RECONCILE_EMPTY,
        )
        first.status = VisionObservation.STATUS_APPROVED
        first.save()

        # Second pending row lands cleanly now that the first is no
        # longer pending — the next inference cycle can fire even
        # before the operator restocks the bin.
        VisionObservation.objects.create(
            capture=capture,
            slot=slot,
            classification=VisionObservation.CLASS_EMPTY,
            confidence=Decimal("0.920"),
            suggested_action=VisionObservation.ACTION_RECONCILE_EMPTY,
        )

    def test_different_actions_can_both_be_pending(self, capture, slot):
        # ACTION_REVIEW_ONLY (low-confidence) and ACTION_RECONCILE_EMPTY
        # are separate review queues; the constraint only de-dups within
        # the same action.
        VisionObservation.objects.create(
            capture=capture,
            slot=slot,
            classification=VisionObservation.CLASS_EMPTY,
            confidence=Decimal("0.900"),
            suggested_action=VisionObservation.ACTION_RECONCILE_EMPTY,
        )
        VisionObservation.objects.create(
            capture=capture,
            slot=slot,
            classification=VisionObservation.CLASS_LOW,
            confidence=Decimal("0.300"),
            suggested_action=VisionObservation.ACTION_REVIEW_ONLY,
        )
