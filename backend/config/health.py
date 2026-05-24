"""
Liveness and readiness endpoints (AC-11, AC-12, oms-aam).

Two distinct probes are exposed:

* ``GET /api/health/livez/`` — liveness. Returns 200 as long as the WSGI
  process is running. Does NOT touch the database, cache, or broker. A
  green liveness response means "do not restart this container."
* ``GET /api/health/readyz/`` — readiness. Returns 200 when every check
  marked *required* is reachable. A red readiness response means "do not
  route traffic here yet" without implying the process should be killed.

Splitting the two prevents the failure mode where a transient Redis
hiccup forces every pod to restart.

Each individual check returns one of three states:

* ``ok``       — dependency reachable
* ``fail``     — configured and unreachable; populates ``failures``
* ``skipped``  — not configured for this deployment; populates
  ``skipped`` for operator visibility but never fails readiness

Which checks count toward 503 is configurable via
``settings.READYZ_REQUIRED_CHECKS`` (default: db/cache/broker — preserves
the historical contract). Optional checks always report their status but
their failure does not flap traffic.

The endpoints intentionally do NOT echo back configuration values
(connection strings, credentials, secrets) — they report status only.
"""

from __future__ import annotations

import socket
from typing import Tuple
from urllib.parse import urlparse

from django.conf import settings
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.db import connections
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

# Legacy bool-tuple checks return (ok: bool, error: str | None).
# New checks return ("ok"|"fail"|"skipped", reason: str | None).
LegacyResult = Tuple[bool, "str | None"]
StatusResult = Tuple[str, "str | None"]

DEFAULT_REQUIRED_CHECKS = ("database", "cache", "broker")


def _check_database() -> LegacyResult:
    try:
        connections["default"].ensure_connection()
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return True, None
    except Exception as exc:
        return False, type(exc).__name__


def _check_cache() -> LegacyResult:
    sentinel_key = "_health_readiness_probe"
    try:
        cache.set(sentinel_key, "1", timeout=5)
        value = cache.get(sentinel_key)
        if value != "1":
            return False, "cache round-trip failed"
        return True, None
    except Exception as exc:
        return False, type(exc).__name__


def _check_broker() -> LegacyResult:
    """Confirm the Celery broker (transport) is reachable.

    Distinct from ``_check_celery_worker``: a reachable broker only means
    the AMQP/Redis socket is open. It does not imply any worker is
    actually consuming.
    """
    broker_url = getattr(settings, "CELERY_BROKER_URL", None)
    if not broker_url or broker_url.startswith("memory://"):
        # In-memory broker is only used for tests; treat as healthy.
        return True, None
    try:
        from kombu import Connection

        with Connection(broker_url, connect_timeout=2) as conn:
            conn.ensure_connection(max_retries=1, interval_start=0, interval_step=0)
        return True, None
    except Exception as exc:
        return False, type(exc).__name__


def _check_celery_worker() -> StatusResult:
    """Verify at least one Celery worker replies to a control ping.

    A broker-up / worker-down deployment will accept tasks but never run
    them — readiness must catch this so we don't quietly drop work.

    Skipped under the in-memory eager test broker (no workers exist by
    design) so the test suite doesn't fail readiness on an empty queue.
    """
    broker_url = getattr(settings, "CELERY_BROKER_URL", "") or ""
    if not broker_url or broker_url.startswith("memory://"):
        return "skipped", "no real broker configured"
    if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        return "skipped", "celery is eager"
    try:
        from config.celery import app

        replies = app.control.ping(timeout=1.0)
    except Exception as exc:
        return "fail", type(exc).__name__
    if not replies:
        return "fail", "no workers responded"
    return "ok", None


def _check_emqx() -> StatusResult:
    """Ping the EMQX REST API ``/status`` endpoint.

    Skipped when no API credentials are configured: ``EMQX_API_URL`` has a
    default value baked in (so its presence alone does not mean a real
    EMQX is wired up), but a deployment that talks to EMQX must set
    ``EMQX_API_KEY`` / ``EMQX_API_SECRET`` (see configure_emqx_jwt_auth
    command). Treating empty creds as "not deployed here" matches that
    contract.
    """
    api_url = (getattr(settings, "EMQX_API_URL", "") or "").rstrip("/")
    api_key = getattr(settings, "EMQX_API_KEY", "") or ""
    api_secret = getattr(settings, "EMQX_API_SECRET", "") or ""
    if not api_url or not api_key or not api_secret:
        return "skipped", "EMQX credentials not configured"
    try:
        import requests

        # /status is the EMQX REST liveness endpoint and does not require
        # auth; we still pass creds because some reverse proxies block
        # unauthenticated requests outright.
        resp = requests.get(
            f"{api_url}/status",
            auth=(api_key, api_secret),
            timeout=2,
        )
    except Exception as exc:
        return "fail", type(exc).__name__
    if resp.status_code >= 500:
        return "fail", f"HTTP {resp.status_code}"
    return "ok", None


