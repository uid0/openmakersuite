"""Tests for the CredentialPlaceholderCheck (gh-712 of #455)."""

from __future__ import annotations

import pytest

from config.validators import Issue
from config.validators.credentials import KNOWN_CREDENTIALS, CredentialPlaceholderCheck


class _Bag:
    """Minimal stand-in for settings; only attributes the check reads matter."""

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def _issues(**settings_kw) -> list[Issue]:
    return list(CredentialPlaceholderCheck().run(_Bag(**settings_kw)))


class TestEmptyValues:
    def test_all_empty_passes(self):
        # Empty is acceptable — the dependent feature surfaces an
        # unconfigured state at runtime (e.g. ForgeKey provisioning
        # returns 401 server_unconfigured).
        bag = {name: "" for name in KNOWN_CREDENTIALS}
        assert _issues(**bag) == []

    def test_settings_missing_attr_passes(self):
        # Production deploys may not even set some keys (the
        # `config()` default kicks in). Same effect as empty.
        assert _issues() == []


class TestPlaceholderDetection:
    @pytest.mark.parametrize(
        "credential_name",
        [
            "SENTRY_DSN",
            "FORGEKEY_PROVISIONING_TOKEN",
            "FORGEKEY_SHARED_SECRET",
            "POSTMARK_SERVER_TOKEN",
            "EMQX_API_KEY",
            "EMQX_API_SECRET",
            "POSTMARK_INBOUND_TOKEN",
            "LOCATION_PING_TOKEN",
            "FORGEKEY_WEBHOOK_SECRET",
            "FORGEKEY_CA_KEY_ENCRYPTION_KEY",
            "WHMCS_API_SECRET",
        ],
    )
    @pytest.mark.parametrize(
        "value",
        [
            "change-me-in-production",
            "changeme-now",
            "placeholder-value",
            "your-secret-here",
            "REPLACE-ME-IN-PROD",
            "django-insecure-still-default",
        ],
    )
    def test_placeholder_value_fails(self, credential_name, value):
        issues = _issues(**{credential_name: value})
        assert any(
            i.key == credential_name and "placeholder" in i.reason.lower() for i in issues
        ), [(i.key, i.reason) for i in issues]

    def test_real_value_passes(self):
        # A real-looking token: random, no fragment match, mixed
        # case. None of the documented PLACEHOLDER_FRAGMENTS appear
        # so this should not trip any check.
        bag = {name: "Z9k4mPq1WnXr7bLs2Yv8Hg5Tj0AcFd6E" for name in KNOWN_CREDENTIALS}
        assert _issues(**bag) == []

    def test_pem_value_passes(self):
        # FORGEKEY_JWT_SIGNING_KEY ships as a PEM block — no
        # PLACEHOLDER_FRAGMENTS appears in a real key.
        pem = (
            "-----BEGIN EC PRIVATE KEY-----\n"
            "MHcCAQEEIPL3qZ6mYJ4kFn3lQwGTHFq3yJh3K1nPwRMVxYBjFmuPoAoGCCqGSM49\n"
            "AwEHoUQDQgAEnRkY4u7P2T0K0aXNqXf3R5R9XwGmFqK7v8j8NfHd7nLM6QnGfGvR\n"
            "0w4eJzVy8Bv5Q4WiRtZmL3rPyAB+kk5JaQ==\n"
            "-----END EC PRIVATE KEY-----"
        )
        assert _issues(FORGEKEY_JWT_SIGNING_KEY=pem, FORGEKEY_FIRMWARE_SIGNING_KEY=pem) == []


class TestReportingShape:
    def test_one_issue_per_setting_even_with_multiple_fragments(self):
        # "change-me-placeholder" contains both 'change-me' AND 'placeholder'.
        # We only want one Issue per setting — surface the first match and
        # stop, so the report stays readable.
        issues = _issues(SENTRY_DSN="change-me-placeholder")
        sentry_issues = [i for i in issues if i.key == "SENTRY_DSN"]
        assert len(sentry_issues) == 1

    def test_multiple_settings_each_get_their_own_issue(self):
        # Two different placeholdered settings should produce two
        # separate Issues so the operator sees both in one run.
        issues = _issues(
            SENTRY_DSN="placeholder-dsn",
            FORGEKEY_SHARED_SECRET="change-me-in-production",
        )
        keys = {i.key for i in issues}
        assert "SENTRY_DSN" in keys
        assert "FORGEKEY_SHARED_SECRET" in keys

    def test_secret_value_never_in_reason(self):
        # The reason may name the matched FRAGMENT (public) but
        # never the operator's full chosen string.
        marker = "totally-unique-marker-string"
        leaky = f"change-me-{marker}-padding"
        issues = _issues(SENTRY_DSN=leaky)
        assert all(marker not in i.reason for i in issues), [i.reason for i in issues]
