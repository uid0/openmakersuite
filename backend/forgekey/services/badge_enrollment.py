"""Badge enrollment: arm "enroll next scan" and capture a badge UID (op-vj9).

Staff arm enrollment for a target user, optionally scoped to a specific reader.
The next access-request UID the interlock sees is bound to that user's
``badge_number`` instead of being authorized. State lives in the Django cache
(django-redis in prod) so the web process that arms enrollment and the MQTT
consumer that captures the scan share it across the cluster — exactly the
pattern the consumer's log rate-limiter already relies on.

The cache holds two short-lived records per enrollment:

* an *armed* record (``arm`` → ``consume``) telling the interlock to capture the
  next scan for a user instead of authorizing it, and
* a *result* record (``record_capture`` → ``poll_result``) so the arming UI can
  poll for "the member tapped; here's the captured UID".
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from django.core.cache import cache

# How long an armed enrollment waits for the member to tap before it expires.
# Long enough to walk to the reader, short enough that a forgotten arm doesn't
# silently capture an unrelated scan an hour later.
ENROLL_TTL_SECONDS = 120
# How long a captured result stays pollable by the arming UI.
RESULT_TTL_SECONDS = 120

_ARM_PREFIX = "forgekey:badge_enroll:arm:"
_RESULT_PREFIX = "forgekey:badge_enroll:result:"
_GLOBAL_SCOPE = "__global__"


def _scope(reader_id: Any = None, mac: Any = None) -> str:
    """Cache scope for an enrollment: a reader id, else a device MAC, else global."""
    value = reader_id or mac
    return str(value) if value else _GLOBAL_SCOPE


def _arm_key(reader_id: Any = None, mac: Any = None) -> str:
    return f"{_ARM_PREFIX}{_scope(reader_id, mac)}"


def _result_key(user_id: Any) -> str:
    return f"{_RESULT_PREFIX}{user_id}"


def arm(
    user_id: Any,
    *,
    reader_id: Any = None,
    ttl: int = ENROLL_TTL_SECONDS,
) -> Dict[str, Any]:
    """Arm "enroll next scan" for ``user_id``, optionally scoped to a reader.

    With no ``reader_id`` the enrollment is global: the next scan on any reader
    is captured. Returns the stored record.
    """
    record = {"user_id": user_id, "reader_id": reader_id}
    cache.set(_arm_key(reader_id=reader_id), record, timeout=ttl)
    return record


def cancel(*, reader_id: Any = None) -> None:
    """Disarm a pending enrollment for the given scope (or the global one)."""
    cache.delete(_arm_key(reader_id=reader_id))


def is_armed(*, reader_id: Any = None) -> Optional[Dict[str, Any]]:
    """Return the armed record for a scope (falling back to global), or None."""
    record = cache.get(_arm_key(reader_id=reader_id))
    if record is None and reader_id:
        record = cache.get(_arm_key())
    return record


def consume(*, reader_id: Any = None, mac: Any = None) -> Optional[Dict[str, Any]]:
    """Pop the pending enrollment matching this scan, if any.

    Checks the reader/MAC-scoped record first, then the global record, deleting
    whichever matched so a single arm captures exactly one scan. Returns the
    armed record (carrying ``user_id``) or None when nothing is armed.
    """
    scoped_key = _arm_key(reader_id=reader_id, mac=mac)
    record = cache.get(scoped_key)
    if record is not None:
        cache.delete(scoped_key)
        return record
    if reader_id or mac:
        global_key = _arm_key()
        record = cache.get(global_key)
        if record is not None:
            cache.delete(global_key)
            return record
    return None


def record_capture(user_id: Any, badge_number: str) -> None:
    """Stash a completed capture so the arming UI can poll for the result."""
    cache.set(
        _result_key(user_id),
        {"user_id": user_id, "badge_number": badge_number},
        timeout=RESULT_TTL_SECONDS,
    )


def poll_result(user_id: Any) -> Optional[Dict[str, Any]]:
    """Return (and clear) a captured-badge result for ``user_id``, if present."""
    key = _result_key(user_id)
    record = cache.get(key)
    if record is not None:
        cache.delete(key)
    return record


__all__ = [
    "ENROLL_TTL_SECONDS",
    "arm",
    "cancel",
    "is_armed",
    "consume",
    "record_capture",
    "poll_result",
]
