"""Storage Vision slice-6 retention tests.

Covers AC-26 (originals older than STORAGE_VISION_RETENTION_DAYS are
deleted; observations + evidence crops + audit metadata survive) and
the AC-27 smoke surface (the management command body).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command

import pytest
from PIL import Image

from inventory.models import Category, InventoryItem, Location
from storage_vision.models import (
    VisionArea,
    VisionCapture,
    VisionObservation,
    VisionReviewAction,
    VisionSlot,
)
from storage_vision.tasks import prune_original_captures

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


def _jpeg_bytes(size=(64, 64), color=(120, 120, 120)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG", quality=70)
    return buf.getvalue()


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
        reorder_quantity=10,
    )


@pytest.fixture
def slot(area, item):
    return VisionSlot.objects.create(
        area=area,
        item=item,
        marker_code="VIS-BAY1-M3HEX",
        empty_low_confidence_threshold=Decimal("0.50"),
    )


@pytest.fixture
def staff_user():
    return get_user_model().objects.create_user(username="warden", password="x", is_staff=True)


def _make_capture(area, *, received_at, status=VisionCapture.STATUS_PROCESSED):
    upload = SimpleUploadedFile("frame.jpg", _jpeg_bytes(), content_type="image/jpeg")
    cap = VisionCapture.objects.create(
        area=area,
        source=VisionCapture.SOURCE_PHONE,
        original_image=upload,
        status=status,
    )
    # Bypass auto_now-style fields by going through .update — we want
    # historical received_at values to test the cutoff.
    VisionCapture.objects.filter(pk=cap.pk).update(received_at=received_at)
    cap.refresh_from_db()
    return cap


# ---------------------------------------------------------------------------
# AC-26 — retention task body
# ---------------------------------------------------------------------------


class TestPruneOriginalCaptures:
    def test_old_capture_original_deleted_metadata_kept(
        self, area, slot, item, staff_user, settings
    ):
        settings.STORAGE_VISION_RETENTION_DAYS = 30

        from django.utils import timezone

        # Capture older than the cutoff with the full review chain
        # attached: observation, evidence crop, review action,
        # markers_detected JSON.
        old_capture = _make_capture(area, received_at=timezone.now() - dt.timedelta(days=45))
        old_capture.markers_detected = [
            {"marker_code": slot.marker_code, "bbox": [0, 0, 100, 100], "confidence": 1.0}
        ]
        old_capture.save(update_fields=["markers_detected"])

        obs = VisionObservation.objects.create(
            capture=old_capture,
            slot=slot,
            classification=VisionObservation.CLASS_EMPTY,
            confidence=Decimal("0.800"),
            suggested_action=VisionObservation.ACTION_RECONCILE_EMPTY,
            status=VisionObservation.STATUS_APPROVED,
            model_version="heuristic-v1",
        )
        obs.evidence_crop.save("crop.jpg", ContentFile(_jpeg_bytes()), save=False)
        obs.save()
        crop_name_before = obs.evidence_crop.name
        assert crop_name_before  # sanity — was saved

        VisionReviewAction.objects.create(
            observation=obs,
            reviewer=staff_user,
            action=VisionReviewAction.ACTION_APPROVE,
            reason="historical",
        )

        # Sanity: original_image is present before the prune.
        assert old_capture.original_image
        original_bytes_present = old_capture.original_image.storage.exists(
            old_capture.original_image.name
        )
        assert original_bytes_present

        result = prune_original_captures()
        assert result["status"] == "ok"
        assert result["deleted"] == 1
        assert result["errors"] == 0
        assert result["retention_days"] == 30
        assert result["freed_bytes"] >= 0

        old_capture.refresh_from_db()

        # AC-26 deletes:
        assert not old_capture.original_image  # FieldFile reference cleared

        # AC-26 preserves: the capture row, the observations, the
        # evidence crop, the review actions, and the markers_detected
        # bookkeeping.
        assert VisionCapture.objects.filter(pk=old_capture.pk).exists()
        assert old_capture.markers_detected[0]["marker_code"] == slot.marker_code

        obs.refresh_from_db()
        assert obs.status == VisionObservation.STATUS_APPROVED
        assert obs.evidence_crop.name == crop_name_before
        assert obs.evidence_crop.storage.exists(obs.evidence_crop.name)

        assert VisionReviewAction.objects.filter(observation=obs).count() == 1

    def test_recent_capture_untouched(self, area, settings):
        from django.utils import timezone

        settings.STORAGE_VISION_RETENTION_DAYS = 30
        recent = _make_capture(area, received_at=timezone.now() - dt.timedelta(days=5))
        name_before = recent.original_image.name

        result = prune_original_captures()
        assert result["deleted"] == 0

        recent.refresh_from_db()
        assert recent.original_image.name == name_before
        assert recent.original_image.storage.exists(recent.original_image.name)

    def test_disabled_when_days_zero(self, area, settings):
        from django.utils import timezone

        settings.STORAGE_VISION_RETENTION_DAYS = 0
        old = _make_capture(area, received_at=timezone.now() - dt.timedelta(days=365))
        result = prune_original_captures()
        assert result["status"] == "disabled"
        assert result["deleted"] == 0
        old.refresh_from_db()
        assert old.original_image  # untouched

    def test_idempotent_second_run(self, area, settings):
        from django.utils import timezone

        settings.STORAGE_VISION_RETENTION_DAYS = 30
        _make_capture(area, received_at=timezone.now() - dt.timedelta(days=45))
        first = prune_original_captures()
        assert first["deleted"] == 1
        # Second run: candidates returns 0 because the FieldFile
        # reference was cleared on the first pass.
        second = prune_original_captures()
        assert second["deleted"] == 0

    def test_mixed_window_only_old_pruned(self, area, settings):
        from django.utils import timezone

        settings.STORAGE_VISION_RETENTION_DAYS = 30
        old = _make_capture(area, received_at=timezone.now() - dt.timedelta(days=60))
        edge = _make_capture(area, received_at=timezone.now() - dt.timedelta(days=29))
        new = _make_capture(area, received_at=timezone.now() - dt.timedelta(hours=1))

        result = prune_original_captures()
        assert result["deleted"] == 1

        old.refresh_from_db()
        edge.refresh_from_db()
        new.refresh_from_db()
        assert not old.original_image
        assert edge.original_image
        assert new.original_image


# ---------------------------------------------------------------------------
# AC-27 — management command surface (smoke + dry-run)
# ---------------------------------------------------------------------------


class TestPruneManagementCommand:
    def test_dry_run_does_not_delete(self, area, settings, capsys):
        from django.utils import timezone

        settings.STORAGE_VISION_RETENTION_DAYS = 30
        old = _make_capture(area, received_at=timezone.now() - dt.timedelta(days=45))
        call_command("prune_storage_vision_captures", "--dry-run")
        old.refresh_from_db()
        # Dry-run leaves the file alone.
        assert old.original_image

        out = capsys.readouterr().out
        assert "Candidates: 1" in out
        assert "dry-run" in out.lower()

    def test_real_run_deletes_old_only(self, area, settings):
        from django.utils import timezone

        settings.STORAGE_VISION_RETENTION_DAYS = 30
        old = _make_capture(area, received_at=timezone.now() - dt.timedelta(days=45))
        recent = _make_capture(area, received_at=timezone.now() - dt.timedelta(days=3))
        call_command("prune_storage_vision_captures")
        old.refresh_from_db()
        recent.refresh_from_db()
        assert not old.original_image
        assert recent.original_image

    def test_days_flag_overrides_setting(self, area, settings):
        from django.utils import timezone

        # System setting says keep 30 days, but operator overrides to 7
        # for this one run — anything older than 7 days should drop.
        settings.STORAGE_VISION_RETENTION_DAYS = 30
        eight_days = _make_capture(area, received_at=timezone.now() - dt.timedelta(days=8))
        three_days = _make_capture(area, received_at=timezone.now() - dt.timedelta(days=3))
        call_command("prune_storage_vision_captures", "--days", "7")
        eight_days.refresh_from_db()
        three_days.refresh_from_db()
        assert not eight_days.original_image
        assert three_days.original_image

    def test_zero_days_short_circuits(self, area, settings, capsys):
        settings.STORAGE_VISION_RETENTION_DAYS = 0
        call_command("prune_storage_vision_captures")
        out = capsys.readouterr().out
        assert "disabled" in out.lower()
