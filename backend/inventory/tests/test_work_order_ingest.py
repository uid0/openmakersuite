"""
Tests for the Postmark-inbound work-order ingestion pipeline.

Covers:
    - parse_work_order_pdf: pulls WO id + checkbox state out of a PDF
    - apply_submission:     end-to-end DB effects on WorkOrder / MaintenanceItem
    - postmark_inbound_work_order view: auth gating + happy path + dup detection
"""

import base64
import io
from datetime import date, timedelta

from django.core.files.base import ContentFile
from django.urls import reverse
from django.utils import timezone

import pytest
from pypdf import PdfReader, PdfWriter
from rest_framework import status

from inventory.models import (
    MaintenanceItem,
    MaintenanceLog,
    MaintenanceTask,
    WorkOrder,
    WorkOrderSubmission,
    WorkOrderTaskCompletion,
)
from inventory.services.work_order_ingest import apply_submission, parse_work_order_pdf
from inventory.tests.factories import AssetFactory
from inventory.utils.work_order_pdf import generate_work_order_pdf

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_work_order(num_tasks: int = 2, all_required: bool = True) -> WorkOrder:
    """Build an asset → maintenance item → work order with N task completions."""
    asset = AssetFactory()
    item = MaintenanceItem.objects.create(
        asset=asset,
        title="Monthly inspection",
        description="Standard monthly checklist",
        interval_days=30,
    )
    tasks = []
    for i in range(num_tasks):
        tasks.append(
            MaintenanceTask.objects.create(
                maintenance_item=item,
                order=i,
                title=f"Step {i + 1}",
                is_required=all_required,
            )
        )
    wo = WorkOrder.objects.create(
        maintenance_item=item,
        due_date=date.today() + timedelta(days=7),
    )
    for t in tasks:
        WorkOrderTaskCompletion.objects.create(
            work_order=wo,
            task=t,
            task_title=t.title,
            task_order=t.order,
            is_required=t.is_required,
        )
    return wo


