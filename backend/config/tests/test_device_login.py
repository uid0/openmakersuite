"""
Tests for known-device login tracking + new-device alerting (oms-bwcdb, FP1).

These exercise the device hook through the real login/register endpoints:

* a staff sign-in from a new device records a KnownDevice and raises exactly
  one informational alert, and sets the signed device cookie;
* a returning (known) device does NOT re-alert or duplicate the row;
* non-staff accounts are skipped entirely (no row, no cookie, no alert);
* a failure inside device tracking never breaks the login it observes.
"""

from django.contrib.auth import get_user_model
from django.urls import reverse

import pytest

from membership.models import Membership
from notifications.device_login import DEVICE_COOKIE_NAME
from notifications.models import KnownDevice, Notification

User = get_user_model()


@pytest.fixture
def staff_user(db):
    """Staff user — in scope for new-device alerting."""
    return User.objects.create_user(
        username="staffer",
        email="staff@example.com",
        password="staff-secret-42",
        is_staff=True,
    )


@pytest.fixture
def member_user(db):
    """Active non-staff member — allowed to log in, but out of alert scope."""
    user = User.objects.create_user(
        username="member",
        email="member@example.com",
        password="correct-horse-battery",
    )
    membership = Membership.objects.create(
        membership_type=Membership.MEMBERSHIP_TYPE_MONTHLY,
        status=Membership.STATUS_ACTIVE,
    )
    membership.users.add(user)
    return user


def _login(client, username, password):
    return client.post(
        reverse("login"),
        {"username": username, "password": password},
        format="json",
    )


class TestNewDeviceAlert:
    def test_staff_new_device_creates_row_and_notification(self, api_client, staff_user):
        response = _login(api_client, "staffer", "staff-secret-42")

        assert response.status_code == 200
        # A signed device cookie is set on the response.
        assert DEVICE_COOKIE_NAME in response.cookies
        assert response.cookies[DEVICE_COOKIE_NAME].value

        # Exactly one KnownDevice row was recorded for this user.
        devices = KnownDevice.objects.filter(user=staff_user)
        assert devices.count() == 1

        # Exactly one informational alert was raised.
        notes = Notification.objects.filter(user=staff_user, title="New device sign-in")
        assert notes.count() == 1
        note = notes.first()
        assert note.type == "warning"
        assert note.action_url == "/account/devices"
        assert note.metadata.get("device_token") == devices.first().device_token

    def test_known_device_does_not_renotify(self, api_client, staff_user):
        first = _login(api_client, "staffer", "staff-secret-42")
        assert first.status_code == 200
        cookie_value = first.cookies[DEVICE_COOKIE_NAME].value
        assert KnownDevice.objects.filter(user=staff_user).count() == 1
        assert Notification.objects.filter(user=staff_user).count() == 1

        # Replay the signed device cookie on a second sign-in (set it directly
        # so the Secure flag doesn't suppress resend over the test transport).
        api_client.cookies[DEVICE_COOKIE_NAME] = cookie_value
        second = _login(api_client, "staffer", "staff-secret-42")

        assert second.status_code == 200
        # Same device -> no new row and no new alert.
        assert KnownDevice.objects.filter(user=staff_user).count() == 1
        assert Notification.objects.filter(user=staff_user).count() == 1

    def test_non_staff_login_is_skipped(self, api_client, member_user):
        response = _login(api_client, "member", "correct-horse-battery")

        assert response.status_code == 200
        assert DEVICE_COOKIE_NAME not in response.cookies
        assert KnownDevice.objects.filter(user=member_user).count() == 0
        assert Notification.objects.filter(user=member_user).count() == 0

    def test_login_succeeds_when_tracking_raises(self, api_client, staff_user, monkeypatch):
        """A blow-up inside device tracking must never break the login."""

        def boom(request):
            raise RuntimeError("device backend down")

        monkeypatch.setattr("notifications.device_login.get_client_ip", boom)

        response = _login(api_client, "staffer", "staff-secret-42")

        assert response.status_code == 200
        assert response.json()["is_staff"] is True
        # Tracking failed cleanly: nothing recorded, no cookie set.
        assert KnownDevice.objects.filter(user=staff_user).count() == 0
        assert Notification.objects.filter(user=staff_user).count() == 0
        assert DEVICE_COOKIE_NAME not in response.cookies
