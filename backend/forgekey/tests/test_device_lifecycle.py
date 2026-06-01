"""Tests for ESP32 device lifecycle actions: retire / reactivate + delete gating.

Lock/unlock (enable/disable) and reset (restart) reuse existing, separately
tested endpoints; this file covers the new retire/reactivate actions and the
staff-only gate on delete.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.urls import reverse

import pytest
from rest_framework.test import APIClient

from forgekey.models import ESP32Device
from forgekey.tests.factories import ESP32DeviceFactory

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_api_client(admin_user):
    client = APIClient()
    client.force_authenticate(user=admin_user)
    return client


@pytest.fixture
def member_api_client():
    user = User.objects.create_user(username="member", email="m@example.com", password="x" * 20)
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _action_url(name, device):
    return reverse(f"forgekey:esp32-device-{name}", kwargs={"pk": device.id})


def _detail_url(device):
    return reverse("forgekey:esp32-device-detail", kwargs={"pk": device.id})


class TestRetireReactivate:
    def test_staff_retire_deactivates(self, admin_api_client):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:00:00:01", is_active=True)
        resp = admin_api_client.post(_action_url("retire", device))
        assert resp.status_code == 200, resp.data
        assert resp.json()["is_active"] is False
        device.refresh_from_db()
        assert device.is_active is False

    def test_staff_reactivate_restores(self, admin_api_client):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:00:00:02", is_active=False)
        resp = admin_api_client.post(_action_url("reactivate", device))
        assert resp.status_code == 200, resp.data
        device.refresh_from_db()
        assert device.is_active is True

    def test_member_cannot_retire(self, member_api_client):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:00:00:03", is_active=True)
        resp = member_api_client.post(_action_url("retire", device))
        assert resp.status_code == 403
        device.refresh_from_db()
        assert device.is_active is True


class TestDeleteGating:
    def test_staff_can_delete(self, admin_api_client):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:00:00:04")
        resp = admin_api_client.delete(_detail_url(device))
        assert resp.status_code == 204
        assert not ESP32Device.objects.filter(pk=device.id).exists()

    def test_member_cannot_delete(self, member_api_client):
        device = ESP32DeviceFactory(mac_address="AA:BB:CC:00:00:05")
        resp = member_api_client.delete(_detail_url(device))
        assert resp.status_code == 403
        assert ESP32Device.objects.filter(pk=device.id).exists()
