"""Access-decision tests for lockers (Phase 1+2).

The bypass order matters for audit clarity: staff is the
fastest-recognised reason, then Logistics, then SIG admin, then
certified user. We pin all four bypass paths plus the denied paths.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group
from django.utils import timezone

import pytest

from inventory.tests.factories import LocationFactory
from lockers.models import Locker
from lockers.services.access import (
    AccessDecision,
    OtpDenied,
    can_user_access_locker,
    decide_locker_access,
    generate_otp,
    redeem_otp,
)
from membership.models import Certification, SIGAdmin, UserCertification

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture
def sig():
    return Group.objects.create(name="Wood Shop SIG")


@pytest.fixture
def location():
    return LocationFactory()


@pytest.fixture
def locker(sig, location):
    return Locker.objects.create(
        name="Wood Shop locker 1",
        slug="wood-shop-1",
        location=location,
        owning_sig=sig,
    )


@pytest.fixture
def cert(sig):
    return Certification.objects.create(
        name="Wood Shop Operator",
        slug="wood-shop-operator",
        sig=sig,
    )


def _make_user(username, **flags):
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="x" * 24,
        **flags,
    )


class TestDecideLockerAccess:
    def test_anonymous_denied(self, locker):
        decision = decide_locker_access(AnonymousUser(), locker)
        assert decision == AccessDecision(allowed=False, reason="anonymous")

    def test_inactive_locker_denied_even_for_staff(self, locker):
        locker.is_active = False
        locker.save()
        staff = _make_user("admin", is_staff=True)
        decision = decide_locker_access(staff, locker)
        assert decision.allowed is False
        assert decision.reason == "locker_inactive"

    def test_staff_bypass(self, locker):
        decision = decide_locker_access(_make_user("admin", is_staff=True), locker)
        assert decision == AccessDecision(allowed=True, reason="staff_bypass")

    def test_superuser_bypass(self, locker):
        decision = decide_locker_access(_make_user("root", is_superuser=True), locker)
        assert decision == AccessDecision(allowed=True, reason="staff_bypass")

    def test_logistics_bypass(self, locker):
        u = _make_user("logi")
        u.groups.add(Group.objects.create(name="Logistics"))
        decision = decide_locker_access(u, locker)
        assert decision == AccessDecision(allowed=True, reason="logistics_bypass")

    def test_sig_admin_bypass(self, locker, sig):
        u = _make_user("sigadmin")
        SIGAdmin.objects.create(user=u, group=sig)
        decision = decide_locker_access(u, locker)
        assert decision == AccessDecision(allowed=True, reason="sig_admin_bypass")

    def test_sig_admin_of_other_sig_does_not_bypass(self, locker):
        u = _make_user("other_sigadmin")
        other = Group.objects.create(name="Metal Shop SIG")
        SIGAdmin.objects.create(user=u, group=other)
        decision = decide_locker_access(u, locker)
        # No required certs configured → falls through to no_required_certs.
        assert decision.allowed is False

    def test_certified_user_allowed(self, locker, cert):
        locker.required_certifications.add(cert)
        u = _make_user("certified")
        UserCertification.objects.create(user=u, certification=cert)
        decision = decide_locker_access(u, locker)
        assert decision == AccessDecision(allowed=True, reason="certified")

    def test_uncertified_user_denied_with_reason(self, locker, cert, sig):
        cert2 = Certification.objects.create(name="Sander cert", slug="sander", sig=sig)
        locker.required_certifications.add(cert, cert2)
        u = _make_user("partial")
        # Only holds one of the two required certs.
        UserCertification.objects.create(user=u, certification=cert)
        decision = decide_locker_access(u, locker)
        assert decision.allowed is False
        assert decision.reason == "missing_certification"
        assert "Sander cert" in decision.missing_certifications
        assert "Wood Shop Operator" not in decision.missing_certifications

    def test_revoked_certification_does_not_count(self, locker, cert):
        locker.required_certifications.add(cert)
        u = _make_user("revoked")
        grant = UserCertification.objects.create(user=u, certification=cert)
        grant.revoke(by=_make_user("revoker", is_staff=True))
        decision = decide_locker_access(u, locker)
        assert decision.allowed is False
        assert decision.reason == "missing_certification"

    def test_inactive_certification_does_not_count(self, locker, cert):
        locker.required_certifications.add(cert)
        u = _make_user("on_inactive")
        UserCertification.objects.create(user=u, certification=cert)
        cert.is_active = False
        cert.save()
        decision = decide_locker_access(u, locker)
        # The locker has no *active* required certs — falls through to
        # no_required_certs (locker not yet provisioned for self-serve).
        assert decision.allowed is False
        assert decision.reason == "no_required_certs"

    def test_locker_with_no_required_certs_denies_volunteers(self, locker):
        u = _make_user("volunteer")
        decision = decide_locker_access(u, locker)
        assert decision.allowed is False
        assert decision.reason == "no_required_certs"


class TestCanUserAccessLockerShortcut:
    def test_returns_decision_allowed_bool(self, locker, cert):
        locker.required_certifications.add(cert)
        u = _make_user("certified")
        UserCertification.objects.create(user=u, certification=cert)
        assert can_user_access_locker(u, locker) is True
        assert can_user_access_locker(_make_user("denied"), locker) is False


class TestGenerateOtp:
    def test_denies_with_structured_reason(self, locker):
        u = _make_user("volunteer")
        with pytest.raises(OtpDenied) as exc_info:
            generate_otp(user=u, locker=locker)
        assert exc_info.value.decision.reason == "no_required_certs"

    def test_mints_a_redeemable_otp_for_staff(self, locker):
        staff = _make_user("admin", is_staff=True)
        otp = generate_otp(user=staff, locker=locker)
        assert otp.is_redeemable
        assert otp.code.isdigit() and len(otp.code) == 6
        assert otp.requesting_user == staff
        # Default TTL is 60 minutes.
        assert otp.expires_at - otp.created_at >= timedelta(minutes=59, seconds=30)

    def test_respects_custom_digit_count(self, locker):
        staff = _make_user("admin", is_staff=True)
        otp = generate_otp(user=staff, locker=locker, digits=8)
        assert len(otp.code) == 8

    def test_respects_custom_ttl(self, locker):
        staff = _make_user("admin", is_staff=True)
        otp = generate_otp(user=staff, locker=locker, ttl=timedelta(minutes=5))
        assert otp.expires_at - otp.created_at <= timedelta(minutes=5, seconds=1)


class TestRedeemOtp:
    def test_returns_otp_and_marks_used(self, locker):
        staff = _make_user("admin", is_staff=True)
        otp = generate_otp(user=staff, locker=locker)
        redeemed = redeem_otp(locker=locker, code=otp.code)
        assert redeemed is not None
        assert redeemed.id == otp.id
        assert redeemed.used_at is not None

    def test_returns_none_for_unknown_code(self, locker):
        assert redeem_otp(locker=locker, code="000000") is None

    def test_returns_none_for_expired_otp(self, locker):
        staff = _make_user("admin", is_staff=True)
        otp = generate_otp(user=staff, locker=locker)
        # Backdate expiry.
        otp.expires_at = timezone.now() - timedelta(minutes=1)
        otp.save(update_fields=["expires_at"])
        assert redeem_otp(locker=locker, code=otp.code) is None

    def test_returns_none_for_revoked_otp(self, locker):
        staff = _make_user("admin", is_staff=True)
        otp = generate_otp(user=staff, locker=locker)
        otp.revoke(by=staff)
        assert redeem_otp(locker=locker, code=otp.code) is None

    def test_already_used_otp_is_not_redeemable_again(self, locker):
        staff = _make_user("admin", is_staff=True)
        otp = generate_otp(user=staff, locker=locker)
        first = redeem_otp(locker=locker, code=otp.code)
        assert first is not None
        second = redeem_otp(locker=locker, code=otp.code)
        assert second is None
