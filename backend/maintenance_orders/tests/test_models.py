"""Tests for the third-party maintenance work order models."""

import uuid
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

import pytest

from inventory.tests.factories import AssetFactory
from maintenance_orders.models import (
    ThirdPartyWorkOrder,
    ThirdPartyWorkOrderAsset,
    ThirdPartyWorkOrderAttachment,
)
from vendors.models import Vendor


@pytest.fixture
def vendor(db):
    return Vendor.objects.create(name="ACME HVAC", vendor_kind=Vendor.KIND_HVAC)


def _make_asset(**kwargs):
    kwargs.setdefault("asset_tag", f"TPWO-T-{uuid.uuid4().hex[:10].upper()}")
    return AssetFactory(**kwargs)


@pytest.fixture
def asset(db):
    return _make_asset()


@pytest.mark.django_db
class TestThirdPartyWorkOrder:
    def test_create_minimal(self, vendor, asset):
        """AC-2: ThirdPartyWorkOrder model exists with required fields/choices."""
        wo = ThirdPartyWorkOrder.objects.create(
            title="Replace compressor", vendor=vendor, asset=asset
        )
        assert wo.id is not None
        assert wo.status == ThirdPartyWorkOrder.STATUS_REQUESTED
        assert wo.work_type == ThirdPartyWorkOrder.WORK_TYPE_STANDARD
        assert wo.is_emergency is False
        assert wo.short_id.startswith("TPWO-")
        assert "Replace compressor" in str(wo)

    def test_status_choices_complete(self, vendor, asset):
        for code, _label in ThirdPartyWorkOrder.STATUS_CHOICES:
            wo = ThirdPartyWorkOrder.objects.create(
                title=f"WO {code}", vendor=vendor, asset=asset, status=code
            )
            assert wo.status == code

    def test_emergency_allows_no_asset(self, vendor):
        wo = ThirdPartyWorkOrder.objects.create(
            title="Burst pipe",
            vendor=vendor,
            asset=None,
            work_type=ThirdPartyWorkOrder.WORK_TYPE_BUILDING_EMERGENCY,
            is_emergency=True,
        )
        assert wo.asset is None
        assert wo.is_emergency is True

    def test_downtime_duration(self, vendor, asset):
        start = timezone.now()
        wo = ThirdPartyWorkOrder.objects.create(
            title="Downtime check",
            vendor=vendor,
            asset=asset,
            downtime_start=start,
            downtime_end=start + timedelta(hours=2, minutes=15),
        )
        assert wo.downtime_duration == timedelta(hours=2, minutes=15)

    def test_downtime_duration_unbounded(self, vendor, asset):
        wo = ThirdPartyWorkOrder.objects.create(title="x", vendor=vendor, asset=asset)
        assert wo.downtime_duration is None


@pytest.mark.django_db
class TestThirdPartyWorkOrderAssetM2M:
    def test_multi_asset_distribution(self, vendor):
        """AC-3: M2M through-model enables multi-asset distribution with share_pct."""
        a1 = _make_asset()
        a2 = _make_asset()
        a3 = _make_asset()
        wo = ThirdPartyWorkOrder.objects.create(title="Dispatch fee split", vendor=vendor, asset=a1)

        ThirdPartyWorkOrderAsset.objects.create(work_order=wo, asset=a1, share_pct=Decimal("33.34"))
        ThirdPartyWorkOrderAsset.objects.create(work_order=wo, asset=a2, share_pct=Decimal("33.33"))
        ThirdPartyWorkOrderAsset.objects.create(work_order=wo, asset=a3, share_pct=Decimal("33.33"))

        assert wo.asset_links.count() == 3
        assert set(wo.assets.values_list("id", flat=True)) == {a1.id, a2.id, a3.id}
        total = sum(link.share_pct for link in wo.asset_links.all())
        assert Decimal("99.99") <= total <= Decimal("100.01")

    def test_unique_asset_per_work_order(self, vendor, asset):
        from django.db import IntegrityError, transaction

        wo = ThirdPartyWorkOrder.objects.create(title="t", vendor=vendor, asset=asset)
        ThirdPartyWorkOrderAsset.objects.create(work_order=wo, asset=asset, share_pct=Decimal("50"))
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ThirdPartyWorkOrderAsset.objects.create(
                    work_order=wo, asset=asset, share_pct=Decimal("50")
                )


@pytest.mark.django_db
class TestThirdPartyWorkOrderAttachment:
    def test_attachment_kinds(self, vendor, asset):
        """AC-4: Attachments support invoices/FSRs/photos/quotes/paper-form."""
        wo = ThirdPartyWorkOrder.objects.create(title="att", vendor=vendor, asset=asset)
        from django.core.files.uploadedfile import SimpleUploadedFile

        for kind, _label in ThirdPartyWorkOrderAttachment.KIND_CHOICES:
            f = SimpleUploadedFile(f"{kind}.pdf", b"PDF", content_type="application/pdf")
            ThirdPartyWorkOrderAttachment.objects.create(
                work_order=wo, kind=kind, file=f, caption=f"{kind} doc"
            )

        kinds_on_wo = set(wo.attachments.values_list("kind", flat=True))
        assert kinds_on_wo == {k for k, _ in ThirdPartyWorkOrderAttachment.KIND_CHOICES}
