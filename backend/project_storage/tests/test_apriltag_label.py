"""AprilTag (WHERE fiducial) lifecycle + label-embed tests for project storage (op-e9w).

* Lifecycle: ``start`` allocates a tag, ``mark-removed`` releases it.
* Renderer: the rendered receipt embeds a 36h11 marker that decodes back to
  the allocated id; legacy (untagged) stints render QR-only and unchanged.
"""

from __future__ import annotations

from io import BytesIO

from django.contrib.auth import get_user_model

import pytest
from PIL import Image
from rest_framework.test import APIClient

from fiducials.models import AprilTagAssignment
from fiducials.services.allocator import allocate_tag, get_active_tag_id
from fiducials.services.apriltag_render import decode_apriltag_ids
from project_storage.services.label_service import (
    BROTHER_SIZE_PX,
    EPSON_SIZE_PX,
    render_stint_label,
)
from project_storage.tests.factories import ProjectStorageStintFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def staff_client():
    user = get_user_model().objects.create_user(username="warden", password="x", is_staff=True)
    api = APIClient()
    api.force_authenticate(user=user)
    return api


# ---------------------------------------------------------------------------
# Lifecycle: allocate on start, release on mark-removed
# ---------------------------------------------------------------------------


def test_start_allocates_active_apriltag(client):
    resp = client.post(
        "/api/project-storage/stints/start/",
        {"username": "newbie", "first_name": "New", "last_name": "Bie"},
        format="json",
    )
    assert resp.status_code == 201, resp.content
    body = resp.json()
    # One active allocation now points at the new stint, and the API exposes it.
    from project_storage.models import ProjectStorageStint

    stint = ProjectStorageStint.objects.get(stint_id=body["stint_id"])
    assignment = AprilTagAssignment.objects.get(object_id=stint.pk, released_at__isnull=True)
    assert body["april_tag_id"] == assignment.tag_id == 0


def test_mark_removed_releases_apriltag(client, staff_client):
    start = client.post(
        "/api/project-storage/stints/start/",
        {"username": "leaver"},
        format="json",
    )
    stint_id = start.json()["stint_id"]
    from project_storage.models import ProjectStorageStint

    stint = ProjectStorageStint.objects.get(stint_id=stint_id)
    assert get_active_tag_id(stint) == 0

    resp = staff_client.post(f"/api/project-storage/stints/{stint_id}/mark-removed/", {})
    assert resp.status_code == 200, resp.content
    # The id is freed (no active allocation) but the row remains for audit.
    assert get_active_tag_id(stint) is None
    assert resp.json()["april_tag_id"] is None
    assert (
        AprilTagAssignment.objects.filter(object_id=stint.pk, released_at__isnull=False).count()
        == 1
    )


def test_released_id_recycles_to_next_stint(client, staff_client):
    first = client.post("/api/project-storage/stints/start/", {"username": "a"}, format="json")
    first_id = first.json()["stint_id"]
    assert first.json()["april_tag_id"] == 0
    staff_client.post(f"/api/project-storage/stints/{first_id}/mark-removed/", {})
    # Freed id 0 is reused by the next stint.
    second = client.post("/api/project-storage/stints/start/", {"username": "b"}, format="json")
    assert second.json()["april_tag_id"] == 0


# ---------------------------------------------------------------------------
# Renderer: the receipt embeds a decodable 36h11 marker
# ---------------------------------------------------------------------------


def _decode(png_bytes: bytes) -> list[int]:
    return decode_apriltag_ids(Image.open(BytesIO(png_bytes)))


@pytest.mark.parametrize("printer", ["brother_ql", "epson_tm"])
def test_receipt_embeds_decodable_apriltag(printer):
    stint = ProjectStorageStintFactory()
    assignment = allocate_tag(stint)
    png = render_stint_label(stint, printer=printer)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert _decode(png) == [assignment.tag_id]


def test_untagged_stint_renders_qr_only_and_unchanged_size():
    # A stint with no allocation (legacy / released) keeps the original
    # layout: QR only, base dimensions, no detectable AprilTag.
    stint = ProjectStorageStintFactory()
    for printer, base in (("brother_ql", BROTHER_SIZE_PX), ("epson_tm", EPSON_SIZE_PX)):
        png = render_stint_label(stint, printer=printer)
        img = Image.open(BytesIO(png))
        assert img.size == base
        assert decode_apriltag_ids(img) == []


def test_tagged_receipt_grows_along_media_length():
    stint = ProjectStorageStintFactory()
    allocate_tag(stint)
    tagged = Image.open(BytesIO(render_stint_label(stint, printer="brother_ql")))
    # Width (tape width) is fixed; the strip grows the length only.
    assert tagged.size[0] == BROTHER_SIZE_PX[0]
    assert tagged.size[1] > BROTHER_SIZE_PX[1]
