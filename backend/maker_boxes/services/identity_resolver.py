"""Cascading identity resolver for maker-box workflows.

When an operator scans a badge or types a username on the
pre-conversion / scan screens, OMS needs to answer "who is this?"
against two independent backends:

  * **WHMCS** — billing system; canonical for paid-membership
    expiry. Keyed by username (not badge). Doesn't see add-on /
    sub-account users.
  * **Common API** (via the Pi proxy) — Active Directory–backed
    badge-to-identity service running on the LAN. Keyed by badge.
    Sees add-on users that WHMCS doesn't.

This module is the only place that knows how to compose the two:

  * Digit-only input  →  treat as a badge; query Common API first.
    If we get a hit, use the returned username to re-query WHMCS
    so we still know whether this person's membership is paid up.
    If Common API misses, the input is unknown (badges don't map
    to WHMCS usernames).
  * Otherwise         →  treat as a username; query WHMCS only.
    Common API is keyed by badge, not username, so there's no
    second leg here.

Either backend being unconfigured degrades gracefully — the other
leg still runs. A miss from both legs returns ``None``.

The return type is the existing ``MemberLookup`` dataclass so
callers don't care which backend answered; ``identity_source``
records which one for audit / display.
"""

from __future__ import annotations

import logging
from typing import Optional

from . import common_api_client, whmcs_client
from .common_api_client import CommonApiNotConfigured, CommonApiUser
from .whmcs_client import MemberLookup, WhmcsNotConfigured

logger = logging.getLogger(__name__)


def _looks_like_badge(query: str) -> bool:
    """Treat a digit-only string of >= 4 chars as a badge guess.

    Real RFID badge numbers in the DMS deploy are 8–10 digits; we
    accept >= 4 to be tolerant of test fixtures and short pilot IDs.
    Usernames in the DMS WHMCS install are alphanumeric but rarely
    purely numeric, so this heuristic almost never sends a real
    username down the badge path.
    """
    cleaned = query.strip()
    return len(cleaned) >= 4 and cleaned.isdigit()


def _merge(common: CommonApiUser, whmcs: Optional[MemberLookup]) -> MemberLookup:
    """Take Common API identity + (optional) WHMCS membership expiry."""
    first, _, last = common.full_name.partition(" ")
    if whmcs is None:
        # AD knows the user; WHMCS doesn't. That's the add-on case —
        # report ``valid`` because the badge resolved, and leave
        # expiry None. The caller (pre-conversion UI) can render the
        # identity_source so staff sees "Common API (badge / AD)" and
        # knows there's no WHMCS billing tie.
        return MemberLookup(
            status="valid",
            username=common.username,
            first_name=first,
            last_name=last,
            email=common.email,
            expires_at=None,
            days_remaining=None,
        )
    # WHMCS knows them too — prefer WHMCS first/last/email since those
    # are the ones that print on the label, but fill blanks from AD.
    return MemberLookup(
        status=whmcs.status,
        username=whmcs.username or common.username,
        first_name=whmcs.first_name or first,
        last_name=whmcs.last_name or last,
        email=whmcs.email or common.email,
        expires_at=whmcs.expires_at,
        days_remaining=whmcs.days_remaining,
    )


def resolve(query: str, *, use_cache: bool = True) -> tuple[Optional[MemberLookup], str]:
    """Cascade identity lookup. Returns ``(MemberLookup | None, source)``.

    ``source`` is one of ``"whmcs"`` / ``"common_api"`` / ``""`` (when
    neither backend hit). The string mirrors
    ``MakerBox.IDENTITY_SOURCE_CHOICES`` so the caller can write it
    directly to the row.
    """
    if not query:
        return None, ""

    if _looks_like_badge(query):
        return _resolve_badge(query, use_cache=use_cache)
    return _resolve_username(query, use_cache=use_cache)


def _resolve_badge(badge: str, *, use_cache: bool) -> tuple[Optional[MemberLookup], str]:
    try:
        common = common_api_client.lookup_by_rfid(badge, use_cache=use_cache)
    except CommonApiNotConfigured:
        logger.info("Common API not configured; badge %s cannot be resolved", badge)
        return None, ""
    if common is None:
        return None, ""

    # We have an identity from AD; layer WHMCS on top for billing
    # status. WHMCS may also be unconfigured — that's fine, we still
    # return the identity record marked as common_api.
    whmcs: Optional[MemberLookup] = None
    try:
        whmcs = whmcs_client.lookup_member(common.username, use_cache=use_cache)
    except WhmcsNotConfigured:
        logger.debug("WHMCS not configured; returning Common API only for %s", common.username)

    return _merge(common, whmcs), "common_api"


def _resolve_username(username: str, *, use_cache: bool) -> tuple[Optional[MemberLookup], str]:
    try:
        whmcs = whmcs_client.lookup_member(username, use_cache=use_cache)
    except WhmcsNotConfigured:
        logger.info("WHMCS not configured; username %s cannot be resolved", username)
        return None, ""
    if whmcs is None:
        return None, ""
    return whmcs, "whmcs"


__all__ = ["resolve"]
