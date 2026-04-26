"""
Tests for the multi-method Work Order ID extraction (AC for oms-z07).

Covers the fallback chain: AcroForm field → embedded QR code → plain-text
regex. Each "Given/When/Then" block in the bug's ACs maps to one test below.

Real-world failure mode reproduced here: a printed work order is filled by
hand and scanned back to PDF. Every page becomes a single embedded image
with no extractable text. pypdf returns "" for page.extract_text(), but the
QR code embedded on the page is still visually scannable — and so the
extractor must reach it through the QR fallback.
"""

import io
import uuid
from datetime import date, timedelta

from django.core.files.base import ContentFile

import pytest
import qrcode
from PIL import Image
from pypdf import PdfReader
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas as rl_canvas

from inventory.models import (
    MaintenanceItem,
    MaintenanceTask,
    WorkOrder,
    WorkOrderSubmission,
    WorkOrderTaskCompletion,
)
from inventory.services.work_order_ingest import (
    apply_submission,
    extract_work_order_id,
    parse_work_order_pdf,
)
from inventory.tests.factories import AssetFactory
from inventory.utils.work_order_pdf import generate_work_order_pdf

pytestmark = pytest.mark.django_db


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — build minimal PDFs that carry exactly one of the three markers,
# so each fallback step can be exercised in isolation.
# ─────────────────────────────────────────────────────────────────────────────


def _qr_png_bytes(payload: str, box_size: int = 10) -> bytes:
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=4,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _pdf_with_acroform_only(wo_id: str) -> bytes:
    """A bare PDF whose only ID marker is a 'work_order_id' AcroForm field."""
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=letter)
    # Off-page invisible field — same shape the real generator emits.
    c.acroForm.textfield(
        name="work_order_id",
        value=wo_id,
        x=-100,
        y=-100,
        width=1,
        height=1,
        borderWidth=0,
        forceBorder=False,
        fontSize=1,
        tooltip="work_order_id",
    )
    c.drawString(72, 720, "Form-only PDF (no visible WO id text, no QR).")
    c.save()
    return buf.getvalue()


def _pdf_with_qr_only(wo_id: str) -> bytes:
    """A PDF whose only ID marker is an embedded QR code."""
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=letter)
    qr_png = _qr_png_bytes(f"http://example.com/maintenance/work-orders/{wo_id}")
    qr_io = io.BytesIO(qr_png)
    c.drawImage(
        rl_canvas.ImageReader(qr_io),
        x=4 * inch,
        y=8 * inch,
        width=2 * inch,
        height=2 * inch,
    )
    c.drawString(72, 100, "QR-only PDF (no visible WO id text, no AcroForm field).")
    c.save()
    return buf.getvalue()


def _pdf_with_visible_text_only(wo_id: str) -> bytes:
    """A PDF whose only ID marker is the plain-text 'Work Order ID: <uuid>' line."""
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=letter)
    c.drawString(72, 720, f"Work Order ID: {wo_id}")
    c.save()
    return buf.getvalue()


def _pdf_with_all_three(wo_id_acroform: str, wo_id_qr: str, wo_id_text: str) -> bytes:
    """A PDF carrying three different UUIDs across the three markers — used
    to verify the fallback order picks the AcroForm value first."""
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=letter)
    c.acroForm.textfield(
        name="work_order_id",
        value=wo_id_acroform,
        x=-100,
        y=-100,
        width=1,
        height=1,
        borderWidth=0,
        forceBorder=False,
        fontSize=1,
        tooltip="work_order_id",
    )
    qr_png = _qr_png_bytes(f"http://example.com/maintenance/work-orders/{wo_id_qr}")
    qr_io = io.BytesIO(qr_png)
    c.drawImage(
        rl_canvas.ImageReader(qr_io),
        x=4 * inch,
        y=8 * inch,
        width=2 * inch,
        height=2 * inch,
    )
    c.drawString(72, 100, f"Work Order ID: {wo_id_text}")
    c.save()
    return buf.getvalue()


def _pdf_with_no_markers() -> bytes:
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=letter)
    c.drawString(72, 720, "Nothing identifying on this page.")
    c.save()
    return buf.getvalue()


def _make_work_order(num_tasks: int = 2) -> WorkOrder:
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
                is_required=True,
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


