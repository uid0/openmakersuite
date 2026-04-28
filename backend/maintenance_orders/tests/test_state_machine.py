"""Phase 3 tests: 7-step state machine + variance + dispatch fee split."""

import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from django.utils.crypto import get_random_string

import pytest

from inventory.tests.factories import AssetFactory
from maintenance_orders import transitions as t
from maintenance_orders.models import (
    EmergencyAuthorization,
    ThirdPartyWorkOrder,
    ThirdPartyWorkOrderAsset,
    ThirdPartyWorkOrderAttachment,
    ThirdPartyWorkOrderQuote,
)
from vendors.models import Vendor

User = get_user_model()


def _make_asset(**kwargs):
    kwargs.setdefault("asset_tag", f"P3-T-{uuid.uuid4().hex[:10].upper()}")
    return AssetFactory(**kwargs)


@pytest.fixture
def vendor(db):
    return Vendor.objects.create(name="ACME HVAC", vendor_kind=Vendor.KIND_HVAC)


@pytest.fixture
def vendor2(db):
    return Vendor.objects.create(name="Beta HVAC", vendor_kind=Vendor.KIND_HVAC)


@pytest.fixture
def vendor3(db):
    return Vendor.objects.create(name="Gamma HVAC", vendor_kind=Vendor.KIND_HVAC)


@pytest.fixture
def asset(db):
    return _make_asset()


@pytest.fixture
def staff(db):
    return User.objects.create_user(
        username="p3-staff",
        email="p3s@example.com",
        password=get_random_string(24),
        is_staff=True,
    )


@pytest.fixture
def logistics_user(db):
    user = User.objects.create_user(
        username="p3-logistics",
        email="p3l@example.com",
        password=get_random_string(24),
    )
    grp, _ = Group.objects.get_or_create(name="Logistics")
    user.groups.add(grp)
    return user


@pytest.fixture
def member(db):
    return User.objects.create_user(
        username="p3-member",
        email="p3m@example.com",
        password=get_random_string(24),
    )


def _photo(wo, user=None):
    f = SimpleUploadedFile("p.jpg", b"FAKE", content_type="image/jpeg")
    return ThirdPartyWorkOrderAttachment.objects.create(
        work_order=wo,
        kind=ThirdPartyWorkOrderAttachment.KIND_PHOTO,
        file=f,
        uploaded_by=user,
    )


def _attach(wo, kind, name="x.pdf"):
    f = SimpleUploadedFile(name, b"%PDF", content_type="application/pdf")
    return ThirdPartyWorkOrderAttachment.objects.create(work_order=wo, kind=kind, file=f)


