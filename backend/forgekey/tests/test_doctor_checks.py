"""Tests for ForgeKey deployment-hygiene system checks added in oms-f9z."""

from __future__ import annotations

from unittest import mock

import pytest

from forgekey.checks import (
    W_BACKEND_LITERAL_MQTT_PASSWORD,
    W_DOCKER_ALLOWED_HOSTS,
    W_EMQX_API,
    W_EMQX_PASSWORD,
    W_PROVISIONING_TOKEN,
    check_backend_mqtt_uses_jwt,
    check_docker_allowed_hosts,
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


class TestDockerAllowedHostsCheck:
    """oms-zad: W007 warns when running in docker without 'backend' in ALLOWED_HOSTS."""

    def test_no_warnings_in_debug_even_in_docker(self, settings):
        settings.DEBUG = True
        settings.ALLOWED_HOSTS = ["oms.example.com"]
        with mock.patch("forgekey.checks._running_in_docker", return_value=True):
            assert check_docker_allowed_hosts(app_configs=None) == []

    def test_no_warnings_outside_docker(self, prod):
        prod.ALLOWED_HOSTS = ["oms.example.com"]
        with mock.patch("forgekey.checks._running_in_docker", return_value=False):
            assert check_docker_allowed_hosts(app_configs=None) == []

    def test_warns_in_docker_without_backend(self, prod):
        prod.ALLOWED_HOSTS = ["oms.example.com", "localhost"]
        with mock.patch("forgekey.checks._running_in_docker", return_value=True):
            warnings = check_docker_allowed_hosts(app_configs=None)
        assert [w.id for w in warnings] == [W_DOCKER_ALLOWED_HOSTS]

    def test_no_warning_when_backend_present(self, prod):
        prod.ALLOWED_HOSTS = ["oms.example.com", "backend", "localhost"]
        with mock.patch("forgekey.checks._running_in_docker", return_value=True):
            assert check_docker_allowed_hosts(app_configs=None) == []

    def test_no_warning_when_wildcard(self, prod):
        prod.ALLOWED_HOSTS = ["*"]
        with mock.patch("forgekey.checks._running_in_docker", return_value=True):
            assert check_docker_allowed_hosts(app_configs=None) == []

    def test_case_insensitive_match(self, prod):
        prod.ALLOWED_HOSTS = ["oms.example.com", "Backend"]
        with mock.patch("forgekey.checks._running_in_docker", return_value=True):
            assert check_docker_allowed_hosts(app_configs=None) == []


class TestBackendMqttUsesJwtCheck:
    """oms-y5p AC-4: W008 warns when literal MQTT_BROKER credentials are set,
    because EMQX's JWT authenticator silently rejects them and every device
    command publish then fails."""

    def test_no_warnings_in_debug(self, settings):
        settings.DEBUG = True
        settings.MQTT_BROKER_USERNAME = "literal-user"
        settings.MQTT_BROKER_PASSWORD = "literal-pass"  # nosec B105
        assert check_backend_mqtt_uses_jwt(app_configs=None) == []

    def test_no_warning_when_unset(self, prod):
        prod.MQTT_BROKER_USERNAME = ""
        prod.MQTT_BROKER_PASSWORD = ""
        assert check_backend_mqtt_uses_jwt(app_configs=None) == []

    def test_warns_when_username_set(self, prod):
        prod.MQTT_BROKER_USERNAME = "oms-backend"
        prod.MQTT_BROKER_PASSWORD = ""
        warnings = check_backend_mqtt_uses_jwt(app_configs=None)
        assert [w.id for w in warnings] == [W_BACKEND_LITERAL_MQTT_PASSWORD]

    def test_warns_when_password_set(self, prod):
        prod.MQTT_BROKER_USERNAME = ""
        prod.MQTT_BROKER_PASSWORD = "literal-secret"  # nosec B105
        warnings = check_backend_mqtt_uses_jwt(app_configs=None)
        assert [w.id for w in warnings] == [W_BACKEND_LITERAL_MQTT_PASSWORD]

    def test_warns_when_both_set(self, prod):
        prod.MQTT_BROKER_USERNAME = "user"
        prod.MQTT_BROKER_PASSWORD = "pw"  # nosec B105
        warnings = check_backend_mqtt_uses_jwt(app_configs=None)
        assert [w.id for w in warnings] == [W_BACKEND_LITERAL_MQTT_PASSWORD]
