"""Tests for the read-only PKI surface + CA rotation (parity gaps #5 / #6)."""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from forgekey.models import CertificateAuthority, DeviceCertificate, DeviceIdentity
from forgekey.services.ca_lifecycle import mint_ca

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_api_client(admin_user):
    client = APIClient()
    client.force_authenticate(user=admin_user)
    return client


@pytest.fixture
def member_api_client():
    user = User.objects.create_user(username="m", email="m@example.com", password="x" * 20)
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def ca_kek(settings):
    from cryptography.fernet import Fernet

    settings.FORGEKEY_CA_KEY_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")


@pytest.fixture
def active_ca(ca_kek):
    return mint_ca(validity_years=5)


def _rows(resp):
    body = resp.json()
    return body if isinstance(body, list) else body["results"]


class TestCertificateAuthorityRead:
    def test_active_ca_exposes_cn_fingerprint_and_counts(self, admin_api_client, active_ca):
        resp = admin_api_client.get(reverse("forgekey:certificate-authority-list"))
        assert resp.status_code == 200
        ca = next(r for r in _rows(resp) if r["is_active"])
        assert ca["common_name"] == "ForgeKey Internal Root CA"
        assert len(ca["fingerprint_sha256"]) == 64
        assert ca["active_cert_count"] == 0
        assert ca["revoked_cert_count"] == 0

    def test_non_staff_forbidden(self, member_api_client, active_ca):
        resp = member_api_client.get(reverse("forgekey:certificate-authority-list"))
        assert resp.status_code == 403


class TestDeviceCertificateRead:
    def test_lists_certs_with_status(self, admin_api_client):
        device = DeviceIdentity.objects.create(device_id="chip-aabbcc000009")
        now = timezone.now()
        DeviceCertificate.objects.create(
            device=device,
            serial="01",
            subject="CN=dev",
            fingerprint_sha256="a" * 64,
            not_before=now - timedelta(days=1),
            not_after=now + timedelta(days=365),
            issued_by="test",
        )
        DeviceCertificate.objects.create(
            device=device,
            serial="02",
            subject="CN=dev2",
            fingerprint_sha256="b" * 64,
            not_before=now - timedelta(days=2),
            not_after=now - timedelta(days=1),  # expired
            issued_by="test",
        )
        DeviceCertificate.objects.create(
            device=device,
            serial="03",
            subject="CN=dev3",
            fingerprint_sha256="c" * 64,
            not_before=now - timedelta(days=1),
            not_after=now + timedelta(days=365),
            revoked_at=now,
            issued_by="test",
        )
        resp = admin_api_client.get(reverse("forgekey:device-certificate-list"))
        assert resp.status_code == 200
        by_serial = {r["serial"]: r for r in _rows(resp)}
        assert by_serial["01"]["status"] == "active"
        assert by_serial["02"]["status"] == "expired"
        assert by_serial["03"]["status"] == "revoked"
        assert by_serial["01"]["device_chip_id"] == "chip-aabbcc000009"


class TestCaRotation:
    def test_staff_rotate_mints_new_active_ca(self, admin_api_client, active_ca):
        old_id = str(active_ca.id)
        resp = admin_api_client.post(
            reverse("forgekey:certificate-authority-rotate"),
            {"validity_years": 3},
            format="json",
        )
        assert resp.status_code == 201, resp.data
        new = resp.json()
        assert new["is_active"] is True
        assert new["id"] != old_id
        active = CertificateAuthority.objects.filter(is_active=True)
        assert active.count() == 1
        assert str(active.first().id) == new["id"]
        active_ca.refresh_from_db()
        assert active_ca.is_active is False

    def test_member_cannot_rotate(self, member_api_client):
        resp = member_api_client.post(reverse("forgekey:certificate-authority-rotate"))
        assert resp.status_code == 403
        assert not CertificateAuthority.objects.exists()

    def test_rotate_rejects_non_positive_validity(self, admin_api_client):
        resp = admin_api_client.post(
            reverse("forgekey:certificate-authority-rotate"),
            {"validity_years": 0},
            format="json",
        )
        assert resp.status_code == 400
