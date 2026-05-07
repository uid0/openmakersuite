"""Unit tests for observability redaction and error-envelope scrubbing."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory

from config.api_errors import standardized_exception_handler
from config.observability_redaction import REDACTED, is_sensitive_key, redact

pytestmark = pytest.mark.unit


def _drf_context():
    request = APIRequestFactory().post("/api/_test/redaction/", {}, format="json")
    return {
        "view": SimpleNamespace(),
        "request": request,
        "args": (),
        "kwargs": {},
    }


def test_redacts_token_key():
    assert redact({"token": "secret-value"}) == {"token": REDACTED}


def test_redacts_secret_key():
    assert redact({"client_secret": "secret-value"}) == {"client_secret": REDACTED}


@pytest.mark.parametrize("key", ["password", "passwd", "pwd"])
def test_redacts_password_key_variants(key):
    assert redact({key: "secret-value"}) == {key: REDACTED}


@pytest.mark.parametrize("key", ["api_key", "api-key", "apikey", "apiKey", "API_KEY"])
def test_redacts_api_key_variants(key):
    # The regex ``api[_-]?key`` runs with re.IGNORECASE, so camelCase
    # and SHOUTING_CASE both match. Default-deny on key names — every
    # spelling of "api key" lands as REDACTED.
    assert is_sensitive_key(key) is True
    assert redact({key: "secret-value"}) == {key: REDACTED}


def test_redacts_signature_key():
    assert redact({"signature": "abc123"}) == {"signature": REDACTED}


def test_redacts_private_key_pem_field():
    pem = "-----BEGIN PRIVATE KEY-----\nabc123\n-----END PRIVATE KEY-----"
    assert redact({"private_key": pem}) == {"private_key": REDACTED}


@pytest.mark.parametrize("key", ["Authorization", "X-Authorization"])
def test_redacts_authorization_header_key(key):
    assert redact({key: "Bearer should-not-survive"}) == {key: REDACTED}


@pytest.mark.parametrize("key", ["Cookie", "Set-Cookie"])
def test_redacts_cookie_keys(key):
    assert redact({key: "sessionid=abc"}) == {key: REDACTED}


def test_does_not_redact_innocuous_keys():
    payload = {
        "name": "widget",
        "count": 3,
        "description": "safe text",
        "item_id": "abc-123",
    }

    assert redact(payload) == payload


def test_scrubs_bearer_token_in_string():
    value = "Bearer abcdefghijklmnopqrstuvwxyz1234567890"

    assert redact(value) == REDACTED


def test_scrubs_jwt_in_string():
    jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJl"
    value = f"Authorization failed for token {jwt} during webhook delivery."

    assert redact(value) == (
        "Authorization failed for token " f"{REDACTED} during webhook delivery."
    )


def test_scrubs_pem_private_key_block():
    pem = (
        "prefix\n"
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "supersecretmaterial\n"
        "-----END RSA PRIVATE KEY-----\n"
        "suffix"
    )

    assert redact(pem) == f"prefix\n{REDACTED}\nsuffix"


def test_scrubs_high_entropy_token():
    token = "0123456789abcdef0123456789abcdef01234567"
    value = f"received token={token} from upstream"

    assert redact(value) == f"received token={REDACTED} from upstream"
    assert redact("received token=hello from upstream") == "received token=hello from upstream"


def test_redacts_nested_dict():
    payload = {"outer": {"inner": {"token": "secret-value"}}}

    assert redact(payload) == {"outer": {"inner": {"token": REDACTED}}}


def test_redacts_dict_in_list():
    payload = [{"token": "one"}, {"token": "two"}]

    assert redact(payload) == [{"token": REDACTED}, {"token": REDACTED}]


def test_redacts_tuple_preserves_type():
    payload = ("safe", {"token": "secret-value"})

    result = redact(payload)

    assert isinstance(result, tuple)
    assert result == ("safe", {"token": REDACTED})


def test_redacts_list_preserves_type():
    payload = ["safe", {"token": "secret-value"}]

    result = redact(payload)

    assert isinstance(result, list)
    assert result == ["safe", {"token": REDACTED}]


def test_redact_does_not_mutate_input():
    payload = {
        "message": "Bearer abcdefghijklmnopqrstuvwxyz1234567890",
        "nested": {"token": "secret-value"},
        "items": [{"secret": "top-secret"}],
    }
    original = deepcopy(payload)

    result = redact(payload)

    assert payload == original
    assert result == {
        "message": REDACTED,
        "nested": {"token": REDACTED},
        "items": [{"secret": REDACTED}],
    }


def test_exception_handler_redacts_password_in_validation_details():
    exc = ValidationError({"password": ["This field may not be blank."]})

    response = standardized_exception_handler(exc, _drf_context())

    assert response is not None
    assert response.status_code == 400
    assert response.data["error"]["details"]["password"] == REDACTED


def test_exception_handler_preserves_innocuous_validation_details():
    exc = ValidationError({"username": ["This field may not be blank."]})

    response = standardized_exception_handler(exc, _drf_context())

    assert response is not None
    assert response.status_code == 400
    assert response.data["error"]["details"] == {"username": ["This field may not be blank."]}
