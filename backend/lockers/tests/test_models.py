"""Model-layer tests for the Lockers app (Phase 1+2)."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import IntegrityError
from django.utils import timezone

import pytest

from inventory.tests.factories import LocationFactory
from lockers.models import Locker, LockerOtp, _generate_otp_code

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture
def sig():
    return Group.objects.create(name="Wood Shop SIG")


@pytest.fixture
def locker(sig):
    return Locker.objects.create(
        name="Locker A", slug="loc-a", location=LocationFactory(), owning_sig=sig
    )


@pytest.fixture
def user():
    return User.objects.create_user(username="u", email="u@example.com", password="x" * 24)


class TestGenerateOtpCode:
    def test_default_is_six_digit_string(self):
        code = _generate_otp_code()
        assert len(code) == 6 and code.isdigit()

    def test_eight_digit_supported(self):
        assert len(_generate_otp_code(8)) == 8

    def test_rejects_too_few_digits(self):
        with pytest.raises(ValueError):
            _generate_otp_code(4)

    def test_rejects_too_many_digits(self):
        with pytest.raises(ValueError):
            _generate_otp_code(9)


class TestLockerOtpStateMachine:
    def _make(self, locker, user, **overrides):
        defaults = dict(
            locker=locker,
            requesting_user=user,
            code="123456",
            expires_at=timezone.now() + timedelta(minutes=60),
        )
        defaults.update(overrides)
        return LockerOtp.objects.create(**defaults)

    def test_active_state(self, locker, user):
        otp = self._make(locker, user)
        assert otp.state == "active"
        assert otp.is_redeemable

    def test_expired_state(self, locker, user):
        otp = self._make(locker, user, expires_at=timezone.now() - timedelta(minutes=1))
        assert otp.state == "expired"
        assert not otp.is_redeemable

    def test_used_state(self, locker, user):
        otp = self._make(locker, user)
        otp.mark_used()
        otp.refresh_from_db()
        assert otp.state == "used"
        assert not otp.is_redeemable

    def test_mark_used_idempotent(self, locker, user):
        otp = self._make(locker, user)
        otp.mark_used()
        original_used_at = otp.used_at
        otp.mark_used()
        otp.refresh_from_db()
        assert otp.used_at == original_used_at

    def test_revoked_state(self, locker, user):
        otp = self._make(locker, user)
        otp.revoke(by=user)
        otp.refresh_from_db()
        assert otp.state == "revoked"
        assert not otp.is_redeemable
        assert otp.revoked_by == user

    def test_revoke_idempotent(self, locker, user):
        otp = self._make(locker, user)
        otp.revoke(by=user)
        first = otp.revoked_at
        # Second revoke should not move the timestamp.
        otp.revoke(by=user)
        otp.refresh_from_db()
        assert otp.revoked_at == first


class TestLockerOtpUniqueActiveCode:
    def test_two_active_codes_with_same_value_collide(self, locker, user):
        LockerOtp.objects.create(
            locker=locker,
            requesting_user=user,
            code="111111",
            expires_at=timezone.now() + timedelta(minutes=60),
        )
        with pytest.raises(IntegrityError):
            LockerOtp.objects.create(
                locker=locker,
                requesting_user=user,
                code="111111",
                expires_at=timezone.now() + timedelta(minutes=60),
            )

    def test_used_code_can_be_reissued(self, locker, user):
        first = LockerOtp.objects.create(
            locker=locker,
            requesting_user=user,
            code="222222",
            expires_at=timezone.now() + timedelta(minutes=60),
        )
        first.mark_used()
        # New active OTP with same value succeeds — uniqueness is gated on
        # the partial index over (used_at IS NULL, revoked_at IS NULL).
        LockerOtp.objects.create(
            locker=locker,
            requesting_user=user,
            code="222222",
            expires_at=timezone.now() + timedelta(minutes=60),
        )
