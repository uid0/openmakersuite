"""AprilTag (WHERE fiducial) lifecycle + label-embed tests for maker boxes (op-e9w).

* Lifecycle: ``convert`` allocates a tag, ``DELETE`` (destroy) releases it.
* Renderer: the business card embeds a 36h11 marker that decodes back to the
  allocated id; the Avery sheet inherits it; untagged boxes render QR-only.
"""

from __future__ import annotations

from datetime import timedelta
from io import BytesIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.urls import reverse
from django.utils import timezone

import pytest
from PIL import Image
from rest_framework.test import APIClient

from fiducials.models import AprilTagAssignment
from fiducials.services.allocator import allocate_tag, get_active_tag_id
from fiducials.services.apriltag_render import decode_apriltag_ids
from maker_boxes.models import MakerBox
from maker_boxes.services.label_service import (
    DEFAULT_DPI,
    LABEL_HEIGHT_INCHES,
    LABEL_WIDTH_INCHES,
    render_box_label,
)
from maker_boxes.services.sheet_service import render_avery_5371_sheet

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_client():
    user = User.objects.create_user(username="staff", password="x", is_staff=True)
    Group.objects.get_or_create(name="Logistics")
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _pre_conversion_row(username="ada"):
    return MakerBox.objects.create(
        assigned_username=username,
        first_name="Ada",
        last_name="Lovelace",
        status=MakerBox.STATUS_PRE_CONVERSION,
        identity_source=MakerBox.IDENTITY_WHMCS,
        expires_at=timezone.now() + timedelta(days=30),
    )


def _decode(png_bytes: bytes) -> list[int]:
    return decode_apriltag_ids(Image.open(BytesIO(png_bytes)))


# ---------------------------------------------------------------------------
# Lifecycle: allocate on convert, release on destroy
# ---------------------------------------------------------------------------


def test_convert_allocates_active_apriltag(staff_client):
    row = _pre_conversion_row()
    resp = staff_client.post(reverse("maker-box-convert"), {"id": row.id}, format="json")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["bin_id"] == "MBX-001"
    row.refresh_from_db()
    assignment = AprilTagAssignment.objects.get(object_id=row.pk, released_at__isnull=True)
    assert body["april_tag_id"] == assignment.tag_id == 0


def test_two_converts_get_distinct_ids_from_global_pool(staff_client):
    a = _pre_conversion_row("ada")
    b = _pre_conversion_row("bob")
    id_a = staff_client.post(reverse("maker-box-convert"), {"id": a.id}, format="json").json()
    id_b = staff_client.post(reverse("maker-box-convert"), {"id": b.id}, format="json").json()
    assert {id_a["april_tag_id"], id_b["april_tag_id"]} == {0, 1}


def test_destroy_releases_apriltag(staff_client):
    box = MakerBox.objects.create(bin_id="MBX-007", assigned_username="ada")
    allocate_tag(box)
    assert get_active_tag_id(box) == 0

    resp = staff_client.delete(reverse("maker-box-detail", args=[box.pk]))
    assert resp.status_code == 204, resp.content
    # Row is gone, but its allocation was released (not orphaned-active) so
    # the id recycles instead of leaking.
    assert (
        AprilTagAssignment.objects.filter(object_id=box.pk, released_at__isnull=True).count() == 0
    )
    assert (
        AprilTagAssignment.objects.filter(object_id=box.pk, released_at__isnull=False).count() == 1
    )


def test_tag_persists_across_reassignment(staff_client):
    # The tag travels with the physical bin: reassigning the row to a new
    # owner (a non-destroy update) keeps the same active allocation.
    box = MakerBox.objects.create(bin_id="MBX-009", assigned_username="ada")
    tag_id = allocate_tag(box).tag_id
    staff_client.patch(
        reverse("maker-box-detail", args=[box.pk]),
        {"assigned_username": "bob"},
        format="json",
    )
    box.refresh_from_db()
    assert box.assigned_username == "bob"
    assert get_active_tag_id(box) == tag_id


# ---------------------------------------------------------------------------
# Renderer: the card / sheet embed a decodable 36h11 marker
# ---------------------------------------------------------------------------


def test_card_embeds_decodable_apriltag():
    box = MakerBox.objects.create(bin_id="MBX-001", assigned_username="ada", first_name="Ada")
    assignment = allocate_tag(box)
    png = render_box_label(box)
    img = Image.open(BytesIO(png))
    # Card dimensions are unchanged (tag stacks inside the left column).
    assert img.size == (
        int(round(LABEL_WIDTH_INCHES * DEFAULT_DPI)),
        int(round(LABEL_HEIGHT_INCHES * DEFAULT_DPI)),
    )
    assert decode_apriltag_ids(img) == [assignment.tag_id]


def test_untagged_box_card_is_qr_only():
    box = MakerBox.objects.create(bin_id="MBX-002", assigned_username="bob")
    png = render_box_label(box)
    assert _decode(png) == []


def test_manual_label_has_no_apriltag():
    # manual_label passes maker_box=None -> no subject -> QR-only, no crash.
    png = render_box_label(None, username_override="walkin", first_name_override="Walk")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert _decode(png) == []


def test_avery_sheet_inherits_apriltag():
    box = MakerBox.objects.create(bin_id="MBX-003", assigned_username="ada", first_name="Ada")
    assignment = allocate_tag(box)
    png = render_avery_5371_sheet([box])
    assert assignment.tag_id in _decode(png)
