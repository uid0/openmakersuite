import fnmatch

import pytest

from resilience.circuit import (
    STATE_CLOSED,
    STATE_HALF_OPEN,
    STATE_OPEN,
    CircuitBreaker,
    CircuitBreakerOpen,
    InMemoryStorage,
    RedisStorage,
    get_breaker,
    reset_storage,
)
from resilience.models import CircuitBreakerEvent

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def memory_storage(settings):
    """Hermetic per-test breaker state; no Redis."""
    settings.CIRCUIT_BREAKER_USE_REDIS = False
    settings.CIRCUIT_BREAKERS_ENABLED = True
    reset_storage(InMemoryStorage())
    yield
    reset_storage(None)


def _boom():
    raise ValueError("boom")


def test_trips_open_after_fail_max_then_fails_fast():
    breaker = get_breaker("t-trip", fail_max=3, reset_timeout=100)
    for _ in range(3):
        with pytest.raises(ValueError):
            breaker.call(_boom)
    assert breaker.storage.state("t-trip") == STATE_OPEN
    # Once open, calls are rejected without invoking the wrapped fn.
    with pytest.raises(CircuitBreakerOpen):
        breaker.call(_boom)


def test_successful_calls_keep_circuit_closed():
    breaker = get_breaker("t-ok", fail_max=2, reset_timeout=100)
    for _ in range(5):
        assert breaker.call(lambda: "ok") == "ok"
    assert breaker.storage.state("t-ok") == STATE_CLOSED


def test_records_audit_event_on_open():
    breaker = get_breaker("t-audit", fail_max=1, reset_timeout=100)
    with pytest.raises(ValueError):
        breaker.call(_boom)
    assert CircuitBreakerEvent.objects.filter(name="t-audit", to_state=STATE_OPEN).exists()


def test_half_open_recovers_on_success():
    # reset_timeout=0 => the next call after a trip is a half-open trial.
    breaker = get_breaker("t-recover", fail_max=1, reset_timeout=0)
    with pytest.raises(ValueError):
        breaker.call(_boom)
    assert breaker.storage.state("t-recover") == STATE_OPEN
    assert breaker.call(lambda: "ok") == "ok"
    assert breaker.storage.state("t-recover") == STATE_CLOSED


def test_half_open_failure_reopens():
    breaker = get_breaker("t-reopen", fail_max=1, reset_timeout=0)
    with pytest.raises(ValueError):
        breaker.call(_boom)
    # half-open trial fails -> straight back to open
    with pytest.raises(ValueError):
        breaker.call(_boom)
    assert breaker.storage.state("t-reopen") == STATE_OPEN


def test_excluded_exception_does_not_trip():
    breaker = get_breaker("t-exclude", fail_max=1, reset_timeout=100, exclude=(KeyError,))

    def _key_error():
        raise KeyError("expected")

    with pytest.raises(KeyError):
        breaker.call(_key_error)
    assert breaker.storage.state("t-exclude") == STATE_CLOSED


def test_disabled_setting_passes_through(settings):
    settings.CIRCUIT_BREAKERS_ENABLED = False
    breaker = get_breaker("t-disabled", fail_max=1, reset_timeout=100)
    for _ in range(5):
        with pytest.raises(ValueError):
            breaker.call(_boom)
    # Breaker logic is bypassed entirely, so it never trips.
    assert breaker.storage.state("t-disabled") == STATE_CLOSED


def test_decorator_form_wraps_function():
    breaker = get_breaker("t-decorator", fail_max=1, reset_timeout=100)

    @breaker
    def flaky():
        raise ValueError("boom")

    with pytest.raises(ValueError):
        flaky()
    with pytest.raises(CircuitBreakerOpen):
        flaky()


def test_breakered_request_trips_and_translates_to_connection_error(monkeypatch):
    import requests

    from resilience.circuit import breakered_request

    calls = {"n": 0}

    def _fake_request(method, url, **kwargs):
        calls["n"] += 1
        raise requests.exceptions.ConnectionError("down")

    monkeypatch.setattr(requests, "request", _fake_request)
    # Pre-register the breaker with a low threshold (breakered_request reuses
    # the cached instance by name).
    get_breaker("http-test", fail_max=2, reset_timeout=100)

    for _ in range(2):
        with pytest.raises(requests.exceptions.ConnectionError):
            breakered_request("GET", "http://x/", name="http-test")

    # Now open: the helper translates the open breaker to ConnectionError and
    # never calls the underlying requests.request.
    before = calls["n"]
    with pytest.raises(requests.exceptions.ConnectionError):
        breakered_request("GET", "http://x/", name="http-test")
    assert calls["n"] == before


def test_circuit_helper_returns_cached_breaker():
    from resilience.circuit import circuit

    assert circuit("c-helper") is get_breaker("c-helper")


def test_default_storage_builds_inmemory_when_redis_disabled(settings):
    from resilience.circuit import InMemoryStorage as _IM
    from resilience.circuit import default_storage

    settings.CIRCUIT_BREAKER_USE_REDIS = False
    reset_storage(None)  # force a rebuild on next access
    assert isinstance(default_storage(), _IM)


