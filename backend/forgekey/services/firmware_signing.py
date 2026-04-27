"""
ECDSA(P-256) firmware signing.

The OMS holds the private signing key (FORGEKEY_FIRMWARE_SIGNING_KEY env var,
PEM-encoded). Each FirmwareVersion's binary is signed on save and the base64
DER-encoded signature is included in MQTT firmware-update payloads dispatched
to forgekey/<mac>/firmware. Devices verify with a baked-in public key and
refuse unsigned dispatches once they're on a verifying firmware.
"""

from __future__ import annotations

import base64

from django.conf import settings

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec


class FirmwareSigningError(Exception):
    """Raised when the configured signing key is missing or unusable."""


def _get_pem() -> str:
    return getattr(settings, "FORGEKEY_FIRMWARE_SIGNING_KEY", "") or ""


def is_signing_configured() -> bool:
    """True iff a non-empty signing key is configured."""
    return bool(_get_pem().strip())


def load_signing_key() -> ec.EllipticCurvePrivateKey:
    pem = _get_pem()
    if not pem.strip():
        raise FirmwareSigningError("FORGEKEY_FIRMWARE_SIGNING_KEY is not configured")
    try:
        key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    except Exception as exc:
        raise FirmwareSigningError(f"Failed to load signing key: {exc}") from exc
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise FirmwareSigningError("FORGEKEY_FIRMWARE_SIGNING_KEY is not an EC private key")
    if key.curve.name != "secp256r1":
        raise FirmwareSigningError(
            f"FORGEKEY_FIRMWARE_SIGNING_KEY must be P-256 (secp256r1); got {key.curve.name}"
        )
    return key


def get_public_key_pem() -> str:
    """Return the PEM-encoded public key derived from the configured private key."""
    pub = load_signing_key().public_key()
    return pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")


def sign_firmware_bytes(data: bytes) -> str:
    """Sign ``data`` with the configured ECDSA(P-256) key and return base64(DER)."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("firmware payload must be bytes")
    key = load_signing_key()
    signature = key.sign(data, ec.ECDSA(hashes.SHA256()))
    return base64.b64encode(signature).decode("ascii")


def verify_firmware_signature(data: bytes, signature_b64: str, public_key_pem: str) -> bool:
    """Verify ``signature_b64`` against ``data`` using ``public_key_pem``."""
    try:
        signature = base64.b64decode(signature_b64.encode("ascii"), validate=True)
    except Exception:
        return False
    try:
        public_key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
    except Exception:
        return False
    if not isinstance(public_key, ec.EllipticCurvePublicKey):
        return False
    try:
        public_key.verify(signature, data, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        return False
    except Exception:
        return False
    return True


__all__ = (
    "FirmwareSigningError",
    "get_public_key_pem",
    "is_signing_configured",
    "load_signing_key",
    "sign_firmware_bytes",
    "verify_firmware_signature",
)
