"""Tests for the CookiesCSRFCORSCheck (gh-711 of #455)."""

from __future__ import annotations

import pytest

from config.validators import Issue
from config.validators.cookies import CookiesCSRFCORSCheck


class _Bag:
    """Minimal stand-in for settings, same pattern as the gh-710 tests."""

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


# A baseline that passes every cookie/CSRF/CORS rule. Tests flip ONE
# field at a time to exercise each rule in isolation.
_GOOD = dict(
    SESSION_COOKIE_SECURE=True,
    CSRF_COOKIE_SECURE=True,
    SESSION_COOKIE_SAMESITE="Lax",
    CSRF_COOKIE_SAMESITE="Lax",
    SECURE_SSL_REDIRECT=True,
    SECURE_HSTS_SECONDS=3600,
    CORS_ALLOWED_ORIGINS=["https://oms.example.com"],
    CSRF_TRUSTED_ORIGINS=["https://oms.example.com"],
)


def _issues(**overrides) -> list[Issue]:
    settings = _Bag(**{**_GOOD, **overrides})
    return list(CookiesCSRFCORSCheck().run(settings))


class TestSessionCookieSecure:
    @pytest.mark.parametrize("falsy", [False, 0, None, ""])
    def test_non_truthy_fails(self, falsy):
        issues = _issues(SESSION_COOKIE_SECURE=falsy)
        assert any(i.key == "SESSION_COOKIE_SECURE" for i in issues)

    def test_true_passes(self):
        issues = _issues(SESSION_COOKIE_SECURE=True)
        assert not any(i.key == "SESSION_COOKIE_SECURE" for i in issues)


class TestCsrfCookieSecure:
    def test_false_fails(self):
        issues = _issues(CSRF_COOKIE_SECURE=False)
        assert any(i.key == "CSRF_COOKIE_SECURE" for i in issues)


class TestSessionCookieSamesite:
    @pytest.mark.parametrize("bad", ["None", "none", "", None, "lax", "STRICT"])
    def test_non_safe_fails(self, bad):
        # 'lax' / 'STRICT' fail because Django's matcher is case-sensitive
        # — we want operators to use the canonical capitalization.
        issues = _issues(SESSION_COOKIE_SAMESITE=bad)
        assert any(i.key == "SESSION_COOKIE_SAMESITE" for i in issues)

    @pytest.mark.parametrize("good", ["Lax", "Strict"])
    def test_safe_passes(self, good):
        issues = _issues(SESSION_COOKIE_SAMESITE=good)
        assert not any(i.key == "SESSION_COOKIE_SAMESITE" for i in issues)


class TestCsrfCookieSamesite:
    def test_none_fails(self):
        issues = _issues(CSRF_COOKIE_SAMESITE="None")
        assert any(i.key == "CSRF_COOKIE_SAMESITE" for i in issues)


class TestSslRedirect:
    def test_false_fails(self):
        issues = _issues(SECURE_SSL_REDIRECT=False)
        assert any(i.key == "SECURE_SSL_REDIRECT" for i in issues)


class TestHsts:
    @pytest.mark.parametrize("bad", [0, "0", -1, None, "", "not-a-number"])
    def test_non_positive_fails(self, bad):
        issues = _issues(SECURE_HSTS_SECONDS=bad)
        assert any(i.key == "SECURE_HSTS_SECONDS" for i in issues)

    @pytest.mark.parametrize("good", [3600, "3600", 31536000])
    def test_positive_passes(self, good):
        issues = _issues(SECURE_HSTS_SECONDS=good)
        assert not any(i.key == "SECURE_HSTS_SECONDS" for i in issues)


class TestWildcardOrigins:
    def test_cors_wildcard_fails(self):
        issues = _issues(CORS_ALLOWED_ORIGINS=["*"])
        assert any(i.key == "CORS_ALLOWED_ORIGINS" for i in issues)

    def test_csrf_trusted_wildcard_fails(self):
        issues = _issues(CSRF_TRUSTED_ORIGINS=["https://oms.example.com", "*"])
        assert any(i.key == "CSRF_TRUSTED_ORIGINS" for i in issues)

    def test_real_origins_pass(self):
        issues = _issues(
            CORS_ALLOWED_ORIGINS=["https://oms.example.com"],
            CSRF_TRUSTED_ORIGINS=["https://oms.example.com"],
        )
        assert not any(i.key in ("CORS_ALLOWED_ORIGINS", "CSRF_TRUSTED_ORIGINS") for i in issues)

    def test_string_value_normalized(self):
        # Some operators set CSRF_TRUSTED_ORIGINS to a bare string when
        # there's only one origin. The check should still catch '*'.
        issues = _issues(CSRF_TRUSTED_ORIGINS="*")
        assert any(i.key == "CSRF_TRUSTED_ORIGINS" for i in issues)


class TestGoodBaseline:
    def test_full_good_baseline_passes(self):
        assert _issues() == []