def test_event_str():
    from resilience.models import CircuitBreakerEvent

    event = CircuitBreakerEvent(name="mqtt", from_state=STATE_CLOSED, to_state=STATE_OPEN)
    assert "mqtt" in str(event)
    assert STATE_OPEN in str(event)


class _FakeRedis:
    """Minimal dict-backed stand-in for redis.Redis."""

    def __init__(self):
        self.kv = {}

    def get(self, key):
        return self.kv.get(key)

    def set(self, key, value):
        self.kv[key] = value if isinstance(value, str) else str(value)

    def delete(self, key):
        self.kv.pop(key, None)

    def incr(self, key):
        self.kv[key] = int(self.kv.get(key, 0)) + 1
        return self.kv[key]

    def expire(self, key, seconds):
        return True

    def mget(self, keys):
        return [self.kv.get(key) for key in keys]

    def scan_iter(self, match=None, count=None):
        return iter(fnmatch.filter(list(self.kv), match) if match else list(self.kv))


class _BrokenRedis:
    """Every operation raises — models a Redis outage."""

    def _down(self, *args, **kwargs):
        raise RuntimeError("redis down")

    get = set = delete = incr = expire = mget = scan_iter = _down


class TestRedisStorage:
    def test_full_lifecycle(self):
        storage = RedisStorage(_FakeRedis())
        assert storage.state("x") == STATE_CLOSED
        assert storage.record_failure("x", 60) == 1
        assert storage.record_failure("x", 60) == 2
        storage.trip_open("x")
        assert storage.state("x") == STATE_OPEN
        assert storage.opened_at("x") > 0
        storage.to_half_open("x")
        assert storage.state("x") == STATE_HALF_OPEN
        storage.close("x")
        assert storage.state("x") == STATE_CLOSED
        assert storage.opened_at("x") == 0.0

    def test_degrades_gracefully_when_redis_is_down(self):
        storage = RedisStorage(_BrokenRedis())
        # Reads report CLOSED, record_failure reports 0 (never self-trips),
        # and writes swallow the error instead of bubbling up.
        assert storage.state("x") == STATE_CLOSED
        assert storage.record_failure("x", 60) == 0
        assert storage.opened_at("x") == 0.0
        assert storage.states("webhook:") == {}
        storage.trip_open("x")
        storage.to_half_open("x")
        storage.close("x")

    def test_states_enumerates_a_prefix_family(self):
        storage = RedisStorage(_FakeRedis())
        storage.trip_open("webhook:1")
        storage.to_half_open("webhook:2")
        storage.close("webhook:3")
        storage.trip_open("mqtt")

        assert storage.states("webhook:") == {
            "webhook:1": STATE_OPEN,
            "webhook:2": STATE_HALF_OPEN,
            "webhook:3": STATE_CLOSED,
        }
        # An empty prefix enumerates everything; unrelated breakers are not
        # swept into a family.
        assert storage.states()["mqtt"] == STATE_OPEN

    def test_states_uses_scan_not_keys(self):
        """Prod Redis is shared — KEYS would block the whole server."""
        client = _FakeRedis()
        client.keys = lambda *a, **kw: pytest.fail("RedisStorage must not call KEYS")
        storage = RedisStorage(client)
        storage.trip_open("webhook:1")
        assert storage.states("webhook:") == {"webhook:1": STATE_OPEN}

    def test_states_ignores_non_state_keys(self):
        storage = RedisStorage(_FakeRedis())
        # record_failure writes cb:webhook:9:failures; trip_open also writes
        # cb:webhook:9:opened_at. Neither is a state key.
        storage.record_failure("webhook:9", 60)
        assert storage.states("webhook:") == {}
        storage.trip_open("webhook:9")
        assert storage.states("webhook:") == {"webhook:9": STATE_OPEN}

    def test_escape_glob_keeps_a_literal_prefix_literal(self):
        assert RedisStorage._escape_glob("webhook:") == "webhook:"
        assert RedisStorage._escape_glob("odd*name[") == "odd\\*name\\["


def test_in_memory_states_filters_by_prefix():
    storage = InMemoryStorage()
    storage.trip_open("webhook:1")
    storage.close("webhook:2")
    storage.trip_open("mqtt")

    assert storage.states("webhook:") == {"webhook:1": STATE_OPEN, "webhook:2": STATE_CLOSED}
    assert storage.states("mqtt") == {"mqtt": STATE_OPEN}
    assert storage.states("nothing-here:") == {}


def test_base_storage_states_is_abstract():
    from resilience.circuit import BaseStorage

    with pytest.raises(NotImplementedError):
        BaseStorage().states("x")


def test_breaker_trips_over_redis_storage():
    breaker = CircuitBreaker(
        "redis-trip", fail_max=2, reset_timeout=100, storage=RedisStorage(_FakeRedis())
    )
    for _ in range(2):
        with pytest.raises(ValueError):
            breaker.call(_boom)
    with pytest.raises(CircuitBreakerOpen):
        breaker.call(_boom)
