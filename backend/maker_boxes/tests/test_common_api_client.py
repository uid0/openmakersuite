"""Tests for the Common API badge → identity client (PR1)."""

from __future__ import annotations

from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings

import pytest

from maker_boxes.services import common_api_client
from maker_boxes.services.common_api_client import CommonApiNotConfigured, lookup_by_rfid

CONFIGURED = dict(
    COMMON_API_PROXY_URL="http://pi-proxy.local/resolve",
    COMMON_API_PROXY_TOKEN="shh",
)


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def _hit_response(**user_overrides):
    user = {
        "username": "ada",
        "fullName": "Ada Lovelace",
        "email": "ada@example.org",
        "groups": ["Members", "Vetted"],
    }
    user.update(user_overrides)
    return {"result": {"user": user}}


@override_settings(**CONFIGURED)
def test_lookup_by_rfid_returns_user_on_hit():
    with patch.object(common_api_client, "_request", return_value=_hit_response()):
        record = lookup_by_rfid("12345678", use_cache=False)

    assert record is not None
    assert record.username == "ada"
    assert record.full_name == "Ada Lovelace"
    assert record.email == "ada@example.org"
    assert record.groups == ["Members", "Vetted"]


@override_settings(**CONFIGURED)
def test_lookup_by_rfid_returns_none_on_miss():
    # Common API miss shape: result without user key.
    with patch.object(common_api_client, "_request", return_value={"result": {}}):
        assert lookup_by_rfid("99999999", use_cache=False) is None


@override_settings(**CONFIGURED)
def test_lookup_by_rfid_returns_none_on_malformed_payload():
    # Defensive: missing username can't be propagated to a label,
    # so we should refuse to construct a record.
    response = _hit_response(username="")
    with patch.object(common_api_client, "_request", return_value=response):
        assert lookup_by_rfid("12345678", use_cache=False) is None


@override_settings(COMMON_API_PROXY_URL="", COMMON_API_PROXY_TOKEN="")
def test_lookup_by_rfid_raises_when_unconfigured():
    with pytest.raises(CommonApiNotConfigured):
        lookup_by_rfid("12345678", use_cache=False)


@override_settings(**CONFIGURED)
def test_lookup_by_rfid_caches_results():
    with patch.object(common_api_client, "_request", return_value=_hit_response()) as mock:
        lookup_by_rfid("12345678")
        lookup_by_rfid("12345678")
        assert mock.call_count == 1


@override_settings(**CONFIGURED)
def test_lookup_by_rfid_empty_input_returns_none():
    # Should not even attempt the network call.
    with patch.object(common_api_client, "_request") as mock:
        assert lookup_by_rfid("", use_cache=False) is None
        assert mock.call_count == 0