def _check_object_store() -> StatusResult:
    """Verify the configured object store (S3 or compatible) is reachable.

    Skipped when the deployment uses local-filesystem media (the default —
    ``MEDIA_ROOT`` on disk). Detected by inspecting Django's STORAGES
    setting for an S3 / boto backend.
    """
    storages = getattr(settings, "STORAGES", {}) or {}
    default_backend = (storages.get("default", {}) or {}).get("BACKEND", "") or ""
    legacy_backend = getattr(settings, "DEFAULT_FILE_STORAGE", "") or ""
    backend_name = (default_backend + "|" + legacy_backend).lower()
    if "s3" not in backend_name and "boto" not in backend_name:
        return "skipped", "no object store configured"
    try:
        # exists() forces a HEAD against the bucket. The probe key does
        # not need to exist — a 404 still proves the bucket is reachable.
        default_storage.exists("_health_probe")
        return "ok", None
    except Exception as exc:
        return "fail", type(exc).__name__


def _check_telemetry() -> StatusResult:
    """Verify the error/observability backend (Sentry).

    Skipped when SENTRY_DSN is unset so the readiness probe still returns 200
    in environments without telemetry configured.
    """
    sentry_dsn = getattr(settings, "SENTRY_DSN", "") or ""
    if not sentry_dsn:
        return "skipped", "no telemetry DSN configured"
    try:
        parsed = urlparse(sentry_dsn)
        host = parsed.hostname
        if not host:
            return "fail", "unparseable telemetry URL"
        port = parsed.port or (443 if parsed.scheme in ("https", "grpcs") else 80)
        with socket.create_connection((host, port), timeout=2):
            pass
        return "ok", None
    except Exception as exc:
        return "fail", type(exc).__name__


# Map of public check name -> attribute name on this module. We resolve the
# callable via ``getattr`` at request time so ``unittest.mock.patch`` on the
# underlying ``_check_*`` symbols takes effect (a dict of direct function
# references would freeze the originals at import time).
CHECKS: dict[str, str] = {
    "database": "_check_database",
    "cache": "_check_cache",
    "broker": "_check_broker",
    "celery_worker": "_check_celery_worker",
    "emqx": "_check_emqx",
    "object_store": "_check_object_store",
    "telemetry": "_check_telemetry",
}


def _normalize(raw) -> StatusResult:
    """Coerce legacy bool-tuple results into the 3-state form."""
    status, reason = raw
    if isinstance(status, bool):
        return ("ok" if status else "fail"), reason
    return status, reason


def _required_checks() -> "set[str]":
    raw = getattr(settings, "READYZ_REQUIRED_CHECKS", None)
    if raw is None:
        return set(DEFAULT_REQUIRED_CHECKS)
    if isinstance(raw, str):
        names = [n.strip() for n in raw.split(",") if n.strip()]
    else:
        names = [str(n).strip() for n in raw if str(n).strip()]
    return set(names)


@require_GET
@never_cache
def livez(request):
    """Liveness probe — process is up.

    Deliberately performs no I/O so a slow or failed dependency cannot
    cause container restarts.
    """
    return JsonResponse({"status": "ok"}, status=200)


@require_GET
@never_cache
def readyz(request):
    """Readiness probe — required dependencies reachable.

    Reports per-check status (``ok``/``fail``/``skipped``). HTTP 503 only
    when a *required* check fails; optional check failures are surfaced
    in the body for operators but do not gate traffic.
    """
    required = _required_checks()
    module = globals()
    results: dict[str, StatusResult] = {
        name: _normalize(module[attr]()) for name, attr in CHECKS.items()
    }

    checks_view: dict[str, str] = {name: status for name, (status, _) in results.items()}
    failures: dict[str, str] = {
        name: (reason or "fail") for name, (status, reason) in results.items() if status == "fail"
    }
    skipped: dict[str, str] = {
        name: (reason or "skipped")
        for name, (status, reason) in results.items()
        if status == "skipped"
    }
    required_failures = {name: failures[name] for name in failures if name in required}

    body: dict = {
        "status": "ok" if not required_failures else "unavailable",
        "checks": checks_view,
        "required": sorted(required),
    }
    if failures:
        body["failures"] = failures
    if skipped:
        body["skipped"] = skipped

    status_code = 503 if required_failures else 200
    return JsonResponse(body, status=status_code)
