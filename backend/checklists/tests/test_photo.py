"""
Tests for photo upload on checklist step completions.
"""

import io

from django.core.files.uploadedfile import SimpleUploadedFile

import pytest
from PIL import Image
from rest_framework import status

from checklists.tests.factories import (
    ChecklistCompletionFactory,
    ChecklistFactory,
    ChecklistStepFactory,
)

# Photo-upload tests write through factories + endpoints; enable DB access for
# the module, matching the reorder_queue/inventory test suites. (Pre-existing:
# the checklists app was absent from pytest.ini testpaths so these never ran in CI.)
pytestmark = pytest.mark.django_db


def _make_image_file(name="photo.jpg"):
    """Create a small in-memory JPEG suitable for ImageField uploads."""
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color=(255, 0, 0)).save(buf, format="JPEG")
    buf.seek(0)
    return SimpleUploadedFile(name, buf.read(), content_type="image/jpeg")


@pytest.mark.integration
@pytest.mark.django_db
class TestChecklistStepPhotoUpload:
    """Photo support for checklist step completions."""

    def test_photo_upload_succeeds(self, api_client):
        checklist = ChecklistFactory(is_public=True, is_active=True)
        step = ChecklistStepFactory(checklist=checklist, requires_photo=True)
        completion = ChecklistCompletionFactory(checklist=checklist, user=None, user_name="John")

        url = f"/api/checklists/completions/{completion.id}/scan/"
        response = api_client.post(
            url,
            {
                "step_id": str(step.id),
                "asset_id": str(step.asset.id),
                "photo": _make_image_file(),
                "photo_caption": "Wear on the rim",
            },
            format="multipart",
        )

        assert response.status_code == status.HTTP_200_OK
        step_completion = response.data["step_completions"][0]
        assert step_completion["photo_url"] is not None
        assert step_completion["photo_caption"] == "Wear on the rim"

    def test_photo_required_step_rejects_no_photo(self, api_client):
        checklist = ChecklistFactory(is_public=True, is_active=True)
        step = ChecklistStepFactory(checklist=checklist, requires_photo=True)
        completion = ChecklistCompletionFactory(checklist=checklist, user=None, user_name="John")

        url = f"/api/checklists/completions/{completion.id}/scan/"
        response = api_client.post(
            url,
            {
                "step_id": str(step.id),
                "asset_id": str(step.asset.id),
            },
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "photo" in response.data["detail"].lower()

    def test_photo_url_returned_in_completion_response(self, api_client):
        checklist = ChecklistFactory(is_public=True, is_active=True)
        step = ChecklistStepFactory(checklist=checklist, requires_photo=True)
        completion = ChecklistCompletionFactory(checklist=checklist, user=None, user_name="John")

        scan_url = f"/api/checklists/completions/{completion.id}/scan/"
        api_client.post(
            scan_url,
            {
                "step_id": str(step.id),
                "asset_id": str(step.asset.id),
                "photo": _make_image_file(),
            },
            format="multipart",
        )

        get_url = f"/api/checklists/completions/{completion.id}/"
        response = api_client.get(get_url)

        assert response.status_code == status.HTTP_200_OK
        step_completion = response.data["step_completions"][0]
        assert step_completion["photo_url"]
        assert step_completion["photo_url"].startswith("http") or step_completion[
            "photo_url"
        ].startswith("/")

    def test_photo_optional_step_accepts_no_photo(self, api_client):
        checklist = ChecklistFactory(is_public=True, is_active=True)
        step = ChecklistStepFactory(checklist=checklist, requires_photo=False)
        completion = ChecklistCompletionFactory(checklist=checklist, user=None, user_name="John")

        url = f"/api/checklists/completions/{completion.id}/scan/"
        response = api_client.post(
            url,
            {
                "step_id": str(step.id),
                "asset_id": str(step.asset.id),
            },
        )

        assert response.status_code == status.HTTP_200_OK
        step_completion = response.data["step_completions"][0]
        assert step_completion["photo_url"] is None