@pytest.mark.django_db
class TestHappyPath:
    """AC-1: ThirdPartyWorkOrder advances through 7 states with proper guards."""

    def test_full_workflow(self, staff, vendor, vendor2, vendor3, asset):
        wo = ThirdPartyWorkOrder.objects.create(title="HVAC", vendor=vendor, asset=asset)
        assert wo.status == ThirdPartyWorkOrder.STATUS_REQUESTED

        # Step 1: NTE
        t.set_nte(wo, user=staff, nte_amount=Decimal("1000.00"))
        assert wo.nte_amount == Decimal("1000.00")
        assert wo.nte_set_by == staff

        t.advance_to_sourcing(wo, user=staff)
        assert wo.status == ThirdPartyWorkOrder.STATUS_SOURCING

        # Step 2: 3 quotes required
        for v in (vendor, vendor2, vendor3):
            ThirdPartyWorkOrderQuote.objects.create(
                work_order=wo, vendor=v, amount=Decimal("950.00")
            )
        t.advance_to_scheduled(wo, user=staff)
        assert wo.status == ThirdPartyWorkOrder.STATUS_SCHEDULED

        # Step 3 → 4
        t.vendor_arrived(wo, user=staff, keyfob_id="KF-99")
        wo.refresh_from_db()
        assert wo.status == ThirdPartyWorkOrder.STATUS_IN_PROGRESS
        assert wo.downtime_start is not None
        assert wo.keyfob_id == "KF-99"

        # Step 4 → 5: photo evidence required
        with pytest.raises(ValidationError):
            t.ops_sign_off(wo, user=staff)
        _photo(wo, user=staff)
        t.ops_sign_off(wo, user=staff)
        wo.refresh_from_db()
        assert wo.status == ThirdPartyWorkOrder.STATUS_VALIDATED
        assert wo.downtime_end is not None
        assert wo.total_downtime is not None

        # Step 5 → 6: invoice → financial review
        t.advance_to_financial_review(
            wo,
            user=staff,
            actual_invoice_total=Decimal("950.00"),
            dispatch_fee=Decimal("50.00"),
        )
        wo.refresh_from_db()
        assert wo.status == ThirdPartyWorkOrder.STATUS_FINANCIAL_REVIEW
        assert wo.variance_status == "auto_approved"

        # Step 6 → 7: closure requires invoice + FSR
        with pytest.raises(ValidationError):
            t.close_work_order(wo, user=staff)
        _attach(wo, ThirdPartyWorkOrderAttachment.KIND_INVOICE)
        _attach(wo, ThirdPartyWorkOrderAttachment.KIND_FSR)
        t.close_work_order(wo, user=staff)
        wo.refresh_from_db()
        assert wo.status == ThirdPartyWorkOrder.STATUS_CLOSED
        assert wo.closed_at is not None


@pytest.mark.django_db
class TestPermissions:
    def test_member_cannot_transition(self, member, vendor, asset):
        wo = ThirdPartyWorkOrder.objects.create(title="x", vendor=vendor, asset=asset)
        with pytest.raises(PermissionDenied):
            t.set_nte(wo, user=member, nte_amount=Decimal("100"))

    def test_logistics_can_transition(self, logistics_user, vendor, asset):
        wo = ThirdPartyWorkOrder.objects.create(title="x", vendor=vendor, asset=asset)
        t.set_nte(wo, user=logistics_user, nte_amount=Decimal("100"))
        assert wo.nte_amount == Decimal("100")

    def test_only_logistics_or_staff_can_authorize_emergency(self, member, vendor, asset):
        wo = ThirdPartyWorkOrder.objects.create(title="x", vendor=vendor, asset=asset)
        with pytest.raises(PermissionDenied):
            t.authorize_emergency(wo, user=member, reason="fire")


@pytest.mark.django_db
class TestNteAndEmergency:
    """AC-2 (NTE) and emergency-bypass behavior."""

    def test_cannot_advance_to_sourcing_without_nte(self, staff, vendor, asset):
        wo = ThirdPartyWorkOrder.objects.create(title="x", vendor=vendor, asset=asset)
        with pytest.raises(ValidationError):
            t.advance_to_sourcing(wo, user=staff)

    def test_emergency_bypasses_nte_requirement(self, logistics_user, vendor, asset):
        wo = ThirdPartyWorkOrder.objects.create(title="leak", vendor=vendor, asset=asset)
        t.authorize_emergency(wo, user=logistics_user, reason="burst pipe")
        # No NTE; emergency auth lets us advance.
        t.advance_to_sourcing(wo, user=logistics_user)
        assert wo.status == ThirdPartyWorkOrder.STATUS_SOURCING

    def test_emergency_authorization_audit_row_created(self, logistics_user, vendor, asset):
        wo = ThirdPartyWorkOrder.objects.create(title="x", vendor=vendor, asset=asset)
        auth = t.authorize_emergency(wo, user=logistics_user, reason="X")
        assert isinstance(auth, EmergencyAuthorization)
        assert auth.is_currently_valid is True
        assert wo.emergency_authorizations.count() == 1

    def test_expired_emergency_authorization_does_not_count(self, logistics_user, vendor, asset):
        wo = ThirdPartyWorkOrder.objects.create(title="x", vendor=vendor, asset=asset)
        auth = t.authorize_emergency(wo, user=logistics_user)
        # Force expiry.
        auth.expires_at = timezone.now() - timezone.timedelta(seconds=1)
        auth.save()
        assert t.has_active_emergency_authorization(wo) is False


