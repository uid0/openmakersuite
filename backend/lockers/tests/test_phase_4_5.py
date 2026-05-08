"""Phase 4+5 tests: audit state machine + Celery janitors."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from forgekey.models import DeviceType, ESP32Device
from inventory.tests.factories import LocationFactory
from lockers.models import (
    Locker,
    LockerAccessEvent,
    LockerAccessEventKind,
    LockerDevice,
    LockerHighTrustReturn,
    LockerOtp,
)
from lockers.services.access import generate_otp
from lockers.tasks import (
    flag_propped_doors,
    invalidate_expired_otps,
)

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture
def authed_client():
    user = User.objects.create_user(
        username="bridge", email="b@example.com", password="x" * 24, is_staff=True
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client, user


@pytest.fixture
def locker_with_devices():
    sig = Group.objects.create(name="Wood Shop SIG")
    locker = Locker.objects.create(
        name="L-1", slug="l-1", location=LocationFactory(), owning_sig=sig
    )
    latch_type, _ = DeviceType.objects.get_or_create(
        code="locker_latch", defaults={"name": "Locker latch"}
    )
    reed_type, _ = DeviceType.objects.get_or_create(
        code="reed_switch", defaults={"name": "Reed switch"}
    )
    ir_type, _ = DeviceType.objects.get_or_create(code="ir_break", defaults={"name": "IR break"})
    latch = ESP32Device.objects.create(mac_address="AA:BB:CC:11:22:33", device_type=latch_type)
    reed = ESP32Device.objects.create(mac_address="AA:BB:CC:11:22:34", device_type=reed_type)
    ir = ESP32Device.objects.create(mac_address="AA:BB:CC:11:22:35", device_type=ir_type)
    LockerDevice.objects.create(locker=locker, device=latch, role=LockerDevice.ROLE_LATCH)
    LockerDevice.objects.create(locker=locker, device=reed, role=LockerDevice.ROLE_REED_SWITCH)
    LockerDevice.objects.create(locker=locker, device=ir, role=LockerDevice.ROLE_IR_BREAK)
    return locker


# ---------------------------------------------------------------------------
# Phase 4: webhook -> LockerAccessEvent persistence
# ---------------------------------------------------------------------------


class TestLockoutEventWritesAuditRow:
    def test_entered_writes_tamper(self, authed_client, locker_with_devices):
        client, _ = authed_client
        client.post(
            reverse("lockers:event-lockout"),
            {"mac": "AA:BB:CC:11:22:33", "state": "entered", "reason": "tamper"},
            format="json",
        )
        events = LockerAccessEvent.objects.filter(locker=locker_with_devices)
        assert events.count() == 1
        assert events.first().kind == LockerAccessEventKind.TAMPER

    def test_cleared_writes_lockout_cleared(self, authed_client, locker_with_devices):
        client, _ = authed_client
        client.post(
            reverse("lockers:event-lockout"),
            {"mac": "AA:BB:CC:11:22:33", "state": "cleared"},
            format="json",
        )
        kinds = list(
            LockerAccessEvent.objects.filter(locker=locker_with_devices).values_list(
                "kind", flat=True
            )
        )
        assert LockerAccessEventKind.LOCKOUT_CLEARED in kinds


class TestReedAndIrCorrelation:
    def test_open_then_secured_writes_two_rows(self, authed_client, locker_with_devices):
        client, _ = authed_client
        client.post(
            reverse("lockers:event-reed-status"),
            {"mac": "AA:BB:CC:11:22:34", "closed": False},
            format="json",
        )
        client.post(
            reverse("lockers:event-reed-status"),
            {"mac": "AA:BB:CC:11:22:34", "closed": True},
            format="json",
        )
        kinds = list(
            LockerAccessEvent.objects.filter(locker=locker_with_devices)
            .order_by("occurred_at")
            .values_list("kind", flat=True)
        )
        assert kinds == [
            LockerAccessEventKind.OPEN,
            LockerAccessEventKind.SECURED,
        ]

    def test_ir_break_during_open_writes_item_removed(self, authed_client, locker_with_devices):
        client, _ = authed_client
        # Open the door first.
        client.post(
            reverse("lockers:event-reed-status"),
            {"mac": "AA:BB:CC:11:22:34", "closed": False},
            format="json",
        )
        # IR break with an open door => item removed.
        client.post(
            reverse("lockers:event-ir-break"),
            {"mac": "AA:BB:CC:11:22:35", "broken": True},
            format="json",
        )
        kinds = list(
            LockerAccessEvent.objects.filter(locker=locker_with_devices).values_list(
                "kind", flat=True
            )
        )
        assert LockerAccessEventKind.ITEM_REMOVED in kinds

    def test_ir_break_with_no_open_state_does_not_log_item_removed(
        self, authed_client, locker_with_devices
    ):
        client, _ = authed_client
        client.post(
            reverse("lockers:event-ir-break"),
            {"mac": "AA:BB:CC:11:22:35", "broken": True},
            format="json",
        )
        assert (
            LockerAccessEvent.objects.filter(
                locker=locker_with_devices,
                kind=LockerAccessEventKind.ITEM_REMOVED,
            ).count()
            == 0
        )


class TestHighTrustReturnFlow:
    def test_secured_high_trust_with_asset_creates_pending_return(
        self, authed_client, locker_with_devices
    ):
        from inventory.tests.factories import AssetFactory

        _, user = authed_client
        client, _ = authed_client
        locker_with_devices.is_high_trust = True
        locker_with_devices.current_asset = AssetFactory()
        locker_with_devices.save()

        # Mint OTP + simulate unlock event so the return knows who returned it.
        otp = generate_otp(user=user, locker=locker_with_devices)
        LockerAccessEvent.objects.create(
            locker=locker_with_devices,
            kind=LockerAccessEventKind.UNLOCKED,
            actor=user,
            otp=otp,
        )

        # Door secured.
        client.post(
            reverse("lockers:event-reed-status"),
            {"mac": "AA:BB:CC:11:22:34", "closed": True},
            format="json",
        )

        ht = LockerHighTrustReturn.objects.filter(locker=locker_with_devices)
        assert ht.count() == 1
        assert ht.first().status == LockerHighTrustReturn.STATUS_PENDING
        assert ht.first().returning_user == user

    def test_low_trust_secured_does_not_create_return(self, authed_client, locker_with_devices):
        from inventory.tests.factories import AssetFactory

        client, _ = authed_client
        locker_with_devices.current_asset = AssetFactory()
        locker_with_devices.save()
        client.post(
            reverse("lockers:event-reed-status"),
            {"mac": "AA:BB:CC:11:22:34", "closed": True},
            format="json",
        )
        assert LockerHighTrustReturn.objects.filter(locker=locker_with_devices).count() == 0


class TestHighTrustReturnAcceptReject:
    def _setup(self):
        from inventory.tests.factories import AssetFactory

        sig = Group.objects.create(name="Wood Shop SIG")
        locker = Locker.objects.create(
            name="L-1", slug="l-1", location=LocationFactory(), owning_sig=sig
        )
        user = User.objects.create_user(
            username="returning", email="r@example.com", password="x" * 24
        )
        asset = AssetFactory()
        triggering = LockerAccessEvent.objects.create(
            locker=locker, kind=LockerAccessEventKind.SECURED, asset=asset
        )
        return LockerHighTrustReturn.objects.create(
            locker=locker,
            returning_user=user,
            asset=asset,
            triggering_event=triggering,
        )

    def test_accept_marks_accepted(self):
        ret = self._setup()
        admin = User.objects.create_user(
            username="admin", email="a@example.com", password="x" * 24, is_staff=True
        )
        ret.accept(by=admin, notes="looks good")
        ret.refresh_from_db()
        assert ret.status == LockerHighTrustReturn.STATUS_ACCEPTED
        assert ret.resolved_by == admin
        assert ret.resolution_notes == "looks good"

    def test_reject_marks_rejected(self):
        ret = self._setup()
        admin = User.objects.create_user(
            username="admin", email="a@example.com", password="x" * 24, is_staff=True
        )
        ret.reject(by=admin, notes="missing parts")
        ret.refresh_from_db()
        assert ret.status == LockerHighTrustReturn.STATUS_REJECTED


# ---------------------------------------------------------------------------
# Phase 5: Celery tasks
# ---------------------------------------------------------------------------


class TestInvalidateExpiredOtps:
    def test_marks_expired_active_otps(self):
        sig = Group.objects.create(name="SIG")
        locker = Locker.objects.create(
            name="L", slug="l", location=LocationFactory(), owning_sig=sig
        )
        user = User.objects.create_user(username="u", email="u@example.com", password="x" * 24)
        # Three OTPs: expired-active, expired-already-used, fresh-active.
        expired = LockerOtp.objects.create(
            locker=locker,
            requesting_user=user,
            code="111111",
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        used = LockerOtp.objects.create(
            locker=locker,
            requesting_user=user,
            code="222222",
            expires_at=timezone.now() - timedelta(minutes=10),
            used_at=timezone.now() - timedelta(minutes=5),
        )
        fresh = LockerOtp.objects.create(
            locker=locker,
            requesting_user=user,
            code="333333",
            expires_at=timezone.now() + timedelta(minutes=30),
        )

        result = invalidate_expired_otps()
        assert result["invalidated"] == 1
        expired.refresh_from_db()
        used.refresh_from_db()
        fresh.refresh_from_db()
        assert expired.revoked_at is not None
        # Already-used row stays put — no reason to touch it.
        assert used.revoked_at is None
        assert fresh.revoked_at is None


class TestFlagProppedDoors:
    def test_writes_incomplete_when_open_overstays(self, settings):
        sig = Group.objects.create(name="SIG")
        locker = Locker.objects.create(
            name="L", slug="l", location=LocationFactory(), owning_sig=sig
        )
        # Force a 1-second threshold for the test.
        settings.LOCKERS_PROPPED_DOOR_THRESHOLD_SECONDS = 1
        # Open event 60s ago, no SECURED since.
        LockerAccessEvent.objects.create(
            locker=locker,
            kind=LockerAccessEventKind.OPEN,
            occurred_at=timezone.now() - timedelta(seconds=60),
        )

        result = flag_propped_doors()
        assert result["flagged"] == 1
        kinds = list(LockerAccessEvent.objects.filter(locker=locker).values_list("kind", flat=True))
        assert LockerAccessEventKind.INCOMPLETE in kinds

    def test_does_not_double_flag_already_resolved(self, settings):
        sig = Group.objects.create(name="SIG")
        locker = Locker.objects.create(
            name="L", slug="l", location=LocationFactory(), owning_sig=sig
        )
        settings.LOCKERS_PROPPED_DOOR_THRESHOLD_SECONDS = 1
        open_event = LockerAccessEvent.objects.create(
            locker=locker,
            kind=LockerAccessEventKind.OPEN,
            occurred_at=timezone.now() - timedelta(seconds=60),
        )
        # Already resolved by a SECURED.
        LockerAccessEvent.objects.create(
            locker=locker,
            kind=LockerAccessEventKind.SECURED,
            occurred_at=open_event.occurred_at + timedelta(seconds=10),
        )

        result = flag_propped_doors()
        assert result["flagged"] == 0


class TestClearLockerLockoutTask:
    @patch("lockers.services.commands.publish_command")
    @patch("lockers.services.commands.make_command_jwt", return_value="signed-jwt")
    def test_publishes_command_and_writes_audit_row(self, _make_jwt, publish_mock, settings):
        from lockers.tasks import clear_locker_lockout

        publish_mock.return_value = "topic/here"
        sig = Group.objects.create(name="SIG")
        locker = Locker.objects.create(
            name="L", slug="l", location=LocationFactory(), owning_sig=sig
        )
        latch_type, _ = DeviceType.objects.get_or_create(
            code="locker_latch", defaults={"name": "Locker latch"}
        )
        device = ESP32Device.objects.create(mac_address="AA:BB:CC:DD:EE:FF", device_type=latch_type)
        LockerDevice.objects.create(locker=locker, device=device, role=LockerDevice.ROLE_LATCH)

        admin = User.objects.create_user(
            username="admin", email="a@example.com", password="x" * 24, is_staff=True
        )
        result = clear_locker_lockout(locker_id=str(locker.id), actor_id=admin.id)
        assert publish_mock.called
        assert result["topic"] == "topic/here"
        kinds = list(LockerAccessEvent.objects.filter(locker=locker).values_list("kind", flat=True))
        assert LockerAccessEventKind.LOCKOUT_CLEARED in kinds
