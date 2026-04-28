"""Tests for the Vendor model."""

from datetime import date, timedelta

from django.utils import timezone

import pytest

from vendors.models import Vendor


@pytest.mark.django_db
class TestVendorModel:
    def test_create_minimal_vendor(self):
        """AC-1: Vendor model exists with required fields and active flag."""
        vendor = Vendor.objects.create(name="ACME HVAC", vendor_kind=Vendor.KIND_HVAC)
        assert vendor.id is not None
        assert vendor.is_active is True
        assert vendor.vendor_kind == Vendor.KIND_HVAC
        assert str(vendor) == "ACME HVAC (HVAC)"

    def test_tdlr_and_coi_fields_persist(self):
        """AC-1: TDLR + COI fields are stored on the Vendor."""
        vendor = Vendor.objects.create(
            name="Pro Plumbing",
            vendor_kind=Vendor.KIND_PLUMBING,
            tdlr_license_number="TX-PL-12345",
            tdlr_license_expires_at=date(2027, 6, 30),
            coi_provider="State Farm",
            coi_policy_number="POL-9876",
            coi_expires_at=date(2026, 12, 31),
        )
        vendor.refresh_from_db()
        assert vendor.tdlr_license_number == "TX-PL-12345"
        assert vendor.tdlr_license_expires_at == date(2027, 6, 30)
        assert vendor.coi_policy_number == "POL-9876"

    def test_tdlr_expired_helper(self):
        today = timezone.now().date()
        active = Vendor.objects.create(
            name="A",
            vendor_kind=Vendor.KIND_HVAC,
            tdlr_license_expires_at=today + timedelta(days=10),
        )
        expired = Vendor.objects.create(
            name="B",
            vendor_kind=Vendor.KIND_HVAC,
            tdlr_license_expires_at=today - timedelta(days=1),
        )
        no_license = Vendor.objects.create(name="C", vendor_kind=Vendor.KIND_HVAC)
        assert active.tdlr_is_expired is False
        assert expired.tdlr_is_expired is True
        assert no_license.tdlr_is_expired is False

    def test_coi_expired_helper(self):
        today = timezone.now().date()
        active = Vendor.objects.create(
            name="A", vendor_kind=Vendor.KIND_HVAC, coi_expires_at=today + timedelta(days=10)
        )
        expired = Vendor.objects.create(
            name="B", vendor_kind=Vendor.KIND_HVAC, coi_expires_at=today - timedelta(days=1)
        )
        assert active.coi_is_expired is False
        assert expired.coi_is_expired is True