@pytest.mark.django_db
class TestQuoteRule:
    """AC-3: 3-quote requirement + waiver."""

    def _to_sourcing(self, staff, vendor, asset):
        wo = ThirdPartyWorkOrder.objects.create(title="x", vendor=vendor, asset=asset)
        t.set_nte(wo, user=staff, nte_amount=Decimal("500"))
        t.advance_to_sourcing(wo, user=staff)
        return wo

    def test_blocks_scheduling_without_3_quotes(self, staff, vendor, vendor2, asset):
        wo = self._to_sourcing(staff, vendor, asset)
        ThirdPartyWorkOrderQuote.objects.create(work_order=wo, vendor=vendor, amount=Decimal("100"))
        ThirdPartyWorkOrderQuote.objects.create(
            work_order=wo, vendor=vendor2, amount=Decimal("110")
        )
        with pytest.raises(ValidationError):
            t.advance_to_scheduled(wo, user=staff)

    def test_3_quotes_unblock_scheduling(self, staff, vendor, vendor2, vendor3, asset):
        wo = self._to_sourcing(staff, vendor, asset)
        for v in (vendor, vendor2, vendor3):
            ThirdPartyWorkOrderQuote.objects.create(work_order=wo, vendor=v, amount=Decimal("100"))
        t.advance_to_scheduled(wo, user=staff)
        assert wo.status == ThirdPartyWorkOrder.STATUS_SCHEDULED

    def test_waiver_bypasses_quote_rule(self, staff, vendor, asset):
        wo = self._to_sourcing(staff, vendor, asset)
        t.waive_quote_requirement(wo, user=staff, reason="Sole-source replacement part")
        t.advance_to_scheduled(wo, user=staff)
        assert wo.status == ThirdPartyWorkOrder.STATUS_SCHEDULED

    def test_waiver_requires_reason(self, staff, vendor, asset):
        wo = self._to_sourcing(staff, vendor, asset)
        with pytest.raises(ValidationError):
            t.waive_quote_requirement(wo, user=staff, reason="   ")

    def test_emergency_bypasses_quote_rule(self, staff, logistics_user, vendor, asset):
        wo = self._to_sourcing(staff, vendor, asset)
        t.authorize_emergency(wo, user=logistics_user, reason="weekend leak")
        t.advance_to_scheduled(wo, user=staff)
        assert wo.status == ThirdPartyWorkOrder.STATUS_SCHEDULED


@pytest.mark.django_db
class TestPhotoEvidence:
    """AC-5: photo evidence required for validation."""

    def _to_in_progress(self, staff, vendor, vendor2, vendor3, asset):
        wo = ThirdPartyWorkOrder.objects.create(title="x", vendor=vendor, asset=asset)
        t.set_nte(wo, user=staff, nte_amount=Decimal("500"))
        t.advance_to_sourcing(wo, user=staff)
        for v in (vendor, vendor2, vendor3):
            ThirdPartyWorkOrderQuote.objects.create(work_order=wo, vendor=v, amount=Decimal("100"))
        t.advance_to_scheduled(wo, user=staff)
        t.vendor_arrived(wo, user=staff)
        return wo

    def test_blocks_validation_without_photo(self, staff, vendor, vendor2, vendor3, asset):
        wo = self._to_in_progress(staff, vendor, vendor2, vendor3, asset)
        with pytest.raises(ValidationError):
            t.ops_sign_off(wo, user=staff)

    def test_unblocks_with_photo(self, staff, vendor, vendor2, vendor3, asset):
        wo = self._to_in_progress(staff, vendor, vendor2, vendor3, asset)
        _photo(wo)
        t.ops_sign_off(wo, user=staff)
        wo.refresh_from_db()
        assert wo.status == ThirdPartyWorkOrder.STATUS_VALIDATED
        assert wo.total_downtime is not None


