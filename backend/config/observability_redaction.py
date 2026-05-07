"""Observability redaction utility (gh #333 / AC-27).

Centralized helper for scrubbing secrets and PII from log payloads,
error envelopes, and other observability surfaces before they leave
the process.

## Principles

1. **Default-deny on key names.** Any dict key matching a known
   sensitive pattern (``token``, ``secret``, ``password``,
   ``api_key``, ``signature``, ``private_key``, ``csrf``, etc.) is
   redacted regardless of its value type. We err on the side of
   over-redaction — operators can always re-emit a debug field with
   a non-sensitive name when they need value-level visibility.
2. **Value-shape scrubbing.** Some values carry secrets even when
   the key name is innocuous (e.g. a ``message`` field that contains
   a stringified JWT or PEM). The value pass redacts substrings
   matching known secret shapes.
3. **Recursive.** Redaction descends into nested dicts and lists so
   audit metadata, exception details, and structured-log payloads
   are scrubbed all the way down.
4. **Stable output shape.** The redactor never adds, removes, or
   reorders keys — only the values change. This keeps downstream
   parsers and dashboards honest.

## Usage

```python
from config.observability_redaction import redact

logger.error(
    "webhook delivery failed",
    extra=redact({"webhook_id": ..., "url": ..., "headers": req.headers}),
)
```

The module is intentionally dependency-free (pure stdlib) so it can
be imported from any layer — middleware, signal handlers, Celery
tasks — without circular-import risk.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Pattern, Sequence

REDACTED = "***REDACTED***"


# Case-insensitive substring patterns matched against dict keys.
# Order matters only for human readability; matching is OR.
_KEY_PATTERNS: tuple[Pattern[str], ...] = (
    re.compile(r"token", re.IGNORECASE),
    re.compile(r"secret", re.IGNORECASE),
    re.compile(r"password|passwd|pwd", re.IGNORECASE),
    re.compile(r"api[_-]?key", re.IGNORECASE),
    re.compile(r"signing[_-]?key", re.IGNORECASE),
    re.compile(r"private[_-]?key", re.IGNORECASE),
    re.compile(r"signature", re.IGNORECASE),
    re.compile(r"csrf", re.IGNORECASE),
    re.compile(r"session[_-]?key", re.IGNORECASE),
    re.compile(r"bearer", re.IGNORECASE),
    re.compile(r"^(authorization|x-authorization|cookie|set-cookie)$", re.IGNORECASE),
)


# Patterns that match secret shapes anywhere in a string value.
# These run AFTER the key-based pass and replace the matched substring
# with REDACTED. They intentionally do NOT match short low-entropy
# values to avoid scrubbing legitimate words.
_VALUE_PATTERNS: tuple[Pattern[str], ...] = (
    # Bearer + JWT-shape token (any auth header value)
    re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]{20,}=*", re.IGNORECASE),
    # Generic JWT (three base64url segments separated by dots)
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    # PEM private key block (multiline)
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
    # Generic high-entropy hex / base64 token (>= 32 chars). Catches
    # API keys + signed tokens that escape the key-name pass via
    # generic field names like ``payload`` or ``headers``.
    re.compile(r"\b[A-Za-z0-9+/]{32,}={0,2}\b"),
)


def _key_is_sensitive(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    return any(p.search(key) for p in _KEY_PATTERNS)


def _scrub_value(value: str) -> str:
    """Apply value-shape patterns to a string. Returns the (possibly
    rewritten) string; substrings matching any pattern become REDACTED.
    """
    for pattern in _VALUE_PATTERNS:
        value = pattern.sub(REDACTED, value)
    return value


def redact(payload: Any) -> Any:
    """Return a redacted copy of ``payload`` safe for logs.

    - dicts: recursed; sensitive keys → REDACTED, other values
      recursively redacted.
    - lists / tuples: each element redacted, container type preserved.
    - strings: value-shape patterns applied.
    - other scalars (numbers, bools, None, datetime, UUID, etc.):
      returned as-is.

    The function is pure: ``payload`` is not mutated; the returned
    structure is a new object hierarchy where any non-scalar
    container has been rebuilt.
    """
    if isinstance(payload, Mapping):
        return {
            key: REDACTED if _key_is_sensitive(key) else redact(value)
            for key, value in payload.items()
        }
    if isinstance(payload, (list, tuple)):
        rebuilt: Iterable[Any] = (redact(item) for item in payload)
        return type(payload)(rebuilt) if isinstance(payload, tuple) else list(rebuilt)
    if isinstance(payload, (set, frozenset)):
        # Sets can't contain unhashable redacted dicts; redact the
        # member representations as strings to avoid crashing.
        return type(payload)(redact(item) if isinstance(item, str) else item for item in payload)
    if isinstance(payload, str):
        return _scrub_value(payload)
    return payload


def is_sensitive_key(key: Any) -> bool:
    """Public alias of the key check — useful for callers that want to
    integrate redaction into a custom log formatter without using the
    full ``redact`` traversal.
    """
    return _key_is_sensitive(key)


__all__: Sequence[str] = (
    "REDACTED",
    "redact",
    "is_sensitive_key",
)
