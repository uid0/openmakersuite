"""Phase 5 tests: keyfob gate, closure notifications, recovery task, audit log."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils.crypto import get_random_string

import pytest

from inventory.tests.factories import AssetFactory
from maintenance_orders import transitions as t
from maintenance_orders.models import (
    RecoveryTask,
    ThirdPartyWorkOrder,
    ThirdPartyWorkOrderAttachment,
    ThirdPartyWorkOrderAuditLog,
    ThirdPartyWorkOrderQuote,
)
from notifications.models import Notification
from vendors.models import Vendor

User = get_user_model()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vendor(db):
    return Vendor.objects.create(name="Phase5 HVAC", vendor_kind=Vendor.KIND_HVAC)


@pytest.fixture
def vendor2(db):
    return Vendor.objects.create(name="Phase5 Beta", vendor_kind=Vendor.KIND_HVAC)


@pytest.fixture
def vendor3(db):
    return Vendor.objects.create(name="Phase5 Gamma", vendor_kind=Vendor.KIND_HVAC)


@pytest.fixture
def asset(db):
    return AssetFactory()


@pytest.fixture
def staff(db):
    return User.objects.create_user(
        username="p5-staff",
        email="p5-staff@example.com",
        password=get_random_string(24),
        is_staff=True,
    )


@pytest.fixture
def opener(db):
    return User.objects.create_user(
        username="p5-opener",
        email="p5-opener@example.com",
        password=get_random_string(24),
    )


@pytest.fixture
def finance_user(db):
    user = User.objects.create_user(
        username="p5-finance",
        email="p5-finance@example.com",
        password=get_random_string(24),
    )
    grp, _ = Group.objects.get_or_create(name="Finance")
    user.groups.add(grp)
    return user


@pytest.fixture
def logistics_user(db):
    user = User.objects.create_user(
        username="p5-logistics",
        email="p5-logistics@example.com",
        password=get_random_string(24),
    )
    grp, _ = Group.objects.get_or_create(name="Logistics")
    user.groups.add(grp)
    return user


def _photo(wo):
    f = SimpleUploadedFile("p.jpg", b"FAKE", content_type="image/jpeg")
    return ThirdPartyWorkOrderAttachment.objects.create(
        work_order=wo,
        kind=ThirdPartyWorkOrderAttachment.KIND_PHOTO,
        file=f,
    )


def _attach(wo, kind, name="x.pdf"):
    f = SimpleUploadedFile(name, b"%PDF", content_type="application/pdf")
    return ThirdPartyWorkOrderAttachment.objects.create(work_order=wo, kind=kind, file=f)


def _to_financial_review(staff, vendor, vendor2, vendor3, asset, *, opener=None, keyfob_id=""):
    """Drive a WO from intake all the way to financial review with sane defaults."""
    wo = ThirdPartyWorkOrder.objects.create(
        title="HVAC service",
        vendor=vendor,
        asset=asset,
        opened_by=opener,
    )
    t.set_nte(wo, user=staff, nte_amount=Decimal("1000.00"))
    t.advance_to_sourcing(wo, user=staff)
    for v in (vendor, vendor2, vendor3):
        ThirdPartyWorkOrderQuote.objects.create(work_order=wo, vendor=v, amount=Decimal("950.00"))
    t.advance_to_scheduled(wo, user=staff)
    t.vendor_arrived(wo, user=staff, keyfob_id=keyfob_id)
    _photo(wo)
    t.ops_sign_off(wo, user=staff)
    t.advance_to_financial_review(
        wo,
        user=staff,
        actual_invoice_total=Decimal("950.00"),
    )
    _attach(wo, ThirdPartyWorkOrderAttachment.KIND_INVOICE)
    _attach(wo, ThirdPartyWorkOrderAttachment.KIND_FSR)
    return wo


# ---------------------------------------------------------------------------
# AC-1: keyfob return gate
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestKeyfobReturnGate:
    def test_outstanding_keyfob_blocks_closure(self, staff, vendor, vendor2, vendor3, asset):
        wo = _to_financial_review(staff, vendor, vendor2, vendor3, asset, keyfob_id="KF-77")
        with pytest.raises(ValidationError) as ei:
            t.close_work_order(wo, user=staff)
        assert "KF-77" in str(ei.value) or "keyfob" in str(ei.value).lower()
        wo.refresh_from_db()
        assert wo.status == ThirdPartyWorkOrder.STATUS_FINANCIAL_REVIEW

    def test_returned_keyfob_unblocks_closure(self, staff, vendor, vendor2, vendor3, asset):
        wo = _to_financial_review(staff, vendor, vendor2, vendor3, asset, keyfob_id="KF-77")
        t.record_keyfob_return(wo, user=staff)
        wo.refresh_from_db()
        assert wo.keyfob_returned_at is not None
        assert wo.keyfob_returned_by == staff
        t.close_work_order(wo, user=staff)
        wo.refresh_from_db()
        assert wo.status == ThirdPartyWorkOrder.STATUS_CLOSED

    def test_no_keyfob_no_gate(self, staff, vendor, vendor2, vendor3, asset):
        wo = _to_financial_review(staff, vendor, vendor2, vendor3, asset, keyfob_id="")
        t.close_work_order(wo, user=staff)
        wo.refresh_from_db()
        assert wo.status == ThirdPartyWorkOrder.STATUS_CLOSED

    def test_record_keyfob_return_requires_checked_out_keyfob(
        self, staff, vendor, vendor2, vendor3, asset
    ):
        wo = _to_financial_review(staff, vendor, vendor2, vendor3, asset, keyfob_id="")
        with pytest.raises(ValidationError):
            t.record_keyfob_return(wo, user=staff)


# ---------------------------------------------------------------------------
# AC-2: SIG closure notifications (in-app + email to SIG/Finance/opener)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestClosureNotifications:
    def test_finance_and_opener_get_in_app_notification(
        self, staff, vendor, vendor2, vendor3, asset, opener, finance_user
    ):
        wo = _to_financial_review(
            staff, vendor, vendor2, vendor3, asset, opener=opener, keyfob_id=""
        )
        Notification.objects.all().delete()
        mail.outbox = []
        t.close_work_order(wo, user=staff)
        notified_users = set(Notification.objects.values_list("user_id", flat=True))
        assert opener.id in notified_users, "opener missed in-app notification"
        assert finance_user.id in notified_users, "finance missed in-app notification"

    def test_email_sent_to_finance_and_opener(
        self, staff, vendor, vendor2, vendor3, asset, opener, finance_user
    ):
        wo = _to_financial_review(
            staff, vendor, vendor2, vendor3, asset, opener=opener, keyfob_id=""
        )
        mail.outbox = []
        t.close_work_order(wo, user=staff)
        # One email may be sent with multiple recipients OR multiple emails;
        # collect all recipient addresses across messages.
        recipients = set()
        for msg in mail.outbox:
            recipients.update(msg.to)
        assert finance_user.email in recipients
        assert opener.email in recipients

    def test_notification_contains_cost_and_variance(
        self, staff, vendor, vendor2, vendor3, asset, finance_user
    ):
        wo = _to_financial_review(staff, vendor, vendor2, vendor3, asset, keyfob_id="")
        Notification.objects.all().delete()
        t.close_work_order(wo, user=staff)
        n = Notification.objects.filter(user=finance_user).first()
        assert n is not None
        assert "950" in n.message  # final cost
        assert "1000" in n.message  # NTE
        assert n.metadata.get("kind") == "third_party_wo_closed"
        assert n.metadata.get("short_id") == wo.short_id


# ---------------------------------------------------------------------------
# AC-3: warranty recovery auto-task
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestWarrantyRecoveryTask:
    def test_recovery_task_created_when_recovery_flag_set(
        self, staff, vendor, vendor2, vendor3, asset
    ):
        wo = _to_financial_review(staff, vendor, vendor2, vendor3, asset, keyfob_id="")
        wo.warranty_recovery = True
        wo.save(update_fields=["warranty_recovery"])
        t.close_work_order(wo, user=staff)
        tasks = RecoveryTask.objects.filter(work_order=wo)
        assert tasks.count() == 1
        task = tasks.first()
        assert task.assigned_group == "Logistics"
        assert task.vendor == vendor
        assert task.amount == Decimal("950.00")
        assert task.status == RecoveryTask.STATUS_OPEN
        assert vendor.name in task.title

    def test_no_recovery_task_when_flag_false(self, staff, vendor, vendor2, vendor3, asset):
        wo = _to_financial_review(staff, vendor, vendor2, vendor3, asset, keyfob_id="")
        assert wo.warranty_recovery is False
        t.close_work_order(wo, user=staff)
        assert RecoveryTask.objects.filter(work_order=wo).count() == 0


# ---------------------------------------------------------------------------
# AC-4: audit log
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAuditLog:
    def test_state_transitions_record_audit_rows(self, staff, vendor, vendor2, vendor3, asset):
        wo = _to_financial_review(staff, vendor, vendor2, vendor3, asset, keyfob_id="")
        t.close_work_order(wo, user=staff)
        actions = list(wo.audit_logs.order_by("created_at").values_list("action", flat=True))
        # Must include at least one transition row per state change in the
        # full happy-path lifecycle.
        transitions = [a for a in actions if a == ThirdPartyWorkOrderAuditLog.ACTION_TRANSITION]
        # 6 transitions: requested→sourcing, sourcing→scheduled, scheduled→in_progress,
        # in_progress→validated, validated→financial_review, financial_review→closed
        # Plus the initial set_nte audit row also uses ACTION_TRANSITION.
        assert len(transitions) >= 6

    def test_emergency_authorization_creates_audit_row(self, logistics_user, vendor, asset):
        wo = ThirdPartyWorkOrder.objects.create(title="emerg", vendor=vendor, asset=asset)
        t.authorize_emergency(wo, user=logistics_user, reason="burst pipe")
        rows = wo.audit_logs.filter(action=ThirdPartyWorkOrderAuditLog.ACTION_EMERGENCY_AUTH)
        assert rows.count() == 1
        row = rows.first()
        assert row.actor == logistics_user
        assert row.notes == "burst pipe"
        assert row.metadata.get("authorization_id")

    def test_emergency_validation_logged_during_advance(self, logistics_user, staff, vendor, asset):
        wo = ThirdPartyWorkOrder.objects.create(title="emerg", vendor=vendor, asset=asset)
        t.authorize_emergency(wo, user=logistics_user, reason="leak")
        t.advance_to_sourcing(wo, user=staff)
        validations = wo.audit_logs.filter(
            action=ThirdPartyWorkOrderAuditLog.ACTION_EMERGENCY_VALIDATION
        )
        assert validations.count() >= 1

    def test_variance_override_recorded(self, staff, vendor, vendor2, vendor3, asset):
        wo = ThirdPartyWorkOrder.objects.create(
            title="x", vendor=vendor, asset=asset, par_cost_buffer=Decimal("10.00")
        )
        t.set_nte(wo, user=staff, nte_amount=Decimal("1000.00"))
        t.advance_to_sourcing(wo, user=staff)
        for v in (vendor, vendor2, vendor3):
            ThirdPartyWorkOrderQuote.objects.create(
                work_order=wo, vendor=v, amount=Decimal("950.00")
            )
        t.advance_to_scheduled(wo, user=staff)
        t.vendor_arrived(wo, user=staff)
        _photo(wo)
        t.ops_sign_off(wo, user=staff)
        # Massive overage → blocked.
        t.advance_to_financial_review(wo, user=staff, actual_invoice_total=Decimal("2000.00"))
        wo.refresh_from_db()
        assert wo.variance_status == "blocked"
        t.override_variance_block(wo, user=staff, reason="SIG agreed to absorb")
        rows = wo.audit_logs.filter(action=ThirdPartyWorkOrderAuditLog.ACTION_VARIANCE_OVERRIDE)
        assert rows.count() == 1
        assert rows.first().notes == "SIG agreed to absorb"

    def test_keyfob_return_logged(self, staff, vendor, vendor2, vendor3, asset):
        wo = _to_financial_review(staff, vendor, vendor2, vendor3, asset, keyfob_id="KF-1")
        t.record_keyfob_return(wo, user=staff)
        rows = wo.audit_logs.filter(action=ThirdPartyWorkOrderAuditLog.ACTION_KEYFOB_RETURN)
        assert rows.count() == 1
        assert rows.first().metadata.get("keyfob_id") == "KF-1"

    def test_quote_waiver_logged(self, staff, vendor, asset):
        wo = ThirdPartyWorkOrder.objects.create(title="x", vendor=vendor, asset=asset)
        t.set_nte(wo, user=staff, nte_amount=Decimal("500"))
        t.advance_to_sourcing(wo, user=staff)
        t.waive_quote_requirement(wo, user=staff, reason="sole-source replacement")
        rows = wo.audit_logs.filter(action=ThirdPartyWorkOrderAuditLog.ACTION_QUOTE_WAIVER)
        assert rows.count() == 1
        assert rows.first().notes == "sole-source replacement"


# ---------------------------------------------------------------------------
# AC-1+5: variance override permission
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestVarianceOverridePermission:
    def test_non_staff_cannot_override(self, vendor, asset):
        wo = ThirdPartyWorkOrder.objects.create(
            title="x", vendor=vendor, asset=asset, variance_status="blocked"
        )
        member = User.objects.create_user(
            username="random", email="r@example.com", password=get_random_string(24)
        )
        with pytest.raises(PermissionDenied):
            t.override_variance_block(wo, user=member, reason="x")

    def test_override_requires_reason(self, staff, vendor, asset):
        wo = ThirdPartyWorkOrder.objects.create(
            title="x", vendor=vendor, asset=asset, variance_status="blocked"
        )
        with pytest.raises(ValidationError):
            t.override_variance_block(wo, user=staff, reason="   ")

    def test_override_requires_blocked_status(self, staff, vendor, asset):
        wo = ThirdPartyWorkOrder.objects.create(title="x", vendor=vendor, asset=asset)
        with pytest.raises(ValidationError):
            t.override_variance_block(wo, user=staff, reason="why")


# ---------------------------------------------------------------------------
# API smoke tests for new endpoints
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestApiActions:
    def test_record_keyfob_return_endpoint(self, staff, vendor, vendor2, vendor3, asset):
        from rest_framework.test import APIClient

        wo = _to_financial_review(staff, vendor, vendor2, vendor3, asset, keyfob_id="KF-9")
        client = APIClient()
        client.force_authenticate(user=staff)
        resp = client.post(f"/api/maintenance-orders/work-orders/{wo.id}/record-keyfob-return/")
        assert resp.status_code == 200, resp.content
        wo.refresh_from_db()
        assert wo.keyfob_returned_at is not None

    def test_close_blocked_via_api_when_keyfob_outstanding(
        self, staff, vendor, vendor2, vendor3, asset
    ):
        from rest_framework.test import APIClient

        wo = _to_financial_review(staff, vendor, vendor2, vendor3, asset, keyfob_id="KF-X")
        client = APIClient()
        client.force_authenticate(user=staff)
        resp = client.post(f"/api/maintenance-orders/work-orders/{wo.id}/close/")
        assert resp.status_code == 400

    def test_audit_log_endpoint(self, staff, vendor, vendor2, vendor3, asset):
        from rest_framework.test import APIClient

        wo = _to_financial_review(staff, vendor, vendor2, vendor3, asset, keyfob_id="")
        client = APIClient()
        client.force_authenticate(user=staff)
        resp = client.get(f"/api/maintenance-orders/work-orders/{wo.id}/audit-log/")
        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) > 0
        actions = {r["action"] for r in rows}
        assert ThirdPartyWorkOrderAuditLog.ACTION_TRANSITION in actions

    def test_override_variance_endpoint(self, staff, vendor, vendor2, vendor3, asset):
        from rest_framework.test import APIClient

        wo = ThirdPartyWorkOrder.objects.create(
            title="x", vendor=vendor, asset=asset, variance_status="blocked"
        )
        client = APIClient()
        client.force_authenticate(user=staff)
        resp = client.post(
            f"/api/maintenance-orders/work-orders/{wo.id}/override-variance/",
            {"reason": "approved by Finance director"},
            format="json",
        )
        assert resp.status_code == 200, resp.content
        wo.refresh_from_db()
        assert wo.variance_status == "auto_approved"

    def test_recovery_task_listing(self, staff, vendor, vendor2, vendor3, asset):
        from rest_framework.test import APIClient

        wo = _to_financial_review(staff, vendor, vendor2, vendor3, asset, keyfob_id="")
        wo.warranty_recovery = True
        wo.save(update_fields=["warranty_recovery"])
        t.close_work_order(wo, user=staff)
        client = APIClient()
        client.force_authenticate(user=staff)
        resp = client.get("/api/maintenance-orders/recovery-tasks/")
        assert resp.status_code == 200
        items = resp.json().get("results", resp.json())
        assert len(items) >= 1