@pytest.mark.django_db
class TestVarianceGate:
    """AC-2: variance gate auto-approves within par buffer; blocks otherwise."""

    def _to_validated(self, staff, vendor, vendor2, vendor3, asset):
        wo = ThirdPartyWorkOrder.objects.create(
            title="x",
            vendor=vendor,
            asset=asset,
            par_cost_buffer=Decimal("50.00"),
        )
        t.set_nte(wo, user=staff, nte_amount=Decimal("1000.00"))
        t.advance_to_sourcing(wo, user=staff)
        for v in (vendor, vendor2, vendor3):
            ThirdPartyWorkOrderQuote.objects.create(work_order=wo, vendor=v, amount=Decimal("950"))
        t.advance_to_scheduled(wo, user=staff)
        t.vendor_arrived(wo, user=staff)
        _photo(wo)
        t.ops_sign_off(wo, user=staff)
        return wo

    def test_under_nte_auto_approves(self, staff, vendor, vendor2, vendor3, asset):
        wo = self._to_validated(staff, vendor, vendor2, vendor3, asset)
        t.advance_to_financial_review(wo, user=staff, actual_invoice_total=Decimal("900.00"))
        wo.refresh_from_db()
        assert wo.variance_status == "auto_approved"

    def test_within_par_buffer_auto_approves(self, staff, vendor, vendor2, vendor3, asset):
        wo = self._to_validated(staff, vendor, vendor2, vendor3, asset)
        # NTE 1000, par 50, invoice 1040 → overage 40 ≤ 50 ≤ 150 (15%) → auto.
        t.advance_to_financial_review(wo, user=staff, actual_invoice_total=Decimal("1040.00"))
        wo.refresh_from_db()
        assert wo.variance_status == "auto_approved"

    def test_over_par_blocks_closure(self, staff, vendor, vendor2, vendor3, asset):
        wo = self._to_validated(staff, vendor, vendor2, vendor3, asset)
        # NTE 1000, par 50, invoice 1100 → overage 100 > 50 → blocked.
        t.advance_to_financial_review(wo, user=staff, actual_invoice_total=Decimal("1100.00"))
        wo.refresh_from_db()
        assert wo.variance_status == "blocked"
        # Add invoice + FSR but block must still hold.
        _attach(wo, ThirdPartyWorkOrderAttachment.KIND_INVOICE)
        _attach(wo, ThirdPartyWorkOrderAttachment.KIND_FSR)
        with pytest.raises(ValidationError):
            t.close_work_order(wo, user=staff)

    def test_15_percent_relative_cap(self, staff, vendor, vendor2, vendor3, asset):
        # Make par very high so par-buffer alone wouldn't block.
        wo = ThirdPartyWorkOrder.objects.create(
            title="x",
            vendor=vendor,
            asset=asset,
            par_cost_buffer=Decimal("500.00"),
        )
        t.set_nte(wo, user=staff, nte_amount=Decimal("1000.00"))
        t.advance_to_sourcing(wo, user=staff)
        for v in (vendor, vendor2, vendor3):
            ThirdPartyWorkOrderQuote.objects.create(work_order=wo, vendor=v, amount=Decimal("950"))
        t.advance_to_scheduled(wo, user=staff)
        t.vendor_arrived(wo, user=staff)
        _photo(wo)
        t.ops_sign_off(wo, user=staff)
        # Overage 200 ≤ par 500, but > 15% NTE (150) → blocked.
        t.advance_to_financial_review(wo, user=staff, actual_invoice_total=Decimal("1200.00"))
        wo.refresh_from_db()
        assert wo.variance_status == "blocked"


