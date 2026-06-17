"""
Tests for the new-device sign-in *email* alert (oms-1crmp, FP2).

FP1 (oms-bwcdb) records a ``KnownDevice`` and raises an in-app alert on a new
staff/privileged sign-in. FP2 adds the email companion: a Celery task
(``notifications.tasks.send_new_device_alert_email``) fired via
``transaction.on_commit`` from the same new-device branch.

Coverage:

* the task renders + sends one email to the signing-in user, with the device's
  User-Agent, IP, and the ``/account/devices`` link in the body;
* a new-device staff login enqueues exactly one email (on_commit -> eager task);
* the security alert is sent even when the user opted out of email (the default);
* with the opt-out exemption disabled, ``email_enabled=False`` suppresses it;
* missing user/device rows and a user without an email address are clean no-ops.

Email is captured via pytest-django's ``mailoutbox`` (locmem backend); Celery
runs eagerly under the suite's autouse ``configure_celery_for_tests`` fixture.
"""

from django.contrib.auth import get_user_model
from django.urls import reverse

import pytest

from notifications.models import KnownDevice, NotificationPreference
from notifications.tasks import send_new_device_alert_email

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


def _device_for(user, **overrides):
    fields = {
        "device_token": "tok-abc123",
        "user_agent": "Mozilla/5.0 (TestBrowser)",
        "ip_address": "203.0.113.7",
    }
    fields.update(overrides)
    return KnownDevice.objects.create(user=user, **fields)


def _login(client, username, password):
    return client.post(
        reverse("login"),
        {"username": username, "password": password},
        format="json",
    )


class TestSendNewDeviceAlertEmailTask:
    def test_sends_email_with_device_details(self, staff_user, mailoutbox):
        device = _device_for(staff_user)

        result = send_new_device_alert_email(staff_user.id, device.id)

        assert result["sent"] == 1
        assert len(mailoutbox) == 1
        msg = mailoutbox[0]
        assert msg.to == ["staff@example.com"]
        assert "New device sign-in" in msg.subject
        # UA, IP, and the devices link all appear in the plain-text body.
        assert "Mozilla/5.0 (TestBrowser)" in msg.body
        assert "203.0.113.7" in msg.body
        assert "/account/devices" in msg.body
        # An HTML alternative is attached alongside the text body.
        assert any(mimetype == "text/html" for _, mimetype in msg.alternatives)

    def test_security_alert_sent_despite_email_opt_out(self, staff_user, mailoutbox):
        # The user disabled routine email notifications...
        NotificationPreference.objects.create(user=staff_user, email_enabled=False)
        device = _device_for(staff_user)

        result = send_new_device_alert_email(staff_user.id, device.id)

        # ...but a new-device alert is a security signal, sent by default.
        assert result["sent"] == 1
        assert len(mailoutbox) == 1

    def test_respects_email_enabled_when_exemption_disabled(
        self, staff_user, mailoutbox, monkeypatch
    ):
        monkeypatch.setattr("notifications.tasks.NEW_DEVICE_ALERT_BYPASS_EMAIL_OPT_OUT", False)
        NotificationPreference.objects.create(user=staff_user, email_enabled=False)
        device = _device_for(staff_user)

        result = send_new_device_alert_email(staff_user.id, device.id)

        assert result["sent"] == 0
        assert len(mailoutbox) == 0

    def test_no_email_address_is_noop(self, db, mailoutbox):
        user = User.objects.create_user(
            username="noemail", email="", password="x-secret-99", is_staff=True
        )
        device = _device_for(user)

        result = send_new_device_alert_email(user.id, device.id)

        assert result["sent"] == 0
        assert len(mailoutbox) == 0

    def test_email_backend_failure_is_swallowed(self, staff_user, mailoutbox, monkeypatch):
        """A blow-up in the email backend must not fail the task."""
        device = _device_for(staff_user)

        def boom(*args, **kwargs):
            raise RuntimeError("smtp down")

        monkeypatch.setattr("django.core.mail.EmailMultiAlternatives.send", boom)

        result = send_new_device_alert_email(staff_user.id, device.id)

        assert result["sent"] == 0
        assert len(mailoutbox) == 0

    def test_missing_device_is_noop(self, staff_user, mailoutbox):
        result = send_new_device_alert_email(staff_user.id, 999999)

        assert result == {"sent": 0, "reason": "device-not-found"}
        assert len(mailoutbox) == 0

    def test_missing_user_is_noop(self, db, mailoutbox):
        result = send_new_device_alert_email(999999, 1)

        assert result == {"sent": 0, "reason": "user-not-found"}
        assert len(mailoutbox) == 0


class TestLoginEnqueuesEmail:
    def test_new_device_login_sends_one_email(
        self, api_client, staff_user, mailoutbox, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            response = _login(api_client, "staffer", "staff-secret-42")

        assert response.status_code == 200
        # Exactly one on_commit callback (the email dispatch) was registered.
        assert len(callbacks) == 1
        assert KnownDevice.objects.filter(user=staff_user).count() == 1
        assert len(mailoutbox) == 1
        assert mailoutbox[0].to == ["staff@example.com"]

    def test_known_device_login_sends_no_email(
        self, api_client, staff_user, mailoutbox, django_capture_on_commit_callbacks
    ):
        from notifications.device_login import DEVICE_COOKIE_NAME

        with django_capture_on_commit_callbacks(execute=True):
            first = _login(api_client, "staffer", "staff-secret-42")
        assert first.status_code == 200
        assert len(mailoutbox) == 1
        cookie_value = first.cookies[DEVICE_COOKIE_NAME].value

        # Replay the signed device cookie: a returning device must not re-email.
        api_client.cookies[DEVICE_COOKIE_NAME] = cookie_value
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            second = _login(api_client, "staffer", "staff-secret-42")

        assert second.status_code == 200
        assert callbacks == []
        assert len(mailoutbox) == 1  # still just the first sign-in's email
