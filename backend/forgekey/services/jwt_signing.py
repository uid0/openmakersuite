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
import json
import time

from django.conf import settings

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
)


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


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def make_command_jwt(*, mac: str, cmd: str, exp_seconds: int = 60) -> str:
    """Sign a short-TTL command JWT for a ForgeKey device.

    The JWT is the credential the firmware verifies before acting on a
    `forgekey/<mac>/cmd` MQTT message — used by the locker / door /
    electronic-device pipeline (gh ForgeKey expansion, Phase 1).

    Payload shape: ``{"mac": ..., "cmd": ..., "exp": <unix-seconds>}``.
    Signed with ES256 using the same key that backs EMQX device-auth
    JWTs, so firmware only carries one public-key trust root.

    `exp_seconds` defaults to 60 — the TTL must be short enough that a
    captured command can't be replayed minutes later. Caller is
    responsible for picking a value compatible with their NTP-sync
    posture; values above 300 are intentionally allowed but should be
    rare and audited.
    """
    if exp_seconds <= 0:
        raise ValueError("exp_seconds must be positive")

    key = load_jwt_signing_key()
    kid = getattr(settings, "FORGEKEY_JWT_KEY_ID", "forgekey-jwt-1")
    header = {"alg": "ES256", "typ": "JWT", "kid": kid}
    payload = {
        "mac": mac,
        "cmd": cmd,
        "exp": int(time.time()) + exp_seconds,
    }

    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")

    der_signature = key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    # ES256 wants r || s as 32-byte big-endian halves, not the DER
    # encoding produced by `cryptography`.
    r, s = decode_dss_signature(der_signature)
    signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    signature_b64 = _b64url_encode(signature)
    return f"{header_b64}.{payload_b64}.{signature_b64}"


__all__ = (
    "JwtSigningError",
    "generate_jwt_signing_keypair",
    "get_jwt_jwks",
    "get_jwt_public_key_pem",
    "is_jwt_signing_configured",
    "load_jwt_signing_key",
    "make_command_jwt",
)
