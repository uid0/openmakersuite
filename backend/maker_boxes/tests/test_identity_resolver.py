"""Tests for the WHMCS + Common API cascade (PR1).

Cascade rules under test:

* Digit input  → Common API first; layer WHMCS on top if present.
* Word input   → WHMCS only (Common API is keyed by badge, not username).
* If a backend is unconfigured the other still runs.
* Both backends missing the user → ``(None, "")``.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

import pytest

from maker_boxes.services import common_api_client, whmcs_client
from maker_boxes.services.common_api_client import CommonApiNotConfigured, CommonApiUser
from maker_boxes.services.identity_resolver import resolve
from maker_boxes.services.whmcs_client import MemberLookup, WhmcsNotConfigured

ALL_CONFIGURED = dict(
    WHMCS_API_URL="https://billing.example.org/api.php",
    WHMCS_API_IDENTIFIER="ident",
    WHMCS_API_SECRET="secret",
    WHMCS_API_ACCESSKEY="",
    COMMON_API_PROXY_URL="http://pi-proxy.local/resolve",
    COMMON_API_PROXY_TOKEN="shh",
)


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def _whmcs_hit(username="ada", *, expires_offset_days=30):
    expires = timezone.now() + timedelta(days=expires_offset_days)
    return MemberLookup(
        status="valid",
        username=username,
        first_name="Ada",
        last_name="Lovelace",
        email="ada@whmcs.example.org",
        expires_at=expires,
        days_remaining=expires_offset_days,
    )


def _common_hit(username="ada"):
    return CommonApiUser(
        username=username,
        full_name="Ada Lovelace",
        email="ada@ad.example.org",
        groups=["Members", "Vetted"],
    )


# ---------------------------------------------------------------------------
# Digit input → badge path
# ---------------------------------------------------------------------------


@override_settings(**ALL_CONFIGURED)
def test_digit_input_calls_common_api_first_then_whmcs():
    with (
        patch.object(
            common_api_client, "lookup_by_rfid", return_value=_common_hit()
        ) as common_mock,
        patch.object(whmcs_client, "lookup_member", return_value=_whmcs_hit()) as whmcs_mock,
    ):
        result, source = resolve("12345678", use_cache=False)

    assert source == "common_api"
    assert result is not None
    assert result.username == "ada"
    # WHMCS layered on top: status + expiry come from WHMCS.
    assert result.status == "valid"
    assert result.days_remaining is not None and result.days_remaining > 20
    # First/last/email come from WHMCS when both backends agree.
    assert result.first_name == "Ada"
    assert result.last_name == "Lovelace"
    assert result.email == "ada@whmcs.example.org"
    common_mock.assert_called_once_with("12345678", use_cache=False)
    whmcs_mock.assert_called_once_with("ada", use_cache=False)


@override_settings(**ALL_CONFIGURED)
def test_digit_input_common_hit_whmcs_miss_returns_addon_record():
    """Add-on user case: AD knows them, WHMCS doesn't."""
    with (
        patch.object(common_api_client, "lookup_by_rfid", return_value=_common_hit()),
        patch.object(whmcs_client, "lookup_member", return_value=None),
    ):
        result, source = resolve("12345678", use_cache=False)

    assert source == "common_api"
    assert result is not None
    assert result.username == "ada"
    # No WHMCS billing — status is ``valid`` (AD says they exist) but
    # expiry stays None so the UI can show "no billing tie".
    assert result.status == "valid"
    assert result.expires_at is None
    assert result.first_name == "Ada"
    assert result.email == "ada@ad.example.org"


@override_settings(**ALL_CONFIGURED)
def test_digit_input_common_miss_returns_none():
    """Badge that AD doesn't know → unknown, regardless of WHMCS."""
    with (
        patch.object(common_api_client, "lookup_by_rfid", return_value=None),
        patch.object(whmcs_client, "lookup_member") as whmcs_mock,
    ):
        result, source = resolve("99999999", use_cache=False)

    assert result is None
    assert source == ""
    # We should not have asked WHMCS — badges aren't WHMCS usernames.
    whmcs_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Word input → username path
