"""
CSR validation and signing for ForgeKey device enrollments.

Validates a PEM-encoded CSR against the firmware contract (P-256 keypair,
SHA-256 signature, ``CN=forgekey-<mac>`` shape) and issues a client
certificate signed by the active :class:`forgekey.models.CertificateAuthority`.
Issued certificates carry:

  * Subject from the CSR (validated)
  * SAN ``URI = forgekey://devices/<device_id>``
  * Extended Key Usage: ``clientAuth`` only (no serverAuth)
  * Key Usage: ``digitalSignature`` + ``keyAgreement``
  * SubjectKeyIdentifier + AuthorityKeyIdentifier
  * BasicConstraints: CA=False
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Tuple

from django.conf import settings

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtensionOID, NameOID

from .ca_key_storage import CaKeyStorageError, decrypt_ca_key


class CsrValidationError(Exception):
    """Raised when an incoming CSR doesn't satisfy the firmware contract."""


class CsrSigningError(Exception):
    """Raised when the active CA is missing or signing fails."""


# Firmware contract (forgekey/src/provisioning/register.cpp:71):
# the CSR subject's CN is `forgekey-<lowercase-mac-no-separators>`.
_CN_PATTERN = re.compile(r"^forgekey-[0-9a-f]{12}$")


@dataclass(frozen=True)
class SignedCertificate:
    cert_pem: str
    serial: str
    fingerprint_sha256: str
    not_before: datetime
    not_after: datetime
    subject: str


def validate_csr(csr_pem: str | bytes) -> x509.CertificateSigningRequest:
    """Parse + validate a CSR. Raises :class:`CsrValidationError` on rejection."""
    if isinstance(csr_pem, str):
        csr_pem = csr_pem.encode("utf-8")
    try:
        csr = x509.load_pem_x509_csr(csr_pem)
    except Exception as exc:
        raise CsrValidationError(f"Could not parse CSR PEM: {exc}") from exc

    public_key = csr.public_key()
    if not isinstance(public_key, ec.EllipticCurvePublicKey):
        raise CsrValidationError("CSR public key must be elliptic-curve.")
    if public_key.curve.name != "secp256r1":
        raise CsrValidationError(
            f"CSR public key must be P-256 (secp256r1); got {public_key.curve.name}."
        )

    sig_hash = csr.signature_hash_algorithm
    if sig_hash is None or sig_hash.name.lower() != "sha256":
        got = sig_hash.name if sig_hash else "<none>"
        raise CsrValidationError(f"CSR signature hash must be SHA-256; got {got}.")

    if not csr.is_signature_valid:
        raise CsrValidationError("CSR signature does not verify against the public key.")

    try:
        cn_attr = csr.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    except Exception as exc:
        raise CsrValidationError(f"CSR subject is malformed: {exc}") from exc
    if not cn_attr:
        raise CsrValidationError("CSR subject must include a CommonName.")
    cn_value = str(cn_attr[0].value)
    if not _CN_PATTERN.match(cn_value):
        raise CsrValidationError(
            f"CSR CommonName {cn_value!r} does not match the firmware contract "
            "(expected 'forgekey-<12-hex-mac>')."
        )

    return csr


def _load_ca() -> Tuple[x509.Certificate, ec.EllipticCurvePrivateKey, str]:
    """Return ``(ca_cert, ca_private_key, ca_name)`` for the active CA."""
    from ..models import CertificateAuthority

    ca_row = CertificateAuthority.get_active()
    if ca_row is None:
        raise CsrSigningError(
            "No active CertificateAuthority is configured; run "
            "`python manage.py forgekey_ca init` to bootstrap one."
        )
    try:
        private_pem = decrypt_ca_key(bytes(ca_row.encrypted_private_key), ca_row.key_kid or None)
    except CaKeyStorageError as exc:
        raise CsrSigningError(f"Cannot unwrap active CA private key: {exc}") from exc

    try:
        private_key = serialization.load_pem_private_key(private_pem, password=None)
    except Exception as exc:
        raise CsrSigningError(f"Active CA private key is unreadable: {exc}") from exc
    if not isinstance(private_key, ec.EllipticCurvePrivateKey):
        raise CsrSigningError("Active CA private key is not an EC key.")

    try:
        ca_cert = x509.load_pem_x509_certificate(ca_row.cert_pem.encode("utf-8"))
    except Exception as exc:
        raise CsrSigningError(f"Active CA certificate is unreadable: {exc}") from exc

    return ca_cert, private_key, ca_row.name


