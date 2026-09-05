"""A reported asset problem becomes real, completable work (op-olai).

Reporting a problem used to produce a report and nothing else: no work order,
no way to close the loop except editing the report by hand. This is the promote
path the sibling ``LocationProblem`` already had — in-house (a corrective
``WorkOrder`` anchored straight to the asset, no PM template) or vendor (a
``ThirdPartyWorkOrder``) — plus the auto-resolve that closes the report when
either kind of work order finishes.

Each test maps to one acceptance criterion on the bead: promote-standard,
promote-third-party, resolve, the two auto-resolve hooks, and the dashboard
feed no longer double-counting a promoted report.
"""

import io
import uuid
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from django.utils.crypto import get_random_string

import pytest
from PIL import Image
from rest_framework.test import APIClient

from inventory.models import (
    AssetProblem,
    AssetProblemPhoto,
    WorkOrder,
    WorkOrderValidation,
)
from inventory.tests.factories import AssetFactory
from maintenance_orders import transitions as t
from maintenance_orders.models import ThirdPartyWorkOrder, ThirdPartyWorkOrderAttachment
from vendors.models import Vendor

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture(autouse=True)
def _isolated_media(settings, tmp_path):
    """Keep uploaded test images out of the tracked backend/media tree."""
    settings.MEDIA_ROOT = str(tmp_path)


@pytest.fixture
def staff(db):
    return User.objects.create_user(
        username="ap-staff",
        email="ap-staff@example.com",
        password=get_random_string(24),
        is_staff=True,
    )


@pytest.fixture
def staff_client(staff):
    client = APIClient()
    client.force_authenticate(user=staff)
    return client


@pytest.fixture
def asset(db):
    return AssetFactory(asset_tag=f"AP-{uuid.uuid4().hex[:8].upper()}")


@pytest.fixture
def vendor(db):
    return Vendor.objects.create(name="ACME Repair", vendor_kind=Vendor.KIND_HVAC)


