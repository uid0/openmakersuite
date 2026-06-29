"""Tests for DeviceType management — reads open, writes staff-only.

The device-type catalog is seeded by migrations, so management is mostly
editing (rename / describe / activate) existing types; creating only matters
when a new enum code is added. Both paths are staff-gated.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.urls import reverse

import pytest
from rest_framework.test import APIClient

from forgekey.models import DeviceType

User = get_user_model()
pytestmark = pytest.mark.django_db


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def admin_api_client(admin_user):
    return _client(admin_user)


@pytest.fixture
def member_api_client():
    return _client(User.objects.create_user(username="m", email="m@example.com", password="x" * 20))


class TestDeviceTypeManagement:
    def test_authenticated_member_can_list(self, member_api_client):
        resp = member_api_client.get(reverse("forgekey:device-type-list"))
        assert resp.status_code == 200

    def test_staff_can_rename_and_deactivate(self, admin_api_client):
        dt = DeviceType.objects.first()  # seeded by migration
        assert dt is not None
        url = reverse("forgekey:device-type-detail", kwargs={"pk": dt.pk})
        resp = admin_api_client.patch(
            url, {"name": "Renamed Type ZZ", "is_active": False}, format="json"
        )
        assert resp.status_code == 200, resp.data
        dt.refresh_from_db()
        assert dt.name == "Renamed Type ZZ"
        assert dt.is_active is False

    def test_member_cannot_edit(self, member_api_client):
        dt = DeviceType.objects.first()
        url = reverse("forgekey:device-type-detail", kwargs={"pk": dt.pk})
        resp = member_api_client.patch(url, {"is_active": False}, format="json")
        assert resp.status_code == 403

    def test_member_cannot_create(self, member_api_client):
        DeviceType.objects.filter(
            code="power_relay"
        ).delete()  # free the code; member still blocked
        resp = member_api_client.post(
            reverse("forgekey:device-type-list"),
            {"name": "Member Relay", "code": "power_relay"},
            format="json",
        )
        assert resp.status_code == 403

    def test_staff_can_create_a_freed_code(self, admin_api_client):
        DeviceType.objects.filter(code="power_relay").delete()
        resp = admin_api_client.post(
            reverse("forgekey:device-type-list"),
            {"name": "Bench Relay", "code": "power_relay", "description": "", "is_active": True},
            format="json",
        )
        assert resp.status_code == 201, resp.data
        assert DeviceType.objects.filter(code="power_relay", name="Bench Relay").exists()
