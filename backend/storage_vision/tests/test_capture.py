"""Storage Vision slice-3 capture + heartbeat API tests.

Covers AC-8 (camera heartbeat), AC-9 (phone capture upload via staff /
Logistics JWT), AC-10 (camera capture upload via scoped bearer), AC-11
(anonymous capture upload rejected), and AC-12 (capture validation:
max size, JPEG/PNG only, Pillow-decodable, sanitized error messages).
"""

from __future__ import annotations

from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile

import pytest
from PIL import Image
from rest_framework.test import APIClient

from inventory.models import Location
from storage_vision.authentication import VISION_CAMERA_TOKEN_HEADER
from storage_vision.models import VisionArea, VisionCamera, VisionCapture

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
def area():
    location = Location.objects.create(name="Shop floor")
    return VisionArea.objects.create(name="Bay 1", location=location)


@pytest.fixture
def camera(area):
    cam = VisionCamera.objects.create(name="bay1-cam", area=area)
    raw = cam.issue_token()
    cam.save()
    cam.raw_token = raw
    return cam


@pytest.fixture
def feature_on(settings):
    settings.STORAGE_VISION_ENABLED = True


@pytest.fixture
def feature_off(settings):
    settings.STORAGE_VISION_ENABLED = False


def _jpeg_bytes(size=(64, 64), color=(120, 120, 120)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG", quality=70)
    return buf.getvalue()


def _png_bytes(size=(64, 64), color=(120, 120, 120)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _upload(name="capture.jpg", content_type="image/jpeg", data=None) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, data if data is not None else _jpeg_bytes(), content_type)


# ---------------------------------------------------------------------------
# Capture upload — AC-9 / AC-10 / AC-11 / AC-12
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("feature_on")
class TestCaptureUpload:
    """AC-9, AC-10, AC-11, AC-12 — upload paths and validation."""

    def test_phone_upload_by_staff_returns_202(self, staff_client, area):
        with patch("storage_vision.views.process_capture.delay") as delay:
            resp = staff_client.post(
                "/api/storage-vision/captures/",
                {"area": area.id, "original_image": _upload()},
                format="multipart",
            )
        assert resp.status_code == 202, resp.content
        body = resp.json()
        assert body["source"] == VisionCapture.SOURCE_PHONE
        assert body["camera"] is None
        assert body["status"] == VisionCapture.STATUS_QUEUED
        delay.assert_called_once_with(body["id"])

    def test_phone_upload_by_logistics_returns_202(self, logistics_client, area):
        with patch("storage_vision.views.process_capture.delay"):
            resp = logistics_client.post(
                "/api/storage-vision/captures/",
                {
                    "area": area.id,
                    "original_image": _upload(content_type="image/png", data=_png_bytes()),
                },
                format="multipart",
            )
        assert resp.status_code == 202, resp.content
        assert resp.json()["source"] == VisionCapture.SOURCE_PHONE

    def test_phone_upload_by_member_rejected(self, member_client, area):
        resp = member_client.post(
            "/api/storage-vision/captures/",
            {"area": area.id, "original_image": _upload()},
            format="multipart",
        )
        assert resp.status_code == 403

    def test_anonymous_upload_rejected(self, anon_client, area):
        resp = anon_client.post(
            "/api/storage-vision/captures/",
            {"area": area.id, "original_image": _upload()},
            format="multipart",
        )
        # AC-11: anonymous gets 401 from the auth chain (no auth header).
        assert resp.status_code in (401, 403)
        assert not VisionCapture.objects.exists()

    def test_camera_upload_returns_202(self, anon_client, camera):
        # Camera bearer is the ONLY credential — verifies AC-10 dual-auth.
        with patch("storage_vision.views.process_capture.delay") as delay:
            resp = anon_client.post(
                "/api/storage-vision/captures/",
                {"area": camera.area.id, "original_image": _upload()},
                format="multipart",
                **{VISION_CAMERA_TOKEN_HEADER: camera.raw_token},
            )
        assert resp.status_code == 202, resp.content
        body = resp.json()
        assert body["source"] == VisionCapture.SOURCE_CAMERA
        assert body["camera"] == camera.id
        assert body["uploaded_by"] is None
        delay.assert_called_once()

    def test_camera_upload_defaults_area_from_camera(self, anon_client, camera):
        # AC-10: a camera bound to an area shouldn't need to spell out
        # area=<id> on every upload.
        with patch("storage_vision.views.process_capture.delay"):
            resp = anon_client.post(
                "/api/storage-vision/captures/",
                {"original_image": _upload()},
                format="multipart",
                **{VISION_CAMERA_TOKEN_HEADER: camera.raw_token},
            )
        assert resp.status_code == 202, resp.content
        assert resp.json()["area"] == camera.area.id

    def test_invalid_camera_token_returns_401(self, anon_client, area):
        resp = anon_client.post(
            "/api/storage-vision/captures/",
            {"area": area.id, "original_image": _upload()},
            format="multipart",
            **{VISION_CAMERA_TOKEN_HEADER: "not-a-real-token"},
        )
        assert resp.status_code == 401

    def test_oversized_upload_rejected(self, staff_client, area, settings):
        settings.STORAGE_VISION_MAX_UPLOAD_BYTES = 1024  # 1 KB cap
        big = _jpeg_bytes(size=(512, 512))
        assert len(big) > 1024
        resp = staff_client.post(
            "/api/storage-vision/captures/",
            {"area": area.id, "original_image": _upload(data=big)},
            format="multipart",
        )
        assert resp.status_code == 400
        body = resp.json()
        # AC-12: error must be sanitized — no filesystem paths or
        # stack-trace residue.
        message = str(body)
        assert "Traceback" not in message
        assert "/tmp" not in message
        assert "1024" in message

    def test_non_image_upload_rejected(self, staff_client, area):
        bogus = SimpleUploadedFile("notes.txt", b"hello world", content_type="text/plain")
        resp = staff_client.post(
            "/api/storage-vision/captures/",
            {"area": area.id, "original_image": bogus},
            format="multipart",
        )
        assert resp.status_code == 400
        message = str(resp.json())
        # AC-12: no traceback residue or path leakage in the response.
        assert "Traceback" not in message
        assert "/tmp" not in message
        assert "/app" not in message

    def test_corrupt_jpeg_rejected(self, staff_client, area):
        # Right content-type, but the bytes don't decode.
        corrupt = SimpleUploadedFile(
            "bad.jpg", b"\xff\xd8\xff\xe0garbage", content_type="image/jpeg"
        )
        resp = staff_client.post(
            "/api/storage-vision/captures/",
            {"area": area.id, "original_image": corrupt},
            format="multipart",
        )
        assert resp.status_code == 400
        message = str(resp.json())
        # AC-12: any rejection wording is fine — what matters is no
        # traceback / filesystem leakage.
        assert "Traceback" not in message
        assert "/tmp" not in message
        assert "/app" not in message


@pytest.mark.usefixtures("feature_off")
class TestCaptureFeatureGate:
    """AC-2: writes return 503 when the feature flag is off."""

    def test_upload_returns_503_when_disabled(self, staff_client, area):
        resp = staff_client.post(
            "/api/storage-vision/captures/",
            {"area": area.id, "original_image": _upload()},
            format="multipart",
        )
        assert resp.status_code == 503
        assert resp.json()["code"] == "feature_disabled"

    def test_list_still_works_when_disabled(self, staff_client):
        resp = staff_client.get("/api/storage-vision/captures/")
        assert resp.status_code == 200


@pytest.mark.usefixtures("feature_on")
class TestCaptureList:
    """Reads require staff/Logistics — a camera bearer can write but
    not enumerate the queue."""

    def test_member_cannot_list(self, member_client):
        resp = member_client.get("/api/storage-vision/captures/")
        assert resp.status_code == 403

    def test_camera_cannot_list(self, anon_client, camera):
        resp = anon_client.get(
            "/api/storage-vision/captures/",
            **{VISION_CAMERA_TOKEN_HEADER: camera.raw_token},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Camera heartbeat — AC-8
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("feature_on")
class TestCameraHeartbeat:
    """AC-8: cameras stamp last_seen_at via their scoped bearer."""

    def test_heartbeat_updates_last_seen(self, anon_client, camera):
        assert camera.last_seen_at is None
        resp = anon_client.post(
            f"/api/storage-vision/cameras/{camera.id}/heartbeat/",
            {"status": "ok", "queue_depth": 0},
            format="json",
            **{VISION_CAMERA_TOKEN_HEADER: camera.raw_token},
        )
        assert resp.status_code == 200, resp.content
        camera.refresh_from_db()
        assert camera.last_seen_at is not None
        assert camera.last_seen_status == {"status": "ok", "queue_depth": 0}

    def test_heartbeat_without_body_still_stamps(self, anon_client, camera):
        resp = anon_client.post(
            f"/api/storage-vision/cameras/{camera.id}/heartbeat/",
            **{VISION_CAMERA_TOKEN_HEADER: camera.raw_token},
        )
        assert resp.status_code == 200
        camera.refresh_from_db()
        assert camera.last_seen_at is not None

    def test_heartbeat_with_bad_token_returns_401(self, anon_client, camera):
        resp = anon_client.post(
            f"/api/storage-vision/cameras/{camera.id}/heartbeat/",
            **{VISION_CAMERA_TOKEN_HEADER: "garbage"},
        )
        assert resp.status_code == 401
        camera.refresh_from_db()
        assert camera.last_seen_at is None

    def test_heartbeat_without_token_returns_401(self, anon_client, camera):
        # No bearer header at all — auth chain returns nothing, so the
        # action's empty permission_classes still rejects via the view.
        resp = anon_client.post(f"/api/storage-vision/cameras/{camera.id}/heartbeat/")
        assert resp.status_code == 401

    def test_heartbeat_rejects_jwt_user(self, staff_client, camera):
        # AC-8: heartbeat is camera-bearer-ONLY. A staff JWT must not
        # be able to fake liveness on the device's behalf.
        resp = staff_client.post(f"/api/storage-vision/cameras/{camera.id}/heartbeat/")
        assert resp.status_code == 401

    def test_heartbeat_other_camera_404(self, anon_client, area):
        # Camera A's bearer must not stamp camera B.
        cam_a = VisionCamera.objects.create(name="a", area=area)
        cam_a.raw_token = cam_a.issue_token()
        cam_a.save()
        cam_b = VisionCamera.objects.create(name="b", area=area)
        cam_b.issue_token()
        cam_b.save()
        resp = anon_client.post(
            f"/api/storage-vision/cameras/{cam_b.id}/heartbeat/",
            **{VISION_CAMERA_TOKEN_HEADER: cam_a.raw_token},
        )
        assert resp.status_code == 404
        cam_b.refresh_from_db()
        assert cam_b.last_seen_at is None
