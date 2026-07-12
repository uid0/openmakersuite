"""
Tests for the manual work-order PDF upload endpoint
(WorkOrderViewSet.upload_pdf).

Mirrors the postmark inbound webhook tests but for the staff-only manual
upload path. Reuses the helpers in test_work_order_ingest for building a
work order and filling its PDF checkboxes.
"""

import io

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils.crypto import get_random_string

import pytest
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from rest_framework import status
from rest_framework.test import APIClient

from inventory.models import WorkOrder, WorkOrderSubmission
from inventory.tests.test_work_order_ingest import _make_work_order
from inventory.utils.work_order_pdf import generate_work_order_pdf

User = get_user_model()
pytestmark = pytest.mark.django_db

UPLOAD_URL = "/api/inventory/work-orders/upload-pdf/"


def _staff_client():
    user = User.objects.create_user(
        username=f"staff_{get_random_string(6)}",
        email="staff@example.com",
        password=get_random_string(24),
        is_staff=True,
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client, user


def _non_staff_client():
    user = User.objects.create_user(
        username=f"user_{get_random_string(6)}",
        email="user@example.com",
        password=get_random_string(24),
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client, user


def _fill_checkboxes(pdf_bytes: bytes, checked_field_names: list[str]) -> bytes:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter(clone_from=reader)
    field_values = {name: "/Yes" for name in checked_field_names}
    for page in writer.pages:
        writer.update_page_form_field_values(page, field_values)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _upload(client, pdf_bytes, filename="completed-wo.pdf"):
    upload = SimpleUploadedFile(filename, pdf_bytes, content_type="application/pdf")
    return client.post(UPLOAD_URL, data={"pdf": upload}, format="multipart")


@pytest.mark.integration
class TestUploadPdfEndpoint:
    def test_upload_valid_pdf_creates_submission_and_completes_items(self):
        client, user = _staff_client()
        wo = _make_work_order(num_tasks=2)
        tcs = list(wo.task_completions.order_by("task_order"))
        pdf_bytes = generate_work_order_pdf(wo, base_url="http://example.com")
        filled = _fill_checkboxes(pdf_bytes, [f"task_{tc.id}" for tc in tcs])

        resp = _upload(client, filled)

        assert resp.status_code == status.HTTP_200_OK, resp.content
        body = resp.json()
        assert body["status"] == WorkOrderSubmission.Status.APPLIED
        assert body["work_order_id"] == str(wo.id)
        assert body["errors"] == []

        completed_titles = {item["task_title"] for item in body["completed_items"]}
        assert completed_titles == {tc.task_title for tc in tcs}

        submission = WorkOrderSubmission.objects.get(id=body["submission_id"])
        assert submission.source == WorkOrderSubmission.Source.MANUAL
        assert submission.submitted_by_id == user.id

        wo.refresh_from_db()
        assert wo.status == WorkOrder.Status.COMPLETED

    def test_upload_without_pdf_returns_400(self):
        client, _ = _staff_client()
        resp = client.post(UPLOAD_URL, data={}, format="multipart")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "pdf" in resp.json().get("detail", "").lower()
        assert WorkOrderSubmission.objects.count() == 0

    def test_upload_non_staff_returns_403(self):
        client, _ = _non_staff_client()
        # Build any small PDF; doesn't matter — auth check is first.
        buf = io.BytesIO()
        c = canvas.Canvas(buf)
        c.drawString(100, 750, "irrelevant")
        c.showPage()
        c.save()
        resp = _upload(client, buf.getvalue())
        assert resp.status_code == status.HTTP_403_FORBIDDEN
        assert WorkOrderSubmission.objects.count() == 0

    def test_upload_pdf_with_no_recognized_checkboxes_returns_warning(self):
        """A PDF without the embedded WO id marker is reported as a parse error."""
        client, _ = _staff_client()

        buf = io.BytesIO()
        c = canvas.Canvas(buf)
        c.drawString(100, 750, "No work order marker on this page")
        c.showPage()
        c.save()

        resp = _upload(client, buf.getvalue())
        assert resp.status_code == status.HTTP_200_OK, resp.content
        body = resp.json()
        assert body["status"] == WorkOrderSubmission.Status.FAILED
        assert body["work_order_id"] is None
        assert body["completed_items"] == []
        assert body["errors"]
        assert "Work Order ID" in body["errors"][0]

        submission = WorkOrderSubmission.objects.get(id=body["submission_id"])
        assert submission.source == WorkOrderSubmission.Source.MANUAL

    def test_source_set_to_manual(self):
        """Manual uploads must record source='manual' on the submission."""
        client, user = _staff_client()
        wo = _make_work_order(num_tasks=1)
        pdf_bytes = generate_work_order_pdf(wo, base_url="http://example.com")

        resp = _upload(client, pdf_bytes)
        assert resp.status_code == status.HTTP_200_OK

        submission = WorkOrderSubmission.objects.get(id=resp.json()["submission_id"])
        assert submission.source == WorkOrderSubmission.Source.MANUAL
        assert submission.submitted_by_id == user.id


@pytest.mark.integration
class TestWorkOrderSerializerExposesSubmissions:
    """
    The WorkOrder detail API must expose uploaded submissions so the frontend
    can render a downloadable history of paper-form scans.
    """

    def test_workorder_serializer_exposes_pdf_url(self):
        client, _ = _staff_client()
        wo = _make_work_order(num_tasks=1)
        pdf_bytes = generate_work_order_pdf(wo, base_url="http://example.com")

        # Upload one PDF to populate a submission row tied to this WO.
        upload_resp = _upload(client, pdf_bytes)
        assert upload_resp.status_code == status.HTTP_200_OK, upload_resp.content

        detail_resp = client.get(f"/api/inventory/work-orders/{wo.id}/")
        assert detail_resp.status_code == status.HTTP_200_OK, detail_resp.content
        body = detail_resp.json()

        assert "submissions" in body
        assert len(body["submissions"]) == 1
        sub = body["submissions"][0]
        assert sub["pdf_url"], "pdf_url must be a non-empty URL"
        assert sub["pdf_url"].endswith(".pdf")
        assert sub["source"] == WorkOrderSubmission.Source.MANUAL
        assert sub["status"] == WorkOrderSubmission.Status.APPLIED
        assert sub["received_at"]
