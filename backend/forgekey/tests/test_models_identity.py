"""
Tests for the device-identity trust foundation models
(oms-d2axqu / [[forgekey-trust-refactor]]).

Covers:
  * ``DeviceIdentity.device_id`` uniqueness.
  * ``DeviceCertificate.status`` transitions across not_after / revoked_at.
  * ``CertificateAuthority`` single-active partial unique constraint.
"""

from __future__ import annotations

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

import pytest

from forgekey.models import (
    CertificateAuthority,
    DeviceCertificate,
    DeviceIdentity,
)


pytestmark = pytest.mark.django_db


class TestDeviceIdentity:
    def test_device_id_is_unique(self):
        DeviceIdentity.objects.create(device_id="chip-aaaa")
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                DeviceIdentity.objects.create(device_id="chip-aaaa")

    def test_default_status_is_active(self):
        identity = DeviceIdentity.objects.create(device_id="chip-bbbb")
        assert identity.status == DeviceIdentity.STATUS_ACTIVE


class TestDeviceCertificateStatus:
    def _identity(self, name="chip-1"):
        return DeviceIdentity.objects.create(device_id=name)

    def test_valid_when_unrevoked_and_not_expired(self):
        identity = self._identity()
        now = timezone.now()
        cert = DeviceCertificate.objects.create(
            device=identity,
            serial="1",
            subject="CN=forgekey-aabbccddeeff",
            fingerprint_sha256="a" * 64,
            not_before=now - timedelta(days=1),
            not_after=now + timedelta(days=30),
        )
        assert cert.status == DeviceCertificate.STATUS_VALID

    def test_expired_when_not_after_in_past(self):
        identity = self._identity("chip-2")
        now = timezone.now()
        cert = DeviceCertificate.objects.create(
            device=identity,
            serial="2",
            subject="CN=forgekey-001122334455",
            fingerprint_sha256="b" * 64,
            not_before=now - timedelta(days=30),
            not_after=now - timedelta(seconds=1),
        )
        assert cert.status == DeviceCertificate.STATUS_EXPIRED

    def test_revoked_wins_over_expiry(self):
        identity = self._identity("chip-3")
        now = timezone.now()
        cert = DeviceCertificate.objects.create(
            device=identity,
            serial="3",
            subject="CN=forgekey-aabbccddeeff",
            fingerprint_sha256="c" * 64,
            not_before=now - timedelta(days=30),
            not_after=now - timedelta(seconds=1),
            revoked_at=now,
        )
        assert cert.status == DeviceCertificate.STATUS_REVOKED


class TestCertificateAuthoritySingleActive:
    def _make(self, *, is_active: bool, name: str) -> CertificateAuthority:
        now = timezone.now()
        return CertificateAuthority.objects.create(
            name=name,
            cert_pem="-----BEGIN CERTIFICATE-----\n-----END CERTIFICATE-----\n",
            encrypted_private_key=b"\x00",
            key_kid="kek-test",
            not_before=now - timedelta(days=1),
            not_after=now + timedelta(days=365),
            is_active=is_active,
        )

    def test_two_active_rows_violate_constraint(self):
        self._make(is_active=True, name="first")
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                self._make(is_active=True, name="second")

    def test_two_inactive_rows_allowed(self):
        self._make(is_active=False, name="retired-1")
        self._make(is_active=False, name="retired-2")
        assert CertificateAuthority.objects.filter(is_active=False).count() == 2

    def test_get_active_returns_active_row(self):
        retired = self._make(is_active=False, name="retired")
        active = self._make(is_active=True, name="forgekey-root")
        assert CertificateAuthority.get_active() == active
        assert retired.is_active is False
