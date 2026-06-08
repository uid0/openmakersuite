"""Tests for ``manage.py validate_production`` and its category checks.

Each failure mode in the gh-710 acceptance lands here as its own case.
The "no secret values leak" contract is asserted by checking that the
output never includes a marker we plant in the setting.
"""

from __future__ import annotations

from io import StringIO

from django.core.management import call_command
from django.test import override_settings

import pytest

from config.validators import Issue
from config.validators.django_core import SECRET_KEY_MIN_LEN, DjangoCoreCheck

# A 64-char value that has none of the placeholder fragments. Used as
# the happy-path SECRET_KEY in tests so flipping a single field doesn't
# accidentally trip a different rule.
_SAFE_SECRET = "a1B2c3D4" * 8


# Settings shape that passes every registered category. Each happy-path
# command-level test layers this in via override_settings; growing the
# set of categories means appending here once, not editing every test.
_PROD_GOOD_SETTINGS = dict(
    DEBUG=False,
    SECRET_KEY=_SAFE_SECRET,
    ALLOWED_HOSTS=["oms.example.com"],
    SESSION_COOKIE_SECURE=True,
    CSRF_COOKIE_SECURE=True,
    SESSION_COOKIE_SAMESITE="Lax",
    CSRF_COOKIE_SAMESITE="Lax",
    SECURE_SSL_REDIRECT=True,
    SECURE_HSTS_SECONDS=3600,
    CORS_ALLOWED_ORIGINS=["https://oms.example.com"],
    CSRF_TRUSTED_ORIGINS=["https://oms.example.com"],
)


# ---------------------------------------------------------------------------
# DjangoCoreCheck unit tests — each rule in isolation
# ---------------------------------------------------------------------------


class _Bag:
    """Minimal stand-in for Django settings.

    Tests use this rather than @override_settings for the unit-test
    layer so we can drive ALLOWED_HOSTS / SECRET_KEY / DEBUG combos
    that would be illegal-at-import-time in real settings.
    """

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def _issues(**settings_kw) -> list[Issue]:
    return list(DjangoCoreCheck().run(_Bag(**settings_kw)))


class TestDebugRule:
    def test_debug_true_fails(self):
        issues = _issues(DEBUG=True, SECRET_KEY=_SAFE_SECRET, ALLOWED_HOSTS=["oms.example.com"])
        assert any(i.key == "DEBUG" and i.severity == "fail" for i in issues)

    def test_debug_false_passes(self):
        issues = _issues(DEBUG=False, SECRET_KEY=_SAFE_SECRET, ALLOWED_HOSTS=["oms.example.com"])
        assert not any(i.key == "DEBUG" for i in issues)


class TestSecretKeyRule:
    def test_empty_secret_key_fails(self):
        issues = _issues(DEBUG=False, SECRET_KEY="", ALLOWED_HOSTS=["oms.example.com"])
        assert any(i.key == "SECRET_KEY" and "empty" in i.reason.lower() for i in issues)

    @pytest.mark.parametrize(
        "secret",
        [
            "django-insecure-dev-key-change-in-production-aaaaaaaaaaaaaaa",
            "change-me-now-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "placeholder-value-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "your-secret-here-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "TEST-KEY-for-ci-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        ],
    )
    def test_placeholder_secret_key_fails(self, secret):
        issues = _issues(DEBUG=False, SECRET_KEY=secret, ALLOWED_HOSTS=["oms.example.com"])
        assert any(i.key == "SECRET_KEY" and "placeholder" in i.reason.lower() for i in issues), [
            (i.key, i.reason) for i in issues
        ]

    def test_short_secret_key_fails(self):
        short = "a" * (SECRET_KEY_MIN_LEN - 1)
        issues = _issues(DEBUG=False, SECRET_KEY=short, ALLOWED_HOSTS=["oms.example.com"])
        assert any(i.key == "SECRET_KEY" and "shorter than" in i.reason for i in issues)

    def test_safe_secret_passes(self):
        issues = _issues(DEBUG=False, SECRET_KEY=_SAFE_SECRET, ALLOWED_HOSTS=["oms.example.com"])
        assert not any(i.key == "SECRET_KEY" for i in issues)


