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

Reference call sites that already route through the redactor:

- `config.api_errors.standardized_exception_handler` — DRF error
  envelope `details` payload.

Surfaces that should adopt `redact()` next (tracked as follow-ups):

- Webhook delivery failure logging (`WebHook.record_failure` /
  `last_error` field).
- Celery task result `traceback` storage (sanitize before
  `django_celery_results` writes the row).
- ForgeKey MQTT consumer logger when an inbound message contains a
  user payload.
- Frontend error capture (Highlight integration) — sanitize React
  error context before it ships to the collector.

## Operator obligation

When adding a new logging or telemetry call, treat the payload as
untrusted: route it through `redact()` before `logger.info(...)` /
`extra={...}` / structured-event emission. Tests for new audit and
error code paths should assert that representative secret values are
scrubbed before they reach the response/log.
