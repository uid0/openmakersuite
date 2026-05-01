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
import hashlib
import logging

from django.conf import settings

from cryptography.exceptions import InvalidSignature
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

logger = logging.getLogger(__name__)


class FirmwareSigningError(Exception):
    """Raised when the configured signing key is missing or unusable."""


def _get_env_pem() -> str:
    return getattr(settings, "FORGEKEY_FIRMWARE_SIGNING_KEY", "") or ""


def _kek_bytes() -> bytes:
    """Derive the Fernet KEK from Django's SECRET_KEY.

    Domain-separated so a SECRET_KEY rotation invalidates only the firmware
    private-key blobs (re-upload required) and not unrelated Fernet uses.
    """
    secret = getattr(settings, "SECRET_KEY", "") or ""
    digest = hashlib.sha256(b"forgekey.firmware.signing.v1|" + secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _fernet() -> Fernet:
    return Fernet(_kek_bytes())


def _get_db_pem() -> str | None:
    """Return the active database-managed signing key PEM, or ``None``.

    Catches errors aggressively so transient DB issues / unavailable tables
    (during very early startup or in some test settings) cannot make the
    server unable to fall back to the env-var configuration.
    """
    try:
        # Imported lazily to avoid import-time DB activity in management
        # commands that don't need signing.
        from ..models import FirmwareSigningKey
    except Exception:  # pragma: no cover
        return None
    try:
        active = FirmwareSigningKey.get_active()
    except Exception:
        return None
    if active is None:
        return None
    try:
        return active.decrypt_private_pem()
    except Exception as exc:
        logger.warning("Active FirmwareSigningKey could not be decrypted: %s", exc)
        return None


def _resolve_pem() -> str:
    """Pick the active signing-key PEM. DB row wins; env var is the fallback."""
    db_pem = _get_db_pem()
    if db_pem and db_pem.strip():
        return db_pem
    return _get_env_pem()


def is_signing_configured() -> bool:
    """True iff a non-empty signing key is configured (DB or env)."""
    return bool(_resolve_pem().strip())


def load_signing_key() -> ec.EllipticCurvePrivateKey:
    pem = _resolve_pem()
    if not pem.strip():
        raise FirmwareSigningError("Firmware signing key is not configured")
    try:
        key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    except Exception as exc:
        raise FirmwareSigningError(f"Failed to load signing key: {exc}") from exc
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise FirmwareSigningError("Firmware signing key is not an EC private key")
    if key.curve.name != "secp256r1":
        raise FirmwareSigningError(
            f"Firmware signing key must be P-256 (secp256r1); got {key.curve.name}"
        )
    return key


def get_public_key_pem() -> str:
    """Return the PEM-encoded public key for the active signing key.

    Prefers the public key explicitly stored on the active
    :class:`FirmwareSigningKey` row (cheap, no decryption required); falls
    back to deriving it from the loaded private key when only the env
    variable is configured.
    """
    try:
        from ..models import FirmwareSigningKey

        active = FirmwareSigningKey.get_active()
        if active and active.public_key_pem:
            return active.public_key_pem
    except Exception as exc:
        # DB unavailable / table missing — fall through to deriving from the
        # loaded private key. We log so an unexpected DB failure isn't silent.
        logger.warning("Falling back to derived public key: DB lookup failed (%s)", exc)
    pub = load_signing_key().public_key()
    return pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")


def generate_signing_keypair() -> tuple[str, str]:
    """Generate a fresh P-256 keypair. Returns ``(private_pem, public_pem)``."""
    private = ec.generate_private_key(ec.SECP256R1())
    private_pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_pem = (
        private.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    return private_pem, public_pem


def derive_public_pem(private_pem: str) -> str:
    """Derive the SubjectPublicKeyInfo PEM for ``private_pem``.

    Validates that the input is a P-256 EC private key.
    """
    try:
        key = serialization.load_pem_private_key(private_pem.encode("utf-8"), password=None)
    except Exception as exc:
        raise FirmwareSigningError(f"Cannot parse private key: {exc}") from exc
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise FirmwareSigningError("Provided private key is not an EC key")
    if key.curve.name != "secp256r1":
        raise FirmwareSigningError(
            f"Provided private key must be P-256 (secp256r1); got {key.curve.name}"
        )
    return (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )


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
    "derive_public_pem",
    "generate_signing_keypair",
    "get_public_key_pem",
    "is_signing_configured",
    "load_signing_key",
    "sign_firmware_bytes",
    "verify_firmware_signature",
)
