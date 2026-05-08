"""Recipient resolution for the monthly analytics pulse email.

Two sources, deduped on lowercase address:

1. ``settings.BOARD_REPORT_EMAILS`` — comma-separated list of addresses
   for board members or stakeholders without OMS accounts.
2. ``analytics-recipients`` Django ``Group`` — staff or members who opted
   in via their profile and have a valid email on the user record.

The group is created on first read (``Group.objects.get_or_create``) so
admins can immediately assign users to it without a separate seed step.
"""

from __future__ import annotations

import re
from typing import Iterable

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

ANALYTICS_GROUP_NAME = "analytics-recipients"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

User = get_user_model()


def _split_env_emails(raw) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, (list, tuple, set)):
        items = raw
    else:
        items = re.split(r"[,;\s]+", str(raw))
    return [s.strip() for s in items if s and s.strip()]


def _is_valid_email(value: str) -> bool:
    return bool(_EMAIL_RE.match(value))


def _ensure_group() -> Group:
    group, _ = Group.objects.get_or_create(name=ANALYTICS_GROUP_NAME)
    return group


def get_analytics_group() -> Group:
    """Return the ``analytics-recipients`` group, creating it if absent."""
    return _ensure_group()


def _group_member_emails() -> Iterable[str]:
    group = _ensure_group()
    return (
        User.objects.filter(groups=group, is_active=True)
        .exclude(email="")
        .values_list("email", flat=True)
    )


def resolve_pulse_recipients() -> list[str]:
    """Return the deduped list of pulse-email recipients.

    Comparison is case-insensitive; the returned addresses preserve the
    *first* casing seen so admin-set addresses look how the admin typed
    them. Invalid addresses are silently dropped.
    """
    env_raw = getattr(settings, "BOARD_REPORT_EMAILS", "")

    seen: dict[str, str] = {}  # lowercase → original

    def _add(addr: str) -> None:
        if not _is_valid_email(addr):
            return
        key = addr.lower()
        if key not in seen:
            seen[key] = addr

    for addr in _split_env_emails(env_raw):
        _add(addr)
    for addr in _group_member_emails():
        _add(addr)

    return list(seen.values())
