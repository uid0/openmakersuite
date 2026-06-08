"""Tests for the gh-713 public-write throttles.

Exercises the wired throttle classes by patching DRF's
``SimpleRateThrottle.THROTTLE_RATES`` to tiny caps and overriding the
cache to LocMemCache — same code path as production at millisecond speed.

Why patch the class attr instead of ``override_settings(REST_FRAMEWORK=…)``:
DRF caches ``THROTTLE_RATES`` on the class at import time
(``THROTTLE_RATES = api_settings.DEFAULT_THROTTLE_RATES`` in
``rest_framework/throttling.py``). ``override_settings`` updates
``api_settings`` but the cached class attribute still points at the
old dict. Patching the class attr directly is the only reliable way
to drive the throttle from a test.
"""

from __future__ import annotations

import contextlib

from django.test import override_settings

import pytest
from rest_framework.test import APIClient
from rest_framework.throttling import SimpleRateThrottle

pytestmark = pytest.mark.django_db


_TEST_RATES = {
    "scan_dispatch": "2/min",
    "project_storage_start": "2/hour",
    "pi_daemon": "3/min",
}


@contextlib.contextmanager
def _throttle_overrides():
    """Patch SimpleRateThrottle.THROTTLE_RATES + pin LocMemCache, then
    clear the cache so a previous test's counters can't leak in."""
    original_rates = SimpleRateThrottle.THROTTLE_RATES
    SimpleRateThrottle.THROTTLE_RATES = _TEST_RATES
    try:
        with override_settings(
            CACHES={
                "default": {
                    "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                    "LOCATION": "test-throttle-cache",
                }
            }
        ):
            from django.core.cache import caches

            caches["default"].clear()
            yield
            caches["default"].clear()
    finally:
        SimpleRateThrottle.THROTTLE_RATES = original_rates


# ---------------------------------------------------------------------------
# Scanner dispatch — scan_dispatch scope
# ---------------------------------------------------------------------------


class TestScanDispatchThrottle:
    def test_429_after_cap_reached(self):
        with _throttle_overrides():
            client = APIClient()
            # First 2 hits should clear the cap of 2/min.
            for _ in range(2):
                resp = client.post("/api/scanner/dispatch/", {"payload": "ABCDEF"}, format="json")
                assert resp.status_code == 200, resp.content
            resp = client.post("/api/scanner/dispatch/", {"payload": "ABCDEF"}, format="json")
            assert resp.status_code == 429, resp.content


# ---------------------------------------------------------------------------
# Project-storage kiosk start — project_storage_start scope
# ---------------------------------------------------------------------------


class TestProjectStorageStartThrottle:
    def test_429_after_cap_reached(self):
        # Each call uses a unique username so the business-layer guard
        # against repeat-start by the same member doesn't mask the
        # throttle behavior under test.
        with _throttle_overrides():
            client = APIClient()
            for i in range(2):
                resp = client.post(
                    "/api/project-storage/stints/start/",
                    {"username": f"member-throttle-{i}"},
                    format="json",
                )
                assert resp.status_code == 201, resp.content
            resp = client.post(
                "/api/project-storage/stints/start/",
                {"username": "member-throttle-3"},
                format="json",
            )
            assert resp.status_code == 429, resp.content


# ---------------------------------------------------------------------------
# Pi daemon — pi_daemon scope (shared by print_queue + mark_printed + label)
# ---------------------------------------------------------------------------


class TestPiDaemonThrottle:
    def test_print_queue_429_after_cap_reached(self):
        with _throttle_overrides():
            client = APIClient()
            for _ in range(3):
                resp = client.get("/api/project-storage/stints/print-queue/")
                assert resp.status_code == 200, resp.content
            resp = client.get("/api/project-storage/stints/print-queue/")
            assert resp.status_code == 429, resp.content

    def test_pi_daemon_scope_is_shared_across_endpoints(self):
        # mark_printed + print_queue share the same scope, so traffic on
        # one counts against the other. This is the right shape — a
        # daemon hitting both 2x/poll shouldn't double the effective cap.
        with _throttle_overrides():
            client = APIClient()
            # Three calls exhaust the cap of 3.
            client.get("/api/project-storage/stints/print-queue/")
            client.get("/api/project-storage/stints/print-queue/")
            client.get("/api/project-storage/stints/print-queue/")
            # Now a hit on the OTHER endpoint should also be throttled.
            resp = client.post(
                "/api/project-storage/stints/PS-NEVERMIND/mark-printed/", {}, format="json"
            )
            assert resp.status_code == 429, resp.content


# ---------------------------------------------------------------------------
# No throttle on the read-only / staff-gated paths
# ---------------------------------------------------------------------------


class TestNoThrottleOnReads:
    def test_list_is_not_throttled(self):
        # The list endpoint is staff-gated, not part of the public-write
        # surface. Calling it many times unauthenticated returns 401/403
        # but the throttle should never fire — verify by spamming past
        # the test caps and confirming no 429s appear.
        with _throttle_overrides():
            client = APIClient()
            seen_codes = set()
            for _ in range(10):
                resp = client.get("/api/project-storage/stints/")
                seen_codes.add(resp.status_code)
            assert 429 not in seen_codes