@pytest.mark.django_db
class TestDispatchFeeSplit:
    """AC-6: dispatch fee splits across multi-asset by share_pct."""

    def test_three_asset_equal_split_with_dispatch(self, staff, vendor, vendor2, vendor3):
        a1, a2, a3 = _make_asset(), _make_asset(), _make_asset()
        wo = ThirdPartyWorkOrder.objects.create(title="multi", vendor=vendor, asset=a1)
        # Equal split.
        for a in (a1, a2, a3):
            ThirdPartyWorkOrderAsset.objects.create(
                work_order=wo, asset=a, share_pct=Decimal("33.34") if a == a1 else Decimal("33.33")
            )
        t.set_nte(wo, user=staff, nte_amount=Decimal("1000.00"))
        t.advance_to_sourcing(wo, user=staff)
        for v in (vendor, vendor2, vendor3):
            ThirdPartyWorkOrderQuote.objects.create(work_order=wo, vendor=v, amount=Decimal("950"))
        t.advance_to_scheduled(wo, user=staff)
        t.vendor_arrived(wo, user=staff)
        _photo(wo)
        t.ops_sign_off(wo, user=staff)
        # Invoice 900, dispatch 60. Net 840 split by share. Flat 60 split
        # equally → 20 per asset.
        t.advance_to_financial_review(
            wo,
            user=staff,
            actual_invoice_total=Decimal("900.00"),
            dispatch_fee=Decimal("60.00"),
        )
        links = list(wo.asset_links.all())
        assert len(links) == 3
        for link in links:
            assert link.allocated_cost is not None
        total = sum(link.allocated_cost for link in links)
        # Allow ±1¢ rounding tolerance.
        assert abs(total - Decimal("900.00")) <= Decimal("0.05")

    def test_unequal_share_pct_split(self, staff, vendor, vendor2, vendor3):
        a1, a2 = _make_asset(), _make_asset()
        wo = ThirdPartyWorkOrder.objects.create(title="dual", vendor=vendor, asset=a1)
        ThirdPartyWorkOrderAsset.objects.create(work_order=wo, asset=a1, share_pct=Decimal("75.00"))
        ThirdPartyWorkOrderAsset.objects.create(work_order=wo, asset=a2, share_pct=Decimal("25.00"))
        t.set_nte(wo, user=staff, nte_amount=Decimal("1000.00"))
        t.advance_to_sourcing(wo, user=staff)
        for v in (vendor, vendor2, vendor3):
            ThirdPartyWorkOrderQuote.objects.create(work_order=wo, vendor=v, amount=Decimal("950"))
        t.advance_to_scheduled(wo, user=staff)
        t.vendor_arrived(wo, user=staff)
        _photo(wo)
        t.ops_sign_off(wo, user=staff)
        # Invoice 1000, dispatch 100. Net = 900. a1 gets 675, a2 gets 225.
        # Flat split: 50 each. Totals: 725 and 275.
        t.advance_to_financial_review(
            wo,
            user=staff,
            actual_invoice_total=Decimal("1000.00"),
            dispatch_fee=Decimal("100.00"),
        )
        a1_link = wo.asset_links.get(asset=a1)
        a2_link = wo.asset_links.get(asset=a2)
        assert a1_link.allocated_cost == Decimal("725.00")
        assert a2_link.allocated_cost == Decimal("275.00")

    def test_no_links_no_split(self, staff, vendor, vendor2, vendor3, asset):
        wo = ThirdPartyWorkOrder.objects.create(title="x", vendor=vendor, asset=asset)
        t.set_nte(wo, user=staff, nte_amount=Decimal("500"))
        t.advance_to_sourcing(wo, user=staff)
        for v in (vendor, vendor2, vendor3):
            ThirdPartyWorkOrderQuote.objects.create(work_order=wo, vendor=v, amount=Decimal("100"))
        t.advance_to_scheduled(wo, user=staff)
        t.vendor_arrived(wo, user=staff)
        _photo(wo)
        t.ops_sign_off(wo, user=staff)
        # No asset_links — no-op split.
        t.advance_to_financial_review(wo, user=staff, actual_invoice_total=Decimal("450"))
        assert wo.asset_links.count() == 0


