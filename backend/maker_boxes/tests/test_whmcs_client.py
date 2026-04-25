"""Tests for the WHMCS client service.

Covers AC2: lookup returns valid / expired / grace / unknown classifications,
and gracefully degrades when configuration is missing.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

import pytest

from maker_boxes.services import whmcs_client
from maker_boxes.services.whmcs_client import WhmcsNotConfigured, lookup_member

CONFIGURED_SETTINGS = dict(
    WHMCS_API_URL="https://billing.example.org/api.php",
    WHMCS_API_IDENTIFIER="ident",
    WHMCS_API_SECRET="secret",
    WHMCS_API_ACCESSKEY="",
)


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def _whmcs_response(*, expires_offset_days: int, status: str = "Active", **extra):
    expires = timezone.now() + timedelta(days=expires_offset_days)
    body = {
        "result": "success",
        "firstname": "Ada",
        "lastname": "Lovelace",
        "email": "ada@example.org",
        "status": status,
        "nextduedate": expires.strftime("%Y-%m-%d"),
    }
    body.update(extra)
    return body


@override_settings(**CONFIGURED_SETTINGS)
def test_lookup_member_active_returns_valid():
    response = _whmcs_response(expires_offset_days=30)
    with patch.object(whmcs_client, "_request", return_value=response):
        result = lookup_member("ada", use_cache=False)

    assert result is not None
    assert result.status == "valid"
    assert result.first_name == "Ada"
    assert result.last_name == "Lovelace"
    assert result.email == "ada@example.org"
    assert result.expires_at is not None
    assert result.days_remaining is not None and result.days_remaining > 20


@override_settings(**CONFIGURED_SETTINGS)
def test_lookup_member_expired_returns_expired():
    response = _whmcs_response(expires_offset_days=-30, status="Inactive")
    with patch.object(whmcs_client, "_request", return_value=response):
        result = lookup_member("ada", use_cache=False)

    assert result is not None
    assert result.status == "expired"


@override_settings(**CONFIGURED_SETTINGS)
def test_lookup_member_in_grace_period():
    response = _whmcs_response(expires_offset_days=-3, status="Inactive")
    with patch.object(whmcs_client, "_request", return_value=response):
        result = lookup_member("ada", use_cache=False)

    assert result is not None
    assert result.status == "grace"
    assert result.days_remaining is not None
    # 7-day window, used 3 days, so ~4 days left in grace
    assert 0 <= result.days_remaining <= 4


@override_settings(**CONFIGURED_SETTINGS)
def test_lookup_member_not_found_returns_none():
    response = {"result": "error", "message": "Client Not Found"}
    with patch.object(whmcs_client, "_request", return_value=response):
        result = lookup_member("nobody", use_cache=False)
    assert result is None


@override_settings(WHMCS_API_URL="", WHMCS_API_IDENTIFIER="", WHMCS_API_SECRET="")
def test_lookup_member_raises_when_unconfigured():
    with pytest.raises(WhmcsNotConfigured):
        lookup_member("ada", use_cache=False)


@override_settings(**CONFIGURED_SETTINGS)
def test_lookup_member_caches_results():
    response = _whmcs_response(expires_offset_days=30)
    with patch.object(whmcs_client, "_request", return_value=response) as mock:
        lookup_member("ada")
        lookup_member("ada")
        assert mock.call_count == 1