def sign_csr(
    csr_pem: str | bytes,
    *,
    device_id: str,
    validity_days: int | None = None,
) -> SignedCertificate:
    """Sign a validated CSR; return the new certificate and metadata."""
    csr = validate_csr(csr_pem)
    ca_cert, ca_private_key, ca_name = _load_ca()

    if validity_days is None:
        validity_days = int(getattr(settings, "FORGEKEY_CLIENT_CERT_VALIDITY_DAYS", 365))
    if validity_days <= 0:
        raise CsrSigningError("validity_days must be positive.")

    now = datetime.now(timezone.utc)
    not_before = now - timedelta(minutes=1)  # small leeway for clock skew
    not_after = now + timedelta(days=validity_days)
    if not_after > ca_cert.not_valid_after_utc:
        not_after = ca_cert.not_valid_after_utc

    serial = x509.random_serial_number()

    builder = (
        x509.CertificateBuilder()
        .subject_name(csr.subject)
        .issuer_name(ca_cert.subject)
        .public_key(csr.public_key())
        .serial_number(serial)
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=True,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.CLIENT_AUTH]),
            critical=True,
        )
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.UniformResourceIdentifier(f"forgekey://devices/{device_id}")]
            ),
            critical=False,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(csr.public_key()),
            critical=False,
        )
        .add_extension(
            _authority_key_identifier_from(ca_cert),
            critical=False,
        )
    )

    cert = builder.sign(private_key=ca_private_key, algorithm=hashes.SHA256())
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
    fingerprint = cert.fingerprint(hashes.SHA256()).hex()
    subject = cert.subject.rfc4514_string()

    return SignedCertificate(
        cert_pem=cert_pem,
        serial=format(serial, "x"),
        fingerprint_sha256=fingerprint,
        not_before=not_before,
        not_after=not_after,
        subject=subject,
    )


def _authority_key_identifier_from(ca_cert: x509.Certificate) -> x509.AuthorityKeyIdentifier:
    """Derive an AuthorityKeyIdentifier extension from the CA cert.

    Prefers reusing the SubjectKeyIdentifier extension already on the CA; if
    absent, falls back to recomputing it from the CA's public key.
    """
    try:
        ski_ext = ca_cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_KEY_IDENTIFIER)
        return x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ski_ext.value)
    except x509.ExtensionNotFound:
        return x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_cert.public_key())


def generate_ca_keypair(
    *,
    cn: str = "ForgeKey Internal Root CA",
    validity_days: int = 365 * 10,
) -> Tuple[bytes, x509.Certificate]:
    """Generate a fresh self-signed P-256 CA keypair.

    Returns ``(private_pem_bytes, ca_certificate)``. The caller is responsible
    for persisting the wrapped private key (via :mod:`ca_key_storage`) plus
    the public PEM of the CA cert.
    """
    if validity_days <= 0:
        raise ValueError("validity_days must be positive.")

    private_key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(timezone.utc)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    serial = int.from_bytes(secrets.token_bytes(16), "big") | 1

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(serial)
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=validity_days))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(private_key.public_key()),
            critical=False,
        )
        .sign(private_key=private_key, algorithm=hashes.SHA256())
    )

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return private_pem, cert


def sign_firmware_signing_csr(
    public_key: ec.EllipticCurvePublicKey,
    *,
    label: str,
    validity_days: int | None = None,
) -> SignedCertificate:
    """Issue a CA-signed leaf cert intended for firmware signature verification.

    Differences from :func:`sign_csr`:

    * Operates on a public key rather than a CSR PEM — firmware signing keys
      live in :class:`forgekey.models.FirmwareSigningKey`, the caller already
      has the keypair in hand and there is no separate CSR to validate.
    * ``ExtendedKeyUsage = CODE_SIGNING`` (OID 1.3.6.1.5.5.7.3.3) rather than
      CLIENT_AUTH; this leaf is **not** an mTLS client cert and must not be
      accepted as one by the broker / firmware-download endpoints.
    * ``SubjectAlternativeName`` carries a ``forgekey://firmware-signers/<label>``
      URI so devices can introspect which logical signer (not which device)
      authored a given firmware artifact.

    Returns a :class:`SignedCertificate`; the caller persists ``cert_pem`` on
    the :class:`FirmwareSigningKey` row.
    """
    if not isinstance(public_key, ec.EllipticCurvePublicKey):
        raise CsrSigningError("firmware signing public key must be elliptic-curve.")
    if public_key.curve.name != "secp256r1":
        raise CsrSigningError(
            f"firmware signing public key must be P-256; got {public_key.curve.name}."
        )

    ca_cert, ca_private_key, _ca_name = _load_ca()

    if validity_days is None:
        validity_days = int(getattr(settings, "FORGEKEY_FIRMWARE_SIGNER_CERT_VALIDITY_DAYS", 730))
    if validity_days <= 0:
        raise CsrSigningError("validity_days must be positive.")

    now = datetime.now(timezone.utc)
    not_before = now - timedelta(minutes=1)
    not_after = now + timedelta(days=validity_days)
    if not_after > ca_cert.not_valid_after_utc:
        not_after = ca_cert.not_valid_after_utc

    serial = x509.random_serial_number()
    subject = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, f"forgekey-firmware-signer-{label}")]
    )

    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(public_key)
        .serial_number(serial)
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.CODE_SIGNING]),
            critical=True,
        )
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.UniformResourceIdentifier(f"forgekey://firmware-signers/{label}")]
            ),
            critical=False,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(public_key),
            critical=False,
        )
        .add_extension(
            _authority_key_identifier_from(ca_cert),
            critical=False,
        )
    )

    cert = builder.sign(private_key=ca_private_key, algorithm=hashes.SHA256())
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
    fingerprint = cert.fingerprint(hashes.SHA256()).hex()
    return SignedCertificate(
        cert_pem=cert_pem,
        serial=format(serial, "x"),
        fingerprint_sha256=fingerprint,
        not_before=not_before,
        not_after=not_after,
        subject=cert.subject.rfc4514_string(),
    )


__all__ = (
    "CsrSigningError",
    "CsrValidationError",
    "SignedCertificate",
    "generate_ca_keypair",
    "sign_csr",
    "validate_csr",
)
