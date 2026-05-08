"""Tests for Django settings invariants."""

import importlib

import pytest


@pytest.mark.unit
class TestAllowedHostsLoopback:
    """Loopback addresses must always be permitted regardless of env value.

    Container healthchecks (docker-compose.prod.yml) probe
    http://localhost:8000/api/health/livez/. If ALLOWED_HOSTS is supplied
    via env without 'localhost', Django raises DisallowedHost on every probe
    and floods the logs (oms-ujj).
    """

    def _reload_settings(self, monkeypatch, allowed_hosts_env):
        from config import settings

        monkeypatch.setenv("ALLOWED_HOSTS", allowed_hosts_env)
        return importlib.reload(settings)

    def test_loopback_present_when_env_omits_them(self, monkeypatch):
        settings = self._reload_settings(monkeypatch, "example.com")
        assert "localhost" in settings.ALLOWED_HOSTS
        assert "127.0.0.1" in settings.ALLOWED_HOSTS
        assert "example.com" in settings.ALLOWED_HOSTS

    def test_loopback_present_when_env_includes_them(self, monkeypatch):
        settings = self._reload_settings(monkeypatch, "example.com,localhost,127.0.0.1")
        assert settings.ALLOWED_HOSTS.count("localhost") == 1
        assert settings.ALLOWED_HOSTS.count("127.0.0.1") == 1
        assert "example.com" in settings.ALLOWED_HOSTS

    def test_loopback_present_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("ALLOWED_HOSTS", raising=False)
        from config import settings

        settings = importlib.reload(settings)
        assert "localhost" in settings.ALLOWED_HOSTS
        assert "127.0.0.1" in settings.ALLOWED_HOSTS