def _fill_checkboxes(pdf_bytes: bytes, checked_field_names: list[str]) -> bytes:
    """
    Given a PDF produced by generate_work_order_pdf, return new bytes with the
    named AcroForm checkboxes set to the "Yes" state — simulating a user who
    opened the form, checked those boxes, and saved.
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter(clone_from=reader)
    field_values = {name: "/Yes" for name in checked_field_names}
    for page in writer.pages:
        writer.update_page_form_field_values(page, field_values)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _postmark_payload(pdf_bytes: bytes, **overrides) -> dict:
    """Construct a Postmark-shaped inbound JSON payload with a single PDF."""
    payload = {
        "MessageID": "msg-test-00001",
        "From": "tech@example.com",
        "FromFull": {"Email": "tech@example.com", "Name": "Test Tech"},
        "Subject": "Completed work order",
        "Attachments": [
            {
                "Name": "work-order.pdf",
                "ContentType": "application/pdf",
                "Content": base64.b64encode(pdf_bytes).decode("ascii"),
                "ContentLength": len(pdf_bytes),
            }
        ],
    }
    payload.update(overrides)
    return payload


# ─────────────────────────────────────────────────────────────────────────────
# parse_work_order_pdf
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
class TestParseWorkOrderPdf:
    def test_extracts_work_order_id_and_empty_checkboxes(self):
        wo = _make_work_order(num_tasks=2)
        pdf_bytes = generate_work_order_pdf(wo, base_url="http://example.com")

        parsed = parse_work_order_pdf(pdf_bytes)

        assert parsed["work_order_id"] == str(wo.id)
        tcs = list(wo.task_completions.all())
        # All checkboxes present in the field dictionary, all unchecked.
        for tc in tcs:
            assert str(tc.id) in parsed["task_checks"]
            assert parsed["task_checks"][str(tc.id)] is False

    def test_detects_checked_boxes(self):
        wo = _make_work_order(num_tasks=2)
        pdf_bytes = generate_work_order_pdf(wo, base_url="http://example.com")
        tcs = list(wo.task_completions.order_by("task_order"))

        filled = _fill_checkboxes(pdf_bytes, [f"task_{tcs[0].id}"])

        parsed = parse_work_order_pdf(filled)
        assert parsed["work_order_id"] == str(wo.id)
        assert parsed["task_checks"][str(tcs[0].id)] is True
        assert parsed["task_checks"][str(tcs[1].id)] is False

    def test_missing_work_order_id_returns_none(self):
        """A PDF without the marker string returns work_order_id=None."""
        # Craft a tiny PDF via reportlab without any WO marker.
        from reportlab.pdfgen import canvas

        buf = io.BytesIO()
        c = canvas.Canvas(buf)
        c.drawString(100, 750, "No identifier here")
        c.showPage()
        c.save()

        parsed = parse_work_order_pdf(buf.getvalue())
        assert parsed["work_order_id"] is None
        assert parsed["task_checks"] == {}


# ─────────────────────────────────────────────────────────────────────────────
# apply_submission
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
class TestApplySubmission:
    def test_partial_completion_sets_in_progress(self):
        wo = _make_work_order(num_tasks=3)
        tcs = list(wo.task_completions.order_by("task_order"))
        pdf_bytes = generate_work_order_pdf(wo, base_url="http://example.com")
        filled = _fill_checkboxes(pdf_bytes, [f"task_{tcs[0].id}"])

        submission = WorkOrderSubmission(from_email="tech@example.com", subject="WO")
        submission.attachment.save("wo.pdf", ContentFile(filled), save=False)
        submission.save()

        apply_submission(submission)

        submission.refresh_from_db()
        wo.refresh_from_db()
        assert submission.status == WorkOrderSubmission.STATUS_APPLIED
        assert submission.work_order_id == wo.id
        assert wo.status == WorkOrder.STATUS_IN_PROGRESS
        assert wo.completed_at is None
        tcs[0].refresh_from_db()
        tcs[1].refresh_from_db()
        assert tcs[0].is_completed is True
        assert tcs[0].completed_at is not None
        assert tcs[1].is_completed is False
        assert wo.completed_scan  # PDF stored
        # No maintenance log yet.
        assert MaintenanceLog.objects.filter(maintenance_item=wo.maintenance_item).count() == 0

    def test_all_required_checked_completes_work_order(self):
        wo = _make_work_order(num_tasks=2)
        tcs = list(wo.task_completions.order_by("task_order"))
        pdf_bytes = generate_work_order_pdf(wo, base_url="http://example.com")
        filled = _fill_checkboxes(pdf_bytes, [f"task_{tc.id}" for tc in tcs])

        submission = WorkOrderSubmission()
        submission.attachment.save("wo.pdf", ContentFile(filled), save=False)
        submission.save()

        apply_submission(submission)
        wo.refresh_from_db()

        assert wo.status == WorkOrder.STATUS_COMPLETED
        assert wo.completed_at is not None
        # MaintenanceItem.last_completed_at is stamped.
        wo.maintenance_item.refresh_from_db()
        assert wo.maintenance_item.last_completed_at is not None
        # A maintenance history log entry was created.
        assert MaintenanceLog.objects.filter(maintenance_item=wo.maintenance_item).count() == 1

    def test_already_completed_task_is_not_rewound(self):
        """A task marked complete digitally shouldn't be altered by an unchecked PDF."""
        wo = _make_work_order(num_tasks=2)
        tcs = list(wo.task_completions.order_by("task_order"))
        first_completed_at = timezone.now() - timedelta(days=1)
        tcs[0].is_completed = True
        tcs[0].completed_at = first_completed_at
        tcs[0].save()

        pdf_bytes = generate_work_order_pdf(wo, base_url="http://example.com")
        # Only check the second box; leave the first unchecked on paper.
        filled = _fill_checkboxes(pdf_bytes, [f"task_{tcs[1].id}"])

        submission = WorkOrderSubmission()
        submission.attachment.save("wo.pdf", ContentFile(filled), save=False)
        submission.save()

        apply_submission(submission)

        tcs[0].refresh_from_db()
        tcs[1].refresh_from_db()
        assert tcs[0].is_completed is True
        # completed_at preserved — we never un-complete a task.
        assert tcs[0].completed_at == first_completed_at
        assert tcs[1].is_completed is True

    def test_unknown_work_order_id_marks_failed(self):
        wo = _make_work_order(num_tasks=1)
        pdf_bytes = generate_work_order_pdf(wo, base_url="http://example.com")
        # Delete the WO so the id in the PDF no longer resolves.
        wo_id = wo.id
        wo.maintenance_item.delete()  # cascades

        submission = WorkOrderSubmission()
        submission.attachment.save("wo.pdf", ContentFile(pdf_bytes), save=False)
        submission.save()

        apply_submission(submission)
        submission.refresh_from_db()

        assert submission.status == WorkOrderSubmission.STATUS_FAILED
        assert str(wo_id) in submission.parse_error
        assert submission.work_order is None