# ---------------------------------------------------------------------------


@override_settings(**ALL_CONFIGURED)
def test_word_input_calls_whmcs_only():
    with (
        patch.object(whmcs_client, "lookup_member", return_value=_whmcs_hit("ada")) as whmcs_mock,
        patch.object(common_api_client, "lookup_by_rfid") as common_mock,
    ):
        result, source = resolve("ada", use_cache=False)

    assert source == "whmcs"
    assert result is not None
    assert result.username == "ada"
    whmcs_mock.assert_called_once_with("ada", use_cache=False)
    common_mock.assert_not_called()


@override_settings(**ALL_CONFIGURED)
def test_word_input_whmcs_miss_returns_none():
    with patch.object(whmcs_client, "lookup_member", return_value=None):
        result, source = resolve("nobody", use_cache=False)
    assert result is None
    assert source == ""


# ---------------------------------------------------------------------------
# Degraded configurations
# ---------------------------------------------------------------------------


@override_settings(
    WHMCS_API_URL="https://billing.example.org/api.php",
    WHMCS_API_IDENTIFIER="ident",
    WHMCS_API_SECRET="secret",
    WHMCS_API_ACCESSKEY="",
    COMMON_API_PROXY_URL="",
    COMMON_API_PROXY_TOKEN="",
)
def test_common_api_unconfigured_degrades_for_badge():
    """Badge input + no Common API → cannot resolve, but no crash."""
    with (
        patch.object(
            common_api_client,
            "lookup_by_rfid",
            side_effect=CommonApiNotConfigured("nope"),
        ),
        patch.object(whmcs_client, "lookup_member") as whmcs_mock,
    ):
        result, source = resolve("12345678", use_cache=False)

    assert result is None
    assert source == ""
    whmcs_mock.assert_not_called()


@override_settings(
    WHMCS_API_URL="",
    WHMCS_API_IDENTIFIER="",
    WHMCS_API_SECRET="",
    COMMON_API_PROXY_URL="http://pi-proxy.local/resolve",
    COMMON_API_PROXY_TOKEN="shh",
)
def test_whmcs_unconfigured_still_returns_common_api_record_for_badge():
    """Badge input + Common hit + WHMCS unconfigured → still resolves."""
    with (
        patch.object(common_api_client, "lookup_by_rfid", return_value=_common_hit()),
        patch.object(whmcs_client, "lookup_member", side_effect=WhmcsNotConfigured("nope")),
    ):
        result, source = resolve("12345678", use_cache=False)

    assert source == "common_api"
    assert result is not None
    assert result.username == "ada"
    assert result.expires_at is None


@override_settings(
    WHMCS_API_URL="",
    WHMCS_API_IDENTIFIER="",
    WHMCS_API_SECRET="",
    COMMON_API_PROXY_URL="http://pi-proxy.local/resolve",
    COMMON_API_PROXY_TOKEN="shh",
)
def test_whmcs_unconfigured_returns_none_for_username_input():
    with patch.object(whmcs_client, "lookup_member", side_effect=WhmcsNotConfigured("nope")):
        result, source = resolve("ada", use_cache=False)
    assert result is None
    assert source == ""


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


@override_settings(**ALL_CONFIGURED)
def test_empty_query_returns_none():
    result, source = resolve("", use_cache=False)
    assert result is None
    assert source == ""


@override_settings(**ALL_CONFIGURED)
def test_short_digit_string_treated_as_username():
    # 3 digits is below the badge-length threshold; should NOT go to
    # Common API. (Avoids confusing a typo'd '42' with a badge.)
    with (
        patch.object(whmcs_client, "lookup_member", return_value=_whmcs_hit("42")) as whmcs_mock,
        patch.object(common_api_client, "lookup_by_rfid") as common_mock,
    ):
        result, source = resolve("42", use_cache=False)

    assert source == "whmcs"
    assert result is not None
    common_mock.assert_not_called()
    whmcs_mock.assert_called_once()
