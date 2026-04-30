"""API tests for the maintenance_orders app."""

import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils.crypto import get_random_string

import pytest
from rest_framework.test import APIClient

from inventory.tests.factories import AssetFactory
from maintenance_orders.models import ThirdPartyWorkOrder, ThirdPartyWorkOrderAttachment
from vendors.models import Vendor

User = get_user_model()


def _make_asset(**kwargs):
    """AssetFactory wrapper that forces a unique asset_tag.

    Asset.save() auto-fills asset_tag from a UUID slice; under heavy parallel
    factory use a slice can collide. Make the tag explicit per test.
    """
    kwargs.setdefault("asset_tag", f"TPWO-T-{uuid.uuid4().hex[:10].upper()}")
    return AssetFactory(**kwargs)


@pytest.fixture
def admin_client(db):
    user = User.objects.create_user(
        username="mo-admin",
        email="moa@example.com",
        password=get_random_string(24),
        is_staff=True,
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client, user


@pytest.fixture
def vendor(db):
    return Vendor.objects.create(name="ACME HVAC", vendor_kind=Vendor.KIND_HVAC)


@pytest.mark.django_db
class TestThirdPartyWorkOrderAPI:
    def test_create_work_order_for_asset(self, admin_client, vendor):
        """AC-7: minimal CRUD API works for staff (work order create)."""
        client, user = admin_client
        asset = _make_asset()
        resp = client.post(
            "/api/maintenance-orders/work-orders/",
            {
                "title": "HVAC quarterly service",
                "vendor": str(vendor.id),
                "asset": str(asset.id),
                "work_type": ThirdPartyWorkOrder.WORK_TYPE_STANDARD,
                "nte_amount": "750.00",
                "par_cost_buffer": "75.00",
            },
            format="json",
        )
        assert resp.status_code == 201, resp.content
        wo = ThirdPartyWorkOrder.objects.get(id=resp.json()["id"])
        assert wo.opened_by == user
        assert wo.nte_amount == Decimal("750.00")

    def test_standard_work_order_requires_asset(self, admin_client, vendor):
        """SIG work must be linked to an asset."""
        client, _ = admin_client
        resp = client.post(
            "/api/maintenance-orders/work-orders/",
            {
                "title": "Missing asset",
                "vendor": str(vendor.id),
                "work_type": ThirdPartyWorkOrder.WORK_TYPE_STANDARD,
            },
            format="json",
        )
        assert resp.status_code == 400
        assert "asset" in resp.json()["error"]["details"]

    def test_emergency_work_order_can_skip_asset(self, admin_client, vendor):
        client, _ = admin_client
        resp = client.post(
            "/api/maintenance-orders/work-orders/",
            {
                "title": "Building emergency",
                "vendor": str(vendor.id),
                "work_type": ThirdPartyWorkOrder.WORK_TYPE_BUILDING_EMERGENCY,
                "is_emergency": True,
            },
            format="json",
        )
        assert resp.status_code == 201, resp.content

    def test_asset_link_distribution(self, admin_client, vendor):
        """AC-3 + AC-7: M2M asset distribution via API."""
        client, _ = admin_client
        a1 = _make_asset()
        a2 = _make_asset()
        wo = ThirdPartyWorkOrder.objects.create(title="multi", vendor=vendor, asset=a1)

        for asset, share in [(a1, "60.00"), (a2, "40.00")]:
            resp = client.post(
                "/api/maintenance-orders/asset-links/",
                {"work_order": str(wo.id), "asset": str(asset.id), "share_pct": share},
                format="json",
            )
            assert resp.status_code == 201, resp.content

        listing = client.get(f"/api/maintenance-orders/asset-links/?work_order={wo.id}").json()
        assert listing["count"] == 2

        # Nested representation surfaces the links on the WO detail.
        wo_detail = client.get(f"/api/maintenance-orders/work-orders/{wo.id}/").json()
        assert len(wo_detail["asset_links"]) == 2

    def test_attachment_upload(self, admin_client, vendor):
        client, user = admin_client
        asset = _make_asset()
        wo = ThirdPartyWorkOrder.objects.create(title="att", vendor=vendor, asset=asset)
        from django.core.files.uploadedfile import SimpleUploadedFile

        f = SimpleUploadedFile("invoice.pdf", b"%PDF-1.4 fake", content_type="application/pdf")
        resp = client.post(
            "/api/maintenance-orders/attachments/",
            {
                "work_order": str(wo.id),
                "file": f,
                "kind": ThirdPartyWorkOrderAttachment.KIND_INVOICE,
                "caption": "April invoice",
            },
            format="multipart",
        )
        assert resp.status_code == 201, resp.content
        att = ThirdPartyWorkOrderAttachment.objects.get(id=resp.json()["id"])
        assert att.uploaded_by == user
        assert att.kind == ThirdPartyWorkOrderAttachment.KIND_INVOICE

    def test_non_staff_cannot_create(self, db, vendor):
        user = User.objects.create_user(
            username="member", email="m@example.com", password=get_random_string(24)
        )
        client = APIClient()
        client.force_authenticate(user=user)
        asset = _make_asset()
        resp = client.post(
            "/api/maintenance-orders/work-orders/",
            {"title": "no", "vendor": str(vendor.id), "asset": str(asset.id)},
            format="json",
        )
        assert resp.status_code in (401, 403)

    def test_non_staff_can_list(self, db, vendor):
        user = User.objects.create_user(
            username="reader", email="r@example.com", password=get_random_string(24)
        )
        client = APIClient()
        client.force_authenticate(user=user)
        ThirdPartyWorkOrder.objects.create(title="visible", vendor=vendor, asset=_make_asset())
        resp = client.get("/api/maintenance-orders/work-orders/")
        assert resp.status_code == 200
        assert resp.json()["count"] >= 1


class TestAdminSmoke:
    """AC-6: Django admin registers all three with sensible search/filter."""

    def test_models_registered_with_admin(self):
        from django.contrib import admin as django_admin

        from maintenance_orders.models import (
            ThirdPartyWorkOrder,
            ThirdPartyWorkOrderAsset,
            ThirdPartyWorkOrderAttachment,
        )

        registered = django_admin.site._registry
        assert ThirdPartyWorkOrder in registered
        assert ThirdPartyWorkOrderAsset in registered
        assert ThirdPartyWorkOrderAttachment in registered

        wo_admin = registered[ThirdPartyWorkOrder]
        assert "vendor" in wo_admin.search_fields[1] or any(
            "vendor" in f for f in wo_admin.search_fields
        )
        assert "status" in wo_admin.list_filter
        assert "is_emergency" in wo_admin.list_filter

    def test_vendor_registered_with_admin(self):
        from django.contrib import admin as django_admin

        from vendors.models import Vendor

        assert Vendor in django_admin.site._registry
        vendor_admin = django_admin.site._registry[Vendor]
        assert "vendor_kind" in vendor_admin.list_filter
        assert "is_active" in vendor_admin.list_filter
