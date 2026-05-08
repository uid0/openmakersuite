# Observability privacy posture (AC-27)

OpenMakerSuite emits operator-visible observability data through several
surfaces:

| Surface | Producer | Consumer |
|---------|----------|----------|
| Django request logs | gunicorn + nginx | Container stdout, Highlight |
| DRF error envelopes | `config.api_errors.standardized_exception_handler` | API clients, frontend toasts |
| Celery task results | `django_celery_results` | Django admin (`/admin/django_celery_results/`) |
| Webhook delivery audit | `WebHook.failure_count` + `last_error` | Admin webhook detail view |
| Forgekey device logs | `forgekey/management/commands/mqtt_consumer.py` | Container stdout, Highlight |
| Frontend error reporting | `@highlight-run/react` (when `HIGHLIGHT_PROJECT_ID` set) | Highlight |

## Redaction policy

All payloads that traverse one of these surfaces are scrubbed by
`config.observability_redaction.redact()` before emission. The redactor
applies two passes:

### 1. Sensitive key names (default-deny)

Any dict key whose name matches one of these patterns is replaced with
`***REDACTED***` regardless of value type:

- `token` (anywhere in the key)
- `secret`
- `password` / `passwd` / `pwd`
- `api_key` / `api-key`
- `signing_key`
- `private_key`
- `signature`
- `csrf`
- `session_key`
- `bearer`
- Whole-key match: `Authorization`, `X-Authorization`, `Cookie`, `Set-Cookie`

This is a default-deny policy — operators add a non-sensitive sibling
field when they need value-level visibility (e.g. log
`token_fingerprint` instead of `token`).

### 2. Sensitive value shapes

Even when a key name is innocuous, the value is scanned for known
secret shapes and matching substrings are replaced with
`***REDACTED***`:

- `Bearer <token>` (auth header values)
- JWTs (`eyJ...` triple-segment base64url)
- PEM private key blocks (`-----BEGIN ... PRIVATE KEY-----`)
- High-entropy hex/base64 tokens (32+ chars)

This catches secrets that escape the key-name pass via generic field
names like `payload`, `headers`, or stringified `message`.

## What does NOT get scrubbed

The redactor is intentionally conservative — it does not remove member
identifiers (usernames, email addresses, donor names) because these are
operationally necessary for incident response. The audit-trail data
layer (gh #352–#358) records actor identity by design.

If a member-data scrubbing pass becomes necessary (e.g. for export to
third-party telemetry), build a second helper —
`redact_member_pii()` — that runs in addition to `redact()` for the
specific surfaces that cross trust boundaries.

## Wiring

All four named surfaces now route through the redactor (gh #378):

- `config.api_errors.standardized_exception_handler` — DRF error
  envelope `details` payload (gh #333).
- `reorder_queue.models.WebHook.record_failure` — `last_error` is
  scrubbed before storage; the calling task in
  `reorder_queue.tasks.send_webhook_notification` also redacts the
  exception text before logging or returning it.
- `config.celery_result_backend.RedactingDatabaseBackend` — the
  configured `CELERY_RESULT_BACKEND` (production only). Wraps the
  `django_celery_results` `DatabaseBackend` so traceback strings and
  result payloads are scrubbed before `TaskResult` rows are written.
  `_redact_traceback` runs the value-shape pass; `_redact_result`
  runs full recursive redaction for Mappings/lists, including the
  `prepare_exception` dict (`exc_type`, `exc_message`,
  `exc_module`) for failed tasks.
- `forgekey.management.commands.mqtt_consumer.handle_occupancy_message`
  — `OccupancyEvent.raw_payload` is scrubbed before persistence;
  `handle_ota_status_message` scrubs the device-supplied
  `error_message` before `DeviceFirmwareUpdate.error_message` is
  written.
- `frontend/src/utils/highlight.ts` — `initHighlight` configures
  `networkHeadersToRedact` (Authorization, Cookie, X-CSRFToken,
  X-Webhook-Signature, X-Forgekey-Token, etc.) and
  `networkBodyKeysToRedact` (token, password, secret, etc.) on the
  Highlight client, plus a `requestResponseSanitizer` that runs the
  value-shape pass over residual string bodies. The
  `frontend/src/utils/redact.ts` module mirrors the backend's
  value-shape regex set so React `ErrorFallback` can scrub
  `Error.message` + stack via `redactError(error)` before
  `H.consumeError` ships the report.

## Tests pinning the rollout

- `backend/config/tests/test_celery_result_backend.py` — traceback +
  result redaction unit tests.
- `backend/reorder_queue/tests/test_webhooks.py::test_record_failure_redacts_*`
  — webhook `last_error` redaction.
- `backend/forgekey/tests/test_mqtt_consumer.py::test_handle_occupancy_message_redacts_secret_shaped_payload_keys`
  — MQTT consumer payload scrubbing.
- `frontend/src/__tests__/utils/redact.test.ts` — frontend value-shape
  + `redactError` unit tests.

## Operator obligation

When adding a new logging or telemetry call, treat the payload as
untrusted: route it through `redact()` before `logger.info(...)` /
`extra={...}` / structured-event emission. Tests for new audit and
error code paths should assert that representative secret values are
scrubbed before they reach the response/log.