class TestAllowedHostsRule:
    def test_empty_allowed_hosts_fails(self):
        issues = _issues(DEBUG=False, SECRET_KEY=_SAFE_SECRET, ALLOWED_HOSTS=[])
        assert any(i.key == "ALLOWED_HOSTS" and "empty" in i.reason.lower() for i in issues)

    def test_wildcard_allowed_hosts_fails(self):
        issues = _issues(
            DEBUG=False, SECRET_KEY=_SAFE_SECRET, ALLOWED_HOSTS=["*", "oms.example.com"]
        )
        assert any(i.key == "ALLOWED_HOSTS" and "*" in i.reason for i in issues)

    @pytest.mark.parametrize(
        "hosts",
        [
            ["localhost"],
            ["127.0.0.1"],
            ["localhost", "127.0.0.1"],
            ["0.0.0.0"],
            ["::1"],
        ],
    )
    def test_loopback_only_allowed_hosts_fails(self, hosts):
        issues = _issues(DEBUG=False, SECRET_KEY=_SAFE_SECRET, ALLOWED_HOSTS=hosts)
        assert any(i.key == "ALLOWED_HOSTS" and "loopback" in i.reason.lower() for i in issues)

    def test_real_host_passes(self):
        issues = _issues(
            DEBUG=False,
            SECRET_KEY=_SAFE_SECRET,
            ALLOWED_HOSTS=["oms.example.com", "localhost"],
        )
        assert not any(i.key == "ALLOWED_HOSTS" for i in issues)


# ---------------------------------------------------------------------------
# Management-command integration tests
# ---------------------------------------------------------------------------


class TestValidateProductionCommand:
    def test_debug_true_skips_without_strict(self):
        out, err = StringIO(), StringIO()
        with override_settings(DEBUG=True):
            call_command("validate_production", stdout=out, stderr=err)
        assert "skipping production checks" in out.getvalue()
        assert err.getvalue() == ""

    def test_debug_true_runs_under_strict_and_fails(self):
        out, err = StringIO(), StringIO()
        with pytest.raises(SystemExit) as exc_info:
            with override_settings(**{**_PROD_GOOD_SETTINGS, "DEBUG": True}):
                call_command("validate_production", "--strict", stdout=out, stderr=err)
        assert exc_info.value.code == 1
        # The fatal report goes to stderr.
        assert "django_core.DEBUG" in err.getvalue()
        assert "DEBUG=True" in err.getvalue()

    def test_well_formed_settings_pass(self):
        out, err = StringIO(), StringIO()
        with override_settings(**_PROD_GOOD_SETTINGS):
            call_command("validate_production", stdout=out, stderr=err)
        assert "all production safety checks passed" in out.getvalue()
        assert err.getvalue() == ""

    def test_quiet_suppresses_success_summary(self):
        out, err = StringIO(), StringIO()
        with override_settings(**_PROD_GOOD_SETTINGS):
            call_command("validate_production", "--quiet", stdout=out, stderr=err)
        assert out.getvalue() == ""

    def test_multiple_failures_all_reported(self):
        out, err = StringIO(), StringIO()
        # Plant DEBUG, placeholder secret, AND loopback hosts — all
        # three should be in the same report rather than the command
        # short-circuiting on the first.
        with pytest.raises(SystemExit) as exc_info:
            with override_settings(
                DEBUG=True,
                SECRET_KEY="x" * 30,  # short — fail
                ALLOWED_HOSTS=["localhost"],  # loopback-only — fail
            ):
                call_command("validate_production", "--strict", stdout=out, stderr=err)
        assert exc_info.value.code == 1
        report = err.getvalue()
        assert "django_core.DEBUG" in report
        assert "django_core.SECRET_KEY" in report
        assert "django_core.ALLOWED_HOSTS" in report

    def test_secret_value_never_leaked(self):
        out, err = StringIO(), StringIO()
        marker = "marker-secret-value-should-never-appear-in-output"
        # SECRET_KEY length is enough — only the placeholder fragment
        # check should trip, but we're really verifying the OUTPUT
        # never contains the literal value.
        leaky_value = "django-insecure-" + marker + "padding-padding-padding"
        with pytest.raises(SystemExit):
            with override_settings(**{**_PROD_GOOD_SETTINGS, "SECRET_KEY": leaky_value}):
                call_command("validate_production", stdout=out, stderr=err)
        combined = out.getvalue() + err.getvalue()
        assert marker not in combined, (
            "validate_production must not print the secret value, only the "
            "matched fragment + reason."
        )
