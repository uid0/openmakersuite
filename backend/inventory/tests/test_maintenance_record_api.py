"""API tests for MaintenanceRecordViewSet."""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from django.utils.crypto import get_random_string

import pytest
from rest_framework.test import APIClient

from inventory.models import MaintenanceRecord
from inventory.tests.factories import AssetFactory
from membership.models import SIGAdmin
from vendors.models import Vendor

User = get_user_model()
pytestmark = pytest.mark.django_db


LIST_URL = "/api/inventory/maintenance-records/"


def _detail_url(rec_id):
    return f"/api/inventory/maintenance-records/{rec_id}/"


def _user(username, **flags):
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password=get_random_string(24),
        **flags,
    )


def _client(user=None):
    c = APIClient()
    if user is not None:
        c.force_authenticate(user=user)
    return c


@pytest.fixture
def asset():
    return AssetFactory()


@pytest.fixture
def vendor():
    return Vendor.objects.create(name="ACME HVAC", vendor_kind=Vendor.KIND_HVAC)


def _payload(asset, vendor, **overrides):
    data = {
        "asset": str(asset.id),
        "title": "Annual HVAC service",
        "description": "Filter swap + refrigerant top-off",
        "completed_on": "2024-03-14",
        "vendor": str(vendor.id),
        "cost": "450.00",
        "invoice_number": "INV-001",
    }
    data.update(overrides)
    return data


class TestMaintenanceRecordCreate:
    def test_anonymous_cannot_create(self, asset, vendor):
        resp = _client().post(LIST_URL, data=_payload(asset, vendor), format="json")
        assert resp.status_code in (401, 403)

    def test_volunteer_cannot_create(self, asset, vendor):
        resp = _client(_user("vol")).post(LIST_URL, data=_payload(asset, vendor), format="json")
        assert resp.status_code == 403

    def test_staff_can_create(self, asset, vendor):
        staff = _user("staff", is_staff=True)
        resp = _client(staff).post(LIST_URL, data=_payload(asset, vendor), format="json")
        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert body["title"] == "Annual HVAC service"
        assert body["cost"] == "450.00"
        assert body["vendor_name"] == "ACME HVAC"
        rec = MaintenanceRecord.objects.get(id=body["id"])
        assert rec.recorded_by_id == staff.id

    def test_sig_admin_can_create(self, asset, vendor):
        sig_user = _user("sigleader")
        SIGAdmin.objects.create(user=sig_user, group=Group.objects.create(name="3D Printing SIG"))
        resp = _client(sig_user).post(LIST_URL, data=_payload(asset, vendor), format="json")
        assert resp.status_code == 201, resp.content

    def test_create_requires_vendor_or_internal(self, asset):
        staff = _user("staff2", is_staff=True)
        data = {
            "asset": str(asset.id),
            "title": "Unknown performer",
            "description": "Test",
            "completed_on": "2024-03-14",
        }
        resp = _client(staff).post(LIST_URL, data=data, format="json")
        assert resp.status_code == 400, resp.content

    def test_create_rejects_future_date(self, asset, vendor):
        staff = _user("staff3", is_staff=True)
        future = (timezone.localdate() + timedelta(days=1)).isoformat()
        resp = _client(staff).post(
            LIST_URL,
            data=_payload(asset, vendor, completed_on=future),
            format="json",
        )
        assert resp.status_code == 400, resp.content
        body = resp.json()
        flat = str(body)
        assert "completed_on" in flat

    def test_create_with_internal_user(self, asset):
        staff = _user("staff4", is_staff=True)
        worker = _user("worker")
        data = {
            "asset": str(asset.id),
            "title": "In-house belt swap",
            "description": "Internal staff replaced belt",
            "completed_on": "2024-04-01",
            "performed_by_internal": worker.id,
            "cost": "0.00",
        }
        resp = _client(staff).post(LIST_URL, data=data, format="json")
        assert resp.status_code == 201, resp.content
        assert resp.json()["performed_by_internal_username"] == "worker"

    def test_create_with_attachment(self, asset, vendor):
        staff = _user("staff_att", is_staff=True)
        upload = SimpleUploadedFile("invoice.pdf", b"%PDF-1.4 fake", content_type="application/pdf")
        resp = _client(staff).post(
            LIST_URL,
            data={
                "asset": str(asset.id),
                "title": "With attachment",
                "description": "Has invoice attached",
                "completed_on": "2024-03-14",
                "vendor": str(vendor.id),
                "attachment": upload,
            },
            format="multipart",
        )
        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert body["attachment_url"]


