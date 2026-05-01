"""
Short-lived signed-URL tokens for firmware download endpoints.

Each token authorizes the bearer to GET a single FirmwareVersion's binary
within a short window (default 5 minutes). Tokens are stateless HMAC-SHA256
of ``{firmware_id}:{expiry_unix}`` keyed by Django's SECRET_KEY, so no DB
state is required to issue or verify them.

Used by ``forgekey.tasks.trigger_ota`` to embed a one-shot download URL in
the MQTT trigger payload, and by ``ForgeKeyFirmwareDownloadView`` to
authorize the resulting GET from an ESP32 device.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Optional, Tuple

from django.conf import settings


DEFAULT_TTL_SECONDS = 300


def _key() -> bytes:
    secret = getattr(settings, "SECRET_KEY", "") or ""
    return secret.encode("utf-8")


def _scope() -> bytes:
    # Domain-separate from any other HMAC use of SECRET_KEY in the project.
    return b"forgekey.firmware.download.v1"


def _digest(firmware_id: str, expiry_unix: int) -> bytes:
    msg = f"{firmware_id}:{expiry_unix}".encode("utf-8")
    return hmac.new(_key(), _scope() + b"|" + msg, hashlib.sha256).digest()


def make_download_token(firmware_id: str, *, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> Tuple[str, int]:
    """Mint a short-lived token. Returns ``(token, expiry_unix)``."""
    expiry_unix = int(time.time()) + max(int(ttl_seconds), 1)
    raw = _digest(str(firmware_id), expiry_unix)
    token = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return token, expiry_unix


def verify_download_token(
    firmware_id: str,
    token: str,
    expiry_unix: int,
    *,
    now: Optional[int] = None,
) -> bool:
    """Constant-time verification of ``token`` for ``firmware_id``/``expiry_unix``."""
    if not token or not firmware_id or not expiry_unix:
        return False
    current = int(now if now is not None else time.time())
    try:
        exp = int(expiry_unix)
    except (TypeError, ValueError):
        return False
    if exp < current:
        return False
    expected_raw = _digest(str(firmware_id), exp)
    expected = base64.urlsafe_b64encode(expected_raw).rstrip(b"=").decode("ascii")
    try:
        return hmac.compare_digest(expected, token)
    except Exception:
        return False


__all__ = (
    "DEFAULT_TTL_SECONDS",
    "make_download_token",
    "verify_download_token",
)
