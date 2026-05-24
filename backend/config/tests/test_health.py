"""Tests for liveness and readiness probes (AC-11, AC-12, oms-aam)."""

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
def test_readyz_returns_200_when_required_checks_pass(api_client):
    """Required deps healthy under the test harness; optional checks
    (celery worker, emqx, object store, telemetry) skip cleanly."""
    resp = api_client.get(reverse("health-readyz"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    # Required checks all green:
    for name in ("database", "cache", "broker"):
        assert body["checks"][name] == "ok"
    # New checks are present and skipped under the test harness:
    for name in ("celery_worker", "emqx", "object_store", "telemetry"):
        assert body["checks"][name] == "skipped"
    # Failures dict only present when something failed:
    assert "failures" not in body
    # Skipped dict surfaces per-check reasons for operator visibility:
    assert set(body["skipped"]) == {"celery_worker", "emqx", "object_store", "telemetry"}


@pytest.mark.django_db
def test_readyz_returns_503_when_database_fails(api_client):
    with patch("config.health._check_database", return_value=(False, "ConnectionError")):
        resp = api_client.get(reverse("health-readyz"))
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unavailable"
    assert body["checks"]["database"] == "fail"
    assert body["failures"]["database"] == "ConnectionError"


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
def test_readyz_lists_all_required_failures_simultaneously(api_client):
    with (
        patch("config.health._check_database", return_value=(False, "DBError")),
        patch("config.health._check_cache", return_value=(False, "CacheError")),
        patch("config.health._check_broker", return_value=(False, "BrokerError")),
    ):
        resp = api_client.get(reverse("health-readyz"))
    assert resp.status_code == 503
    failures = resp.json()["failures"]
    assert {"database", "cache", "broker"}.issubset(failures)


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


# ---------------------------------------------------------------------------
# New per-check coverage (oms-aam)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_readyz_optional_check_failure_does_not_503(api_client):
    """An optional check failing must surface in failures but stay 200."""
    with patch("config.health._check_emqx", return_value=("fail", "ConnectionError")):
        resp = api_client.get(reverse("health-readyz"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["checks"]["emqx"] == "fail"
    assert body["failures"]["emqx"] == "ConnectionError"


@pytest.mark.django_db
def test_readyz_required_checks_setting_can_promote_optional(api_client, settings):
    """Operators can promote any check to required via settings."""
    settings.READYZ_REQUIRED_CHECKS = ["database", "cache", "broker", "emqx"]
    with patch("config.health._check_emqx", return_value=("fail", "ConnectionError")):
        resp = api_client.get(reverse("health-readyz"))
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unavailable"
    assert "emqx" in body["failures"]


@pytest.mark.django_db
def test_readyz_required_checks_setting_csv_form(api_client, settings):
    """READYZ_REQUIRED_CHECKS may be a comma-separated string (env-var friendly)."""
    settings.READYZ_REQUIRED_CHECKS = "database,cache,broker,celery_worker"
    with patch("config.health._check_celery_worker", return_value=("fail", "no workers responded")):
        resp = api_client.get(reverse("health-readyz"))
    assert resp.status_code == 503
    assert "celery_worker" in resp.json()["failures"]


@pytest.mark.django_db
def test_readyz_skipped_checks_never_cause_503(api_client, settings):
    """Even if promoted to required, a skipped check (not configured)
    must not 503 — skipped means "not deployed here" not "broken"."""
    settings.READYZ_REQUIRED_CHECKS = ["database", "cache", "broker", "emqx"]
    # Default test env: no EMQX creds -> _check_emqx returns ("skipped", ...)
    resp = api_client.get(reverse("health-readyz"))
    assert resp.status_code == 200
    assert resp.json()["checks"]["emqx"] == "skipped"


# Per-check unit coverage --------------------------------------------------


def test_check_celery_worker_skips_on_eager_broker(settings):
    from config.health import _check_celery_worker

    settings.CELERY_BROKER_URL = "memory://"
    status, reason = _check_celery_worker()
    assert status == "skipped"
    assert reason


def test_check_celery_worker_fails_when_no_workers(settings):
    from config import health

    settings.CELERY_BROKER_URL = "redis://localhost:6379/0"
    settings.CELERY_TASK_ALWAYS_EAGER = False
    with patch("config.celery.app.control.ping", return_value=[]):
        status, reason = health._check_celery_worker()
    assert status == "fail"
    assert "no workers" in reason


def test_check_celery_worker_ok_when_worker_replies(settings):
    from config import health

    settings.CELERY_BROKER_URL = "redis://localhost:6379/0"
    settings.CELERY_TASK_ALWAYS_EAGER = False
    with patch("config.celery.app.control.ping", return_value=[{"worker@host": {"ok": "pong"}}]):
        status, _ = health._check_celery_worker()
    assert status == "ok"


def test_check_emqx_skipped_without_credentials(settings):
    from config.health import _check_emqx

    settings.EMQX_API_URL = "http://emqx:18083/api/v5"
    settings.EMQX_API_KEY = ""
    settings.EMQX_API_SECRET = ""
    status, reason = _check_emqx()
    assert status == "skipped"
    assert "credentials" in reason.lower()


def test_check_emqx_ok_on_2xx(settings):
    from config import health

    settings.EMQX_API_URL = "http://emqx:18083/api/v5"
    settings.EMQX_API_KEY = "k"
    settings.EMQX_API_SECRET = "s"

    class _Resp:
        status_code = 200

    with patch("requests.get", return_value=_Resp()):
        status, _ = health._check_emqx()
    assert status == "ok"


def test_check_emqx_fail_on_5xx(settings):
    from config import health

    settings.EMQX_API_URL = "http://emqx:18083/api/v5"
    settings.EMQX_API_KEY = "k"
    settings.EMQX_API_SECRET = "s"

    class _Resp:
        status_code = 503

    with patch("requests.get", return_value=_Resp()):
        status, reason = health._check_emqx()
    assert status == "fail"
    assert "503" in reason


def test_check_emqx_fail_on_exception(settings):
    from config import health

    settings.EMQX_API_URL = "http://emqx:18083/api/v5"
    settings.EMQX_API_KEY = "k"
    settings.EMQX_API_SECRET = "s"
    with patch("requests.get", side_effect=ConnectionError("refused")):
        status, reason = health._check_emqx()
    assert status == "fail"
    assert reason == "ConnectionError"


def test_check_object_store_skipped_for_filesystem(settings):
    from config.health import _check_object_store

    settings.STORAGES = {"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"}}
    status, reason = _check_object_store()
    assert status == "skipped"
    assert "object store" in reason


def test_check_object_store_probes_when_s3_configured(settings):
    from unittest.mock import MagicMock

    from config import health

    settings.STORAGES = {
        "default": {"BACKEND": "storages.backends.s3boto3.S3Boto3Storage"},
    }
    fake_storage = MagicMock()
    fake_storage.exists.return_value = False
    with patch.object(health, "default_storage", fake_storage):
        status, _ = health._check_object_store()
    assert status == "ok"
    fake_storage.exists.assert_called_once()


def test_check_object_store_fails_when_probe_raises(settings):
    from unittest.mock import MagicMock

    from config import health

    settings.STORAGES = {
        "default": {"BACKEND": "storages.backends.s3boto3.S3Boto3Storage"},
    }
    fake_storage = MagicMock()
    fake_storage.exists.side_effect = ConnectionError("nope")
    with patch.object(health, "default_storage", fake_storage):
        status, reason = health._check_object_store()
    assert status == "fail"
    assert reason == "ConnectionError"


def test_check_telemetry_skipped_when_no_dsn(settings):
    from config.health import _check_telemetry

    settings.SENTRY_DSN = ""
    status, reason = _check_telemetry()
    assert status == "skipped"
    assert "DSN" in reason


def test_check_telemetry_probes_sentry_dsn(settings):
    from config import health

    settings.SENTRY_DSN = "https://k@sentry.io/1234"
    with patch("socket.create_connection") as connect:
        connect.return_value.__enter__.return_value = None
        connect.return_value.__exit__.return_value = False
        status, _ = health._check_telemetry()
    assert status == "ok"
    args, _kwargs = connect.call_args
    assert args[0] == ("sentry.io", 443)


def test_check_telemetry_fails_when_sentry_unreachable(settings):
    from config import health

    settings.SENTRY_DSN = "https://k@sentry.io/1234"
    with patch("socket.create_connection", side_effect=OSError("no route")):
        status, reason = health._check_telemetry()
    assert status == "fail"
    assert reason == "OSError"
