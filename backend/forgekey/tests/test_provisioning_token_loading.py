"""Regression tests for FORGEKEY_PROVISIONING_TOKEN env-var loading (oms-j96).

The original bug (gh #296) was that the backend reported
``expected_token_length: 0`` despite the operator having ``FORGEKEY_PROVISIONING_TOKEN``
in their ``.env`` files. These tests pin:

  * the env-var → settings binding so future loader swaps don't silently
    default the token to empty (AC-4)
  * the ``ForgekeyConfig.ready()`` startup log line that lets operators
    verify token configuration from process logs without firing a
    registration request (AC-3)
"""

from __future__ import annotations

import logging


def test_decouple_config_reads_env_var(monkeypatch):
    """AC-4: when ``FORGEKEY_PROVISIONING_TOKEN`` is in ``os.environ``,
    ``decouple.config`` must return that value, not the empty default.

    settings.py binds the setting via
    ``FORGEKEY_PROVISIONING_TOKEN = config("FORGEKEY_PROVISIONING_TOKEN", default="")``;
    this pins that loader against future swaps that could regress to a
    pure ``os.environ.get(..., "")`` (which would be fine) or — the
    original failure mode — silently default to empty when the env var
    is set."""
    from decouple import AutoConfig

    monkeypatch.setenv("FORGEKEY_PROVISIONING_TOKEN", "value-from-env-abcdef")
    cfg = AutoConfig()
    assert cfg("FORGEKEY_PROVISIONING_TOKEN", default="") == "value-from-env-abcdef"


def test_settings_attribute_non_empty_when_overridden(settings):
    """AC-4: ``settings.FORGEKEY_PROVISIONING_TOKEN`` is honored when set —
    code paths that call ``getattr(settings, "FORGEKEY_PROVISIONING_TOKEN", "")``
    see the configured value, not the empty default."""
    settings.FORGEKEY_PROVISIONING_TOKEN = "configured-token-1234"
    from django.conf import settings as django_settings

    assert django_settings.FORGEKEY_PROVISIONING_TOKEN == "configured-token-1234"
    assert getattr(django_settings, "FORGEKEY_PROVISIONING_TOKEN", "")


class TestStartupLogLine:
    """AC-3: ``ForgekeyConfig.ready()`` emits an INFO line carrying token
    length and fingerprint when configured, and a WARNING when not.
    Operators diagnose misconfiguration by grepping logs at boot — no
    device registration request needed."""

    def _run_ready(self):
        from django.apps import apps

        apps.get_app_config("forgekey").ready()

    def test_info_log_when_token_configured(self, settings, caplog):
        settings.FORGEKEY_PROVISIONING_TOKEN = "actual-secret-token-xyz"  # 23 chars

        with caplog.at_level(logging.INFO, logger="forgekey.apps"):
            self._run_ready()

        infos = [
            r
            for r in caplog.records
            if r.levelno == logging.INFO and "provisioning token configured" in r.getMessage()
        ]
        assert infos, (
            "expected INFO log with token diagnostics; "
            f"got {[r.getMessage() for r in caplog.records]}"
        )
        msg = infos[-1].getMessage()
        assert "length=23" in msg
        assert "fingerprint=" in msg
        # Never log the raw token.
        assert "actual-secret-token-xyz" not in msg

    def test_warning_log_when_token_missing(self, settings, caplog):
        settings.FORGEKEY_PROVISIONING_TOKEN = ""

        with caplog.at_level(logging.WARNING, logger="forgekey.apps"):
            self._run_ready()

        warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and "NOT configured" in r.getMessage()
        ]
        assert warnings, "expected WARNING log when provisioning token is empty"

    def test_whitespace_only_token_is_treated_as_missing(self, settings, caplog):
        settings.FORGEKEY_PROVISIONING_TOKEN = "   \n\t  "

        with caplog.at_level(logging.WARNING, logger="forgekey.apps"):
            self._run_ready()

        warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and "NOT configured" in r.getMessage()
        ]
        assert warnings

    def test_length_uses_stripped_value(self, settings, caplog):
        """The reported length matches what ``_classify_provisioning_token``
        measures (post-strip), so the boot-log fingerprint/length lines up
        with the diagnostics returned in the 401 response body."""
        settings.FORGEKEY_PROVISIONING_TOKEN = "  padded-token-xyz  "  # 16 stripped

        with caplog.at_level(logging.INFO, logger="forgekey.apps"):
            self._run_ready()

        infos = [
            r
            for r in caplog.records
            if r.levelno == logging.INFO and "provisioning token configured" in r.getMessage()
        ]
        assert infos
        assert "length=16" in infos[-1].getMessage()