class TestMaintenanceRecordRead:
    def test_volunteer_can_list(self, asset, vendor):
        MaintenanceRecord.objects.create(
            asset=asset,
            title="Past job",
            description="x",
            completed_on=date(2024, 3, 14),
            vendor=vendor,
        )
        resp = _client(_user("vol_read")).get(LIST_URL)
        assert resp.status_code == 200
        body = resp.json()
        results = body["results"] if isinstance(body, dict) else body
        assert len(results) >= 1

    def test_filter_by_asset(self, asset, vendor):
        other = AssetFactory()
        MaintenanceRecord.objects.create(
            asset=asset,
            title="A",
            description="",
            completed_on=date(2024, 3, 14),
            vendor=vendor,
        )
        MaintenanceRecord.objects.create(
            asset=other,
            title="B",
            description="",
            completed_on=date(2024, 3, 14),
            vendor=vendor,
        )
        resp = _client(_user("vol_filter")).get(LIST_URL, {"asset": str(asset.id)})
        assert resp.status_code == 200
        body = resp.json()
        results = body["results"] if isinstance(body, dict) else body
        for row in results:
            assert row["asset"] == str(asset.id)

    def test_filter_by_date_range(self, asset, vendor):
        MaintenanceRecord.objects.create(
            asset=asset,
            title="Older",
            description="",
            completed_on=date(2020, 1, 1),
            vendor=vendor,
        )
        MaintenanceRecord.objects.create(
            asset=asset,
            title="Newer",
            description="",
            completed_on=date(2024, 6, 1),
            vendor=vendor,
        )
        resp = _client(_user("vol_range")).get(
            LIST_URL, {"asset": str(asset.id), "since": "2023-01-01"}
        )
        assert resp.status_code == 200
        body = resp.json()
        results = body["results"] if isinstance(body, dict) else body
        titles = [r["title"] for r in results]
        assert "Newer" in titles
        assert "Older" not in titles


class TestMaintenanceRecordUpdate:
    def test_staff_can_patch(self, asset, vendor):
        staff = _user("staff5", is_staff=True)
        rec = MaintenanceRecord.objects.create(
            asset=asset,
            title="Old title",
            description="x",
            completed_on=date(2024, 3, 14),
            vendor=vendor,
        )
        resp = _client(staff).patch(_detail_url(rec.id), data={"title": "New title"}, format="json")
        assert resp.status_code == 200
        assert resp.json()["title"] == "New title"

    def test_volunteer_cannot_patch(self, asset, vendor):
        rec = MaintenanceRecord.objects.create(
            asset=asset,
            title="x",
            description="x",
            completed_on=date(2024, 3, 14),
            vendor=vendor,
        )
        resp = _client(_user("vol2")).patch(
            _detail_url(rec.id), data={"title": "Hack"}, format="json"
        )
        assert resp.status_code == 403


class TestMaintenanceRecordDelete:
    def test_staff_can_delete(self, asset, vendor):
        staff = _user("staff6", is_staff=True)
        rec = MaintenanceRecord.objects.create(
            asset=asset,
            title="x",
            description="x",
            completed_on=date(2024, 3, 14),
            vendor=vendor,
        )
        resp = _client(staff).delete(_detail_url(rec.id))
        assert resp.status_code == 204
        assert not MaintenanceRecord.objects.filter(id=rec.id).exists()

    def test_sig_admin_cannot_delete(self, asset, vendor):
        sig_user = _user("sigleader2")
        SIGAdmin.objects.create(user=sig_user, group=Group.objects.create(name="HVAC SIG"))
        rec = MaintenanceRecord.objects.create(
            asset=asset,
            title="x",
            description="x",
            completed_on=date(2024, 3, 14),
            vendor=vendor,
        )
        resp = _client(sig_user).delete(_detail_url(rec.id))
        assert resp.status_code == 403

    def test_volunteer_cannot_delete(self, asset, vendor):
        rec = MaintenanceRecord.objects.create(
            asset=asset,
            title="x",
            description="x",
            completed_on=date(2024, 3, 14),
            vendor=vendor,
        )
        resp = _client(_user("vol3")).delete(_detail_url(rec.id))
        assert resp.status_code == 403
