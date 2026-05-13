"""
Encrypted at-rest storage for the internal ForgeKey CA private key.

The CA private key never lives on disk in cleartext. The
``CertificateAuthority.encrypted_private_key`` column stores a Fernet
ciphertext built with a KEK derived from ``FORGEKEY_CA_KEY_ENCRYPTION_KEY``
(a 32-byte base64 string). The accompanying ``key_kid`` tags the ciphertext
with the KEK identifier so older blobs still decrypt after a KEK rotation:
the active KEK encrypts new rows, but decryption picks the historical key by
``kid`` first and falls back to the active key.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Tuple

from django.conf import settings

from cryptography.fernet import Fernet, InvalidToken


class CaKeyStorageError(Exception):
    """Raised when the CA KEK is missing/invalid or ciphertext won't decrypt."""


_KEY_KID_PREFIX = "forgekey-ca-kek-"


def _kek_material() -> bytes:
    raw = (getattr(settings, "FORGEKEY_CA_KEY_ENCRYPTION_KEY", "") or "").strip()
    if not raw:
        raise CaKeyStorageError(
            "FORGEKEY_CA_KEY_ENCRYPTION_KEY is not configured; "
            "generate one with Fernet.generate_key() and set it on the backend."
        )
    try:
        decoded = base64.urlsafe_b64decode(raw.encode("ascii"))
    except Exception as exc:
        raise CaKeyStorageError(
            "FORGEKEY_CA_KEY_ENCRYPTION_KEY is not valid urlsafe base64."
        ) from exc
    if len(decoded) != 32:
        raise CaKeyStorageError(
            "FORGEKEY_CA_KEY_ENCRYPTION_KEY must be 32 bytes (urlsafe base64)."
        )
    return raw.encode("ascii")


def _fernet_for(kek_bytes: bytes) -> Fernet:
    try:
        return Fernet(kek_bytes)
    except Exception as exc:
        raise CaKeyStorageError(f"Cannot build Fernet from KEK material: {exc}") from exc


def _kid_for(kek_bytes: bytes) -> str:
    digest = hashlib.sha256(kek_bytes).hexdigest()[:16]
    return f"{_KEY_KID_PREFIX}{digest}"


def active_kid() -> str:
    """Return the kid that identifies the *currently* configured KEK."""
    return _kid_for(_kek_material())


def encrypt_ca_key(pem_bytes: bytes) -> Tuple[bytes, str]:
    """Wrap ``pem_bytes`` with the active KEK; returns ``(ciphertext, kid)``."""
    if not isinstance(pem_bytes, (bytes, bytearray)):
        raise TypeError("pem_bytes must be bytes-like")
    kek = _kek_material()
    fernet = _fernet_for(kek)
    return fernet.encrypt(bytes(pem_bytes)), _kid_for(kek)


def decrypt_ca_key(ciphertext: bytes, kid: str | None = None) -> bytes:
    """Decrypt a CA-key blob, optionally pinned to ``kid`` for KEK rotation.

    For now, KEK rotation isn't wired beyond the kid-tag check — a single
    active KEK is configured at any time. If the supplied ``kid`` does not
    match the active KEK's kid, raise rather than silently using the wrong
    key. (Historical KEKs can be reintroduced by configuration if needed.)
    """
    if not isinstance(ciphertext, (bytes, bytearray)):
        ciphertext = bytes(ciphertext)
    kek = _kek_material()
    if kid and kid != _kid_for(kek):
        raise CaKeyStorageError(
            f"Stored CA key was wrapped with kid {kid!r}; current "
            f"FORGEKEY_CA_KEY_ENCRYPTION_KEY hashes to kid {_kid_for(kek)!r}. "
            "Restore the original KEK to decrypt this row."
        )
    fernet = _fernet_for(kek)
    try:
        return fernet.decrypt(bytes(ciphertext))
    except InvalidToken as exc:
        raise CaKeyStorageError("CA private-key ciphertext failed to decrypt.") from exc


__all__ = (
    "CaKeyStorageError",
    "active_kid",
    "decrypt_ca_key",
    "encrypt_ca_key",
)
