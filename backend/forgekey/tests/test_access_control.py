"""Tests for the access-control interlock (op-vj9).

Covers:
  - ``resolve_user`` badge lookup + unknown/OTP paths
  - the ``is_authorized`` matrix (authorized / not / inactive / expired /
    locked-out / maintenance)
  - ``handle_access_request`` decisions: grant, deny (unknown card / unknown
    device / no asset / not authorized / in-use / relay error), same-user
    session end, and badge enrollment capture
  - the relay enable/disable endpoints now enforcing authorization (bypass
    closed) with a staff override
  - ``end_idle_sessions`` metered-idle / metered-active / meterless behavior
  - the MQTT ``handle_access_request_message`` envelope validation + routing
  - the badge-enrollment API (arm / cancel / set-badge / status)
  - audit rows for grant / deny / session-end / enrollment

Broker I/O is stubbed (``publish_command``) exactly like ``test_indicator`` so
no real MQTT happens.
"""

from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import patch

from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from forgekey.management.commands.mqtt_consumer import (
    _mac_from_topic_segment,
    dispatch_message,
    handle_access_request_message,
)
from forgekey.models import (
    DeviceCommand,
    DeviceUsage,
    ForgeKeyAuditEvent,
    OperationalMode,
)
from forgekey.services import access_control as ac
from forgekey.services import badge_enrollment
from forgekey.services.device_commands import DeviceCommandError
from forgekey.tests.factories import (
    AssetAuthorizationFactory,
    AssetDeviceFactory,
    DeviceLockoutFactory,
    DeviceUsageFactory,
    ESP32DeviceFactory,
    OperationalModeFactory,
    PowerMeterReadingFactory,
    UserFactory,
)
from inventory.tests.factories import AssetFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def mock_publish():
    """Stub the broker publish for every test (sync + relay + indicator paths)."""
    with patch(
        "forgekey.services.device_commands.publish_command",
        return_value="forgekey/x/command",
    ) as mocked:
        yield mocked


@pytest.fixture
def asset():
    return AssetFactory()


@pytest.fixture
def device(asset):
    """A relay device bound (primary) to ``asset``."""
    dev = ESP32DeviceFactory(mac_address="AA:BB:CC:00:0A:01")
    AssetDeviceFactory(asset=asset, device=dev, is_primary=True, role="power_control")
    return dev


@pytest.fixture
def member():
    return UserFactory(badge_number="CARD-OWNER")


def _badge_payload(credential_id="CARD-OWNER", credential_type=ac.CREDENTIAL_BADGE):
    return {
        "schema_version": ac.SCHEMA_VERSION,
        "credential_type": credential_type,
        "credential_id": credential_id,
        "timestamp": timezone.now().isoformat(),
    }


def _open_sessions(asset):
    return DeviceUsage.objects.filter(asset=asset, ended_at__isnull=True)


def _commands(device, verb):
    return DeviceCommand.objects.filter(device=device, command=verb)


def _audit(action):
    return ForgeKeyAuditEvent.objects.filter(action=action)


# ---------------------------------------------------------------------------
# resolve_user
# ---------------------------------------------------------------------------
class TestResolveUser:
    def test_badge_resolves_to_owner(self, member):
        assert ac.resolve_user(ac.CREDENTIAL_BADGE, "CARD-OWNER") == member

    def test_badge_trims_whitespace(self, member):
        assert ac.resolve_user(ac.CREDENTIAL_BADGE, "  CARD-OWNER ") == member

    def test_unknown_badge_returns_none(self, member):
        assert ac.resolve_user(ac.CREDENTIAL_BADGE, "NOPE") is None

    def test_empty_credential_returns_none(self):
        assert ac.resolve_user(ac.CREDENTIAL_BADGE, "") is None
        assert ac.resolve_user(ac.CREDENTIAL_BADGE, None) is None

    def test_otp_path_deferred_returns_none(self, member):
        # OTP is stubbed for v1 — must fail safe (deny) rather than resolve.
        assert ac.resolve_user(ac.CREDENTIAL_OTP, "123456") is None

    def test_unknown_credential_type_returns_none(self, member):
        assert ac.resolve_user("retina", "CARD-OWNER") is None


