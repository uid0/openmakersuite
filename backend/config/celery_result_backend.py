"""Celery result backend that scrubs traceback + result payloads (gh #378).

Wraps ``django_celery_results.backends.database.DatabaseBackend`` so the
``TaskResult.traceback`` text and the encoded ``result`` payload are
scrubbed by ``config.observability_redaction.redact`` before they reach
the database.

## Why this exists

Celery's default behavior is to capture the full Python traceback as a
string and store it on the result row. Tracebacks routinely include:

- The args/kwargs the task was invoked with (e.g. webhook URLs that
  embed signing keys, headers dicts that include ``Authorization``).
- Stringified exceptions whose ``.args[0]`` quotes upstream response
  bodies, ``Bearer …`` headers, or PEM blocks.
- Local-variable repr fragments (when the traceback is
  ``traceback.format_exc()``-style with ``--tb=long``).

Operators legitimately need access to that admin row to debug failures,
but the credentials inside it should not survive the round-trip. The
redactor's value-shape pass strips the secret-shaped substrings while
keeping the surrounding context intact, so the admin still sees "the
upstream returned 401 with body X" without the row also containing the
token that was rejected.

## What gets redacted

- ``traceback`` (string) — every value-shape pattern (``Bearer …``,
  JWT, PEM, high-entropy 32+ char tokens) is rewritten.
- ``result`` (only when it's a Mapping or list) — recursive redaction
  using the same dict/list traversal as `redact()`. Exceptions are
  encoded into a ``{exc_type, exc_message, exc_module}`` dict by
  ``Backend.store_result`` before they reach ``_store_result``, so
  this branch covers the failure case too.
- Successful task results that are JSON-serialisable dicts/lists also
  flow through the same redactor — a task that returns a webhook
  payload echoing a token would otherwise persist that token in
  ``TaskResult.result``.

## What doesn't change

- The ``status`` enum, ``task_id``, ``request`` metadata, and timestamps
  are untouched.
- Result encoding / content-type negotiation is delegated to the
  parent class — we only mutate the payload before super sees it.
"""

from __future__ import annotations

from typing import Any, Mapping

from django_celery_results.backends.database import DatabaseBackend

from .observability_redaction import redact


def _redact_traceback(traceback: Any) -> Any:
    if traceback is None:
        return None
    if isinstance(traceback, str):
        return redact(traceback)
    return traceback


def _redact_result(result: Any) -> Any:
    # Exceptions are passed through as-is here because ``Backend.store_result``
    # converts them to the ``prepare_exception`` dict before _store_result is
    # called. Mappings + sequences flow through the recursive redactor;
    # primitives (None, bool, int, float, datetime) are returned unchanged.
    if isinstance(result, (Mapping, list, tuple, str)):
        return redact(result)
    return result


class RedactingDatabaseBackend(DatabaseBackend):
    """Drop-in replacement for the django-db result backend that scrubs
    secrets out of the stored traceback and result payload (gh #378).
    """

    def _store_result(
        self,
        task_id,
        result,
        status,
        traceback=None,
        request=None,
        using=None,
    ):
        return super()._store_result(
            task_id,
            _redact_result(result),
            status,
            traceback=_redact_traceback(traceback),
            request=request,
            using=using,
        )
