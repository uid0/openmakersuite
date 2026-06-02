"""
Server-side mTLS verification for the firmware download endpoint.

verify_mtls_request reads the X-SSL-Client-* headers nginx forwards from
the dedicated mTLS listener, re-checks the cert chain against the
currently active CA (defense-in-depth — never trusts the proxy alone),
and gates on the DB-side revoke / decommission flags so an admin-side
state change takes effect immediately without nginx-side CRL plumbing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import StringIO
from urllib.parse import quote

from django.core.management import call_command
from django.test import RequestFactory

import pytest
from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from forgekey.models import CertificateAuthority, DeviceCertificate, DeviceIdentity
from forgekey.services.mtls_auth import verify_mtls_request

pytestmark = pytest.mark.django_db


# ----- fixtures --------------------------------------------------------------


@pytest.fixture
def kek(settings):
    settings.FORGEKEY_CA_KEY_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")


@pytest.fixture
def active_ca(kek):
    out = StringIO()
    call_command("forgekey_ca", "init", "--validity-years", "1", stdout=out)
    return CertificateAuthority.get_active()


@pytest.fixture
def device():
    return DeviceIdentity.objects.create(device_id="chip-aabbccddeeff")


@pytest.fixture
def signed_device_cert(active_ca, device):
    """Issue a device cert via the same sign_csr path /enroll/ uses, persist
    the metadata row, return (cert_pem, private_key) so the test can craft
    realistic mTLS headers."""
    from forgekey.services.csr_signing import sign_csr

    private_key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "forgekey-aabbccddeeff")])
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(subject)
        .sign(private_key=private_key, algorithm=hashes.SHA256())
    )
    csr_pem = csr.public_bytes(serialization.Encoding.PEM).decode("ascii")

    signed = sign_csr(csr_pem, device_id=device.device_id)
    DeviceCertificate.objects.create(
        device=device,
        serial=signed.serial,
        subject=signed.subject,
        fingerprint_sha256=signed.fingerprint_sha256,
        not_before=signed.not_before,
        not_after=signed.not_after,
        issued_by=active_ca.name,
    )
    return signed.cert_pem, private_key


def _request_with_mtls_headers(cert_pem: str, verify: str = "SUCCESS"):
    """Build a request mirroring what nginx forwards from the mTLS listener."""
    request = RequestFactory().get("/api/forgekey/firmware/xxx/download")
    request.META["HTTP_X_SSL_CLIENT_VERIFY"] = verify
    # nginx's $ssl_client_escaped_cert is URL-encoded so the PEM newlines
    # survive the HTTP header byte set.
    request.META["HTTP_X_SSL_CLIENT_CERT"] = quote(cert_pem)
    return request


# ----- happy path ------------------------------------------------------------


def test_valid_cert_authorizes_and_returns_device_id(signed_device_cert, device):
    cert_pem, _key = signed_device_cert
    request = _request_with_mtls_headers(cert_pem)

    result = verify_mtls_request(request)

    assert result.authorized is True
    assert result.device_id == device.device_id
    assert result.reason == "ok"


# ----- rejection paths --------------------------------------------------------


def test_missing_verify_header_rejects():
    request = RequestFactory().get("/")
    result = verify_mtls_request(request)
    assert result.authorized is False
    assert "proxy verify status" in result.reason


def test_verify_header_not_success_rejects():
    request = RequestFactory().get("/")
    request.META["HTTP_X_SSL_CLIENT_VERIFY"] = "FAILED:self-signed certificate"
    result = verify_mtls_request(request)
    assert result.authorized is False
    assert "FAILED" in result.reason


def test_verify_success_but_no_cert_header_rejects():
    request = RequestFactory().get("/")
    request.META["HTTP_X_SSL_CLIENT_VERIFY"] = "SUCCESS"
    # X-SSL-Client-Cert intentionally missing
    result = verify_mtls_request(request)
    assert result.authorized is False
    assert "no client cert header" in result.reason


def test_unparseable_cert_rejects():
    request = _request_with_mtls_headers("not a valid PEM at all")
    result = verify_mtls_request(request)
    assert result.authorized is False
    assert "unparseable" in result.reason


def test_no_active_ca_rejects(signed_device_cert):
    cert_pem, _key = signed_device_cert
    # Deactivate the only CA after a cert was signed against it.
    CertificateAuthority.objects.update(is_active=False)
    request = _request_with_mtls_headers(cert_pem)
    result = verify_mtls_request(request)
    assert result.authorized is False
    assert "no active CA" in result.reason


def test_cert_signed_by_different_ca_rejects(signed_device_cert, kek):
    """Issue a leaf against an outsider CA, present it under the active CA's
    listener — must fail signature verification."""
    from forgekey.services.csr_signing import generate_ca_keypair

    cert_pem, _key = signed_device_cert
    # Tear down the original active CA and stand up a different one — the
    # original-signed leaf must no longer verify under the new active CA.
    CertificateAuthority.objects.all().delete()
    other_private_pem, other_cert = generate_ca_keypair(cn="other CA", validity_days=30)
    from forgekey.services.ca_key_storage import encrypt_ca_key

    ct, kid = encrypt_ca_key(other_private_pem)
    CertificateAuthority.objects.create(
        name="other-root",
        cert_pem=other_cert.public_bytes(serialization.Encoding.PEM).decode("ascii"),
        encrypted_private_key=ct,
        key_kid=kid,
        not_before=other_cert.not_valid_before_utc,
        not_after=other_cert.not_valid_after_utc,
        is_active=True,
    )

    request = _request_with_mtls_headers(cert_pem)
    result = verify_mtls_request(request)
    assert result.authorized is False
    assert "not signed by active CA" in result.reason


def test_revoked_cert_rejects(signed_device_cert):
    cert_pem, _key = signed_device_cert
    DeviceCertificate.objects.update(revoked_at=datetime.now(timezone.utc))
    request = _request_with_mtls_headers(cert_pem)
    result = verify_mtls_request(request)
    assert result.authorized is False
    assert "revoked" in result.reason


def test_decommissioned_device_rejects(signed_device_cert, device):
    cert_pem, _key = signed_device_cert
    device.status = DeviceIdentity.STATUS_DECOMMISSIONED
    device.save()
    request = _request_with_mtls_headers(cert_pem)
    result = verify_mtls_request(request)
    assert result.authorized is False
    assert "decommissioned" in result.reason


def test_unknown_serial_rejects(active_ca, device):
    """A leaf signed by the active CA but with no DeviceCertificate row —
    e.g., a cert issued out-of-band that bypassed our admin/enroll flow."""
    from forgekey.services.csr_signing import sign_csr

    private_key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "forgekey-aabbccddeeff")])
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(subject)
        .sign(private_key=private_key, algorithm=hashes.SHA256())
    )
    csr_pem = csr.public_bytes(serialization.Encoding.PEM).decode("ascii")
    signed = sign_csr(csr_pem, device_id=device.device_id)
    # Intentionally do NOT persist a DeviceCertificate row.

    request = _request_with_mtls_headers(signed.cert_pem)
    result = verify_mtls_request(request)
    assert result.authorized is False
    assert "serial" in result.reason and "unknown" in result.reason


def test_expired_cert_rejects(active_ca, device, monkeypatch):
    """sign_csr enforces a non-trivial validity floor; simulate expiry by
    monkeypatching `datetime.now` in mtls_auth so the validity window
    arithmetic treats our newly-signed cert as past its not_after."""
    from forgekey.services.csr_signing import sign_csr

    private_key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "forgekey-aabbccddeeff")])
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(subject)
        .sign(private_key=private_key, algorithm=hashes.SHA256())
    )
    csr_pem = csr.public_bytes(serialization.Encoding.PEM).decode("ascii")
    signed = sign_csr(csr_pem, device_id=device.device_id)
    DeviceCertificate.objects.create(
        device=device,
        serial=signed.serial,
        subject=signed.subject,
        fingerprint_sha256=signed.fingerprint_sha256,
        not_before=signed.not_before,
        not_after=signed.not_after,
        issued_by=active_ca.name,
    )

    # Fast-forward time past the cert's not_after.
    from forgekey.services import mtls_auth

    fake_now = signed.not_after + timedelta(days=1)
    monkeypatch.setattr(mtls_auth, "datetime", _FrozenDatetime.with_now(fake_now))

    request = _request_with_mtls_headers(signed.cert_pem)
    result = verify_mtls_request(request)
    assert result.authorized is False
    assert "expired" in result.reason


class _FrozenDatetime(datetime):
    """A datetime subclass with a pinned ``now()`` for monkeypatching."""

    _frozen: datetime

    @classmethod
    def with_now(cls, when: datetime) -> "type[_FrozenDatetime]":
        return type("_FrozenAt", (cls,), {"_frozen": when})

    @classmethod
    def now(cls, tz=None):
        return cls._frozen if tz is None else cls._frozen.astimezone(tz)
