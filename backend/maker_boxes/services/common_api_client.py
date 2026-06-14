"""Common API client — badge → identity lookup via the Pi proxy.

The Dallas-Makerspace Common API exposes a badge-to-identity endpoint
(``POST /api/v1/lookupByRfid``) that returns the member's username,
full name, email, and AD group memberships. It's behind the LAN
firewall, so OMS reaches it through the Raspberry Pi printer running
the ``oms-claim-print`` daemon, which exposes a ``POST /resolve``
listener that proxies the request and returns the response.

Distinct from the WHMCS path: Common API covers add-on / sub-account
users (badges that aren't tied to a billing record) that WHMCS doesn't
see. It's authoritative for identity but says nothing about
membership expiry — for that, the caller should also hit WHMCS using
the returned username.

KeyMaster reference (`Dallas-Makerspace/RFID-KeyMaster
.../drivers/Auth/ADApiAuth.py`) and `ad-lookup-kiosk` both call this
endpoint shape.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from django.conf import settings
from django.core.cache import cache

import requests

from resilience.circuit import CircuitBreakerOpen, get_breaker

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 5 * 60
CACHE_PREFIX = "maker_boxes:common_api:"
REQUEST_TIMEOUT_SECONDS = 10


class CommonApiNotConfigured(RuntimeError):
    """Raised when ``COMMON_API_PROXY_URL`` is not set."""


@dataclass
class CommonApiUser:
    """Result of a Common API badge lookup."""

    username: str
    full_name: str = ""
    email: str = ""
    groups: list = field(default_factory=list)


def _settings():
    url = getattr(settings, "COMMON_API_PROXY_URL", "") or ""
    token = getattr(settings, "COMMON_API_PROXY_TOKEN", "") or ""
    if not url:
        raise CommonApiNotConfigured(
            "COMMON_API_PROXY_URL must be set to enable badge-keyed lookups."
        )
    return url, token


def _request(url: str, token: str, payload: dict) -> dict:
    """POST to the Pi proxy. Circuit-broken so a LAN outage fails fast."""

    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    def _post() -> requests.Response:
        response = requests.post(
            url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        return response

    try:
        return get_breaker("common_api").call(_post).json()
    except CircuitBreakerOpen as exc:
        raise requests.exceptions.ConnectionError(
            "Common API circuit breaker open — Pi proxy unhealthy"
        ) from exc


def lookup_by_rfid(rfid: str, *, use_cache: bool = True) -> Optional[CommonApiUser]:
    """Resolve a badge number to a Common API user record.

    Returns ``None`` when the Pi proxy reports no match. Raises
    :class:`CommonApiNotConfigured` when the proxy URL is unset (so
    callers can choose to either degrade to WHMCS-only or return 503).
    """
    if not rfid:
        return None

    cache_key = f"{CACHE_PREFIX}{rfid}"
    if use_cache:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    url, token = _settings()
    try:
        data = _request(url, token, {"rfid": str(rfid)})
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Common API lookup failed for badge %s: %s", rfid, exc)
        return None

    # Response shape (matches what ad-lookup-kiosk consumes):
    #   {"result": {"user": {"fullName", "username", "email", "groups": [...]}}}
    result = data.get("result") if isinstance(data, dict) else None
    user = result.get("user") if isinstance(result, dict) else None
    if not user:
        return None
    username = user.get("username") or ""
    if not username:
        return None

    record = CommonApiUser(
        username=username,
        full_name=user.get("fullName", "") or "",
        email=user.get("email", "") or "",
        groups=list(user.get("groups") or []),
    )
    if use_cache:
        cache.set(cache_key, record, CACHE_TTL_SECONDS)
    return record


__all__ = [
    "CACHE_TTL_SECONDS",
    "CommonApiNotConfigured",
    "CommonApiUser",
    "lookup_by_rfid",
]
