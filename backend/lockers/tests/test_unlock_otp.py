"""Tests for the locker unlock + OTP management API actions."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import override_settings
from django.urls import reverse

import pytest
from rest_framework.test import APIClient

from forgekey.models import DeviceType, ESP32Device
from forgekey.services.jwt_signing import generate_jwt_signing_keypair
from inventory.tests.factories import LocationFactory
from lockers.models import Locker, LockerDevice

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture
def signed_settings():
    private_pem, _ = generate_jwt_signing_keypair()
    with override_settings(FORGEKEY_JWT_SIGNING_KEY=private_pem, FORGEKEY_JWT_KEY_ID="test-kid"):
        yield


@pytest.fixture
def mqtt_ok():
    client = MagicMock()
    client.publish.return_value = MagicMock(rc=0)
    # publish_command imports get_mqtt_client at module load, so patch the
    # name where it is looked up (device_commands), not where it is defined.
    with patch("forgekey.services.device_commands.get_mqtt_client", return_value=client):
        yield client


@pytest.fixture
def locker_with_latch():
    sig = Group.objects.create(name="Wood Shop SIG")
    locker = Locker.objects.create(
        name="L-1", slug="l-1", location=LocationFactory(), owning_sig=sig
    )
    latch_type, _ = DeviceType.objects.get_or_create(
        code="locker_latch", defaults={"name": "Locker latch controller"}
    )
    device = ESP32Device.objects.create(mac_address="AA:BB:CC:11:22:33", device_type=latch_type)
    LockerDevice.objects.create(
        locker=locker, device=device, role=LockerDevice.ROLE_LATCH, is_primary=True
    )
    return locker


@pytest.fixture
def staff_client():
    user = User.objects.create_user(
        username="ops", email="o@example.com", password="x" * 24, is_staff=True
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def member_client():
    user = User.objects.create_user(username="member", email="m@example.com", password="x" * 24)
    client = APIClient()
    client.force_authenticate(user=user)
    return client


class TestUnlock:
    def test_staff_unlock_publishes(
        self, staff_client, locker_with_latch, signed_settings, mqtt_ok
    ):
        url = reverse("lockers:locker-unlock", kwargs={"pk": locker_with_latch.pk})
        resp = staff_client.post(url)
        assert resp.status_code == 200, resp.data
        assert resp.json()["status"] == "unlock_sent"
        assert mqtt_ok.publish.called

    def test_member_without_access_denied(
        self, member_client, locker_with_latch, signed_settings, mqtt_ok
    ):
        url = reverse("lockers:locker-unlock", kwargs={"pk": locker_with_latch.pk})
        resp = member_client.post(url)
        assert resp.status_code == 403
        assert "reason" in resp.json()
        assert not mqtt_ok.publish.called

    def test_unlock_without_latch_returns_409(self, staff_client, signed_settings, mqtt_ok):
        sig = Group.objects.create(name="Empty SIG")
        locker = Locker.objects.create(
            name="E-1", slug="e-1", location=LocationFactory(), owning_sig=sig
        )
        url = reverse("lockers:locker-unlock", kwargs={"pk": locker.pk})
        resp = staff_client.post(url)
        assert resp.status_code == 409


class TestOtp:
    def test_staff_issues_otp(self, staff_client, locker_with_latch):
        url = reverse("lockers:locker-issue-otp", kwargs={"pk": locker_with_latch.pk})
        resp = staff_client.post(url)
        assert resp.status_code == 201, resp.data
        body = resp.json()
        assert len(body["code"]) >= 6
        assert body["state"] == "active"

    def test_member_without_access_cannot_issue(self, member_client, locker_with_latch):
        url = reverse("lockers:locker-issue-otp", kwargs={"pk": locker_with_latch.pk})
        assert member_client.post(url).status_code == 403

    def test_manager_lists_and_revokes(self, staff_client, locker_with_latch):
        issue = staff_client.post(
            reverse("lockers:locker-issue-otp", kwargs={"pk": locker_with_latch.pk})
        )
        otp_id = issue.json()["id"]

        listing = staff_client.get(
            reverse("lockers:locker-otps", kwargs={"pk": locker_with_latch.pk})
        )
        assert listing.status_code == 200
        assert any(o["id"] == otp_id for o in listing.json())

        revoke = staff_client.post(
            reverse("lockers:locker-revoke-otp", kwargs={"pk": locker_with_latch.pk}),
            {"otp_id": otp_id},
            format="json",
        )
        assert revoke.status_code == 200
        assert revoke.json()["state"] == "revoked"

    def test_member_cannot_list_otps(self, member_client, locker_with_latch):
        resp = member_client.get(
            reverse("lockers:locker-otps", kwargs={"pk": locker_with_latch.pk})
        )
        assert resp.status_code == 403
