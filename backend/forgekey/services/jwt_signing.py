"""
ECDSA(P-256) device-JWT signing.

Devices receive a per-MAC JWT at registration (see ``forgekey.utils``); EMQX
verifies that JWT against the public key served from ``/api/forgekey/jwks/``
to grant the device its MQTT session. The private key lives in
``FORGEKEY_JWT_SIGNING_KEY`` (PEM, may contain literal ``\\n`` newlines).

This is a separate keypair from firmware signing (``firmware_signing.py``)
so the two trust roots can be rotated independently.
"""

from __future__ import annotations

import base64

from django.conf import settings

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


class JwtSigningError(Exception):
    """Raised when the configured JWT signing key is missing or unusable."""


def _get_env_pem() -> str:
    return getattr(settings, "FORGEKEY_JWT_SIGNING_KEY", "") or ""


def is_jwt_signing_configured() -> bool:
    """True iff a non-empty JWT signing key is configured."""
    return bool(_get_env_pem().strip())


def load_jwt_signing_key() -> ec.EllipticCurvePrivateKey:
    """Return the parsed EC private key, or raise :class:`JwtSigningError`."""
    pem = _get_env_pem()
    if not pem.strip():
        raise JwtSigningError("FORGEKEY_JWT_SIGNING_KEY is not configured")
    try:
        key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    except Exception as exc:
        raise JwtSigningError(f"Failed to load JWT signing key: {exc}") from exc
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise JwtSigningError("JWT signing key is not an EC private key")
    if key.curve.name != "secp256r1":
        raise JwtSigningError(f"JWT signing key must be P-256 (secp256r1); got {key.curve.name}")
    return key


def get_jwt_public_key_pem() -> str:
    """Return the PEM-encoded public key for the configured JWT signing key."""
    pub = load_jwt_signing_key().public_key()
    return pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")


def _b64url_uint(value: int, byte_length: int) -> str:
    raw = value.to_bytes(byte_length, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def get_jwt_jwks() -> dict:
    """Return the JWK Set advertising the active JWT public key.

    EMQX fetches this URL and uses the matching key (by ``kid``) to verify
    incoming device JWTs. RFC 7517 / RFC 7518 §6.2 format for EC keys.
    """
    pub = load_jwt_signing_key().public_key()
    numbers = pub.public_numbers()
    # P-256 coordinates are 32 bytes
    x = _b64url_uint(numbers.x, 32)
    y = _b64url_uint(numbers.y, 32)
    kid = getattr(settings, "FORGEKEY_JWT_KEY_ID", "forgekey-jwt-1")
    return {
        "keys": [
            {
                "kty": "EC",
                "crv": "P-256",
                "alg": "ES256",
                "use": "sig",
                "kid": kid,
                "x": x,
                "y": y,
            }
        ]
    }


def generate_jwt_signing_keypair() -> tuple[str, str]:
    """Generate a fresh P-256 keypair. Returns ``(private_pem, public_pem)``.

    Used by ``manage.py generate_forgekey_jwt_key`` and tests; the result
    must be persisted to ``FORGEKEY_JWT_SIGNING_KEY``.
    """
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


__all__ = (
    "JwtSigningError",
    "generate_jwt_signing_keypair",
    "get_jwt_jwks",
    "get_jwt_public_key_pem",
    "is_jwt_signing_configured",
    "load_jwt_signing_key",
)
