"""Tests for the EMQX rule-engine webhook receivers (Phase 3)."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.urls import reverse

import pytest
from rest_framework.test import APIClient

from forgekey.models import DeviceType, ESP32Device
from inventory.tests.factories import LocationFactory
from lockers.models import Locker, LockerDevice

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture
def authed_client():
    user = User.objects.create_user(
        username="bridge", email="b@example.com", password="x" * 24, is_staff=True
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def locker_with_devices():
    sig = Group.objects.create(name="Wood Shop SIG")
    locker = Locker.objects.create(
        name="L-1", slug="l-1", location=LocationFactory(), owning_sig=sig
    )
    latch_type, _ = DeviceType.objects.get_or_create(
        code="locker_latch",
        defaults={"name": "Locker latch controller"},
    )
    reed_type, _ = DeviceType.objects.get_or_create(
        code="reed_switch",
        defaults={"name": "Door reed switch"},
    )
    ir_type, _ = DeviceType.objects.get_or_create(
        code="ir_break",
        defaults={"name": "Inventory IR-break sensor"},
    )
    latch = ESP32Device.objects.create(mac_address="AA:BB:CC:11:22:33", device_type=latch_type)
    reed = ESP32Device.objects.create(mac_address="AA:BB:CC:11:22:34", device_type=reed_type)
    ir = ESP32Device.objects.create(mac_address="AA:BB:CC:11:22:35", device_type=ir_type)
    LockerDevice.objects.create(locker=locker, device=latch, role=LockerDevice.ROLE_LATCH)
    LockerDevice.objects.create(locker=locker, device=reed, role=LockerDevice.ROLE_REED_SWITCH)
    LockerDevice.objects.create(locker=locker, device=ir, role=LockerDevice.ROLE_IR_BREAK)
    return locker


class TestLockoutEvent:
    def test_accepts_entered_event(self, authed_client, locker_with_devices):
        url = reverse("lockers:event-lockout")
        resp = authed_client.post(
            url,
            {"mac": "AA:BB:CC:11:22:33", "state": "entered", "reason": "tamper"},
            format="json",
        )
        assert resp.status_code == 202
        assert resp.json()["status"] == "accepted"

    def test_accepts_cleared_event(self, authed_client, locker_with_devices):
        url = reverse("lockers:event-lockout")
        resp = authed_client.post(
            url, {"mac": "AA:BB:CC:11:22:33", "state": "cleared"}, format="json"
        )
        assert resp.status_code == 202

    def test_unknown_mac_returns_404(self, authed_client, locker_with_devices):
        url = reverse("lockers:event-lockout")
        resp = authed_client.post(
            url, {"mac": "00:00:00:00:00:00", "state": "entered"}, format="json"
        )
        assert resp.status_code == 404

    def test_invalid_state_rejected(self, authed_client, locker_with_devices):
        url = reverse("lockers:event-lockout")
        resp = authed_client.post(url, {"mac": "AA:BB:CC:11:22:33", "state": "wat"}, format="json")
        assert resp.status_code == 400

    def test_unauthenticated_blocked(self, locker_with_devices):
        url = reverse("lockers:event-lockout")
        resp = APIClient().post(
            url, {"mac": "AA:BB:CC:11:22:33", "state": "entered"}, format="json"
        )
        assert resp.status_code in (401, 403)


class TestIrBreakEvent:
    def test_accepts_break_event(self, authed_client, locker_with_devices):
        url = reverse("lockers:event-ir-break")
        resp = authed_client.post(url, {"mac": "AA:BB:CC:11:22:35", "broken": True}, format="json")
        assert resp.status_code == 202

    def test_resolves_via_ir_device_mac(self, authed_client, locker_with_devices):
        # The IR sensor is bound to the same locker as the latch but has its
        # own MAC; both should resolve to the same locker.
        url = reverse("lockers:event-ir-break")
        for mac in ("AA:BB:CC:11:22:33", "AA:BB:CC:11:22:35"):
            resp = authed_client.post(url, {"mac": mac, "broken": False}, format="json")
            assert resp.status_code == 202


class TestReedStatusEvent:
    def test_accepts_closed_event(self, authed_client, locker_with_devices):
        url = reverse("lockers:event-reed-status")
        resp = authed_client.post(url, {"mac": "AA:BB:CC:11:22:34", "closed": True}, format="json")
        assert resp.status_code == 202

    def test_missing_required_field_400(self, authed_client, locker_with_devices):
        url = reverse("lockers:event-reed-status")
        resp = authed_client.post(url, {"mac": "AA:BB:CC:11:22:34"}, format="json")
        assert resp.status_code == 400


class TestRegistrationAck:
    def test_returns_404_for_unknown_mac(self, authed_client):
        url = reverse("lockers:registration-ack")
        resp = authed_client.post(url, {"mac": "00:00:00:00:00:00"}, format="json")
        assert resp.status_code == 404

    def test_requires_mac(self, authed_client):
        url = reverse("lockers:registration-ack")
        resp = authed_client.post(url, {}, format="json")
        assert resp.status_code == 400