# ─────────────────────────────────────────────────────────────────────────────
# Webhook view
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
class TestPostmarkInboundView:
    URL_NAME = "postmark-inbound-work-order"

    def test_requires_configured_token(self, api_client, settings):
        settings.POSTMARK_INBOUND_TOKEN = ""
        resp = api_client.post(reverse(self.URL_NAME), data={}, format="json")
        assert resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE

    def test_rejects_wrong_token(self, api_client, settings):
        settings.POSTMARK_INBOUND_TOKEN = "correct"
        resp = api_client.post(
            reverse(self.URL_NAME),
            data={},
            format="json",
            HTTP_X_POSTMARK_WEBHOOK_TOKEN="wrong",
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_rejects_missing_pdf_attachment(self, api_client, settings):
        settings.POSTMARK_INBOUND_TOKEN = "correct"
        resp = api_client.post(
            reverse(self.URL_NAME),
            data={"MessageID": "m1", "Attachments": []},
            format="json",
            HTTP_X_POSTMARK_WEBHOOK_TOKEN="correct",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_happy_path_applies_submission(self, api_client, settings):
        settings.POSTMARK_INBOUND_TOKEN = "correct"
        wo = _make_work_order(num_tasks=2)
        tcs = list(wo.task_completions.order_by("task_order"))
        pdf_bytes = generate_work_order_pdf(wo, base_url="http://example.com")
        filled = _fill_checkboxes(pdf_bytes, [f"task_{tc.id}" for tc in tcs])

        resp = api_client.post(
            reverse(self.URL_NAME),
            data=_postmark_payload(filled),
            format="json",
            HTTP_X_POSTMARK_WEBHOOK_TOKEN="correct",
        )
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        assert body["status"] == WorkOrderSubmission.STATUS_APPLIED
        assert body["work_order_id"] == str(wo.id)

        wo.refresh_from_db()
        assert wo.status == WorkOrder.STATUS_COMPLETED

    def test_duplicate_message_id_is_idempotent(self, api_client, settings):
        settings.POSTMARK_INBOUND_TOKEN = "correct"
        wo = _make_work_order(num_tasks=1)
        pdf_bytes = generate_work_order_pdf(wo, base_url="http://example.com")
        payload = _postmark_payload(pdf_bytes, MessageID="dup-abc")

        first = api_client.post(
            reverse(self.URL_NAME),
            data=payload,
            format="json",
            HTTP_X_POSTMARK_WEBHOOK_TOKEN="correct",
        )
        assert first.status_code == status.HTTP_200_OK
        assert WorkOrderSubmission.objects.count() == 1

        second = api_client.post(
            reverse(self.URL_NAME),
            data=payload,
            format="json",
            HTTP_X_POSTMARK_WEBHOOK_TOKEN="correct",
        )
        assert second.status_code == status.HTTP_200_OK
        assert second.json().get("duplicate") is True
        # No second submission row created.
        assert WorkOrderSubmission.objects.count() == 1