# ─────────────────────────────────────────────────────────────────────────────
# AC 6: six required test cases for extraction
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
class TestExtractWorkOrderId:
    def test_extracts_id_from_acroform_field(self):
        wo_id = str(uuid.uuid4())
        reader = PdfReader(io.BytesIO(_pdf_with_acroform_only(wo_id)))
        got, errors = extract_work_order_id(reader)
        assert got == wo_id
        assert errors == []  # acroform succeeded on first try, nothing to surface

    def test_extracts_id_from_qr_code(self):
        wo_id = str(uuid.uuid4())
        reader = PdfReader(io.BytesIO(_pdf_with_qr_only(wo_id)))
        got, errors = extract_work_order_id(reader)
        assert got == wo_id
        # AcroForm tried first and reported a clear miss.
        assert any("acroform" in e for e in errors)

    def test_extracts_id_from_visible_text(self):
        wo_id = str(uuid.uuid4())
        reader = PdfReader(io.BytesIO(_pdf_with_visible_text_only(wo_id)))
        got, errors = extract_work_order_id(reader)
        assert got == wo_id
        # AcroForm + QR both reported misses before text won.
        assert any("acroform" in e for e in errors)
        assert any("qr" in e for e in errors)

    def test_fallback_order_acroform_then_qr_then_text(self):
        """Three different UUIDs across the three markers — AcroForm wins."""
        acro_id = str(uuid.uuid4())
        qr_id = str(uuid.uuid4())
        text_id = str(uuid.uuid4())
        assert len({acro_id, qr_id, text_id}) == 3
        reader = PdfReader(io.BytesIO(_pdf_with_all_three(acro_id, qr_id, text_id)))
        got, errors = extract_work_order_id(reader)
        assert got == acro_id
        assert errors == []

    def test_no_id_returns_clear_error(self):
        reader = PdfReader(io.BytesIO(_pdf_with_no_markers()))
        got, errors = extract_work_order_id(reader)
        assert got is None
        # Each failed method should have surfaced a message.
        joined = " | ".join(errors)
        assert "acroform" in joined
        assert "qr" in joined
        assert "text" in joined

    def test_real_world_template_roundtrip(self):
        """
        Acceptance test (AC 4): generate a PDF via the real template, feed
        it back into the extractor, assert the original WO ID round-trips.
        """
        wo = _make_work_order(num_tasks=2)
        pdf_bytes = generate_work_order_pdf(wo, base_url="http://example.com")

        parsed = parse_work_order_pdf(pdf_bytes)

        assert parsed["work_order_id"] == str(wo.id)
        assert parsed["extraction_errors"] == []
        # Existing checkbox plumbing is still wired up alongside the new field.
        for tc in wo.task_completions.all():
            assert str(tc.id) in parsed["task_checks"]


# ─────────────────────────────────────────────────────────────────────────────
# AC 5: failure path returns a structured error listing what was tried
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
class TestApplySubmissionFailureReporting:
    def test_failure_lists_all_attempted_methods(self):
        submission = WorkOrderSubmission()
        submission.attachment.save(
            "blank.pdf",
            ContentFile(_pdf_with_no_markers()),
            save=False,
        )
        submission.save()

        apply_submission(submission)
        submission.refresh_from_db()

        assert submission.status == WorkOrderSubmission.STATUS_FAILED
        # Backwards-compatible prefix preserved so existing tests / dashboards
        # still match against "Work Order ID":
        assert "Work Order ID" in submission.parse_error
        # Plus the specific methods tried:
        assert "acroform" in submission.parse_error
        assert "qr" in submission.parse_error
        assert "text" in submission.parse_error


# ─────────────────────────────────────────────────────────────────────────────
# AC 1 + AC 7: end-to-end with a real template, including the image-based
# scanned-PDF case that is the actual user-reported failure mode.
# ─────────────────────────────────────────────────────────────────────────────


def _flatten_to_image_pdf(pdf_bytes: bytes, dpi: int = 150) -> bytes:
    """
    Approximate a "user printed it, filled it, then scanned it back" by
    extracting every embedded image from the PDF and re-wrapping them as a
    pages-of-images PDF. Strips text and form fields entirely — this is the
    pathological case the original parser couldn't handle.
    """
    src = PdfReader(io.BytesIO(pdf_bytes))
    images: list[Image.Image] = []
    for page in src.pages:
        for img in page.images:
            try:
                pil = Image.open(io.BytesIO(img.data)).convert("RGB")
                images.append(pil)
            except Exception:
                continue
    assert images, "fixture failure: source PDF carried no embedded images"

    out = io.BytesIO()
    images[0].save(out, format="PDF", save_all=True, append_images=images[1:], resolution=dpi)
    return out.getvalue()


@pytest.mark.integration
class TestImageOnlyPdfIngest:
    """The user-reported scenario: a scanned PDF where text extraction is
    empty. The QR fallback must rescue it."""

    def test_scanned_pdf_extracts_via_qr(self):
        wo = _make_work_order(num_tasks=1)
        original = generate_work_order_pdf(wo, base_url="http://example.com")
        scanned = _flatten_to_image_pdf(original)

        # Sanity-check the fixture: text extraction returns nothing useful.
        reader = PdfReader(io.BytesIO(scanned))
        text = "".join((p.extract_text() or "") for p in reader.pages)
        assert "Work Order ID:" not in text

        parsed = parse_work_order_pdf(scanned)
        assert parsed["work_order_id"] == str(wo.id)

    def test_scanned_pdf_apply_submission_succeeds(self):
        wo = _make_work_order(num_tasks=1)
        original = generate_work_order_pdf(wo, base_url="http://example.com")
        scanned = _flatten_to_image_pdf(original)

        submission = WorkOrderSubmission()
        submission.attachment.save("scanned.pdf", ContentFile(scanned), save=False)
        submission.save()

        apply_submission(submission)
        submission.refresh_from_db()

        assert submission.status == WorkOrderSubmission.STATUS_APPLIED
        assert submission.work_order_id == wo.id
