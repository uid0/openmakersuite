"""Phase 2 tests: vendor compliance celery task + admin filters."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import mail
from django.utils import timezone

import pytest

from vendors.admin import COIComplianceFilter, TDLRComplianceFilter
from vendors.models import Vendor
from vendors.tasks import flag_expiring_compliance

User = get_user_model()


def _vendor(name: str, *, tdlr_days=None, coi_days=None, active=True) -> Vendor:
    today = timezone.now().date()
    return Vendor.objects.create(
        name=name,
        vendor_kind=Vendor.KIND_HVAC,
        is_active=active,
        tdlr_license_expires_at=(
            today + timedelta(days=tdlr_days) if tdlr_days is not None else None
        ),
        coi_expires_at=today + timedelta(days=coi_days) if coi_days is not None else None,
    )


@pytest.fixture
def logistics_group(db):
    g, _ = Group.objects.get_or_create(name="Logistics")
    u = User.objects.create_user(username="logi", email="logi@example.com", password="x" * 24)
    u.groups.add(g)
    return g


@pytest.mark.django_db
class TestComplianceTask:
    """AC-2: beat task flags vendors expiring within 30 days; emails Logistics."""

    def test_emails_only_when_flagged(self, logistics_group):
        _vendor("Clean", tdlr_days=200, coi_days=200)
        mail.outbox.clear()
        counts = flag_expiring_compliance()
        assert sum(counts.values()) == 0
        assert len(mail.outbox) == 0

    def test_flags_expiring_and_expired(self, logistics_group):
        _vendor("Clean", tdlr_days=200, coi_days=200)
        _vendor("TDLR Soon", tdlr_days=10, coi_days=200)
        _vendor("COI Soon", tdlr_days=200, coi_days=10)
        _vendor("TDLR Expired", tdlr_days=-5, coi_days=200)
        _vendor("COI Expired", tdlr_days=200, coi_days=-5)
        # Inactive vendors must be ignored.
        _vendor("Inactive", tdlr_days=-5, coi_days=-5, active=False)

        mail.outbox.clear()
        counts = flag_expiring_compliance()
        assert counts == {
            "tdlr_expired": 1,
            "tdlr_expiring": 1,
            "coi_expired": 1,
            "coi_expiring": 1,
        }
        assert len(mail.outbox) == 1
        msg = mail.outbox[0]
        assert "logi@example.com" in msg.to
        assert "TDLR Soon" in msg.body
        assert "COI Expired" in msg.body
        assert "Inactive" not in msg.body

    def test_no_logistics_group_skips_send(self, db):
        _vendor("TDLR Soon", tdlr_days=10, coi_days=200)
        mail.outbox.clear()
        counts = flag_expiring_compliance()
        # Counts still returned for observability, but no mail sent.
        assert counts["tdlr_expiring"] == 1
        assert len(mail.outbox) == 0


@pytest.mark.django_db
class TestVendorAdminFilters:
    """AC-2 admin: VendorAdmin Expiring/Expired filters are wired."""

    def _filter(self, cls, value):
        from django.contrib.admin import site

        # Django 5.1 SimpleListFilter expects params as dict of list-values.
        return cls(
            request=None,
            params={cls.parameter_name: [value]},
            model=Vendor,
            model_admin=site._registry[Vendor],
        )

    def test_tdlr_expiring_soon(self, db):
        soon = _vendor("Soon", tdlr_days=10, coi_days=200)
        _vendor("Far", tdlr_days=200, coi_days=200)
        f = self._filter(TDLRComplianceFilter, "expiring_soon")
        qs = f.queryset(None, Vendor.objects.all())
        assert list(qs) == [soon]

    def test_coi_expired(self, db):
        past = _vendor("Past", tdlr_days=200, coi_days=-5)
        _vendor("Ok", tdlr_days=200, coi_days=200)
        f = self._filter(COIComplianceFilter, "expired")
        qs = f.queryset(None, Vendor.objects.all())
        assert list(qs) == [past]

    def test_tdlr_missing(self, db):
        missing = _vendor("Nope")  # no dates
        _vendor("Has", tdlr_days=200, coi_days=200)
        f = self._filter(TDLRComplianceFilter, "missing")
        qs = f.queryset(None, Vendor.objects.all())
        assert list(qs) == [missing]