@pytest.mark.django_db
class TestClosure:
    """AC-2 + closure preconditions."""

    def _to_financial_review(self, staff, vendor, vendor2, vendor3, asset, invoice):
        wo = ThirdPartyWorkOrder.objects.create(title="x", vendor=vendor, asset=asset)
        t.set_nte(wo, user=staff, nte_amount=Decimal("1000.00"))
        t.advance_to_sourcing(wo, user=staff)
        for v in (vendor, vendor2, vendor3):
            ThirdPartyWorkOrderQuote.objects.create(work_order=wo, vendor=v, amount=Decimal("950"))
        t.advance_to_scheduled(wo, user=staff)
        t.vendor_arrived(wo, user=staff)
        _photo(wo)
        t.ops_sign_off(wo, user=staff)
        t.advance_to_financial_review(wo, user=staff, actual_invoice_total=invoice)
        return wo

    def test_close_requires_invoice_and_fsr(self, staff, vendor, vendor2, vendor3, asset):
        wo = self._to_financial_review(staff, vendor, vendor2, vendor3, asset, Decimal("900"))
        # Only invoice — still blocked.
        _attach(wo, ThirdPartyWorkOrderAttachment.KIND_INVOICE)
        with pytest.raises(ValidationError):
            t.close_work_order(wo, user=staff)
        _attach(wo, ThirdPartyWorkOrderAttachment.KIND_FSR)
        t.close_work_order(wo, user=staff)
        wo.refresh_from_db()
        assert wo.status == ThirdPartyWorkOrder.STATUS_CLOSED


@pytest.mark.django_db
class TestApiActions:
    """AC-1/2/3/5/6 surfaced via the REST action endpoints."""

    def test_set_nte_action(self, vendor, asset, staff):
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(user=staff)
        wo = ThirdPartyWorkOrder.objects.create(title="x", vendor=vendor, asset=asset)
        resp = client.post(
            f"/api/maintenance-orders/work-orders/{wo.id}/set-nte/",
            {"nte_amount": "750.00"},
            format="json",
        )
        assert resp.status_code == 200, resp.content
        wo.refresh_from_db()
        assert wo.nte_amount == Decimal("750.00")

    def test_advance_to_scheduled_blocked_without_quotes(self, vendor, asset, staff):
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(user=staff)
        wo = ThirdPartyWorkOrder.objects.create(title="x", vendor=vendor, asset=asset)
        t.set_nte(wo, user=staff, nte_amount=Decimal("500"))
        t.advance_to_sourcing(wo, user=staff)
        resp = client.post(f"/api/maintenance-orders/work-orders/{wo.id}/advance-to-scheduled/")
        assert resp.status_code == 400

    def test_quote_create_endpoint(self, vendor, asset, staff):
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(user=staff)
        wo = ThirdPartyWorkOrder.objects.create(title="x", vendor=vendor, asset=asset)
        resp = client.post(
            "/api/maintenance-orders/quotes/",
            {
                "work_order": str(wo.id),
                "vendor": str(vendor.id),
                "amount": "100.00",
                "notes": "first quote",
            },
            format="json",
        )
        assert resp.status_code == 201, resp.content
        q = ThirdPartyWorkOrderQuote.objects.get(id=resp.json()["id"])
        assert q.submitted_by == staff

    def test_workflow_block_in_serializer(self, vendor, asset, staff):
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(user=staff)
        wo = ThirdPartyWorkOrder.objects.create(title="x", vendor=vendor, asset=asset)
        resp = client.get(f"/api/maintenance-orders/work-orders/{wo.id}/")
        assert resp.status_code == 200
        wf = resp.json()["workflow"]
        assert wf["has_nte"] is False
        assert wf["has_required_quotes"] is False
        assert wf["quote_count"] == 0
        assert wf["has_photo_evidence"] is False
