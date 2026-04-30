"""
Liveness and readiness endpoints (AC-11, AC-12).

Two distinct probes are exposed:

* ``GET /api/health/livez/`` — liveness. Returns 200 as long as the WSGI
  process is running. Does NOT touch the database, cache, or broker. A
  green liveness response means "do not restart this container."
* ``GET /api/health/readyz/`` — readiness. Returns 200 only when the
  critical runtime dependencies are reachable: database, cache (Redis),
  and the Celery broker. A red readiness response means "do not route
  traffic here yet" without implying the process should be killed.

Splitting the two prevents the failure mode where a transient Redis
hiccup forces every pod to restart.

The endpoints intentionally do NOT echo back configuration values
(connection strings, credentials, secrets) — they report status only.
"""

from __future__ import annotations

from django.conf import settings
from django.core.cache import cache
from django.db import connections
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET


def _check_database() -> tuple[bool, str | None]:
    try:
        connections["default"].ensure_connection()
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return True, None
    except Exception as exc:
        return False, type(exc).__name__


def _check_cache() -> tuple[bool, str | None]:
    sentinel_key = "_health_readiness_probe"
    try:
        cache.set(sentinel_key, "1", timeout=5)
        value = cache.get(sentinel_key)
        if value != "1":
            return False, "cache round-trip failed"
        return True, None
    except Exception as exc:
        return False, type(exc).__name__


def _check_broker() -> tuple[bool, str | None]:
    """
    Confirm the Celery broker is reachable.

    Uses ``Connection.ensure_connection`` with a small max-retries budget
    so the readiness probe never blocks a deployment for more than a few
    seconds when the broker is down.
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
    """Readiness probe — critical dependencies reachable.

    Reports the status of each checked dependency; if any check fails
    the response is 503 with details so operators can see which
    component is unhealthy.
    """
    checks = {
        "database": _check_database(),
        "cache": _check_cache(),
        "broker": _check_broker(),
    }
    failures = {name: err for name, (ok, err) in checks.items() if not ok}
    body = {
        "status": "ok" if not failures else "unavailable",
        "checks": {name: ("ok" if ok else "fail") for name, (ok, _) in checks.items()},
    }
    if failures:
        body["failures"] = failures
        return JsonResponse(body, status=503)
    return JsonResponse(body, status=200)
