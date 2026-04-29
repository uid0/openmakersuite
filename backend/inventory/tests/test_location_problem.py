"""Tests for LocationProblem flows (oms-sd1).

Covers:
    - reporting a problem against a Location
    - promoting a LocationProblem to a standard PM WorkOrder
    - promoting a LocationProblem to a ThirdPartyWorkOrder (with location FK)
    - the unioned active maintenance list endpoint surfaces all kinds
    - Postmark/manual PDF ingestion creates a LocationProblem
"""

import io
import uuid

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.utils.crypto import get_random_string

import pytest
from PIL import Image
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from rest_framework.test import APIClient

from inventory.models import (
    Asset,
    Category,
    Location,
    LocationProblem,
    MaintenanceItem,
    WorkOrder,
    WorkOrderSubmission,
)
from inventory.services.work_order_ingest import (
    _apply_location_problem_submission,
    detect_submission_kind,
)
from vendors.models import Vendor

User = get_user_model()


@pytest.fixture
def admin_client(db):
    user = User.objects.create_user(
        username="lp-admin",
        email="lpa@example.com",
        password=get_random_string(24),
        is_staff=True,
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client, user


@pytest.fixture
def location(db):
    return Location.objects.create(name="Bathroom 1", description="ground floor")


@pytest.fixture
def vendor(db):
    return Vendor.objects.create(name="ACME Plumbing", vendor_kind=Vendor.KIND_PLUMBING)


@pytest.fixture
def category(db):
    return Category.objects.create(name="building")


@pytest.fixture
def asset(db, category, location):
    return Asset.objects.create(
        name="Building",
        category=category,
        location=location,
        asset_tag=f"BLD-{uuid.uuid4().hex[:6].upper()}",
    )


@pytest.fixture
def maintenance_item(db, asset):
    return MaintenanceItem.objects.create(
        asset=asset,
        title="As-needed building repair",
        description="Catch-all PM item for promoted location problems",
    )


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), color=(255, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


def _build_location_problem_pdf(
    *,
    location_id: int,
    severity: str = "high",
    description: str = "Leak in the wall by the window.",
) -> bytes:
    """Build a minimal Location Problem Report PDF via reportlab.

    The form contains the literal "Location Problem Report" header,
    a "Location ID:" line, "Severity:" line, and "Description:" line so
    the text-based extractors all match.
    """
    out = io.BytesIO()
    c = canvas.Canvas(out, pagesize=letter)
    c.setFont("Helvetica", 14)
    c.drawString(72, 720, "Location Problem Report")
    c.setFont("Helvetica", 12)
    c.drawString(72, 690, f"Location ID: {location_id}")
    c.drawString(72, 670, f"Severity: {severity}")
    c.drawString(72, 650, f"Description: {description}")
    c.showPage()
    c.save()
    return out.getvalue()


@pytest.mark.django_db
class TestLocationProblemReporting:
    def test_create_location_problem(self, admin_client, location):
        client, _ = admin_client
        resp = client.post(
            f"/api/inventory/locations/{location.id}/report_problem/",
            {
                "description": "Light is flickering and buzzing.",
                "severity": "high",
            },
            format="multipart",
        )
        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert body["location"] == location.id
        assert body["status"] == LocationProblem.REPORTED
        assert body["severity"] == "high"
        assert LocationProblem.objects.filter(location=location).count() == 1

    def test_report_problem_anonymous_allowed(self, db, location):
        client = APIClient()
        resp = client.post(
            f"/api/inventory/locations/{location.id}/report_problem/",
            {"description": "Door wont latch."},
            format="multipart",
        )
        assert resp.status_code == 201, resp.content
        problem = LocationProblem.objects.get()
        assert problem.reported_by == ""
        assert problem.severity == LocationProblem.SEVERITY_MEDIUM

    def test_report_problem_requires_description(self, admin_client, location):
        client, _ = admin_client
        resp = client.post(
            f"/api/inventory/locations/{location.id}/report_problem/",
            {"severity": "low"},
            format="multipart",
        )
        assert resp.status_code == 400


@pytest.mark.django_db
class TestPromoteToWorkOrder:
    def test_promote_to_workorder_links_back(self, admin_client, location, maintenance_item):
        client, _ = admin_client
        problem = LocationProblem.objects.create(
            location=location,
            description="Drain backed up",
            severity=LocationProblem.SEVERITY_HIGH,
        )
        resp = client.post(
            f"/api/inventory/location-problems/{problem.id}/promote-standard/",
            {"maintenance_item": str(maintenance_item.id)},
            format="json",
        )
        assert resp.status_code == 201, resp.content

        problem.refresh_from_db()
        assert problem.work_order is not None
        wo = problem.work_order
        assert problem.status == LocationProblem.IN_PROGRESS
        assert wo.maintenance_item_id == maintenance_item.id
        assert wo.notes == "Drain backed up"
        # Reverse relation: WorkOrder -> location_problems
        assert problem in wo.location_problems.all()

    def test_promote_to_workorder_rejects_double(self, admin_client, location, maintenance_item):
        client, _ = admin_client
        problem = LocationProblem.objects.create(
            location=location,
            description="x",
            work_order=WorkOrder.objects.create(maintenance_item=maintenance_item),
        )
        resp = client.post(
            f"/api/inventory/location-problems/{problem.id}/promote-standard/",
            {"maintenance_item": str(maintenance_item.id)},
            format="json",
        )
        assert resp.status_code == 400


@pytest.mark.django_db
class TestPromoteToThirdPartyWorkOrder:
    def test_promote_to_third_party_workorder_links_back(self, admin_client, location, vendor):
        client, _ = admin_client
        problem = LocationProblem.objects.create(
            location=location,
            description="HVAC making grinding noise.",
            severity=LocationProblem.SEVERITY_URGENT,
        )
        # Attach a fake photo so the copy path is exercised.
        problem.photo.save("photo.png", ContentFile(_png_bytes()), save=True)

        resp = client.post(
            f"/api/inventory/location-problems/{problem.id}/promote-third-party/",
            {
                "vendor": str(vendor.id),
                "title": "HVAC grinding investigation",
            },
            format="json",
        )
        assert resp.status_code == 201, resp.content

        problem.refresh_from_db()
        tpwo = problem.third_party_work_order
        assert tpwo is not None
        assert tpwo.title == "HVAC grinding investigation"
        assert tpwo.location_id == location.id
        assert tpwo.notes == "HVAC making grinding noise."
        assert problem.status == LocationProblem.IN_PROGRESS
        # Photo was copied to TPWO attachments
        assert tpwo.attachments.filter(kind="photo").count() == 1

    def test_promote_third_party_requires_vendor_and_title(self, admin_client, location):
        client, _ = admin_client
        problem = LocationProblem.objects.create(location=location, description="x")
        resp = client.post(
            f"/api/inventory/location-problems/{problem.id}/promote-third-party/",
            {},
            format="json",
        )
        assert resp.status_code == 400


@pytest.mark.django_db
class TestUnionedActiveMaintenanceList:
    def test_includes_open_problems(
        self,
        admin_client,
        location,
        asset,
        maintenance_item,
    ):
        client, _ = admin_client

        # Open WO
        wo = WorkOrder.objects.create(maintenance_item=maintenance_item)

        # Open AssetProblem
        from inventory.models import AssetProblem

        ap = AssetProblem.objects.create(asset=asset, description="Squeak")

        # Open LocationProblem (no WO promotion yet)
        lp = LocationProblem.objects.create(
            location=location, description="Tile cracked", severity="medium"
        )

        # Closed LocationProblem (should NOT appear)
        LocationProblem.objects.create(
            location=location,
            description="Old issue",
            status=LocationProblem.CLOSED,
        )

        resp = client.get("/api/inventory/maintenance/active/")
        assert resp.status_code == 200, resp.content
        body = resp.json()
        kinds = {(row["kind"], row["id"]) for row in body["results"]}
        assert ("work_order", str(wo.id)) in kinds
        assert ("asset_problem", str(ap.id)) in kinds
        assert ("location_problem", str(lp.id)) in kinds
        assert all(
            row["kind"] != "location_problem" or row["status"] != "closed"
            for row in body["results"]
        )


@pytest.mark.django_db
class TestPaperFormIngestion:
    def test_detect_kind_by_subject(self, location):
        # Even with empty PDF bytes the subject should route correctly.
        kind = detect_submission_kind(b"", subject="[LOCPROB] leaky faucet")
        assert kind == WorkOrderSubmission.KIND_LOCATION_PROBLEM

    def test_detect_kind_by_header_text(self, location):
        pdf = _build_location_problem_pdf(location_id=location.id)
        assert detect_submission_kind(pdf) == WorkOrderSubmission.KIND_LOCATION_PROBLEM

    def test_paper_form_ingestion_creates_location_problem(self, location):
        pdf_bytes = _build_location_problem_pdf(
            location_id=location.id,
            severity="urgent",
            description="Sink overflowing - water on floor",
        )
        submission = WorkOrderSubmission.objects.create(
            kind=WorkOrderSubmission.KIND_LOCATION_PROBLEM,
            from_email="reporter@example.com",
            subject="[LOCPROB] urgent leak",
        )
        submission.attachment.save("form.pdf", ContentFile(pdf_bytes), save=True)

        result = _apply_location_problem_submission(submission, pdf_bytes)
        assert result.status == WorkOrderSubmission.STATUS_APPLIED
        problem = result.location_problem
        assert problem is not None
        assert problem.location_id == location.id
        assert problem.reported_by == "reporter@example.com"
        assert problem.paper_form_attachment.name.endswith(".pdf")

    def test_paper_form_ingestion_unknown_location_fails(self, db):
        pdf_bytes = _build_location_problem_pdf(location_id=999999)
        submission = WorkOrderSubmission.objects.create(
            kind=WorkOrderSubmission.KIND_LOCATION_PROBLEM,
        )
        submission.attachment.save("f.pdf", ContentFile(pdf_bytes), save=True)
        result = _apply_location_problem_submission(submission, pdf_bytes)
        assert result.status == WorkOrderSubmission.STATUS_FAILED
        assert "999999" in result.parse_error
