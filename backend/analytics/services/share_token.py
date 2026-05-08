"""Signed-URL share tokens for the analytics dashboard.

Built on Django's ``TimestampSigner`` so we can stamp an expiry without
needing a database row per token. The signature is derived from
``SECRET_KEY`` plus the salt below; rotating the salt invalidates every
outstanding token (the kill-switch).

Tokens are intentionally minimal: they carry only a ``v1`` marker so we
can change the format later without breaking older URLs gracefully. They
are *not* per-user — anyone with the URL can view the dashboard until it
expires.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner

SHARE_TOKEN_SALT_SETTING = "ANALYTICS_SHARE_SALT"
DEFAULT_TTL_DAYS = 30
MAX_TTL_DAYS = 365
TOKEN_PAYLOAD_V1 = "v1"


class InvalidShareToken(Exception):
    """Raised when a token is malformed, tampered with, or expired."""


def _signer() -> TimestampSigner:
    salt = getattr(settings, SHARE_TOKEN_SALT_SETTING, "analytics-share-v1")
    return TimestampSigner(salt=salt)


def mint_share_token(ttl_days: int = DEFAULT_TTL_DAYS) -> str:
    """Return a signed token good for ``ttl_days`` days.

    Caller is responsible for clamping ``ttl_days`` to its desired range
    before reaching here; we still cap at ``MAX_TTL_DAYS`` defensively.
    """
    if ttl_days <= 0:
        raise ValueError("ttl_days must be positive")
    if ttl_days > MAX_TTL_DAYS:
        ttl_days = MAX_TTL_DAYS
    return _signer().sign(f"{TOKEN_PAYLOAD_V1}:{ttl_days}")


def verify_share_token(token: str) -> int:
    """Return the original ttl_days when valid, else raise InvalidShareToken.

    Validates both the signature and the embedded TTL — a token signed
    yesterday with ``ttl_days=1`` will be rejected today even though the
    signature itself is fine.
    """
    try:
        unsigned = _signer().unsign(token, max_age=timedelta(days=MAX_TTL_DAYS))
    except SignatureExpired as exc:
        raise InvalidShareToken("token expired") from exc
    except BadSignature as exc:
        raise InvalidShareToken("invalid signature") from exc

    try:
        version, ttl_str = unsigned.split(":", 1)
        ttl_days = int(ttl_str)
    except (ValueError, AttributeError) as exc:
        raise InvalidShareToken("malformed payload") from exc

    if version != TOKEN_PAYLOAD_V1:
        raise InvalidShareToken(f"unsupported token version {version!r}")

    # Re-check the embedded TTL — TimestampSigner only enforces MAX_TTL_DAYS above.
    try:
        _signer().unsign(token, max_age=timedelta(days=ttl_days))
    except SignatureExpired as exc:
        raise InvalidShareToken("token expired") from exc

    return ttl_days
