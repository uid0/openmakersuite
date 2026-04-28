"""Tests for ForgeKey deployment-hygiene system checks added in oms-f9z."""

from __future__ import annotations

import pytest

from forgekey.checks import (
    W_EMQX_API,
    W_EMQX_PASSWORD,
    W_PROVISIONING_TOKEN,
    check_emqx_api_credentials,
    check_emqx_dashboard_password,
    check_forgekey_provisioning_token,
)


@pytest.fixture
def prod(settings):
    settings.DEBUG = False
    return settings


class TestProvisioningTokenCheck:
    def test_no_warnings_in_debug(self, settings):
        settings.DEBUG = True
        settings.FORGEKEY_PROVISIONING_TOKEN = ""
        assert check_forgekey_provisioning_token(app_configs=None) == []

    def test_warns_when_unset(self, prod):
        prod.FORGEKEY_PROVISIONING_TOKEN = ""
        warnings = check_forgekey_provisioning_token(app_configs=None)
        assert [w.id for w in warnings] == [W_PROVISIONING_TOKEN]

    def test_warns_on_placeholder(self, prod):
        prod.FORGEKEY_PROVISIONING_TOKEN = "REPLACE_ME_PROVISIONING_TOKEN"
        warnings = check_forgekey_provisioning_token(app_configs=None)
        assert [w.id for w in warnings] == [W_PROVISIONING_TOKEN]

    def test_no_warning_on_real_value(self, prod):
        prod.FORGEKEY_PROVISIONING_TOKEN = "actual-secret-token-xyz"
        assert check_forgekey_provisioning_token(app_configs=None) == []


class TestEmqxDashboardPasswordCheck:
    def test_no_warnings_in_debug(self, settings):
        settings.DEBUG = True
        settings.EMQX_DASHBOARD_PASSWORD = ""
        assert check_emqx_dashboard_password(app_configs=None) == []

    def test_warns_when_unset(self, prod):
        prod.EMQX_DASHBOARD_PASSWORD = ""
        warnings = check_emqx_dashboard_password(app_configs=None)
        assert [w.id for w in warnings] == [W_EMQX_PASSWORD]

    def test_warns_on_placeholder(self, prod):
        prod.EMQX_DASHBOARD_PASSWORD = "change-me-on-first-deploy"
        warnings = check_emqx_dashboard_password(app_configs=None)
        assert [w.id for w in warnings] == [W_EMQX_PASSWORD]

    def test_warns_on_too_short(self, prod):
        prod.EMQX_DASHBOARD_PASSWORD = "Ab1c"
        warnings = check_emqx_dashboard_password(app_configs=None)
        assert [w.id for w in warnings] == [W_EMQX_PASSWORD]

    def test_warns_when_no_uppercase(self, prod):
        prod.EMQX_DASHBOARD_PASSWORD = "lowercase1ish"
        warnings = check_emqx_dashboard_password(app_configs=None)
        assert [w.id for w in warnings] == [W_EMQX_PASSWORD]

    def test_warns_when_no_digit(self, prod):
        prod.EMQX_DASHBOARD_PASSWORD = "MixedCaseNoDigit"
        warnings = check_emqx_dashboard_password(app_configs=None)
        assert [w.id for w in warnings] == [W_EMQX_PASSWORD]

    def test_no_warning_on_complex_value(self, prod):
        prod.EMQX_DASHBOARD_PASSWORD = "GoodPass1word"
        assert check_emqx_dashboard_password(app_configs=None) == []


class TestEmqxApiCredentialsCheck:
    def test_no_warnings_in_debug(self, settings):
        settings.DEBUG = True
        settings.EMQX_API_KEY = ""
        settings.EMQX_API_SECRET = ""
        assert check_emqx_api_credentials(app_configs=None) == []

    def test_warns_when_key_missing(self, prod):
        prod.EMQX_API_KEY = ""
        prod.EMQX_API_SECRET = "something"
        warnings = check_emqx_api_credentials(app_configs=None)
        assert [w.id for w in warnings] == [W_EMQX_API]

    def test_warns_when_secret_missing(self, prod):
        prod.EMQX_API_KEY = "something"
        prod.EMQX_API_SECRET = ""
        warnings = check_emqx_api_credentials(app_configs=None)
        assert [w.id for w in warnings] == [W_EMQX_API]

    def test_no_warning_when_both_set(self, prod):
        prod.EMQX_API_KEY = "key"
        prod.EMQX_API_SECRET = "secret"
        assert check_emqx_api_credentials(app_configs=None) == []
