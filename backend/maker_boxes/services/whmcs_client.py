"""WHMCS client for looking up makerspace member status.

WHMCS exposes a JSON API endpoint that accepts ``identifier`` /
``secret`` (or ``username`` / ``password``) plus an ``accesskey`` for
admin actions. We call ``GetClientsDetails`` and read the client status
plus the latest active service end date to decide whether the membership
is currently valid, in a 7-day grace window, or expired.

The credentials must be supplied via environment variables (see
``.env.example``); when any of the required values are missing the
service raises :class:`WhmcsNotConfigured` so callers can return a 503.
Lookups are cached for 5 minutes to absorb scan bursts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

import requests

logger = logging.getLogger(__name__)

GRACE_PERIOD_DAYS = 7
CACHE_TTL_SECONDS = 5 * 60
CACHE_PREFIX = "maker_boxes:whmcs:"
REQUEST_TIMEOUT_SECONDS = 10


class WhmcsNotConfigured(RuntimeError):
    """Raised when the WHMCS environment variables are missing."""


@dataclass
class MemberLookup:
    """Result of a WHMCS membership lookup.

    ``status`` is one of ``valid`` / ``grace`` / ``expired`` / ``unknown``.
    """

    status: str
    username: str
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    expires_at: Optional[datetime] = None
    days_remaining: Optional[int] = None


def _settings():
    url = getattr(settings, "WHMCS_API_URL", "") or ""
    identifier = getattr(settings, "WHMCS_API_IDENTIFIER", "") or ""
    secret = getattr(settings, "WHMCS_API_SECRET", "") or ""
    accesskey = getattr(settings, "WHMCS_API_ACCESSKEY", "") or ""
    if not (url and identifier and secret):
        raise WhmcsNotConfigured(
            "WHMCS_API_URL, WHMCS_API_IDENTIFIER, and WHMCS_API_SECRET must be set"
        )
    return url, identifier, secret, accesskey


def _parse_date(value: Optional[str]) -> Optional[datetime]:
    """WHMCS dates come as YYYY-MM-DD or empty / 0000-00-00 sentinels."""
    if not value or value.startswith("0000"):
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None
    return timezone.make_aware(parsed, timezone.get_current_timezone())


def _classify(expires_at: Optional[datetime], whmcs_status: str) -> tuple[str, Optional[int]]:
    """Decide our internal status from WHMCS status + nextduedate."""
    now = timezone.now()
    if expires_at is None:
        # No service end date — fall back to WHMCS account status only.
        if whmcs_status.lower() == "active":
            return "valid", None
        return "expired", None

    delta = expires_at - now
    days_remaining = int(delta.total_seconds() // 86400)
    if expires_at >= now:
        return "valid", max(days_remaining, 0)
    if -delta <= timedelta(days=GRACE_PERIOD_DAYS):
        # Days remaining is negative-or-zero in grace; expose 0 lower bound.
        return "grace", max(GRACE_PERIOD_DAYS + days_remaining, 0)
    return "expired", days_remaining


def _request(url: str, payload: dict) -> dict:
    response = requests.post(url, data=payload, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def lookup_member(username: str, *, use_cache: bool = True) -> Optional[MemberLookup]:
    """Look up a member by WHMCS username.

    Returns ``None`` when WHMCS reports no such client. Raises
    :class:`WhmcsNotConfigured` when the API credentials are missing.
    """
    if not username:
        return None

    cache_key = f"{CACHE_PREFIX}{username.lower()}"
    if use_cache:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    url, identifier, secret, accesskey = _settings()
    payload = {
        "action": "GetClientsDetails",
        "identifier": identifier,
        "secret": secret,
        "username2": username,
        "stats": "false",
        "responsetype": "json",
    }
    if accesskey:
        payload["accesskey"] = accesskey

    try:
        data = _request(url, payload)
    except (requests.RequestException, ValueError) as exc:
        logger.warning("WHMCS lookup failed for %s: %s", username, exc)
        return None

    if str(data.get("result", "")).lower() != "success":
        # WHMCS returns result=error with message="Client Not Found" for misses.
        return None

    expires_at = _parse_date(data.get("nextduedate") or data.get("client_nextduedate"))
    whmcs_status = str(data.get("status") or data.get("clientstatus") or "").strip()

    classified, days_remaining = _classify(expires_at, whmcs_status)
    result = MemberLookup(
        status=classified,
        username=username,
        first_name=data.get("firstname", "") or "",
        last_name=data.get("lastname", "") or "",
        email=data.get("email", "") or "",
        expires_at=expires_at,
        days_remaining=days_remaining,
    )

    if use_cache:
        cache.set(cache_key, result, CACHE_TTL_SECONDS)
    return result


__all__ = [
    "GRACE_PERIOD_DAYS",
    "CACHE_TTL_SECONDS",
    "MemberLookup",
    "WhmcsNotConfigured",
    "lookup_member",
]
