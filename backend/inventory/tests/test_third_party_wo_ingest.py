"""
Tests for the third-party paper-form work-order ingestion pipeline.

Covers:
    - parse_third_party_wo_pdf: pulls TPWO id + vendor/invoice/downtime fields
    - detect_submission_kind:   routes by subject prefix, AcroForm marker, header
    - apply_submission (3PWO):  end-to-end DB effects on ThirdPartyWorkOrder
    - postmark_inbound_work_order: routes 3PWO submissions correctly
"""

import base64
import io
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.urls import reverse
from django.utils.crypto import get_random_string

import pytest
from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from rest_framework import status

from inventory.models import WorkOrderSubmission
from inventory.services.work_order_ingest import (
    apply_submission,
    detect_submission_kind,
    parse_third_party_wo_pdf,
)
from inventory.tests.factories import AssetFactory
from maintenance_orders.models import (
    ThirdPartyWorkOrder,
    ThirdPartyWorkOrderAsset,
    ThirdPartyWorkOrderAttachment,
)
from vendors.models import Vendor

User = get_user_model()
pytestmark = pytest.mark.django_db


# ─────────────────────────────────────────────────────────────────────────────
# PDF construction helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_3pwo_form_pdf(
    tpwo_id: str,
    *,
    asset_id_fields: int = 3,
    text_field_names: tuple[str, ...] = (
        "vendor_name",
        "invoice_total",
        "downtime_start",
        "downtime_end",
        "keyfob_id",
    ),
    include_header: bool = True,
) -> bytes:
    """Build a blank 3PWO paper-form PDF carrying:

      - hidden AcroForm text field ``third_party_work_order_id`` = tpwo_id
      - visible AcroForm text fields the vendor would fill out
      - N visible AcroForm text fields ``asset_id_<n>``
      - the 'Third-Party Work Order' header (optional)

    Returns PDF bytes. Use ``_fill_text_fields`` to populate values.
    """
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)

    if include_header:
        c.setFont("Helvetica-Bold", 14)
        c.drawString(72, 740, "Third-Party Work Order Form")
        c.setFont("Helvetica", 9)
        c.drawString(72, 725, f"Third-Party Work Order ID: {tpwo_id}")

    form = c.acroForm
    # Hidden id field — off-page so it doesn't show up visually.
    form.textfield(
        name="third_party_work_order_id",
        value=tpwo_id,
        x=-100,
        y=-100,
        width=1,
        height=1,
        borderWidth=0,
        forceBorder=False,
        tooltip="third_party_work_order_id",
    )

    y = 690
    for name in text_field_names:
        c.setFont("Helvetica", 9)
        c.drawString(72, y + 4, f"{name}:")
        form.textfield(
            name=name,
            value="",
            x=200,
            y=y,
            width=300,
            height=14,
            borderColor=colors.black,
            borderWidth=0.5,
            tooltip=name,
        )
        y -= 24

    for i in range(1, asset_id_fields + 1):
        c.setFont("Helvetica", 9)
        c.drawString(72, y + 4, f"asset_id_{i}:")
        form.textfield(
            name=f"asset_id_{i}",
            value="",
            x=200,
            y=y,
            width=300,
            height=14,
            borderColor=colors.black,
            borderWidth=0.5,
            tooltip=f"asset_id_{i}",
        )
        y -= 24

    c.showPage()
    c.save()
    return buf.getvalue()


