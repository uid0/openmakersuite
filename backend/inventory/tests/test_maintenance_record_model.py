"""Tests for the inventory.MaintenanceRecord model."""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

import pytest

from inventory.models import MaintenanceRecord
from inventory.tests.factories import AssetFactory
from vendors.models import Vendor

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture
def asset():
    return AssetFactory()


@pytest.fixture
def vendor():
    return Vendor.objects.create(name="ACME HVAC", vendor_kind=Vendor.KIND_HVAC)


@pytest.fixture
def staff_user():
    return User.objects.create_user(
        username="staff1", email="staff1@example.com", password="pw", is_staff=True
    )


class TestMaintenanceRecordModel:
    def test_create_with_vendor_only(self, asset, vendor):
        rec = MaintenanceRecord(
            asset=asset,
            title="Annual HVAC service",
            description="Replaced filters and topped off refrigerant",
            completed_on=date(2024, 3, 14),
            vendor=vendor,
            cost=Decimal("450.00"),
        )
        rec.full_clean()
        rec.save()
        assert rec.id is not None
        assert rec.performed_by_internal_id is None

    def test_create_with_internal_only(self, asset, staff_user):
        rec = MaintenanceRecord(
            asset=asset,
            title="Belt swap",
            description="Internal staff replaced drive belt",
            completed_on=date(2024, 5, 1),
            performed_by_internal=staff_user,
        )
        rec.full_clean()
        rec.save()
        assert rec.vendor_id is None

    def test_create_with_both_vendor_and_internal(self, asset, vendor, staff_user):
        """A shadowed vendor visit may have both an internal staff and vendor."""
        rec = MaintenanceRecord(
            asset=asset,
            title="Shadow visit",
            description="Vendor performed work while staff member shadowed",
            completed_on=date(2024, 6, 4),
            vendor=vendor,
            performed_by_internal=staff_user,
        )
        rec.full_clean()
        rec.save()
        assert rec.vendor_id == vendor.id
        assert rec.performed_by_internal_id == staff_user.id

    def test_requires_vendor_or_internal(self, asset):
        rec = MaintenanceRecord(
            asset=asset,
            title="Empty performer",
            description="Neither vendor nor staff recorded",
            completed_on=date(2024, 7, 1),
        )
        with pytest.raises(ValidationError):
            rec.full_clean()

    def test_completed_on_cannot_be_future(self, asset, vendor):
        future = timezone.localdate() + timedelta(days=1)
        rec = MaintenanceRecord(
            asset=asset,
            title="Future job",
            description="Scheduled, not historical",
            completed_on=future,
            vendor=vendor,
        )
        with pytest.raises(ValidationError):
            rec.full_clean()

    def test_completed_on_today_is_allowed(self, asset, vendor):
        rec = MaintenanceRecord(
            asset=asset,
            title="Today's job",
            description="Just finished",
            completed_on=timezone.localdate(),
            vendor=vendor,
        )
        rec.full_clean()

    def test_ordering_recent_first(self, asset, vendor):
        older = MaintenanceRecord.objects.create(
            asset=asset,
            title="Old",
            description="Old job",
            completed_on=date(2020, 1, 1),
            vendor=vendor,
        )
        newer = MaintenanceRecord.objects.create(
            asset=asset,
            title="New",
            description="Recent job",
            completed_on=date(2024, 1, 1),
            vendor=vendor,
        )
        ids = list(MaintenanceRecord.objects.filter(asset=asset).values_list("id", flat=True))
        assert ids == [newer.id, older.id]

    def test_str_representation(self, asset, vendor):
        rec = MaintenanceRecord.objects.create(
            asset=asset,
            title="Pump rebuild",
            description="...",
            completed_on=date(2024, 2, 14),
            vendor=vendor,
        )
        text = str(rec)
        assert "Pump rebuild" in text
        assert "2024-02-14" in text
