"""Outbound email routed through the "email" circuit breaker.

Covers the helper itself (trip / translate / swallow), one representative
converted call site, and the fact that a tripped email breaker surfaces on
``GET /api/resilience/status/``.
"""

import smtplib

from django.core import mail
from django.core.mail import EmailMultiAlternatives

import pytest

from resilience.circuit import STATE_OPEN, CircuitBreakerOpen, InMemoryStorage, reset_storage
from resilience.email import (
    BREAKER_NAME,
    EmailCircuitOpen,
    breakered_send_mail,
    email_breaker,
    send_breakered_email,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def memory_storage(settings):
    """Hermetic breaker state, breakers explicitly re-enabled (settings turn
    them off under pytest). ``reset_storage`` also clears the breaker registry,
    so each test gets an "email" breaker built from that test's settings."""
    settings.CIRCUIT_BREAKERS_ENABLED = True
    settings.CIRCUIT_BREAKER_USE_REDIS = False
    settings.CIRCUIT_BREAKER_EMAIL_FAIL_MAX = 3
    settings.CIRCUIT_BREAKER_EMAIL_RESET_TIMEOUT = 300
    storage = InMemoryStorage()
    reset_storage(storage)
    yield storage
    reset_storage(None)


def _message(to=None):
    return EmailMultiAlternatives(
        subject="s", body="b", from_email="from@example.com", to=to or ["to@example.com"]
    )


class _Boom(EmailMultiAlternatives):
    """A message whose send always fails the way a bad Postmark token does."""

    def send(self, fail_silently=False):  # noqa: D102
        raise smtplib.SMTPException("provider said 401")


class TestBreakerTuning:
    def test_settings_override_fail_max_and_reset_timeout(self, settings):
        settings.CIRCUIT_BREAKER_EMAIL_FAIL_MAX = 7
        settings.CIRCUIT_BREAKER_EMAIL_RESET_TIMEOUT = 900
        reset_storage(InMemoryStorage())  # drop the cached breaker instance

        breaker = email_breaker()
        assert breaker.name == BREAKER_NAME
        assert breaker.fail_max == 7
        assert breaker.reset_timeout == 900

    def test_default_reset_timeout_backs_off_much_further_than_the_circuit_default(self):
        # A Postmark 401 is persistent, not transient: the shared 30s default
        # would re-probe a broken token forever.
        from resilience.email import DEFAULT_RESET_TIMEOUT

        assert DEFAULT_RESET_TIMEOUT >= 300


class TestSendBreakeredEmail:
    def test_success_sends_and_returns_the_count(self):
        assert send_breakered_email(_message()) == 1
        assert len(mail.outbox) == 1

    def test_trips_open_after_fail_max_failures(self, memory_storage):
        for _ in range(3):
            with pytest.raises(smtplib.SMTPException):
                send_breakered_email(_Boom(to=["to@example.com"]))
        assert memory_storage.state(BREAKER_NAME) == STATE_OPEN

    def test_open_breaker_raises_a_caller_compatible_exception(self, memory_storage):
        memory_storage.trip_open(BREAKER_NAME)

        with pytest.raises(EmailCircuitOpen) as excinfo:
            send_breakered_email(_message())

        # Every real failure family callers already survive: SMTPException for
        # Django's SMTP backend, and OSError — the only ancestor anymail's
        # AnymailRequestsAPIError and smtplib actually share.
        assert isinstance(excinfo.value, smtplib.SMTPException)
        assert isinstance(excinfo.value, OSError)
        assert isinstance(excinfo.value.__cause__, CircuitBreakerOpen)

    def test_open_breaker_does_not_call_the_provider(self, memory_storage):
        memory_storage.trip_open(BREAKER_NAME)

        with pytest.raises(EmailCircuitOpen):
            send_breakered_email(_message())

        assert mail.outbox == []

    def test_fail_silently_swallows_an_open_breaker(self, memory_storage):
        memory_storage.trip_open(BREAKER_NAME)

        assert send_breakered_email(_message(), fail_silently=True) == 0
        assert mail.outbox == []

    def test_disabled_breakers_bypass_the_circuit_entirely(self, settings, memory_storage):
        settings.CIRCUIT_BREAKERS_ENABLED = False
        memory_storage.trip_open(BREAKER_NAME)

        assert send_breakered_email(_message()) == 1
        assert len(mail.outbox) == 1


class TestBreakeredSendMail:
    def test_success_sends_and_returns_the_count(self):
        assert breakered_send_mail("s", "b", None, ["to@example.com"]) == 1
        assert len(mail.outbox) == 1

    def test_open_breaker_raises_for_a_non_silent_caller(self, memory_storage):
        memory_storage.trip_open(BREAKER_NAME)

        with pytest.raises(EmailCircuitOpen):
            breakered_send_mail("s", "b", None, ["to@example.com"])
        assert mail.outbox == []

    def test_open_breaker_is_swallowed_for_a_fail_silently_caller(self, memory_storage):
        memory_storage.trip_open(BREAKER_NAME)

        assert breakered_send_mail("s", "b", None, ["to@example.com"], fail_silently=True) == 0
        assert mail.outbox == []

    def test_extra_kwargs_pass_through(self):
        breakered_send_mail("s", "b", None, ["to@example.com"], html_message="<p>b</p>")

        assert len(mail.outbox) == 1
        assert mail.outbox[0].alternatives[0][0] == "<p>b</p>"


class TestConvertedCallSite:
    """notifications/tasks.py — representative of the converted sites: it must
    behave exactly as before on success and on failure."""

    @pytest.fixture
    def user_and_device(self, django_user_model):
        from notifications.models import KnownDevice

        user = django_user_model.objects.create_user(
            username="alertee", email="alertee@example.com", password="x"  # nosec B106
        )
        device = KnownDevice.objects.create(
            user=user, device_token="token-1", user_agent="Firefox", ip_address="10.0.0.1"
        )
        return user, device

    def test_success_still_sends_and_returns_the_count(self, user_and_device):
        from notifications.tasks import send_new_device_alert

        user, device = user_and_device
        assert send_new_device_alert(user, device) == 1
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["alertee@example.com"]

    def test_open_breaker_is_swallowed_by_the_call_site_exactly_like_a_provider_error(
        self, user_and_device, memory_storage
    ):
        from notifications.tasks import send_new_device_alert

        user, device = user_and_device
        memory_storage.trip_open(BREAKER_NAME)

        # The site's ``except Exception`` -> ``return None`` contract is
        # unchanged; the difference is that no provider call was attempted.
        assert send_new_device_alert(user, device) is None
        assert mail.outbox == []

    def test_repeated_provider_failures_at_the_call_site_trip_the_breaker(
        self, user_and_device, memory_storage, monkeypatch
    ):
        from notifications.tasks import send_new_device_alert

        def boom(self, fail_silently=False):
            raise smtplib.SMTPException("provider said 401")

        monkeypatch.setattr("django.core.mail.EmailMultiAlternatives.send", boom)
        user, device = user_and_device

        for _ in range(3):
            assert send_new_device_alert(user, device) is None
        assert memory_storage.state(BREAKER_NAME) == STATE_OPEN


class TestStatusApi:
    def test_email_service_reports_degraded_when_the_breaker_is_open(
        self, api_client, memory_storage, django_user_model
    ):
        user = django_user_model.objects.create_user(username="member", password="x")  # nosec B106
        api_client.force_authenticate(user=user)

        healthy = api_client.get("/api/resilience/status/").json()
        email_row = {row["key"]: row for row in healthy["services"]}["email"]
        assert email_row["label"] == "Email delivery"
        assert email_row["healthy"] is True

        memory_storage.trip_open(BREAKER_NAME)

        payload = api_client.get("/api/resilience/status/").json()
        email_row = {row["key"]: row for row in payload["services"]}["email"]
        assert payload["degraded"] is True
        assert email_row["state"] == STATE_OPEN
        assert email_row["healthy"] is False
        assert email_row["degraded_count"] == 1
        assert email_row["total_count"] == 1
