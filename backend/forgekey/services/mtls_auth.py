"""
Verify an mTLS client certificate forwarded by the reverse proxy.

nginx terminates TLS, validates the client cert against the OMS CA, and
forwards three headers when ``ssl_verify_client on`` is configured on
the listen block:

  * ``X-SSL-Client-Verify``  — ``SUCCESS`` when nginx accepted the chain.
  * ``X-SSL-Client-S-DN``    — RFC4514 subject DN of the client cert.
  * ``X-SSL-Client-Cert``    — URL-escaped PEM of the client cert (via
                               nginx's ``$ssl_client_escaped_cert``).

This module re-checks the chain inside Django for defense-in-depth (the
proxy could be misconfigured to forward headers it didn't actually
verify), pins the issuer to the *currently active* CA row, and looks
up the cert in ``DeviceCertificate`` so revocation flows still gate
access without needing nginx-side CRL plumbing.

Returns ``(authorized, device_id_or_None, reason)`` so callers can log
the rejection reason without branching on header values themselves.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import unquote

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ec

from ..models import CertificateAuthority, DeviceCertificate, DeviceIdentity

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MtlsAuthResult:
    authorized: bool
    device_id: Optional[str]
    reason: str


_OK = "ok"


def verify_mtls_request(request) -> MtlsAuthResult:
    """Validate an incoming nginx-forwarded mTLS client cert.

    Rejects with a structured reason for every failure mode rather than a
    bare ``False`` so the caller (and Sentry, when logged) can distinguish
    "proxy didn't verify" from "we don't know this cert".
    """
    verify = request.headers.get("x-ssl-client-verify", "")
    if verify != "SUCCESS":
        return MtlsAuthResult(False, None, f"proxy verify status {verify!r}")

    cert_header = request.headers.get("x-ssl-client-cert", "")
    if not cert_header:
        return MtlsAuthResult(False, None, "no client cert header")

    cert_pem = unquote(cert_header)
    try:
        cert = x509.load_pem_x509_certificate(cert_pem.encode("ascii"))
    except Exception as exc:
        return MtlsAuthResult(False, None, f"client cert unparseable: {exc}")

    active_ca = CertificateAuthority.get_active()
    if active_ca is None:
        return MtlsAuthResult(False, None, "no active CA configured")
    try:
        ca_cert = x509.load_pem_x509_certificate(active_ca.cert_pem.encode("ascii"))
    except Exception as exc:
        return MtlsAuthResult(False, None, f"active CA unparseable: {exc}")

    # Verify the leaf was actually signed by the active CA — never trust the
    # proxy's verify result alone, in case it's been reconfigured to a
    # different trust bundle.
    ca_public_key = ca_cert.public_key()
    if not isinstance(ca_public_key, ec.EllipticCurvePublicKey):
        return MtlsAuthResult(False, None, "active CA public key is not EC")
    try:
        ca_public_key.verify(
            cert.signature,
            cert.tbs_certificate_bytes,
            ec.ECDSA(cert.signature_hash_algorithm),
        )
    except InvalidSignature:
        return MtlsAuthResult(False, None, "client cert not signed by active CA")
    except Exception as exc:
        return MtlsAuthResult(False, None, f"signature verify error: {exc}")

    now = datetime.now(timezone.utc)
    if cert.not_valid_before_utc > now:
        return MtlsAuthResult(False, None, "client cert not yet valid")
    if cert.not_valid_after_utc <= now:
        return MtlsAuthResult(False, None, "client cert expired")

    # Pin to a row in DeviceCertificate so an admin-side revoke
    # (revoked_at != NULL) takes effect immediately, without nginx-side CRL.
    serial_hex = format(cert.serial_number, "x")
    db_cert = DeviceCertificate.objects.select_related("device").filter(serial=serial_hex).first()
    if db_cert is None:
        return MtlsAuthResult(False, None, f"client cert serial {serial_hex} unknown")
    if db_cert.revoked_at is not None:
        return MtlsAuthResult(False, None, f"client cert serial {serial_hex} revoked")
    if db_cert.device.status == DeviceIdentity.STATUS_DECOMMISSIONED:
        return MtlsAuthResult(False, None, f"device {db_cert.device.device_id!r} decommissioned")

    return MtlsAuthResult(True, db_cert.device.device_id, _OK)