@pytest.fixture
def problem(asset):
    return AssetProblem.objects.create(
        asset=asset,
        description="Spindle screams above 8000 rpm",
        reported_by="member1",
    )


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), color=(255, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


def _attach_photo(problem, caption="") -> AssetProblemPhoto:
    photo = AssetProblemPhoto(problem=problem, caption=caption)
    photo.image.save("report.png", ContentFile(_png_bytes()), save=True)
    return photo


def _complete_via_api(client, work_order, *, notes=None):
    """Complete a work order the way the web app does, past the AC-3 gate."""
    WorkOrderValidation.objects.create(
        work_order=work_order,
        electrical_acknowledged=True,
        loto_acknowledged=True,
        required_fields_acknowledged=True,
    )
    payload = {"status": WorkOrder.Status.COMPLETED}
    if notes is not None:
        payload["notes"] = notes
    return client.patch(
        f"/api/inventory/work-orders/{work_order.id}/",
        payload,
        format="json",
    )


def _ready_to_close(tpwo, *, user):
    """Fast-forward a vendor WO to the last gate before closure.

    The 7-step state machine is exercised in ``maintenance_orders`` tests; here
    only the closure step matters, so jump to financial review and satisfy the
    invoice + FSR requirement.
    """
    tpwo.status = ThirdPartyWorkOrder.STATUS_FINANCIAL_REVIEW
    tpwo.actual_invoice_total = Decimal("250.00")
    tpwo.variance_status = "auto_approved"
    tpwo.save(update_fields=["status", "actual_invoice_total", "variance_status"])
    for kind in (
        ThirdPartyWorkOrderAttachment.KIND_INVOICE,
        ThirdPartyWorkOrderAttachment.KIND_FSR,
    ):
        ThirdPartyWorkOrderAttachment.objects.create(
            work_order=tpwo,
            kind=kind,
            file=SimpleUploadedFile(f"{kind}.pdf", b"%PDF", content_type="application/pdf"),
            uploaded_by=user,
        )


class TestPromoteToStandardWorkOrder:
    def test_creates_corrective_work_order_anchored_to_the_asset(
        self, staff_client, staff, problem, asset
    ):
        _attach_photo(problem, caption="burn mark")

        resp = staff_client.post(
            f"/api/inventory/asset-problems/{problem.id}/promote-standard/",
            {},
            format="json",
        )
        assert resp.status_code == 201, resp.content

        problem.refresh_from_db()
        wo = problem.work_order
        assert wo is not None
        # The point of the corrective shape: an asset, no PM template.
        assert wo.maintenance_item_id is None
        assert wo.asset_id == asset.id
        assert wo.notes == problem.description
        assert wo.assigned_to_id == staff.id
        assert problem.status == AssetProblem.Status.IN_PROGRESS
        # Reverse relation the auto-resolve hooks walk.
        assert problem in wo.asset_problems.all()
        # The reporter's photo came along.
        assert wo.photos.count() == 1
        assert wo.photos.get().caption == "burn mark"

    def test_response_exposes_the_new_work_order(self, staff_client, problem):
        resp = staff_client.post(
            f"/api/inventory/asset-problems/{problem.id}/promote-standard/",
            {},
            format="json",
        )
        problem.refresh_from_db()
        body = resp.json()
        assert body["work_order"] == str(problem.work_order_id)
        assert body["work_order_short_id"] == problem.work_order.short_id
        assert body["third_party_work_order"] is None
        assert body["third_party_work_order_short_id"] is None

    def test_work_order_is_titled_by_the_reported_problem(self, staff_client, problem):
        staff_client.post(
            f"/api/inventory/asset-problems/{problem.id}/promote-standard/",
            {},
            format="json",
        )
        problem.refresh_from_db()
        # No PM template to name it, so it falls back to the report.
        assert problem.work_order.display_title == problem.description[:60]

    def test_second_promotion_is_rejected(self, staff_client, problem):
        first = staff_client.post(
            f"/api/inventory/asset-problems/{problem.id}/promote-standard/",
            {},
            format="json",
        )
        assert first.status_code == 201, first.content

        second = staff_client.post(
            f"/api/inventory/asset-problems/{problem.id}/promote-standard/",
            {},
            format="json",
        )
        assert second.status_code == 400
        assert WorkOrder.objects.filter(asset_problems=problem).count() == 1

    def test_requires_authentication(self, problem):
        resp = APIClient().post(
            f"/api/inventory/asset-problems/{problem.id}/promote-standard/",
            {},
            format="json",
        )
        assert resp.status_code in (401, 403)
        problem.refresh_from_db()
        assert problem.work_order_id is None

    def test_materializes_loto_completion_rows(self, staff_client, problem, asset):
        """A corrective WO prints and scans back like a generated one."""
        from loto.models import AssetEnergySource

        AssetEnergySource.objects.create(
            asset=asset,
            source_type=AssetEnergySource.SOURCE_ELECTRICAL,
            magnitude="240V",
            isolation_point="Panel B, breaker 12",
        )
        staff_client.post(
            f"/api/inventory/asset-problems/{problem.id}/promote-standard/",
            {},
            format="json",
        )
        problem.refresh_from_db()
        assert problem.work_order.loto_completions.count() == 1


class TestPromoteToThirdPartyWorkOrder:
    def test_creates_vendor_work_order_on_the_asset(
        self, staff_client, staff, problem, asset, vendor
    ):
        _attach_photo(problem)

        resp = staff_client.post(
            f"/api/inventory/asset-problems/{problem.id}/promote-third-party/",
            {"vendor": str(vendor.id), "title": "Spindle bearing replacement"},
            format="json",
        )
        assert resp.status_code == 201, resp.content

        problem.refresh_from_db()
        tpwo = problem.third_party_work_order
        assert tpwo is not None
        assert tpwo.title == "Spindle bearing replacement"
        assert tpwo.asset_id == asset.id
        assert tpwo.location_id == asset.location_id
        assert tpwo.vendor_id == vendor.id
        assert tpwo.notes == problem.description
        assert tpwo.opened_by_id == staff.id
        assert problem.status == AssetProblem.Status.IN_PROGRESS
        assert problem in tpwo.asset_problems.all()
        # The reporter's photo came along as an attachment.
        assert tpwo.attachments.filter(kind=ThirdPartyWorkOrderAttachment.KIND_PHOTO).count() == 1

    def test_requires_vendor_and_title(self, staff_client, problem, vendor):
        missing_both = staff_client.post(
            f"/api/inventory/asset-problems/{problem.id}/promote-third-party/",
            {},
            format="json",
        )
        assert missing_both.status_code == 400

        missing_title = staff_client.post(
            f"/api/inventory/asset-problems/{problem.id}/promote-third-party/",
            {"vendor": str(vendor.id)},
            format="json",
        )
        assert missing_title.status_code == 400

        problem.refresh_from_db()
        assert problem.third_party_work_order_id is None
        assert problem.status == AssetProblem.Status.REPORTED

    def test_unknown_vendor_is_404(self, staff_client, problem):
        resp = staff_client.post(
            f"/api/inventory/asset-problems/{problem.id}/promote-third-party/",
            {"vendor": str(uuid.uuid4()), "title": "x"},
            format="json",
        )
        assert resp.status_code == 404

    def test_second_promotion_is_rejected(self, staff_client, problem, vendor):
        body = {"vendor": str(vendor.id), "title": "Spindle bearing replacement"}
        first = staff_client.post(
            f"/api/inventory/asset-problems/{problem.id}/promote-third-party/",
            body,
            format="json",
        )
        assert first.status_code == 201, first.content

        second = staff_client.post(
            f"/api/inventory/asset-problems/{problem.id}/promote-third-party/",
            body,
            format="json",
        )
        assert second.status_code == 400
        assert ThirdPartyWorkOrder.objects.count() == 1


class TestResolve:
    def test_resolve_stamps_the_report(self, staff_client, staff, problem):
        resp = staff_client.post(
            f"/api/inventory/asset-problems/{problem.id}/resolve/",
            {"resolution_notes": "Replaced the bearing"},
            format="json",
        )
        assert resp.status_code == 200, resp.content

        problem.refresh_from_db()
        assert problem.status == AssetProblem.Status.RESOLVED
        assert problem.resolution_notes == "Replaced the bearing"
        assert problem.resolved_at is not None
        assert problem.resolved_by == (staff.handle or staff.username)

    def test_resolve_accepts_closed(self, staff_client, problem):
        resp = staff_client.post(
            f"/api/inventory/asset-problems/{problem.id}/resolve/",
            {"status": AssetProblem.Status.CLOSED},
            format="json",
        )
        assert resp.status_code == 200, resp.content
        problem.refresh_from_db()
        assert problem.status == AssetProblem.Status.CLOSED

    def test_resolve_rejects_other_statuses(self, staff_client, problem):
        resp = staff_client.post(
            f"/api/inventory/asset-problems/{problem.id}/resolve/",
            {"status": AssetProblem.Status.IN_PROGRESS},
            format="json",
        )
        assert resp.status_code == 400
        problem.refresh_from_db()
        assert problem.status == AssetProblem.Status.REPORTED

    def test_resolve_requires_authentication(self, problem):
        resp = APIClient().post(
            f"/api/inventory/asset-problems/{problem.id}/resolve/",
            {},
            format="json",
        )
        assert resp.status_code in (401, 403)


class TestAutoResolveOnCompletion:
    def test_report_to_work_order_to_resolved(self, staff_client, staff, asset):
        """End to end: report → promote-standard → complete → RESOLVED."""
        report = staff_client.post(
            f"/api/inventory/assets/{asset.id}/report_problem/",
            {"description": "Coolant pump leaking"},
            format="multipart",
        )
        assert report.status_code == 201, report.content
        problem = AssetProblem.objects.get(id=report.json()["id"])

        promote = staff_client.post(
            f"/api/inventory/asset-problems/{problem.id}/promote-standard/",
            {},
            format="json",
        )
        assert promote.status_code == 201, promote.content
        problem.refresh_from_db()

        done = _complete_via_api(staff_client, problem.work_order, notes="Swapped the pump")
        assert done.status_code == 200, done.content

        problem.refresh_from_db()
        assert problem.status == AssetProblem.Status.RESOLVED
        assert problem.resolved_at is not None
        assert problem.resolved_by == (staff.handle or staff.username)
        assert problem.resolution_notes == "Swapped the pump"

    def test_completion_notes_echoing_the_report_are_not_copied(self, staff_client, problem):
        """Promotion seeds the WO notes with the report text; don't echo it back."""
        staff_client.post(
            f"/api/inventory/asset-problems/{problem.id}/promote-standard/",
            {},
            format="json",
        )
        problem.refresh_from_db()

        _complete_via_api(staff_client, problem.work_order)

        problem.refresh_from_db()
        assert problem.status == AssetProblem.Status.RESOLVED
        assert problem.resolution_notes == ""

    def test_already_resolved_report_keeps_its_original_stamp(self, staff_client, problem):
        staff_client.post(
            f"/api/inventory/asset-problems/{problem.id}/promote-standard/",
            {},
            format="json",
        )
        problem.refresh_from_db()
        staff_client.post(
            f"/api/inventory/asset-problems/{problem.id}/resolve/",
            {"resolution_notes": "Fixed on the spot"},
            format="json",
        )
        problem.refresh_from_db()
        stamped_at = problem.resolved_at

        _complete_via_api(staff_client, problem.work_order, notes="Later paperwork")

        problem.refresh_from_db()
        assert problem.resolved_at == stamped_at
        assert problem.resolution_notes == "Fixed on the spot"

    def test_unpromoted_reports_are_untouched(self, staff_client, asset, problem):
        """A WO on the same asset that this report was not promoted to."""
        unrelated = WorkOrder.objects.create(maintenance_item=None, asset=asset)

        _complete_via_api(staff_client, unrelated)

        problem.refresh_from_db()
        assert problem.status == AssetProblem.Status.REPORTED
        assert problem.resolved_at is None

    def test_vendor_closure_resolves_the_report(self, staff_client, staff, problem, vendor):
        """End to end: promote-third-party → close_work_order → RESOLVED."""
        promote = staff_client.post(
            f"/api/inventory/asset-problems/{problem.id}/promote-third-party/",
            {"vendor": str(vendor.id), "title": "Spindle bearing replacement"},
            format="json",
        )
        assert promote.status_code == 201, promote.content
        problem.refresh_from_db()
        tpwo = problem.third_party_work_order

        _ready_to_close(tpwo, user=staff)
        t.close_work_order(tpwo, user=staff)

        problem.refresh_from_db()
        assert problem.status == AssetProblem.Status.RESOLVED
        assert problem.resolved_at is not None
        assert problem.resolved_by == (staff.handle or staff.username)

    def test_scanned_paper_completion_resolves_the_report(self, staff, asset):
        """The paper path completes work orders too, so it must close reports.

        A scanned sheet can only close a work order that has required tasks, so
        this is the PM shape — the one a promoted LocationProblem rides on.
        """
        from inventory.models import (
            MaintenanceItem,
            MaintenanceTask,
            WorkOrderSubmission,
            WorkOrderTaskCompletion,
        )
        from inventory.services.work_order_ingest import omr_confirm_completion

        item = MaintenanceItem.objects.create(
            asset=asset, title="Monthly inspection", interval_days=30
        )
        wo = WorkOrder.objects.create(maintenance_item=item)
        task = MaintenanceTask.objects.create(
            maintenance_item=item, title="Check the belt", order=1, is_required=True
        )
        WorkOrderTaskCompletion.objects.create(
            work_order=wo,
            task=task,
            task_title=task.title,
            task_order=task.order,
            is_required=True,
            is_completed=True,
        )
        problem = AssetProblem.objects.create(
            asset=asset,
            description="Belt squeals",
            work_order=wo,
            status=AssetProblem.Status.IN_PROGRESS,
        )
        submission = WorkOrderSubmission.objects.create(
            kind=WorkOrderSubmission.Kind.PM_COMPLETION,
            work_order=wo,
            status=WorkOrderSubmission.Status.PENDING_REVIEW,
        )

        assert omr_confirm_completion(wo, submission, user=staff) is True

        problem.refresh_from_db()
        assert problem.status == AssetProblem.Status.RESOLVED
        assert problem.resolved_by == (staff.handle or staff.username)

    def test_vendor_closure_resolves_a_promoted_location_problem(self, staff, vendor):
        """The same hook covers the location sibling, which had no closer either."""
        from inventory.models import Location, LocationProblem

        location = Location.objects.create(name="Bay 3")
        tpwo = ThirdPartyWorkOrder.objects.create(
            title="Leak", vendor=vendor, location=location, notes="fix the leak"
        )
        lp = LocationProblem.objects.create(
            location=location,
            description="Water on the floor",
            third_party_work_order=tpwo,
            status=LocationProblem.Status.IN_PROGRESS,
        )

        _ready_to_close(tpwo, user=staff)
        t.close_work_order(tpwo, user=staff)

        lp.refresh_from_db()
        assert lp.status == LocationProblem.Status.RESOLVED
        assert lp.resolved_at is not None


class TestActiveMaintenanceFeed:
    def test_promoted_problem_is_listed_as_its_work_order(self, staff_client, problem):
        before = staff_client.get("/api/inventory/maintenance/active/")
        assert before.status_code == 200, before.content
        kinds = [row["kind"] for row in before.json()["results"]]
        assert kinds.count("asset_problem") == 1

        staff_client.post(
            f"/api/inventory/asset-problems/{problem.id}/promote-standard/",
            {},
            format="json",
        )

        after = staff_client.get("/api/inventory/maintenance/active/")
        rows = after.json()["results"]
        # The raw report is gone; the work order it became took its place.
        assert [row["kind"] for row in rows].count("asset_problem") == 0
        work_orders = [row for row in rows if row["kind"] == "work_order"]
        assert len(work_orders) == 1
        assert work_orders[0]["title"] == problem.description[:60]

    def test_vendor_promoted_problem_also_drops_out(self, staff_client, problem, vendor):
        staff_client.post(
            f"/api/inventory/asset-problems/{problem.id}/promote-third-party/",
            {"vendor": str(vendor.id), "title": "Spindle bearing replacement"},
            format="json",
        )

        resp = staff_client.get("/api/inventory/maintenance/active/")
        assert [row["kind"] for row in resp.json()["results"]].count("asset_problem") == 0


class TestSettleStampRule:
    """A new resolution restamps; a close preserves. Both API routes, one rule.

    ``AssetProblemViewSet.resolve`` (the route ScanTTY uses) and
    ``AssetViewSet.resolve_problem`` each carried their own copy of the stamp,
    and each stamped only ``if not problem.resolved_at``. Nothing clears
    ``resolved_at`` when a recurrence is put back to ``reported`` from the admin
    change form, so a resolve of that row inherited the previous occurrence's
    date and resolver — a resolution dated months before the work, shown on
    ``AssetProblemSerializer`` and decoded by ScanTTY, with no error.

    The rule now lives in ``services.problem_settlement.settle_problem`` and
    these drive the real DRF routes, one pair each, so a route that stops
    calling it fails here.
    """

    @staticmethod
    def reopened(problem, *, resolved_by, resolved_at):
        """A report resolved once, then edited back to ``reported``."""
        problem.status = AssetProblem.Status.RESOLVED
        problem.resolved_by = resolved_by
        problem.resolved_at = resolved_at
        problem.save()
        AssetProblem.objects.filter(pk=problem.pk).update(status=AssetProblem.Status.REPORTED)
        problem.refresh_from_db()
        return problem

    def test_problem_route_restamps_a_reopened_report(self, staff_client, staff, problem):
        stale = timezone.now() - timedelta(days=238)
        self.reopened(problem, resolved_by="dana", resolved_at=stale)

        resp = staff_client.post(
            f"/api/inventory/asset-problems/{problem.id}/resolve/",
            {"resolution_notes": "Bearing replaced again"},
            format="json",
        )
        assert resp.status_code == 200, resp.content

        problem.refresh_from_db()
        assert problem.status == AssetProblem.Status.RESOLVED
        assert problem.resolved_by == (staff.handle or staff.username)
        assert problem.resolved_at > stale

    def test_asset_route_restamps_a_reopened_report(self, staff_client, staff, asset, problem):
        stale = timezone.now() - timedelta(days=238)
        self.reopened(problem, resolved_by="dana", resolved_at=stale)

        resp = staff_client.post(
            f"/api/inventory/assets/{asset.id}/resolve_problem/",
            {"problem_id": str(problem.id)},
            format="json",
        )
        assert resp.status_code == 200, resp.content

        problem.refresh_from_db()
        assert problem.status == AssetProblem.Status.RESOLVED
        assert problem.resolved_by == (staff.handle or staff.username)
        assert problem.resolved_at > stale

    def test_problem_route_close_keeps_a_prior_resolvers_credit(self, staff_client, problem):
        """Filing is not resolving: closing must not take somebody else's name."""
        original = timezone.now() - timedelta(days=3)
        problem.status = AssetProblem.Status.RESOLVED
        problem.resolved_by = "dana"
        problem.resolved_at = original
        problem.save()

        resp = staff_client.post(
            f"/api/inventory/asset-problems/{problem.id}/resolve/",
            {"status": AssetProblem.Status.CLOSED},
            format="json",
        )
        assert resp.status_code == 200, resp.content

        problem.refresh_from_db()
        assert problem.status == AssetProblem.Status.CLOSED
        assert problem.resolved_by == "dana"
        assert problem.resolved_at == original

    def test_asset_route_close_keeps_a_prior_resolvers_credit(self, staff_client, asset, problem):
        original = timezone.now() - timedelta(days=3)
        problem.status = AssetProblem.Status.RESOLVED
        problem.resolved_by = "dana"
        problem.resolved_at = original
        problem.save()

        resp = staff_client.post(
            f"/api/inventory/assets/{asset.id}/resolve_problem/",
            {"problem_id": str(problem.id), "status": AssetProblem.Status.CLOSED},
            format="json",
        )
        assert resp.status_code == 200, resp.content

        problem.refresh_from_db()
        assert problem.status == AssetProblem.Status.CLOSED
        assert problem.resolved_by == "dana"
        assert problem.resolved_at == original

    def test_close_of_an_unstamped_report_still_records_the_settlement(
        self, staff_client, staff, problem
    ):
        """A close is the only settlement an unresolved report ever got."""
        resp = staff_client.post(
            f"/api/inventory/asset-problems/{problem.id}/resolve/",
            {"status": AssetProblem.Status.CLOSED},
            format="json",
        )
        assert resp.status_code == 200, resp.content

        problem.refresh_from_db()
        assert problem.status == AssetProblem.Status.CLOSED
        assert problem.resolved_at is not None
        assert problem.resolved_by == (staff.handle or staff.username)

    def test_problem_route_second_resolve_keeps_the_first_resolver(self, staff_client, problem):
        """A resolve of an already-resolved report is not a second resolution.

        Neither API route carries a status precondition, so a stale detail page
        or any repeated POST reaches this. Keying the stamp on the target state
        alone silently replaced the first resolver's name and moment here — the
        row settled once, and that is the moment the column records.
        """
        original = timezone.now() - timedelta(days=3)
        problem.status = AssetProblem.Status.RESOLVED
        problem.resolved_by = "dana"
        problem.resolved_at = original
        problem.save()

        resp = staff_client.post(
            f"/api/inventory/asset-problems/{problem.id}/resolve/",
            {"resolution_notes": "Looked at it again"},
            format="json",
        )
        assert resp.status_code == 200, resp.content

        problem.refresh_from_db()
        assert problem.status == AssetProblem.Status.RESOLVED
        assert problem.resolved_by == "dana"
        assert problem.resolved_at == original

    def test_asset_route_second_resolve_keeps_the_first_resolver(
        self, staff_client, asset, problem
    ):
        original = timezone.now() - timedelta(days=3)
        problem.status = AssetProblem.Status.RESOLVED
        problem.resolved_by = "dana"
        problem.resolved_at = original
        problem.save()

        resp = staff_client.post(
            f"/api/inventory/assets/{asset.id}/resolve_problem/",
            {"problem_id": str(problem.id)},
            format="json",
        )
        assert resp.status_code == 200, resp.content

        problem.refresh_from_db()
        assert problem.resolved_by == "dana"
        assert problem.resolved_at == original

    def test_a_settled_report_with_no_stamp_at_all_is_filled(self, staff_client, staff, problem):
        """The one case where a settled row IS stamped: filling a gap.

        A row damaged by the pre-fix bulk write sits settled carrying no
        ``resolved_at``. Writing one is filling a hole, not taking a name.
        """
        problem.status = AssetProblem.Status.RESOLVED
        problem.resolved_by = ""
        problem.resolved_at = None
        problem.save()

        resp = staff_client.post(
            f"/api/inventory/asset-problems/{problem.id}/resolve/",
            {"status": AssetProblem.Status.CLOSED},
            format="json",
        )
        assert resp.status_code == 200, resp.content

        problem.refresh_from_db()
        assert problem.resolved_at is not None
        assert problem.resolved_by == (staff.handle or staff.username)
