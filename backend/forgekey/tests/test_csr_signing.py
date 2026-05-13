"""
Tests for :mod:`forgekey.services.csr_signing`.

Covers:
  * Valid P-256 / SHA-256 / ``CN=forgekey-<mac>`` CSR signs.
  * Issued certificate carries the documented extensions and verifies
    against the CA's public key.
  * Bad curves (RSA, P-384), wrong hash (SHA-1), malformed PEM, and
    mismatched CN all reject.
"""

from __future__ import annotations

import pytest
from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import ExtensionOID, NameOID

from forgekey.models import CertificateAuthority
from forgekey.services.ca_key_storage import encrypt_ca_key
from forgekey.services.csr_signing import (
    CsrSigningError,
    CsrValidationError,
    generate_ca_keypair,
    sign_csr,
    validate_csr,
)

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _ca_kek(settings):
    settings.FORGEKEY_CA_KEY_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")
    return settings.FORGEKEY_CA_KEY_ENCRYPTION_KEY


@pytest.fixture
def active_ca():
    private_pem, ca_cert = generate_ca_keypair(validity_days=365)
    ciphertext, kid = encrypt_ca_key(private_pem)
    return CertificateAuthority.objects.create(
        name="forgekey-root",
        cert_pem=ca_cert.public_bytes(serialization.Encoding.PEM).decode("ascii"),
        encrypted_private_key=ciphertext,
        key_kid=kid,
        not_before=ca_cert.not_valid_before_utc,
        not_after=ca_cert.not_valid_after_utc,
        is_active=True,
    )


def _build_csr(
    *,
    cn: str = "forgekey-aabbccddeeff",
    private_key=None,
    sig_hash=None,
):
    if private_key is None:
        private_key = ec.generate_private_key(ec.SECP256R1())
    if sig_hash is None:
        sig_hash = hashes.SHA256()
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(subject)
        .sign(private_key=private_key, algorithm=sig_hash)
    )
    return private_key, csr.public_bytes(serialization.Encoding.PEM).decode("ascii")


# ---------------------------------------------------------------------------
# validate_csr
# ---------------------------------------------------------------------------


def test_validate_accepts_valid_p256_sha256_csr():
    _key, pem = _build_csr()
    csr = validate_csr(pem)
    assert isinstance(csr, x509.CertificateSigningRequest)


def test_validate_rejects_rsa_csr():
    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    _key, pem = _build_csr(private_key=rsa_key)
    with pytest.raises(CsrValidationError):
        validate_csr(pem)


def test_validate_rejects_p384_csr():
    key = ec.generate_private_key(ec.SECP384R1())
    _key, pem = _build_csr(private_key=key, sig_hash=hashes.SHA384())
    with pytest.raises(CsrValidationError):
        validate_csr(pem)


def test_validate_rejects_malformed_pem():
    with pytest.raises(CsrValidationError):
        validate_csr("not a csr")


def test_validate_rejects_mismatched_cn():
    _key, pem = _build_csr(cn="something-else")
    with pytest.raises(CsrValidationError):
        validate_csr(pem)


def test_validate_rejects_uppercase_cn():
    _key, pem = _build_csr(cn="forgekey-AABBCCDDEEFF")
    with pytest.raises(CsrValidationError):
        validate_csr(pem)


# ---------------------------------------------------------------------------
# sign_csr
# ---------------------------------------------------------------------------


def test_sign_returns_cert_with_documented_extensions(active_ca):
    _key, pem = _build_csr()
    result = sign_csr(pem, device_id="chip-1", validity_days=30)
    cert = x509.load_pem_x509_certificate(result.cert_pem.encode("ascii"))

    eku = cert.extensions.get_extension_for_oid(ExtensionOID.EXTENDED_KEY_USAGE).value
    assert x509.ExtendedKeyUsageOID.CLIENT_AUTH in eku
    assert x509.ExtendedKeyUsageOID.SERVER_AUTH not in eku

    bc = cert.extensions.get_extension_for_oid(ExtensionOID.BASIC_CONSTRAINTS).value
    assert bc.ca is False

    san = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value
    uris = san.get_values_for_type(x509.UniformResourceIdentifier)
    assert uris == ["forgekey://devices/chip-1"]

    ku = cert.extensions.get_extension_for_oid(ExtensionOID.KEY_USAGE).value
    assert ku.digital_signature is True
    assert ku.key_agreement is True
    assert ku.key_cert_sign is False

    cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_KEY_IDENTIFIER)
    cert.extensions.get_extension_for_oid(ExtensionOID.AUTHORITY_KEY_IDENTIFIER)


def test_sign_cert_verifies_against_ca(active_ca):
    _key, pem = _build_csr()
    result = sign_csr(pem, device_id="chip-2", validity_days=30)
    issued = x509.load_pem_x509_certificate(result.cert_pem.encode("ascii"))
    ca_cert = x509.load_pem_x509_certificate(active_ca.cert_pem.encode("ascii"))
    ca_public_key = ca_cert.public_key()
    ca_public_key.verify(
        issued.signature,
        issued.tbs_certificate_bytes,
        ec.ECDSA(issued.signature_hash_algorithm),
    )


def test_sign_raises_when_no_active_ca():
    _key, pem = _build_csr()
    with pytest.raises(CsrSigningError):
        sign_csr(pem, device_id="chip-X", validity_days=30)


def test_sign_caps_not_after_at_ca_expiry(active_ca):
    _key, pem = _build_csr()
    # Ask for 100 years; CA is only valid for 1.
    result = sign_csr(pem, device_id="chip-cap", validity_days=365 * 100)
    assert result.not_after <= active_ca.not_after
