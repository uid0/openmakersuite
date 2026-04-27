"""
Tests for asset problem photo uploads
(AssetProblemViewSet.upload_photo).
"""

import io

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils.crypto import get_random_string

import pytest
from PIL import Image
from rest_framework import status
from rest_framework.test import APIClient

from inventory.models import AssetProblem, AssetProblemPhoto
from inventory.tests.factories import AssetFactory, AssetProblemFactory

User = get_user_model()
pytestmark = pytest.mark.django_db


def _make_image_file(name="problem.jpg"):
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color=(0, 128, 255)).save(buf, format="JPEG")
    buf.seek(0)
    return SimpleUploadedFile(name, buf.read(), content_type="image/jpeg")


def _upload_url(problem_id):
    return f"/api/inventory/asset-problems/{problem_id}/upload-photo/"


def _problem_detail_url(problem_id):
    return f"/api/inventory/asset-problems/{problem_id}/"


@pytest.mark.integration
class TestAssetProblemPhotoUpload:
    def test_upload_photo_creates_row(self):
        """Reporter can attach a photo; row is persisted with caption + uploader."""
        username = f"reporter_{get_random_string(6)}"
        user = User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password=get_random_string(24),
        )
        problem = AssetProblemFactory(reported_by=user.username)

        client = APIClient()
        client.force_authenticate(user=user)

        resp = client.post(
            _upload_url(problem.id),
            data={"image": _make_image_file(), "caption": "Bent rim"},
            format="multipart",
        )

        assert resp.status_code == status.HTTP_201_CREATED, resp.content
        body = resp.json()
        assert body["caption"] == "Bent rim"
        assert body["image_url"]
        assert body["uploaded_by"] == user.id

        assert AssetProblemPhoto.objects.filter(problem=problem).count() == 1
        photo = AssetProblemPhoto.objects.get(problem=problem)
        assert photo.uploaded_by_id == user.id
        assert photo.caption == "Bent rim"

    def test_anonymous_upload_against_anonymous_report_succeeds(self):
        """Problems reported with no reporter (kiosk flow) accept anonymous uploads."""
        problem = AssetProblemFactory(reported_by="")

        client = APIClient()
        resp = client.post(
            _upload_url(problem.id),
            data={"image": _make_image_file()},
            format="multipart",
        )

        assert resp.status_code == status.HTTP_201_CREATED, resp.content
        photo = AssetProblemPhoto.objects.get(problem=problem)
        assert photo.uploaded_by_id is None

    def test_photo_url_in_problem_response(self):
        """GET /asset-problems/<id>/ returns photos[].image_url."""
        problem = AssetProblemFactory(reported_by="")
        client = APIClient()

        upload = client.post(
            _upload_url(problem.id),
            data={"image": _make_image_file(), "caption": "First angle"},
            format="multipart",
        )
        assert upload.status_code == status.HTTP_201_CREATED

        resp = client.get(_problem_detail_url(problem.id))
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        assert isinstance(body["photos"], list)
        assert len(body["photos"]) == 1
        photo = body["photos"][0]
        assert photo["caption"] == "First angle"
        assert photo["image_url"]

    def test_non_authorized_user_403(self):
        """A non-reporter, non-staff user cannot upload to someone else's problem."""
        owner_name = f"owner_{get_random_string(6)}"
        AssetProblemFactory(reported_by=owner_name)  # ensure factory side effects ok
        problem = AssetProblemFactory(reported_by=owner_name)

        intruder = User.objects.create_user(
            username=f"intruder_{get_random_string(6)}",
            email="intruder@example.com",
            password=get_random_string(24),
        )
        client = APIClient()
        client.force_authenticate(user=intruder)

        resp = client.post(
            _upload_url(problem.id),
            data={"image": _make_image_file()},
            format="multipart",
        )

        assert resp.status_code == status.HTTP_403_FORBIDDEN
        assert AssetProblemPhoto.objects.filter(problem=problem).count() == 0

    def test_staff_can_upload_to_any_problem(self):
        owner_name = f"owner_{get_random_string(6)}"
        problem = AssetProblemFactory(reported_by=owner_name)

        staff = User.objects.create_user(
            username=f"staff_{get_random_string(6)}",
            email="staff@example.com",
            password=get_random_string(24),
            is_staff=True,
        )
        client = APIClient()
        client.force_authenticate(user=staff)

        resp = client.post(
            _upload_url(problem.id),
            data={"image": _make_image_file(), "caption": "Inspector photo"},
            format="multipart",
        )

        assert resp.status_code == status.HTTP_201_CREATED, resp.content
        photo = AssetProblemPhoto.objects.get(problem=problem)
        assert photo.uploaded_by_id == staff.id

    def test_upload_without_image_returns_400(self):
        problem = AssetProblemFactory(reported_by="")
        client = APIClient()

        resp = client.post(_upload_url(problem.id), data={}, format="multipart")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert AssetProblemPhoto.objects.filter(problem=problem).count() == 0

    def test_multiple_photos_per_problem(self):
        """Multi-photo support: many photos may be attached to the same problem."""
        problem = AssetProblemFactory(reported_by="")
        client = APIClient()

        for i in range(3):
            resp = client.post(
                _upload_url(problem.id),
                data={"image": _make_image_file(name=f"p{i}.jpg"), "caption": f"angle {i}"},
                format="multipart",
            )
            assert resp.status_code == status.HTTP_201_CREATED, resp.content

        resp = client.get(_problem_detail_url(problem.id))
        assert resp.status_code == status.HTTP_200_OK
        captions = [p["caption"] for p in resp.json()["photos"]]
        assert captions == ["angle 0", "angle 1", "angle 2"]

    def test_upload_404_for_unknown_problem(self):
        client = APIClient()
        resp = client.post(
            "/api/inventory/asset-problems/00000000-0000-0000-0000-000000000000/upload-photo/",
            data={"image": _make_image_file()},
            format="multipart",
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.integration
@pytest.mark.django_db
class TestAssetProblemListReadOnly:
    """The viewset is read-only; no POST/PUT/DELETE on the collection."""

    def test_create_via_viewset_not_allowed(self):
        asset = AssetFactory()
        client = APIClient()
        resp = client.post(
            "/api/inventory/asset-problems/",
            data={"asset": str(asset.id), "description": "broken"},
            format="json",
        )
        assert resp.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
        assert AssetProblem.objects.count() == 0