# ---------------------------------------------------------------------------
# is_authorized matrix
# ---------------------------------------------------------------------------
class TestIsAuthorized:
    def test_active_authorization_grants(self, asset, member):
        AssetAuthorizationFactory(asset=asset, user=member, is_active=True)
        assert ac.is_authorized(member, asset) is True

    def test_no_authorization_denies(self, asset, member):
        assert ac.is_authorized(member, asset) is False

    def test_inactive_authorization_denies(self, asset, member):
        AssetAuthorizationFactory(asset=asset, user=member, is_active=False)
        assert ac.is_authorized(member, asset) is False

    def test_expired_authorization_denies(self, asset, member):
        AssetAuthorizationFactory(
            asset=asset,
            user=member,
            is_active=True,
            expires_at=timezone.now() - timedelta(hours=1),
        )
        assert ac.is_authorized(member, asset) is False

    def test_future_expiry_grants(self, asset, member):
        AssetAuthorizationFactory(
            asset=asset,
            user=member,
            is_active=True,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        assert ac.is_authorized(member, asset) is True

    def test_active_lockout_denies(self, asset, member):
        AssetAuthorizationFactory(asset=asset, user=member, is_active=True)
        DeviceLockoutFactory(asset=asset, is_active=True)
        assert ac.is_authorized(member, asset) is False

    def test_maintenance_mode_denies(self, asset, member):
        AssetAuthorizationFactory(asset=asset, user=member, is_active=True)
        OperationalModeFactory(asset=asset, mode=OperationalMode.MODE_MAINTENANCE)
        assert ac.is_authorized(member, asset) is False

    def test_locked_out_mode_denies(self, asset, member):
        AssetAuthorizationFactory(asset=asset, user=member, is_active=True)
        OperationalModeFactory(asset=asset, mode=OperationalMode.MODE_LOCKED_OUT)
        assert ac.is_authorized(member, asset) is False

    def test_available_mode_grants(self, asset, member):
        AssetAuthorizationFactory(asset=asset, user=member, is_active=True)
        OperationalModeFactory(asset=asset, mode=OperationalMode.MODE_AVAILABLE)
        assert ac.is_authorized(member, asset) is True

    def test_none_inputs_deny(self, asset, member):
        assert ac.is_authorized(None, asset) is False
        assert ac.is_authorized(member, None) is False


# ---------------------------------------------------------------------------
# handle_access_request — the interlock
# ---------------------------------------------------------------------------
class TestHandleAccessRequest:
    def test_authorized_scan_grants_and_powers(self, asset, device, member, mock_publish):
        AssetAuthorizationFactory(asset=asset, user=member, is_active=True)

        decision = ac.handle_access_request(device.mac_address, _badge_payload())

        assert decision.decision == ac.DECISION_GRANT
        assert decision.granted is True
        # Identified session opened for the badge owner.
        session = _open_sessions(asset).get()
        assert session.user_id == member.id
        # Relay powered on (enable command dispatched).
        assert _commands(device, "enable").exists()
        # Audit row recorded.
        event = _audit(ForgeKeyAuditEvent.ACTION_ACCESS_GRANTED).get()
        assert event.actor_id == member.id
        assert event.asset_id == asset.id

    def test_unauthorized_scan_denies_no_power(self, asset, device, member):
        # member has a badge but no authorization for the asset.
        decision = ac.handle_access_request(device.mac_address, _badge_payload())

        assert decision.decision == ac.DECISION_DENY
        assert decision.reason == ac.REASON_NOT_AUTHORIZED
        assert not _open_sessions(asset).exists()
        assert not _commands(device, "enable").exists()
        event = _audit(ForgeKeyAuditEvent.ACTION_ACCESS_DENIED).get()
        assert event.metadata["reason"] == ac.REASON_NOT_AUTHORIZED

    def test_unknown_card_denies(self, asset, device):
        decision = ac.handle_access_request(
            device.mac_address, _badge_payload(credential_id="GHOST")
        )
        assert decision.decision == ac.DECISION_DENY
        assert decision.reason == ac.REASON_UNKNOWN_CARD
        assert (
            _audit(ForgeKeyAuditEvent.ACTION_ACCESS_DENIED)
            .filter(metadata__reason=ac.REASON_UNKNOWN_CARD)
            .exists()
        )

    def test_unknown_device_denies(self, member):
        decision = ac.handle_access_request("99:99:99:99:99:99", _badge_payload())
        assert decision.decision == ac.DECISION_DENY
        assert decision.reason == ac.REASON_UNKNOWN_DEVICE

    def test_device_without_asset_denies(self, member):
        orphan = ESP32DeviceFactory(mac_address="AA:BB:CC:00:0A:99")
        decision = ac.handle_access_request(orphan.mac_address, _badge_payload())
        assert decision.decision == ac.DECISION_DENY
        assert decision.reason == ac.REASON_NO_ASSET

    def test_same_user_rescan_ends_session_cuts_power(self, asset, device, member):
        AssetAuthorizationFactory(asset=asset, user=member, is_active=True)
        session = DeviceUsageFactory(asset=asset, user=member)

        decision = ac.handle_access_request(device.mac_address, _badge_payload())

        assert decision.decision == ac.DECISION_END
        session.refresh_from_db()
        assert session.ended_at is not None
        assert _commands(device, "disable").exists()
        assert _audit(ForgeKeyAuditEvent.ACTION_SESSION_ENDED).exists()

    def test_different_user_on_in_use_tool_denies(self, asset, device, member):
        holder = UserFactory(badge_number="CARD-HOLDER")
        DeviceUsageFactory(asset=asset, user=holder)
        AssetAuthorizationFactory(asset=asset, user=member, is_active=True)

        decision = ac.handle_access_request(device.mac_address, _badge_payload("CARD-OWNER"))

        assert decision.decision == ac.DECISION_DENY
        assert decision.reason == ac.REASON_IN_USE
        # The holder's session is untouched and no second session was opened.
        assert _open_sessions(asset).count() == 1
        assert _open_sessions(asset).get().user_id == holder.id

    def test_relay_failure_rolls_back_session(self, asset, device, member, mock_publish):
        AssetAuthorizationFactory(asset=asset, user=member, is_active=True)
        mock_publish.side_effect = DeviceCommandError("broker down")

        decision = ac.handle_access_request(device.mac_address, _badge_payload())

        assert decision.decision == ac.DECISION_DENY
        assert decision.reason == ac.REASON_RELAY_ERROR
        # Fail safe: no lingering session, no orphan command row.
        assert not _open_sessions(asset).exists()
        assert not DeviceCommand.objects.filter(device=device, command="enable").exists()
        assert (
            _audit(ForgeKeyAuditEvent.ACTION_ACCESS_DENIED)
            .filter(metadata__reason=ac.REASON_RELAY_ERROR)
            .exists()
        )

    def test_locked_out_asset_denies_even_with_auth(self, asset, device, member):
        AssetAuthorizationFactory(asset=asset, user=member, is_active=True)
        DeviceLockoutFactory(asset=asset, is_active=True)

        decision = ac.handle_access_request(device.mac_address, _badge_payload())

        assert decision.decision == ac.DECISION_DENY
        assert decision.reason == ac.REASON_NOT_AUTHORIZED
        assert not _commands(device, "enable").exists()


# ---------------------------------------------------------------------------
# Badge enrollment capture (interlock side)
# ---------------------------------------------------------------------------
class TestEnrollmentCapture:
    def test_armed_scan_binds_badge_to_user(self, asset, device):
        target = UserFactory(badge_number=None)
        badge_enrollment.arm(target.pk)

        decision = ac.handle_access_request(
            device.mac_address, _badge_payload(credential_id="FRESH-UID")
        )

        assert decision.decision == ac.DECISION_ENROLLED
        target.refresh_from_db()
        assert target.badge_number == "FRESH-UID"
        assert _audit(ForgeKeyAuditEvent.ACTION_BADGE_ENROLLED).exists()
        # Enrollment must not open a session or power the relay.
        assert not _open_sessions(asset).exists()
        assert not _commands(device, "enable").exists()

    def test_armed_scan_consumes_once(self, asset, device):
        target = UserFactory(badge_number=None)
        badge_enrollment.arm(target.pk)
        ac.handle_access_request(device.mac_address, _badge_payload(credential_id="FRESH-UID"))
        # A second scan is no longer in enroll mode → unknown card (deny).
        decision = ac.handle_access_request(
            device.mac_address, _badge_payload(credential_id="OTHER-UID")
        )
        assert decision.decision == ac.DECISION_DENY
        assert decision.reason == ac.REASON_UNKNOWN_CARD

    def test_enroll_refuses_badge_held_by_another(self, asset, device):
        UserFactory(badge_number="TAKEN-UID")
        target = UserFactory(badge_number=None)
        badge_enrollment.arm(target.pk)

        decision = ac.handle_access_request(
            device.mac_address, _badge_payload(credential_id="TAKEN-UID")
        )

        assert decision.decision == ac.DECISION_DENY
        assert decision.reason == ac.REASON_BADGE_IN_USE
        target.refresh_from_db()
        assert target.badge_number is None


# ---------------------------------------------------------------------------
# Idle-session reaper
# ---------------------------------------------------------------------------
class TestEndIdleSessions:
    def _backdate(self, session, minutes):
        DeviceUsage.objects.filter(pk=session.pk).update(
            started_at=timezone.now() - timedelta(minutes=minutes)
        )

    def test_metered_idle_session_ends_and_powers_down(self, asset, device):
        session = DeviceUsageFactory(asset=asset)
        self._backdate(session, minutes=90)
        reading = PowerMeterReadingFactory(asset=asset, usage_session=session, current=1.5)
        type(reading).objects.filter(pk=reading.pk).update(
            timestamp=timezone.now() - timedelta(minutes=90)
        )

        ended = ac.end_idle_sessions(idle_after_minutes=30, max_session_hours=12)

        assert ended == 1
        session.refresh_from_db()
        assert session.ended_at is not None
        assert _commands(device, "disable").exists()
        assert _audit(ForgeKeyAuditEvent.ACTION_SESSION_ENDED).exists()

    def test_metered_active_session_persists(self, asset, device):
        session = DeviceUsageFactory(asset=asset)
        self._backdate(session, minutes=90)
        # Recent above-threshold reading → not idle.
        PowerMeterReadingFactory(asset=asset, usage_session=session, current=2.0)

        ended = ac.end_idle_sessions(idle_after_minutes=30, max_session_hours=12)

        assert ended == 0
        session.refresh_from_db()
        assert session.ended_at is None

    def test_meterless_session_ignores_short_idle_window(self, asset, device):
        # No power meter → the short idle window must not apply (wall-clock only).
        session = DeviceUsageFactory(asset=asset)
        self._backdate(session, minutes=90)

        ended = ac.end_idle_sessions(idle_after_minutes=30, max_session_hours=12)

        assert ended == 0
        session.refresh_from_db()
        assert session.ended_at is None

    def test_meterless_runaway_session_ends_at_wall_clock_cap(self, asset, device):
        session = DeviceUsageFactory(asset=asset)
        self._backdate(session, minutes=13 * 60)  # 13h > 12h cap

        ended = ac.end_idle_sessions(idle_after_minutes=30, max_session_hours=12)

        assert ended == 1
        session.refresh_from_db()
        assert session.ended_at is not None


# ---------------------------------------------------------------------------
# Relay endpoints — authorization bypass closed
# ---------------------------------------------------------------------------
class TestRelayInterlock:
    def _enable_url(self, device):
        return f"/api/forgekey/devices/{device.id}/enable/"

    def _disable_url(self, device):
        return f"/api/forgekey/devices/{device.id}/disable/"

    def test_unauthorized_member_cannot_enable(self, asset, device, member):
        client = APIClient()
        client.force_authenticate(user=member)
        with patch("forgekey.views.enable_device") as task:
            resp = client.post(self._enable_url(device))
        assert resp.status_code == 403
        task.delay.assert_not_called()
        assert (
            _audit(ForgeKeyAuditEvent.ACTION_ACCESS_DENIED)
            .filter(metadata__endpoint="enable")
            .exists()
        )

    def test_authorized_member_can_enable(self, asset, device, member):
        AssetAuthorizationFactory(asset=asset, user=member, is_active=True)
        client = APIClient()
        client.force_authenticate(user=member)
        with patch("forgekey.views.enable_device") as task:
            resp = client.post(self._enable_url(device))
        assert resp.status_code == 200, resp.data
        task.delay.assert_called_once_with(device.mac_address)

    def test_staff_override_enables_without_authorization(self, asset, device, admin_user):
        client = APIClient()
        client.force_authenticate(user=admin_user)
        with patch("forgekey.views.enable_device") as task:
            resp = client.post(self._enable_url(device))
        assert resp.status_code == 200, resp.data
        task.delay.assert_called_once_with(device.mac_address)

    def test_unauthorized_member_cannot_disable(self, asset, device, member):
        client = APIClient()
        client.force_authenticate(user=member)
        with patch("forgekey.views.disable_device") as task:
            resp = client.post(self._disable_url(device))
        assert resp.status_code == 403
        task.delay.assert_not_called()


# ---------------------------------------------------------------------------
# MQTT consumer handler
# ---------------------------------------------------------------------------
class TestAccessRequestMessage:
    def _topic(self, segment="aabbcc000a01"):
        return f"forgekey/{segment}/access/request"

    def test_valid_frame_grants(self, asset):
        segment = "aabbcc000a01"
        mac = _mac_from_topic_segment(segment)
        dev = ESP32DeviceFactory(mac_address=mac)
        AssetDeviceFactory(asset=asset, device=dev, is_primary=True)
        member = UserFactory(badge_number="MQTT-CARD")
        AssetAuthorizationFactory(asset=asset, user=member, is_active=True)

        payload = json.dumps(_badge_payload(credential_id="MQTT-CARD")).encode()
        decision = handle_access_request_message(self._topic(segment), payload)

        assert decision is not None
        assert decision.decision == ac.DECISION_GRANT

    def test_malformed_json_dropped(self):
        assert handle_access_request_message(self._topic(), b"not json") is None

    def test_non_object_payload_dropped(self):
        assert handle_access_request_message(self._topic(), b"[1, 2, 3]") is None

    def test_bad_credential_type_dropped(self):
        payload = json.dumps(_badge_payload(credential_type="retina")).encode()
        assert handle_access_request_message(self._topic(), payload) is None

    def test_missing_credential_id_dropped(self):
        body = _badge_payload()
        body.pop("credential_id")
        assert handle_access_request_message(self._topic(), json.dumps(body).encode()) is None

    def test_bad_mac_segment_dropped(self):
        payload = json.dumps(_badge_payload()).encode()
        assert handle_access_request_message("forgekey/NOTAMAC/access/request", payload) is None

    def test_dispatch_routes_access_topic(self):
        payload = json.dumps(_badge_payload()).encode()
        with patch(
            "forgekey.management.commands.mqtt_consumer.handle_access_request_message"
        ) as handler:
            dispatch_message(self._topic(), payload)
        handler.assert_called_once()


# ---------------------------------------------------------------------------
# Badge-enrollment API
# ---------------------------------------------------------------------------
class TestBadgeEnrollmentApi:
    @pytest.fixture
    def admin_client(self, admin_user):
        client = APIClient()
        client.force_authenticate(user=admin_user)
        return client

    def test_set_badge_assigns(self, admin_client):
        user = UserFactory(badge_number=None)
        resp = admin_client.post(
            "/api/forgekey/badge-enrollment/set-badge/",
            data={"user_id": user.pk, "badge_number": "DIRECT-UID"},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        user.refresh_from_db()
        assert user.badge_number == "DIRECT-UID"

    def test_set_badge_clears(self, admin_client):
        user = UserFactory(badge_number="OLD-UID")
        resp = admin_client.post(
            "/api/forgekey/badge-enrollment/set-badge/",
            data={"user_id": user.pk, "badge_number": None},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        user.refresh_from_db()
        assert user.badge_number is None

    def test_set_badge_conflict(self, admin_client):
        UserFactory(badge_number="SHARED-UID")
        other = UserFactory(badge_number=None)
        resp = admin_client.post(
            "/api/forgekey/badge-enrollment/set-badge/",
            data={"user_id": other.pk, "badge_number": "SHARED-UID"},
            format="json",
        )
        assert resp.status_code == 409

    def test_arm_then_status_then_cancel(self, admin_client):
        user = UserFactory()
        arm = admin_client.post(
            "/api/forgekey/badge-enrollment/arm/",
            data={"user_id": user.pk},
            format="json",
        )
        assert arm.status_code == 200, arm.data

        status_resp = admin_client.get("/api/forgekey/badge-enrollment/")
        assert status_resp.status_code == 200
        assert status_resp.data["armed"] is True
        assert status_resp.data["armed_user_id"] == user.pk

        cancel = admin_client.post("/api/forgekey/badge-enrollment/cancel/", data={}, format="json")
        assert cancel.status_code == 200
        after = admin_client.get("/api/forgekey/badge-enrollment/")
        assert after.data["armed"] is False

    def test_status_reports_captured_badge(self, admin_client, asset, device):
        # End-to-end: arm → reader scan captures the UID → the polling UI sees it.
        target = UserFactory(badge_number=None)
        badge_enrollment.arm(target.pk)
        ac.handle_access_request(device.mac_address, _badge_payload(credential_id="POLLED-UID"))

        status_resp = admin_client.get(
            "/api/forgekey/badge-enrollment/", data={"user_id": target.pk}
        )
        assert status_resp.status_code == 200
        assert status_resp.data["captured"]["badge_number"] == "POLLED-UID"

    def test_non_staff_forbidden(self, member):
        client = APIClient()
        client.force_authenticate(user=member)
        resp = client.post(
            "/api/forgekey/badge-enrollment/arm/",
            data={"user_id": member.pk},
            format="json",
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Access-log endpoint
# ---------------------------------------------------------------------------
class TestAccessLogApi:
    def test_access_only_filters_to_access_actions(self, asset, device, member):
        AssetAuthorizationFactory(asset=asset, user=member, is_active=True)
        ac.handle_access_request(device.mac_address, _badge_payload())  # ACCESS_GRANTED

        # The access log is staff-only (op-2se), so the reader is an admin even
        # though the access event was logged against a plain member.
        staff = UserFactory(is_staff=True)
        client = APIClient()
        client.force_authenticate(user=staff)
        resp = client.get("/api/forgekey/access-log/?access_only=true")
        assert resp.status_code == 200
        actions = {row["action"] for row in resp.data["results"]}
        assert actions <= set(
            [
                ForgeKeyAuditEvent.ACTION_ACCESS_GRANTED,
                ForgeKeyAuditEvent.ACTION_ACCESS_DENIED,
                ForgeKeyAuditEvent.ACTION_SESSION_ENDED,
                ForgeKeyAuditEvent.ACTION_BADGE_ENROLLED,
            ]
        )
        assert ForgeKeyAuditEvent.ACTION_ACCESS_GRANTED in actions

    def test_non_staff_member_cannot_read_access_log(self, asset, device, member):
        # A plain authenticated member is forbidden from the access log (op-2se):
        # IsAdminUser on ForgeKeyAuditEventViewSet returns 403, not the rows.
        AssetAuthorizationFactory(asset=asset, user=member, is_active=True)
        ac.handle_access_request(device.mac_address, _badge_payload())  # ACCESS_GRANTED

        client = APIClient()
        client.force_authenticate(user=member)
        resp = client.get("/api/forgekey/access-log/?access_only=true")
        assert resp.status_code == 403


class TestRelayChannelControl:
    """ga-40w: per-channel power-relay enable/disable via a signed
    ``power_set`` command (not the legacy ``enable``/``disable`` verbs, which
    the firmware's power_relay capability doesn't handle)."""

    def _url(self, device):
        return f"/api/forgekey/devices/{device.id}/relay-channel/"

    def test_staff_can_enable_a_channel(self, asset, device, admin_user):
        client = APIClient()
        client.force_authenticate(user=admin_user)
        with patch("forgekey.views.publish_command", return_value="forgekey/x/command") as pub:
            resp = client.post(self._url(device), {"channel": 2, "on": True}, format="json")
        assert resp.status_code == 200, resp.data
        sent = pub.call_args[0][1]  # full_payload handed to publish_command
        assert sent["cmd"] == "power_set"
        assert sent["channel"] == 2
        assert sent["action"] == "enable"
        rec = DeviceCommand.objects.get(device=device)
        assert rec.command == "power_set"

    def test_disable_sends_disable_action(self, asset, device, admin_user):
        client = APIClient()
        client.force_authenticate(user=admin_user)
        with patch("forgekey.views.publish_command", return_value="forgekey/x/command") as pub:
            resp = client.post(self._url(device), {"channel": 1, "on": False}, format="json")
        assert resp.status_code == 200, resp.data
        assert pub.call_args[0][1]["action"] == "disable"

    def test_unauthorized_member_cannot_control_channel(self, asset, device, member):
        client = APIClient()
        client.force_authenticate(user=member)
        with patch("forgekey.views.publish_command") as pub:
            resp = client.post(self._url(device), {"channel": 1, "on": True}, format="json")
        assert resp.status_code == 403
        pub.assert_not_called()

    def test_invalid_channel_is_rejected(self, asset, device, admin_user):
        client = APIClient()
        client.force_authenticate(user=admin_user)
        with patch("forgekey.views.publish_command") as pub:
            resp = client.post(self._url(device), {"channel": 3, "on": True}, format="json")
        assert resp.status_code == 400
        pub.assert_not_called()

    def test_missing_on_field_is_rejected(self, asset, device, admin_user):
        client = APIClient()
        client.force_authenticate(user=admin_user)
        with patch("forgekey.views.publish_command") as pub:
            resp = client.post(self._url(device), {"channel": 1}, format="json")
        assert resp.status_code == 400
        pub.assert_not_called()


class TestRelayDevicePowerTranslation:
    """ga-40w 'A': a device-level enable/disable on a ``power_relay`` device
    fans out to one signed ``power_set`` per channel, so the web
    'Power-off (lock)' button and ScanTTY ``d``/``e`` keys actually drive the
    relay. Non-relay devices (lockers) keep the legacy ``enable``/``disable``
    verb their firmware understands."""

    def _enable_url(self, device):
        return f"/api/forgekey/devices/{device.id}/enable/"

    def _disable_url(self, device):
        return f"/api/forgekey/devices/{device.id}/disable/"

    def _make_relay(self, device):
        device.capabilities = ["status_led", "power_relay"]
        device.save(update_fields=["capabilities"])
        return device

    def test_enable_fans_out_power_set_on_per_channel(self, asset, device, admin_user):
        self._make_relay(device)
        client = APIClient()
        client.force_authenticate(user=admin_user)
        with patch("forgekey.views.publish_command", return_value="forgekey/x/command") as pub:
            with patch("forgekey.views.enable_device") as task:
                resp = client.post(self._enable_url(device))
        assert resp.status_code == 200, resp.data
        task.delay.assert_not_called()
        sent = [c[0][1] for c in pub.call_args_list]
        assert sorted(p["channel"] for p in sent) == [1, 2]
        assert {p["cmd"] for p in sent} == {"power_set"}
        assert {p["action"] for p in sent} == {"enable"}
        assert DeviceCommand.objects.filter(device=device, command="power_set").count() == 2

    def test_disable_powers_off_all_channels(self, asset, device, admin_user):
        self._make_relay(device)
        client = APIClient()
        client.force_authenticate(user=admin_user)
        with patch("forgekey.views.publish_command", return_value="forgekey/x/command") as pub:
            with patch("forgekey.views.disable_device") as task:
                resp = client.post(self._disable_url(device))
        assert resp.status_code == 200, resp.data
        task.delay.assert_not_called()
        sent = [c[0][1] for c in pub.call_args_list]
        assert sorted(p["channel"] for p in sent) == [1, 2]
        assert {p["action"] for p in sent} == {"disable"}

    def test_non_relay_device_keeps_legacy_verb(self, asset, device, admin_user):
        # Default device fixture announces no power_relay capability.
        client = APIClient()
        client.force_authenticate(user=admin_user)
        with patch("forgekey.views.publish_command") as pub:
            with patch("forgekey.views.disable_device") as task:
                resp = client.post(self._disable_url(device))
        assert resp.status_code == 200, resp.data
        task.delay.assert_called_once_with(device.mac_address, delay_seconds=0)
        pub.assert_not_called()

    def test_unauthorized_member_cannot_power_relay(self, asset, device, member):
        self._make_relay(device)
        client = APIClient()
        client.force_authenticate(user=member)
        with patch("forgekey.views.publish_command") as pub:
            resp = client.post(self._disable_url(device))
        assert resp.status_code == 403
        pub.assert_not_called()
