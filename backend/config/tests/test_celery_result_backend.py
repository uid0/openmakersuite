"""Tests for the redacting Celery result backend (gh #378).

The backend wraps `django_celery_results.backends.database.DatabaseBackend`
so traceback strings and result payloads are scrubbed by
`config.observability_redaction.redact` before they hit `TaskResult`.

These tests exercise the redaction layer directly via
`_redact_traceback` / `_redact_result` so they don't depend on a live
broker or DB write — the parent backend's storage path is already
covered by django_celery_results' own tests.
"""

import pytest

from config.celery_result_backend import _redact_result, _redact_traceback


@pytest.mark.unit
class TestRedactTraceback:
    def test_none_passes_through(self):
        assert _redact_traceback(None) is None

    def test_empty_string_passes_through(self):
        assert _redact_traceback("") == ""

    def test_bearer_token_in_traceback_is_scrubbed(self):
        tb = (
            "Traceback (most recent call last):\n"
            '  File "tasks.py", line 42, in deliver\n'
            "requests.exceptions.HTTPError: 401: Bearer eyJabc.payload.sig"
        )
        scrubbed = _redact_traceback(tb)
        assert "eyJabc.payload.sig" not in scrubbed
        assert "Bearer eyJ" not in scrubbed
        assert "***REDACTED***" in scrubbed
        assert "Traceback" in scrubbed

    def test_pem_block_in_traceback_is_scrubbed(self):
        tb = (
            "ValueError: bad key:\n"
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEowIBAAKCAQEA...\n"
            "-----END RSA PRIVATE KEY-----"
        )
        scrubbed = _redact_traceback(tb)
        assert "BEGIN RSA PRIVATE KEY" not in scrubbed
        assert "***REDACTED***" in scrubbed

    def test_non_string_traceback_is_returned_as_is(self):
        # Some backends pass a non-string (e.g. a TraceBack object).
        # We don't try to redact those — the parent backend will
        # serialize them.
        sentinel = object()
        assert _redact_traceback(sentinel) is sentinel


@pytest.mark.unit
class TestRedactResult:
    def test_none_passes_through(self):
        assert _redact_result(None) is None

    def test_dict_with_sensitive_key_is_scrubbed(self):
        scrubbed = _redact_result({"webhook_id": 1, "token": "abc123"})
        assert scrubbed["webhook_id"] == 1
        assert scrubbed["token"] == "***REDACTED***"

    def test_list_with_secret_strings_is_scrubbed(self):
        scrubbed = _redact_result(["ok", "Bearer eyJabc.payload.sig"])
        assert scrubbed[0] == "ok"
        assert "eyJabc.payload.sig" not in scrubbed[1]

    def test_failure_dict_from_prepare_exception_is_scrubbed(self):
        # Backend.store_result calls prepare_exception(exc) for failed tasks
        # before _store_result sees the result. The shape it produces:
        prepared = {
            "exc_type": "RequestException",
            "exc_message": (
                "401 for url: https://hooks.example.com/?token="
                "abcdef0123456789ABCDEFabcdef0123456789ABCDEF"
            ),
            "exc_module": "requests.exceptions",
        }
        scrubbed = _redact_result(prepared)
        assert (
            "abcdef0123456789ABCDEFabcdef0123456789ABCDEF"
            not in scrubbed["exc_message"]
        )
        assert "401 for url" in scrubbed["exc_message"]
        assert scrubbed["exc_type"] == "RequestException"

    def test_primitive_passes_through(self):
        assert _redact_result(42) == 42
        assert _redact_result(True) is True
