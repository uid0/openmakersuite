"""Tests for the locker setup API: CRUD + device binding (Phase 4).

Covers manager-gated create / edit / delete, ESP32 device bind + unbind
(including primary demotion and duplicate-role conflict), and the
available-certifications picker feed.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.urls import reverse

import pytest
from rest_framework.test import APIClient

from forgekey.models import DeviceType, ESP32Device
from inventory.tests.factories import LocationFactory
from lockers.models import Locker, LockerDevice
from membership.models import Certification, SIGAdmin

User = get_user_model()
pytestmark = pytest.mark.django_db


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def sig():
    return Group.objects.create(name="Wood Shop SIG")


@pytest.fixture
def location():
    return LocationFactory()


@pytest.fixture
def staff_client():
    return _client(
        User.objects.create_user(
            username="ops", email="o@example.com", password="x" * 24, is_staff=True
        )
    )


@pytest.fixture
def member_client():
    return _client(
        User.objects.create_user(username="member", email="m@example.com", password="x" * 24)
    )


@pytest.fixture
def device():
    dt, _ = DeviceType.objects.get_or_create(
        code="locker_latch", defaults={"name": "Locker latch controller"}
    )
    return ESP32Device.objects.create(mac_address="AA:BB:CC:11:22:33", device_type=dt)


def _make_locker(sig, location, **kw):
    return Locker.objects.create(
        name=kw.pop("name", "L-1"),
        slug=kw.pop("slug", "l-1"),
        location=location,
        owning_sig=sig,
        **kw,
    )


class TestLockerCrud:
    def test_staff_creates_locker_with_autoslug(self, staff_client, sig, location):
        resp = staff_client.post(
            reverse("lockers:locker-list"),
            {"name": "Wood Shop Locker 4", "location": location.pk, "owning_sig": sig.pk},
            format="json",
        )
        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert body["slug"] == "wood-shop-locker-4"
        # The detail representation is echoed back so the UI gets devices+status.
        assert body["devices"] == []
        assert body["status"] is None

    def test_member_cannot_create_locker(self, member_client, sig, location):
        resp = member_client.post(
            reverse("lockers:locker-list"),
            {"name": "X", "location": location.pk, "owning_sig": sig.pk},
            format="json",
        )
        assert resp.status_code == 403

    def test_sig_admin_creates_locker_for_their_sig(self, sig, location):
        admin = User.objects.create_user(username="lead", email="l@example.com", password="x" * 24)
        SIGAdmin.objects.create(user=admin, group=sig, is_active=True)
        resp = _client(admin).post(
            reverse("lockers:locker-list"),
            {"name": "Lead Locker", "location": location.pk, "owning_sig": sig.pk},
            format="json",
        )
        assert resp.status_code == 201, resp.content

    def test_staff_updates_locker(self, staff_client, sig, location):
        locker = _make_locker(sig, location)
        resp = staff_client.patch(
            reverse("lockers:locker-detail", kwargs={"pk": locker.pk}),
            {"is_high_trust": True, "led_count": 8},
            format="json",
        )
        assert resp.status_code == 200, resp.content
        locker.refresh_from_db()
        assert locker.is_high_trust is True
        assert locker.led_count == 8

    def test_member_cannot_update_or_delete(self, member_client, sig, location):
        locker = _make_locker(sig, location)
        url = reverse("lockers:locker-detail", kwargs={"pk": locker.pk})
        assert member_client.patch(url, {"led_count": 3}, format="json").status_code == 403
        assert member_client.delete(url).status_code == 403

    def test_staff_deletes_locker(self, staff_client, sig, location):
        locker = _make_locker(sig, location)
        resp = staff_client.delete(reverse("lockers:locker-detail", kwargs={"pk": locker.pk}))
        assert resp.status_code == 204
        assert not Locker.objects.filter(pk=locker.pk).exists()


class TestLockerDeviceBinding:
    def test_add_device_sets_primary_and_demotes_existing(
        self, staff_client, sig, location, device
    ):
        locker = _make_locker(sig, location)
        first = ESP32Device.objects.create(
            mac_address="AA:BB:CC:00:00:01", device_type=device.device_type
        )
        LockerDevice.objects.create(
            locker=locker, device=first, role=LockerDevice.ROLE_LATCH, is_primary=True
        )
        resp = staff_client.post(
            reverse("lockers:locker-add-device", kwargs={"pk": locker.pk}),
            {"device": str(device.pk), "role": "latch", "is_primary": True},
            format="json",
        )
        assert resp.status_code == 201, resp.content
        assert locker.device_assignments.get(device=device).is_primary is True
        assert locker.device_assignments.get(device=first).is_primary is False

    def test_add_device_duplicate_role_conflicts(self, staff_client, sig, location, device):
        locker = _make_locker(sig, location)
        url = reverse("lockers:locker-add-device", kwargs={"pk": locker.pk})
        payload = {"device": str(device.pk), "role": "latch"}
        assert staff_client.post(url, payload, format="json").status_code == 201
        assert staff_client.post(url, payload, format="json").status_code == 409

    def test_add_device_member_forbidden(self, member_client, sig, location, device):
        locker = _make_locker(sig, location)
        resp = member_client.post(
            reverse("lockers:locker-add-device", kwargs={"pk": locker.pk}),
            {"device": str(device.pk), "role": "latch"},
            format="json",
        )
        assert resp.status_code == 403

    def test_remove_device(self, staff_client, sig, location, device):
        locker = _make_locker(sig, location)
        assignment = LockerDevice.objects.create(
            locker=locker, device=device, role=LockerDevice.ROLE_LATCH
        )
        resp = staff_client.delete(
            reverse(
                "lockers:locker-remove-device",
                kwargs={"pk": locker.pk, "assignment_id": assignment.pk},
            )
        )
        assert resp.status_code == 204
        assert not LockerDevice.objects.filter(pk=assignment.pk).exists()


class TestAvailableCertifications:
    def test_lists_active_certifications_only(self, staff_client, sig):
        Certification.objects.create(name="Lathe Cert", slug="lathe-cert", sig=sig)
        Certification.objects.create(
            name="Retired Cert", slug="retired-cert", sig=sig, is_active=False
        )
        resp = staff_client.get(reverse("lockers:locker-available-certifications"))
        assert resp.status_code == 200
        names = [c["name"] for c in resp.json()]
        assert "Lathe Cert" in names
        assert "Retired Cert" not in names
