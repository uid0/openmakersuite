"""Storage Vision slice-2 API tests.

Cover what slice 2 ships:
  - Area / Slot / Camera CRUD respects the staff-or-Logistics gate
    (AC-3, AC-4, AC-5)
  - Camera bearer is exposed exactly once on create and on rotate-token
    (AC-7) — list/retrieve never include it
  - Slot marker label endpoint returns a PNG (AC-6)
  - Feature flag disables write paths but leaves reads intact (AC-2)
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

import pytest
from rest_framework.test import APIClient

from inventory.models import Category, InventoryItem, Location
from storage_vision.models import VisionArea, VisionCamera, VisionSlot

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def staff_client():
    User = get_user_model()
    user = User.objects.create_user(username="warden", password="x", is_staff=True)
    api = APIClient()
    api.force_authenticate(user=user)
    return api


@pytest.fixture
def logistics_client():
    User = get_user_model()
    user = User.objects.create_user(username="logi", password="x")
    group, _ = Group.objects.get_or_create(name="Logistics")
    user.groups.add(group)
    api = APIClient()
    api.force_authenticate(user=user)
    return api


@pytest.fixture
def member_client():
    User = get_user_model()
    user = User.objects.create_user(username="rando", password="x")
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


@pytest.fixture
def feature_on(settings):
    """Enable STORAGE_VISION_ENABLED for the duration of one test."""
    settings.STORAGE_VISION_ENABLED = True


@pytest.fixture
def feature_off(settings):
    settings.STORAGE_VISION_ENABLED = False


# ---------------------------------------------------------------------------
# VisionArea CRUD
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("feature_on")
class TestVisionAreaCRUD:
    def test_staff_can_create(self, staff_client, location):
        resp = staff_client.post(
            "/api/storage-vision/areas/",
            {"name": "Bench top", "location": location.id},
            format="json",
        )
        assert resp.status_code == 201, resp.content
        assert resp.json()["name"] == "Bench top"
        assert resp.json()["is_active"] is True

    def test_logistics_can_create(self, logistics_client, location):
        resp = logistics_client.post(
            "/api/storage-vision/areas/",
            {"name": "Cabinet 7", "location": location.id},
            format="json",
        )
        assert resp.status_code == 201, resp.content

    def test_plain_member_cannot_create(self, member_client, location):
        resp = member_client.post(
            "/api/storage-vision/areas/",
            {"name": "Bench top", "location": location.id},
            format="json",
        )
        assert resp.status_code == 403

    def test_anonymous_cannot_list(self, anon_client):
        resp = anon_client.get("/api/storage-vision/areas/")
        assert resp.status_code in (401, 403)

    def test_member_can_list(self, member_client, area):
        # Reads stay available to any authenticated user — the setup
        # tables don't include the bearer, just metadata.
        resp = member_client.get("/api/storage-vision/areas/")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# VisionSlot CRUD + marker label endpoint
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("feature_on")
class TestVisionSlotCRUD:
    def test_staff_can_create_slot(self, staff_client, area, item):
        resp = staff_client.post(
            "/api/storage-vision/slots/",
            {
                "area": area.id,
                "item": item.id,
                "marker_code": "SV-001",
            },
            format="json",
        )
        assert resp.status_code == 201, resp.content
        assert resp.json()["marker_code"] == "SV-001"

    def test_duplicate_active_marker_rejected(self, staff_client, area, item):
        VisionSlot.objects.create(area=area, item=item, marker_code="DUP-1")
        resp = staff_client.post(
            "/api/storage-vision/slots/",
            {"area": area.id, "item": item.id, "marker_code": "DUP-1"},
            format="json",
        )
        # Standard DRF error envelope.
        assert resp.status_code in (400, 409, 500), resp.content
        # Slot table should still hold just the original row.
        assert VisionSlot.objects.filter(marker_code="DUP-1").count() == 1

    def test_member_cannot_create_slot(self, member_client, area, item):
        resp = member_client.post(
            "/api/storage-vision/slots/",
            {"area": area.id, "item": item.id, "marker_code": "M-1"},
            format="json",
        )
        assert resp.status_code == 403

    def test_filter_by_area(self, member_client, area, item, location):
        other = VisionArea.objects.create(name="Other", location=location)
        VisionSlot.objects.create(area=area, item=item, marker_code="A-1")
        VisionSlot.objects.create(area=other, item=item, marker_code="B-1")

        resp = member_client.get(f"/api/storage-vision/slots/?area={area.id}")
        assert resp.status_code == 200
        codes = [r["marker_code"] for r in resp.json().get("results", resp.json())]
        assert "A-1" in codes and "B-1" not in codes

    def test_marker_label_returns_png(self, member_client, area, item):
        # AC-6: any authenticated user can pull the label (read-flavored).
        slot = VisionSlot.objects.create(area=area, item=item, marker_code="MARK-XY")
        resp = member_client.get(f"/api/storage-vision/slots/{slot.id}/marker/")
        assert resp.status_code == 200, resp.content
        assert resp["Content-Type"] == "image/png"
        # PNG magic bytes
        assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------------------
# VisionCamera CRUD + token rotation
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("feature_on")
class TestVisionCamera:
    def test_create_returns_raw_token_once(self, staff_client, area):
        resp = staff_client.post(
            "/api/storage-vision/cameras/",
            {"name": "Shelf cam 1", "area": area.id},
            format="json",
        )
        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert "raw_token" in body and body["raw_token"]
        assert "token_fingerprint" in body
        cam_id = body["id"]

        # Subsequent GET must NOT include the raw token, only fingerprint.
        retrieve = staff_client.get(f"/api/storage-vision/cameras/{cam_id}/")
        assert retrieve.status_code == 200
        rbody = retrieve.json()
        assert "raw_token" not in rbody
        assert rbody["token_fingerprint"] == body["token_fingerprint"]

    def test_rotate_token_emits_new_bearer(self, staff_client, area):
        cam = VisionCamera(name="cam", area=area)
        raw_before = cam.issue_token()
        cam.save()
        fp_before = cam.token_fingerprint

        resp = staff_client.post(f"/api/storage-vision/cameras/{cam.id}/rotate-token/")
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert "raw_token" in body and body["raw_token"]
        assert body["raw_token"] != raw_before
        assert body["token_fingerprint"] != fp_before

        # Old fingerprint should no longer resolve.
        cam.refresh_from_db()
        assert cam.token_fingerprint == body["token_fingerprint"]

    def test_member_cannot_create_camera(self, member_client, area):
        resp = member_client.post(
            "/api/storage-vision/cameras/",
            {"name": "cam", "area": area.id},
            format="json",
        )
        assert resp.status_code == 403

    def test_member_cannot_rotate_token(self, member_client, area):
        cam = VisionCamera(name="cam", area=area)
        cam.issue_token()
        cam.save()
        resp = member_client.post(f"/api/storage-vision/cameras/{cam.id}/rotate-token/")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Feature-flag gate (AC-2)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("feature_off")
class TestFeatureFlagGatesWrites:
    def test_create_area_returns_503_when_disabled(self, staff_client, location):
        resp = staff_client.post(
            "/api/storage-vision/areas/",
            {"name": "x", "location": location.id},
            format="json",
        )
        assert resp.status_code == 503
        assert resp.json().get("code") == "feature_disabled"

    def test_read_area_succeeds_when_disabled(self, staff_client, area):
        resp = staff_client.get("/api/storage-vision/areas/")
        # AC-2 explicitly leaves READ paths alone — only writes are blocked.
        assert resp.status_code == 200

    def test_create_slot_returns_503_when_disabled(self, staff_client, area, item):
        resp = staff_client.post(
            "/api/storage-vision/slots/",
            {"area": area.id, "item": item.id, "marker_code": "FF-1"},
            format="json",
        )
        assert resp.status_code == 503

    def test_rotate_token_returns_503_when_disabled(self, staff_client, area):
        cam = VisionCamera(name="cam", area=area)
        cam.issue_token()
        cam.save()
        resp = staff_client.post(f"/api/storage-vision/cameras/{cam.id}/rotate-token/")
        assert resp.status_code == 503