def _fill_text_fields(pdf_bytes: bytes, values: dict[str, str]) -> bytes:
    """Set AcroForm text-field values on an existing PDF and return new bytes."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter(clone_from=reader)
    for page in writer.pages:
        writer.update_page_form_field_values(page, values)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _png_bytes(color=(255, 0, 0), size=(200, 200)) -> bytes:
    """Generate a simple PNG so the parser sees an embedded image (photo)."""
    from PIL import Image

    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _attach_image_to_pdf(pdf_bytes: bytes, image_bytes: bytes) -> bytes:
    """Stamp a raster image onto page 1 of the PDF (for photo-evidence tests)."""
    overlay_buf = io.BytesIO()
    overlay = canvas.Canvas(overlay_buf, pagesize=letter)
    overlay.drawImage(ImageReader(io.BytesIO(image_bytes)), 72, 72, width=144, height=144)
    overlay.showPage()
    overlay.save()

    base = PdfReader(io.BytesIO(pdf_bytes))
    overlay_reader = PdfReader(io.BytesIO(overlay_buf.getvalue()))
    writer = PdfWriter(clone_from=base)
    writer.pages[0].merge_page(overlay_reader.pages[0])
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def vendor(db):
    return Vendor.objects.create(name="ACME HVAC", vendor_kind=Vendor.KIND_HVAC)


@pytest.fixture
def asset(db):
    return AssetFactory(asset_tag=f"3PWO-{get_random_string(8).upper()}")


@pytest.fixture
def asset2(db):
    return AssetFactory(asset_tag=f"3PWO-{get_random_string(8).upper()}")


@pytest.fixture
def staff_user(db):
    return User.objects.create_user(
        username="3pwo-staff",
        email="3pwo-staff@example.com",
        password=get_random_string(24),
        is_staff=True,
    )


@pytest.fixture
def tpwo(db, vendor, asset):
    return ThirdPartyWorkOrder.objects.create(
        title="HVAC repair",
        asset=asset,
        vendor=vendor,
        nte_amount=Decimal("1000.00"),
        status=ThirdPartyWorkOrder.STATUS_SCHEDULED,
    )


# ─────────────────────────────────────────────────────────────────────────────
# parse_third_party_wo_pdf
# ─────────────────────────────────────────────────────────────────────────────


class TestParseThirdPartyWoPdf:
    def test_extracts_id_from_acroform_field(self, tpwo):
        pdf_bytes = _make_3pwo_form_pdf(str(tpwo.id))

        parsed = parse_third_party_wo_pdf(pdf_bytes)

        assert parsed["third_party_work_order_id"] == str(tpwo.id)
        assert parsed["text_fields"] == {}
        assert parsed["asset_ids"] == []

    def test_extracts_filled_text_fields(self, tpwo, asset, asset2):
        pdf_bytes = _make_3pwo_form_pdf(str(tpwo.id))
        filled = _fill_text_fields(
            pdf_bytes,
            {
                "vendor_name": "ACME HVAC",
                "invoice_total": "$1,234.56",
                "downtime_start": "2026-04-28 09:00",
                "downtime_end": "2026-04-28 13:30",
                "keyfob_id": "FOB-7788",
                "asset_id_1": str(asset.id),
                "asset_id_2": str(asset2.id),
            },
        )

        parsed = parse_third_party_wo_pdf(filled)

        assert parsed["third_party_work_order_id"] == str(tpwo.id)
        assert parsed["text_fields"]["vendor_name"] == "ACME HVAC"
        assert parsed["invoice_total"] == "1234.56"
        assert parsed["downtime_start"] is not None
        assert parsed["downtime_end"] is not None
        assert str(asset.id) in parsed["asset_ids"]
        assert str(asset2.id) in parsed["asset_ids"]

    def test_missing_id_returns_none_with_errors(self):
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        c.drawString(72, 720, "No identifier here.")
        c.showPage()
        c.save()

        parsed = parse_third_party_wo_pdf(buf.getvalue())
        assert parsed["third_party_work_order_id"] is None
        assert parsed["extraction_errors"]


# ─────────────────────────────────────────────────────────────────────────────
# detect_submission_kind
# ─────────────────────────────────────────────────────────────────────────────


class TestDetectSubmissionKind:
    def test_subject_prefix_routes_to_third_party(self, tpwo):
        # Subject prefix wins even if PDF has no markers.
        plain_pdf = io.BytesIO()
        c = canvas.Canvas(plain_pdf, pagesize=letter)
        c.drawString(72, 720, "Anything")
        c.showPage()
        c.save()

        kind = detect_submission_kind(plain_pdf.getvalue(), subject="[3PWO] Vendor invoice")
        assert kind == WorkOrderSubmission.Kind.THIRD_PARTY_WO

    def test_acroform_marker_routes_to_third_party(self, tpwo):
        pdf_bytes = _make_3pwo_form_pdf(str(tpwo.id), include_header=False)
        kind = detect_submission_kind(pdf_bytes, subject="Completed work")
        assert kind == WorkOrderSubmission.Kind.THIRD_PARTY_WO

    def test_header_text_routes_to_third_party(self):
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        c.drawString(72, 720, "Third-Party Work Order Receipt")
        c.showPage()
        c.save()
        kind = detect_submission_kind(buf.getvalue(), subject="WO from vendor")
        assert kind == WorkOrderSubmission.Kind.THIRD_PARTY_WO

    def test_default_to_pm_completion(self):
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        c.drawString(72, 720, "Boring inspection report.")
        c.showPage()
        c.save()
        kind = detect_submission_kind(buf.getvalue(), subject="Completed monthly check")
        assert kind == WorkOrderSubmission.Kind.PM_COMPLETION


# ─────────────────────────────────────────────────────────────────────────────
# apply_submission for KIND_THIRD_PARTY_WO
# ─────────────────────────────────────────────────────────────────────────────


class TestApplyThirdPartySubmission:
    def test_populates_fields_and_advances_to_validated(self, tpwo, asset, asset2, staff_user):
        pdf_bytes = _make_3pwo_form_pdf(str(tpwo.id))
        filled = _fill_text_fields(
            pdf_bytes,
            {
                "vendor_name": "ACME HVAC",
                "invoice_total": "987.65",
                "downtime_start": "2026-04-28 08:00",
                "downtime_end": "2026-04-28 10:30",
                "keyfob_id": "FOB-1234",
                "asset_id_1": str(asset.id),
                "asset_id_2": str(asset2.id),
            },
        )

        submission = WorkOrderSubmission(
            kind=WorkOrderSubmission.Kind.THIRD_PARTY_WO,
            submitted_by=staff_user,
        )
        submission.attachment.save("3pwo.pdf", ContentFile(filled), save=False)
        submission.save()

        apply_submission(submission)

        submission.refresh_from_db()
        tpwo.refresh_from_db()
        assert submission.status == WorkOrderSubmission.Status.APPLIED
        assert submission.third_party_work_order_id == tpwo.id
        assert submission.work_order_id is None
        assert tpwo.status == ThirdPartyWorkOrder.STATUS_VALIDATED
        assert tpwo.actual_invoice_total == Decimal("987.65")
        assert tpwo.keyfob_id == "FOB-1234"
        assert tpwo.downtime_start is not None
        assert tpwo.downtime_end is not None
        assert tpwo.total_downtime == timedelta(hours=2, minutes=30)

        # Both reported assets are linked.
        linked = set(
            ThirdPartyWorkOrderAsset.objects.filter(work_order=tpwo).values_list(
                "asset_id", flat=True
            )
        )
        assert {asset.id, asset2.id} <= linked

        # The original PDF is attached as KIND_PAPER_FORM.
        assert ThirdPartyWorkOrderAttachment.objects.filter(
            work_order=tpwo,
            kind=ThirdPartyWorkOrderAttachment.KIND_PAPER_FORM,
        ).exists()

    def test_unknown_tpwo_id_marks_failed(self, staff_user):
        # Generate a PDF with a real-looking UUID that isn't in the DB.
        import uuid

        bogus = str(uuid.uuid4())
        pdf_bytes = _make_3pwo_form_pdf(bogus)

        submission = WorkOrderSubmission(
            kind=WorkOrderSubmission.Kind.THIRD_PARTY_WO,
            submitted_by=staff_user,
        )
        submission.attachment.save("3pwo.pdf", ContentFile(pdf_bytes), save=False)
        submission.save()

        apply_submission(submission)
        submission.refresh_from_db()

        assert submission.status == WorkOrderSubmission.Status.FAILED
        assert bogus in submission.parse_error
        assert submission.third_party_work_order_id is None

    def test_already_validated_wo_keeps_state_but_updates_data(self, tpwo, staff_user):
        tpwo.status = ThirdPartyWorkOrder.STATUS_FINANCIAL_REVIEW
        tpwo.save(update_fields=["status"])

        pdf_bytes = _make_3pwo_form_pdf(str(tpwo.id))
        filled = _fill_text_fields(pdf_bytes, {"keyfob_id": "FOB-9999"})

        submission = WorkOrderSubmission(
            kind=WorkOrderSubmission.Kind.THIRD_PARTY_WO,
            submitted_by=staff_user,
        )
        submission.attachment.save("3pwo.pdf", ContentFile(filled), save=False)
        submission.save()

        apply_submission(submission)
        tpwo.refresh_from_db()

        # Status not regressed.
        assert tpwo.status == ThirdPartyWorkOrder.STATUS_FINANCIAL_REVIEW
        assert tpwo.keyfob_id == "FOB-9999"

    def test_embedded_image_becomes_photo_attachment(self, tpwo, staff_user):
        pdf_bytes = _make_3pwo_form_pdf(str(tpwo.id))
        with_photo = _attach_image_to_pdf(pdf_bytes, _png_bytes())

        submission = WorkOrderSubmission(
            kind=WorkOrderSubmission.Kind.THIRD_PARTY_WO,
            submitted_by=staff_user,
        )
        submission.attachment.save("3pwo.pdf", ContentFile(with_photo), save=False)
        submission.save()

        apply_submission(submission)

        photos = ThirdPartyWorkOrderAttachment.objects.filter(
            work_order=tpwo, kind=ThirdPartyWorkOrderAttachment.KIND_PHOTO
        )
        assert photos.count() >= 1


# ─────────────────────────────────────────────────────────────────────────────
# Postmark webhook: 3PWO routing
# ─────────────────────────────────────────────────────────────────────────────


class TestPostmarkInbound3PWO:
    URL_NAME = "postmark-inbound-work-order"

    def _payload(self, pdf_bytes: bytes, **overrides) -> dict:
        payload = {
            "MessageID": "tpwo-msg-1",
            "From": "vendor@example.com",
            "FromFull": {"Email": "vendor@example.com", "Name": "Vendor"},
            "Subject": "[3PWO] Invoice for HVAC repair",
            "Attachments": [
                {
                    "Name": "3pwo.pdf",
                    "ContentType": "application/pdf",
                    "Content": base64.b64encode(pdf_bytes).decode("ascii"),
                    "ContentLength": len(pdf_bytes),
                }
            ],
        }
        payload.update(overrides)
        return payload

    def test_routes_third_party_submission_and_validates(self, api_client, settings, tpwo):
        settings.POSTMARK_INBOUND_TOKEN = "correct"
        pdf_bytes = _make_3pwo_form_pdf(str(tpwo.id))
        filled = _fill_text_fields(
            pdf_bytes,
            {
                "vendor_name": "ACME HVAC",
                "invoice_total": "500.00",
                "downtime_start": "2026-04-28 09:00",
                "downtime_end": "2026-04-28 10:00",
                "keyfob_id": "FOB-555",
            },
        )

        resp = api_client.post(
            reverse(self.URL_NAME),
            data=self._payload(filled),
            format="json",
            HTTP_X_POSTMARK_WEBHOOK_TOKEN="correct",
        )
        assert resp.status_code == status.HTTP_200_OK

        body = resp.json()
        assert body["kind"] == WorkOrderSubmission.Kind.THIRD_PARTY_WO
        assert body["status"] == WorkOrderSubmission.Status.APPLIED
        assert body["third_party_work_order_id"] == str(tpwo.id)
        assert body["work_order_id"] is None

        tpwo.refresh_from_db()
        assert tpwo.status == ThirdPartyWorkOrder.STATUS_VALIDATED
        assert tpwo.actual_invoice_total == Decimal("500.00")
