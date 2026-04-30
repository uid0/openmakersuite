"""Tests for liveness and readiness probes (AC-11, AC-12)."""

from unittest.mock import patch

from django.urls import reverse

import pytest


def test_livez_returns_200_without_authentication(api_client):
    """Liveness must be reachable without auth and without dep checks."""
    resp = api_client.get(reverse("health-livez"))
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_livez_does_not_query_database(api_client):
    """Liveness must NOT touch the database — a DB outage must not flap pods."""
    with patch("config.health.connections") as mock_connections:
        resp = api_client.get(reverse("health-livez"))
    assert resp.status_code == 200
    mock_connections.__getitem__.assert_not_called()


def test_livez_only_allows_get(api_client):
    resp = api_client.post(reverse("health-livez"))
    assert resp.status_code == 405


@pytest.mark.django_db
def test_readyz_returns_200_when_all_checks_pass(api_client):
    """All dependencies are healthy under the test harness (memory broker
    treated as healthy, locmem cache, sqlite DB)."""
    resp = api_client.get(reverse("health-readyz"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["checks"] == {"database": "ok", "cache": "ok", "broker": "ok"}


@pytest.mark.django_db
def test_readyz_returns_503_when_database_fails(api_client):
    with patch("config.health._check_database", return_value=(False, "ConnectionError")):
        resp = api_client.get(reverse("health-readyz"))
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unavailable"
    assert body["checks"]["database"] == "fail"
    assert body["failures"] == {"database": "ConnectionError"}


@pytest.mark.django_db
def test_readyz_returns_503_when_cache_fails(api_client):
    with patch("config.health._check_cache", return_value=(False, "RedisError")):
        resp = api_client.get(reverse("health-readyz"))
    assert resp.status_code == 503
    assert resp.json()["checks"]["cache"] == "fail"


@pytest.mark.django_db
def test_readyz_returns_503_when_broker_fails(api_client):
    with patch("config.health._check_broker", return_value=(False, "OperationalError")):
        resp = api_client.get(reverse("health-readyz"))
    assert resp.status_code == 503
    assert resp.json()["checks"]["broker"] == "fail"


@pytest.mark.django_db
def test_readyz_lists_all_failures_simultaneously(api_client):
    with (
        patch("config.health._check_database", return_value=(False, "DBError")),
        patch("config.health._check_cache", return_value=(False, "CacheError")),
        patch("config.health._check_broker", return_value=(False, "BrokerError")),
    ):
        resp = api_client.get(reverse("health-readyz"))
    assert resp.status_code == 503
    failures = resp.json()["failures"]
    assert set(failures) == {"database", "cache", "broker"}


@pytest.mark.django_db
def test_readyz_response_does_not_leak_connection_strings(api_client):
    """Readiness must not echo broker URLs, cache locations, or DB DSNs."""
    with patch("config.health._check_database", return_value=(False, "ConnectionError")):
        resp = api_client.get(reverse("health-readyz"))
    body = resp.content.decode()
    assert "redis://" not in body
    assert "postgres://" not in body
    assert "amqp://" not in body
    assert "PASSWORD" not in body.upper()


def test_readyz_only_allows_get(api_client):
    resp = api_client.post(reverse("health-readyz"))
    assert resp.status_code == 405
