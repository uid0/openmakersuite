"""Phase 2 tests: AssetWarranty model + warranty gate on WO creation."""

import uuid
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.crypto import get_random_string

import pytest
from rest_framework.test import APIClient

from inventory.tests.factories import AssetFactory
from maintenance_orders.models import AssetWarranty, ThirdPartyWorkOrder
from maintenance_orders.signals import active_warranty_for
from vendors.models import Vendor

User = get_user_model()


def _make_asset(**kwargs):
    kwargs.setdefault("asset_tag", f"WAR-T-{uuid.uuid4().hex[:10].upper()}")
    return AssetFactory(**kwargs)


@pytest.fixture
def vendor(db):
    return Vendor.objects.create(name="ACME HVAC", vendor_kind=Vendor.KIND_HVAC)


@pytest.fixture
def admin_client(db):
    user = User.objects.create_user(
        username="war-admin",
        email="war@example.com",
        password=get_random_string(24),
        is_staff=True,
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client, user


@pytest.mark.django_db
class TestAssetWarrantyModel:
    """AC-1: AssetWarranty model with computed end/duration."""

    def test_duration_computes_end_date(self):
        asset = _make_asset()
        w = AssetWarranty.objects.create(
            asset=asset,
            install_date=date(2026, 1, 1),
            duration_days=365,
            provider="DeWalt",
        )
        assert w.end_date == date(2026, 1, 1) + timedelta(days=365)

    def test_end_date_computes_duration(self):
        asset = _make_asset()
        w = AssetWarranty.objects.create(
            asset=asset,
            install_date=date(2026, 1, 1),
            end_date=date(2027, 1, 1),
            provider="Bosch",
        )
        assert w.duration_days == 365

    def test_neither_field_rejected(self):
        from django.core.exceptions import ValidationError

        asset = _make_asset()
        w = AssetWarranty(asset=asset, install_date=date(2026, 1, 1), provider="None")
        with pytest.raises(ValidationError):
            w.full_clean()

    def test_is_active_today(self):
        asset = _make_asset()
        today = timezone.now().date()
        w = AssetWarranty.objects.create(
            asset=asset,
            install_date=today - timedelta(days=10),
            duration_days=365,
            provider="X",
        )
        assert w.is_active is True

    def test_expired_not_active(self):
        asset = _make_asset()
        today = timezone.now().date()
        w = AssetWarranty.objects.create(
            asset=asset,
            install_date=today - timedelta(days=400),
            duration_days=30,
            provider="X",
        )
        assert w.is_active is False


@pytest.mark.django_db
class TestWarrantyGate:
    """AC-3: WO creation evaluates warranty + sets warranty_recovery."""

    def test_active_warranty_flags_recovery(self, vendor):
        asset = _make_asset()
        today = timezone.now().date()
        AssetWarranty.objects.create(
            asset=asset,
            install_date=today - timedelta(days=30),
            duration_days=365,
            provider="Manufacturer",
        )
        wo = ThirdPartyWorkOrder.objects.create(title="Compressor swap", vendor=vendor, asset=asset)
        wo.refresh_from_db()
        assert wo.warranty_recovery is True

    def test_expired_warranty_does_not_flag(self, vendor):
        asset = _make_asset()
        today = timezone.now().date()
        AssetWarranty.objects.create(
            asset=asset,
            install_date=today - timedelta(days=400),
            duration_days=30,
            provider="Manufacturer",
        )
        wo = ThirdPartyWorkOrder.objects.create(title="Compressor swap", vendor=vendor, asset=asset)
        wo.refresh_from_db()
        assert wo.warranty_recovery is False

    def test_no_warranty_does_not_flag(self, vendor):
        asset = _make_asset()
        wo = ThirdPartyWorkOrder.objects.create(title="Compressor swap", vendor=vendor, asset=asset)
        wo.refresh_from_db()
        assert wo.warranty_recovery is False

    def test_emergency_without_asset_no_warranty_check(self, vendor):
        wo = ThirdPartyWorkOrder.objects.create(
            title="Burst pipe",
            vendor=vendor,
            asset=None,
            work_type=ThirdPartyWorkOrder.WORK_TYPE_BUILDING_EMERGENCY,
            is_emergency=True,
        )
        wo.refresh_from_db()
        assert wo.warranty_recovery is False

    def test_active_warranty_for_helper_picks_latest_end(self, vendor):
        asset = _make_asset()
        today = timezone.now().date()
        AssetWarranty.objects.create(
            asset=asset,
            install_date=today - timedelta(days=10),
            duration_days=30,
            provider="Short",
        )
        long_w = AssetWarranty.objects.create(
            asset=asset,
            install_date=today - timedelta(days=10),
            duration_days=730,
            provider="Extended",
        )
        winner = active_warranty_for(asset)
        assert winner == long_w


@pytest.mark.django_db
class TestAssetWoStatusEndpoint:
    """AC-4 backend: status endpoint returns warranty + vendor compliance."""

    def test_active_warranty_in_payload(self, admin_client, vendor):
        client, _ = admin_client
        asset = _make_asset()
        today = timezone.now().date()
        AssetWarranty.objects.create(
            asset=asset,
            install_date=today - timedelta(days=30),
            duration_days=365,
            provider="Mfg",
        )
        resp = client.get(f"/api/maintenance-orders/assets/{asset.id}/wo-status/")
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body["warranty"] is not None
        assert body["warranty"]["is_active"] is True
        assert body["warranty_recovery_recommended"] is True

    def test_no_warranty_clean(self, admin_client):
        client, _ = admin_client
        asset = _make_asset()
        resp = client.get(f"/api/maintenance-orders/assets/{asset.id}/wo-status/")
        assert resp.status_code == 200
        assert resp.json()["warranty"] is None
        assert resp.json()["warranty_recovery_recommended"] is False

    def test_vendor_compliance_buckets(self, admin_client):
        client, _ = admin_client
        asset = _make_asset()
        today = timezone.now().date()
        clean = Vendor.objects.create(
            name="Clean",
            vendor_kind=Vendor.KIND_HVAC,
            tdlr_license_expires_at=today + timedelta(days=180),
            coi_expires_at=today + timedelta(days=180),
        )
        expiring = Vendor.objects.create(
            name="Soon",
            vendor_kind=Vendor.KIND_HVAC,
            tdlr_license_expires_at=today + timedelta(days=10),
            coi_expires_at=today + timedelta(days=10),
        )
        expired = Vendor.objects.create(
            name="Past",
            vendor_kind=Vendor.KIND_HVAC,
            tdlr_license_expires_at=today - timedelta(days=2),
            coi_expires_at=today - timedelta(days=2),
        )
        for v, expected in [
            (clean, "ok"),
            (expiring, "expiring_soon"),
            (expired, "expired"),
        ]:
            resp = client.get(
                f"/api/maintenance-orders/assets/{asset.id}/wo-status/",
                {"vendor": str(v.id)},
            )
            assert resp.status_code == 200, resp.content
            comp = resp.json()["vendor_compliance"]
            assert comp["tdlr_status"] == expected, (v.name, comp)
            assert comp["coi_status"] == expected
