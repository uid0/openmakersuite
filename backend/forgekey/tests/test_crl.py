"""
Tests for ``GET /api/forgekey/ca/crl.pem`` (oms-d2axqu).

The CRL must include the serial of every revoked DeviceCertificate, omit
non-revoked ones, and verify against the active CA's public key.
"""

from __future__ import annotations

from datetime import timedelta

from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

import pytest
from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import ec

from forgekey.models import (
    CertificateAuthority,
    DeviceCertificate,
    DeviceIdentity,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _ca_kek(settings):
    settings.FORGEKEY_CA_KEY_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")


@pytest.fixture
def active_ca():
    call_command("forgekey_ca", "init", "--validity-years", "1")
    return CertificateAuthority.get_active()


def _make_cert(identity: DeviceIdentity, *, serial_hex: str, revoked: bool):
    now = timezone.now()
    return DeviceCertificate.objects.create(
        device=identity,
        serial=serial_hex,
        subject="CN=forgekey-aabbccddeeff",
        fingerprint_sha256=serial_hex.rjust(64, "0"),
        not_before=now - timedelta(days=1),
        not_after=now + timedelta(days=30),
        revoked_at=now if revoked else None,
    )


def test_crl_returns_pem_with_revoked_serials(api_client, active_ca):
    identity = DeviceIdentity.objects.create(device_id="chip-aabbccddeeff")
    _make_cert(identity, serial_hex="aa", revoked=True)
    _make_cert(identity, serial_hex="bb", revoked=False)

    url = reverse("forgekey:ca-crl")
    resp = api_client.get(url)
    assert resp.status_code == 200, resp.content
    assert resp["Content-Type"].startswith("application/x-pem-file")

    pem = resp.content
    crl = x509.load_pem_x509_crl(pem)
    revoked_serials = {entry.serial_number for entry in crl}
    assert 0xAA in revoked_serials
    assert 0xBB not in revoked_serials


def test_crl_signature_verifies_against_active_ca(api_client, active_ca):
    identity = DeviceIdentity.objects.create(device_id="chip-bbb")
    _make_cert(identity, serial_hex="cc", revoked=True)

    url = reverse("forgekey:ca-crl")
    resp = api_client.get(url)
    assert resp.status_code == 200

    crl = x509.load_pem_x509_crl(resp.content)
    ca_cert = x509.load_pem_x509_certificate(active_ca.cert_pem.encode("ascii"))
    public_key = ca_cert.public_key()
    assert isinstance(public_key, ec.EllipticCurvePublicKey)
    assert crl.is_signature_valid(public_key)


def test_crl_returns_503_when_no_active_ca(api_client):
    url = reverse("forgekey:ca-crl")
    resp = api_client.get(url)
    assert resp.status_code == 503
