"""API tests for the vendors app."""

from django.contrib.auth import get_user_model
from django.utils.crypto import get_random_string

import pytest
from rest_framework.test import APIClient

from vendors.models import Vendor

User = get_user_model()


@pytest.fixture
def admin_client(db):
    user = User.objects.create_user(
        username="vendor-admin",
        email="va@example.com",
        password=get_random_string(24),
        is_staff=True,
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def member_client(db):
    user = User.objects.create_user(
        username="vendor-member",
        email="vm@example.com",
        password=get_random_string(24),
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.django_db
class TestVendorAPI:
    def test_admin_can_create(self, admin_client):
        """AC-7: minimal CRUD works for staff."""
        resp = admin_client.post(
            "/api/vendors/vendors/",
            {"name": "ACME HVAC", "vendor_kind": Vendor.KIND_HVAC},
            format="json",
        )
        assert resp.status_code == 201, resp.content
        assert Vendor.objects.filter(name="ACME HVAC").exists()

    def test_member_cannot_create(self, member_client):
        resp = member_client.post(
            "/api/vendors/vendors/",
            {"name": "Should Fail", "vendor_kind": Vendor.KIND_OTHER},
            format="json",
        )
        assert resp.status_code in (401, 403)

    def test_member_can_list(self, member_client):
        Vendor.objects.create(name="Visible", vendor_kind=Vendor.KIND_HVAC)
        resp = member_client.get("/api/vendors/vendors/")
        assert resp.status_code == 200
        assert resp.json()["count"] >= 1

    def test_active_only_filter(self, admin_client):
        Vendor.objects.create(name="On", vendor_kind=Vendor.KIND_HVAC, is_active=True)
        Vendor.objects.create(name="Off", vendor_kind=Vendor.KIND_HVAC, is_active=False)
        resp = admin_client.get("/api/vendors/vendors/?active_only=1")
        assert resp.status_code == 200
        names = {row["name"] for row in resp.json()["results"]}
        assert "On" in names
        assert "Off" not in names

    def test_serializer_round_trip(self, admin_client):
        payload = {
            "name": "Round Trip",
            "vendor_kind": Vendor.KIND_ELECTRICAL,
            "tdlr_license_number": "TX-EL-1",
            "coi_provider": "ACME Insurance",
        }
        create = admin_client.post("/api/vendors/vendors/", payload, format="json")
        assert create.status_code == 201, create.content
        vid = create.json()["id"]
        get = admin_client.get(f"/api/vendors/vendors/{vid}/")
        assert get.status_code == 200
        body = get.json()
        assert body["name"] == "Round Trip"
        assert body["vendor_kind"] == Vendor.KIND_ELECTRICAL
        assert body["vendor_kind_display"] == "Electrical"
        assert body["tdlr_license_number"] == "TX-EL-1"
        assert body["tdlr_is_expired"] is False
