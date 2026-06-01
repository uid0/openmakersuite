"""Circuit breakers for external dependencies (MQTT, outbound HTTP, etc.).

A breaker fails fast while OPEN so a down dependency isn't hammered from every
web / celery / builder process. State is shared via Redis (so one trip protects
the whole fleet); a pluggable in-memory backend keeps tests hermetic. Open /
half-open / close transitions are logged, captured in Sentry, and recorded as a
:class:`~resilience.models.CircuitBreakerEvent` audit row.

Usage::

    from resilience.circuit import get_breaker, circuit, CircuitBreakerOpen

    get_breaker("mqtt").call(broker.publish, topic, body)   # wrap a call
    @circuit("vendor-api")                                    # or decorate
    def fetch(...): ...
"""

from __future__ import annotations

import functools
import logging
import threading
import time
from typing import Callable

from django.conf import settings

logger = logging.getLogger(__name__)

STATE_CLOSED = "closed"
STATE_OPEN = "open"
STATE_HALF_OPEN = "half_open"


class CircuitBreakerOpen(Exception):
    """Raised when a call is rejected because the named breaker is OPEN."""

    def __init__(self, name: str):
        self.name = name
        super().__init__(f"Circuit '{name}' is open; failing fast.")


# --------------------------------------------------------------------------- #
# Storage backends
# --------------------------------------------------------------------------- #
class BaseStorage:
    def state(self, name: str) -> str:
        raise NotImplementedError

    def trip_open(self, name: str) -> None:
        raise NotImplementedError

    def to_half_open(self, name: str) -> None:
        raise NotImplementedError

    def close(self, name: str) -> None:
        raise NotImplementedError

    def record_failure(self, name: str, window: float) -> int:
        raise NotImplementedError

    def opened_at(self, name: str) -> float:
        raise NotImplementedError


class InMemoryStorage(BaseStorage):
    """Per-process state — used in tests and as a Redis-down fallback."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: dict[str, dict] = {}

    def _row(self, name: str) -> dict:
        return self._rows.setdefault(
            name, {"state": STATE_CLOSED, "failures": 0, "window_end": 0.0, "opened_at": 0.0}
        )

    def state(self, name: str) -> str:
        with self._lock:
            return self._row(name)["state"]

    def trip_open(self, name: str) -> None:
        with self._lock:
            row = self._row(name)
            row["state"] = STATE_OPEN
            row["opened_at"] = time.time()

    def to_half_open(self, name: str) -> None:
        with self._lock:
            self._row(name)["state"] = STATE_HALF_OPEN

    def close(self, name: str) -> None:
        with self._lock:
            self._row(name).update(state=STATE_CLOSED, failures=0, window_end=0.0, opened_at=0.0)

    def record_failure(self, name: str, window: float) -> int:
        with self._lock:
            row = self._row(name)
            now = time.time()
            if now > row["window_end"]:
                row["failures"] = 1
                row["window_end"] = now + window
            else:
                row["failures"] += 1
            return row["failures"]

    def opened_at(self, name: str) -> float:
        with self._lock:
            return self._row(name)["opened_at"]


class RedisStorage(BaseStorage):
    """Fleet-shared state in Redis. Minor races during a transition are
    acceptable for a circuit breaker (worst case: a few extra trial calls)."""

    def __init__(self, client, prefix: str = "cb:") -> None:
        self._r = client
        self._p = prefix

    def _k(self, name: str, field: str) -> str:
        return f"{self._p}{name}:{field}"

    @staticmethod
    def _decode(value):
        return value.decode() if isinstance(value, (bytes, bytearray)) else value

    # Every method degrades gracefully if Redis itself is unreachable: reads
    # report CLOSED and record_failure reports 0, so the breaker's own backing
    # store going down can never *block* real traffic — it just stops
    # protecting (the breaker becomes a no-op) until Redis returns.
    def state(self, name: str) -> str:
        try:
            return self._decode(self._r.get(self._k(name, "state"))) or STATE_CLOSED
        except Exception:  # noqa: BLE001 — Redis down must not break callers
            logger.debug("Circuit '%s': redis state read failed", name, exc_info=True)
            return STATE_CLOSED

    def trip_open(self, name: str) -> None:
        try:
            self._r.set(self._k(name, "state"), STATE_OPEN)
            self._r.set(self._k(name, "opened_at"), repr(time.time()))
        except Exception:  # noqa: BLE001
            logger.debug("Circuit '%s': redis trip_open failed", name, exc_info=True)

    def to_half_open(self, name: str) -> None:
        try:
            self._r.set(self._k(name, "state"), STATE_HALF_OPEN)
        except Exception:  # noqa: BLE001
            logger.debug("Circuit '%s': redis to_half_open failed", name, exc_info=True)

    def close(self, name: str) -> None:
        try:
            self._r.set(self._k(name, "state"), STATE_CLOSED)
            self._r.delete(self._k(name, "failures"))
            self._r.delete(self._k(name, "opened_at"))
        except Exception:  # noqa: BLE001
            logger.debug("Circuit '%s': redis close failed", name, exc_info=True)

    def record_failure(self, name: str, window: float) -> int:
        try:
            key = self._k(name, "failures")
            count = int(self._r.incr(key))
            if count == 1:
                self._r.expire(key, int(window) + 1)
            return count
        except Exception:  # noqa: BLE001 — a Redis outage must not itself trip the breaker
            logger.debug("Circuit '%s': redis record_failure failed", name, exc_info=True)
            return 0

    def opened_at(self, name: str) -> float:
        try:
            value = self._decode(self._r.get(self._k(name, "opened_at")))
            return float(value) if value else 0.0
        except Exception:  # noqa: BLE001
            logger.debug("Circuit '%s': redis opened_at failed", name, exc_info=True)
            return 0.0


# --------------------------------------------------------------------------- #
# Breaker
# --------------------------------------------------------------------------- #
class CircuitBreaker:
    def __init__(
        self,
        name: str,
        *,
        fail_max: int = 5,
        reset_timeout: float = 30.0,
        window: float = 60.0,
        storage: BaseStorage | None = None,
        exclude: tuple = (),
    ) -> None:
        self.name = name
        self.fail_max = fail_max
        self.reset_timeout = reset_timeout
        self.window = window
        self.storage = storage or default_storage()
        self.exclude = tuple(exclude)

    def _transition(self, old: str, new: str, error: Exception | None = None) -> None:
        if old == new:
            return
        logger.warning(
            "Circuit '%s' %s -> %s%s", self.name, old, new, f": {error}" if error else ""
        )
        try:
            from .models import CircuitBreakerEvent

            CircuitBreakerEvent.objects.create(
                name=self.name,
                from_state=old,
                to_state=new,
                detail=(str(error)[:500] if error else ""),
            )
        except Exception:  # noqa: BLE001 — never let auditing break the call path
            logger.exception("Failed to record CircuitBreakerEvent for '%s'", self.name)
        if new == STATE_OPEN:
            try:
                import sentry_sdk

                sentry_sdk.capture_message(
                    f"Circuit breaker '{self.name}' opened ({error})", level="error"
                )
            except Exception:  # noqa: BLE001 — Sentry optional
                logger.debug("Sentry capture failed for circuit '%s'", self.name, exc_info=True)

    def call(self, fn: Callable, *args, **kwargs):
        if not getattr(settings, "CIRCUIT_BREAKERS_ENABLED", True):
            return fn(*args, **kwargs)

        state = self.storage.state(self.name)
        if state == STATE_OPEN:
            if time.time() - self.storage.opened_at(self.name) >= self.reset_timeout:
                self.storage.to_half_open(self.name)
                self._transition(STATE_OPEN, STATE_HALF_OPEN)
                state = STATE_HALF_OPEN
            else:
                raise CircuitBreakerOpen(self.name)

        try:
            result = fn(*args, **kwargs)
        except self.exclude:
            raise  # excluded exceptions are "expected" and don't trip the breaker
        except Exception as exc:
            failures = self.storage.record_failure(self.name, self.window)
            if state == STATE_HALF_OPEN or failures >= self.fail_max:
                self.storage.trip_open(self.name)
                self._transition(state, STATE_OPEN, exc)
            raise

        if state == STATE_HALF_OPEN:
            self.storage.close(self.name)
            self._transition(STATE_HALF_OPEN, STATE_CLOSED)
        return result

    def __call__(self, fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            return self.call(fn, *args, **kwargs)

        wrapper.circuit_breaker = self  # type: ignore[attr-defined]
        return wrapper


# --------------------------------------------------------------------------- #
# Registry + default storage
# --------------------------------------------------------------------------- #
_registry: dict[str, CircuitBreaker] = {}
_registry_lock = threading.Lock()
_default_storage: BaseStorage | None = None
_storage_lock = threading.Lock()


def default_storage() -> BaseStorage:
    """The shared storage. Redis when configured/reachable, else in-memory."""
    global _default_storage
    if _default_storage is not None:
        return _default_storage
    with _storage_lock:
        if _default_storage is None:
            _default_storage = _build_storage()
    return _default_storage


def _build_storage() -> BaseStorage:
    if not getattr(settings, "CIRCUIT_BREAKER_USE_REDIS", True):
        return InMemoryStorage()
    try:
        import redis

        url = getattr(settings, "REDIS_URL", "redis://localhost:6379/0")
        return RedisStorage(redis.Redis.from_url(url))
    except Exception:  # noqa: BLE001 — degrade gracefully if Redis is unavailable
        logger.exception("Circuit breaker: Redis unavailable; using in-memory state")
        return InMemoryStorage()


def reset_storage(storage: BaseStorage | None = None) -> None:
    """Swap the default storage (tests) and clear the breaker registry."""
    global _default_storage
    with _storage_lock:
        _default_storage = storage
    with _registry_lock:
        _registry.clear()


def get_breaker(name: str, **kwargs) -> CircuitBreaker:
    with _registry_lock:
        if name not in _registry:
            _registry[name] = CircuitBreaker(name, **kwargs)
        return _registry[name]


def circuit(name: str, **kwargs) -> CircuitBreaker:
    """Decorator form: ``@circuit("vendor-api")`` routes the call through the
    named breaker. Also usable inside a Celery task body."""
    return get_breaker(name, **kwargs)


def breakered_request(method: str, url: str, *, name: str = "http", **kwargs):
    """Drop-in for ``requests.request`` that routes through the named breaker.

    Connection errors / timeouts count as failures and trip the breaker;
    4xx/5xx responses do NOT (the vendor answered — call ``raise_for_status``
    yourself if you want those handled). When the breaker is open this raises
    ``requests.exceptions.ConnectionError`` — the same family a real outage
    raises — so existing ``except requests.exceptions.RequestException``
    handlers absorb an open breaker transparently.
    """
    import requests

    kwargs.setdefault("timeout", 10)
    try:
        return get_breaker(name).call(requests.request, method, url, **kwargs)
    except CircuitBreakerOpen as exc:
        raise requests.exceptions.ConnectionError(
            f"Circuit '{name}' is open; not calling {url}"
        ) from exc
