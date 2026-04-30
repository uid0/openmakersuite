# API Error Contract (AC-7)

Every error response from the OpenMakerSuite API uses a single envelope. Frontend, kiosk, mobile, integration, and webhook clients can switch on `error.code` and surface `error.message` to the user without touching `error.details`.

## Shape

```json
{
  "error": {
    "code": "validation_failed",
    "message": "One or more fields failed validation.",
    "details": {
      "email": ["Enter a valid email address."]
    }
  }
}
```

| Key             | Type                       | Notes                                                                                                            |
| --------------- | -------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `error.code`    | string (stable identifier) | One of the codes in the table below. Safe to switch on; will not change without a deprecation window.            |
| `error.message` | string                     | Single user-safe sentence. Frontends may display this directly. Localization happens client-side.                |
| `error.details` | object \| array \| null    | Optional. Machine-readable hints. Shape varies by code (see below). Absent when there is no extra context.       |

## Codes

| HTTP | `code`                    | Meaning                                                                                  | `details`                                                              |
| ---- | ------------------------- | ---------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| 400  | `validation_failed`       | Serializer or model validation rejected the payload.                                     | Map of `field → [messages]`. May include `non_field_errors`.           |
| 400  | `parse_error`             | Request body could not be parsed (malformed JSON, wrong content type at the parser).     | `null` or parser-specific.                                             |
| 401  | `not_authenticated`       | Endpoint requires authentication and the client supplied none.                           | `null`.                                                                |
| 401  | `authentication_failed`   | Credentials were supplied but rejected (bad token, expired session).                     | `null`.                                                                |
| 403  | `permission_denied`       | Authenticated client lacks permission for this action.                                   | `null`.                                                                |
| 404  | `not_found`               | Resource does not exist or is not visible to the requester.                              | `null`.                                                                |
| 405  | `method_not_allowed`      | HTTP method not supported on this URL.                                                   | `null`.                                                                |
| 409  | `conflict`                | Request conflicts with current resource state (locked record, duplicate submission).     | Endpoint-specific.                                                     |
| 415  | `unsupported_media_type`  | `Content-Type` is not handled by this endpoint.                                          | `null`.                                                                |
| 429  | `throttled`               | Rate limit exceeded.                                                                     | `{"retry_after_seconds": int}`.                                        |
| 503  | `dependency_unavailable`  | A required dependency (broker, MQTT, email gateway, downstream webhook) is unreachable.  | `{"dependency": "redis" \| "celery" \| ...}` (endpoint may extend it). |
| 202  | `task_queued`             | Endpoint accepted the request and handed work off to Celery.                             | `{"task_id": "..."}` plus optional `eta`, `queue`.                     |
| 500  | `server_error`            | Unhandled server error. Reported to Sentry; client should retry idempotent requests.     | `null`.                                                                |

## Implementation

The envelope is produced by `config.api_errors.standardized_exception_handler`, wired into `REST_FRAMEWORK["EXCEPTION_HANDLER"]` in `backend/config/settings.py`. Any DRF `APIException` subclass routes through it automatically. Bare `django.core.exceptions.ValidationError` is translated to the envelope as well, so model `clean()` paths stay consistent.

For non-exception flows (e.g. bulk imports that need to return one envelope at the end of a partial-success run), use `error_response(code, message=..., details=..., status_code=...)`.

For dependency or async flows, raise the typed exceptions:

```python
from config.api_errors import DependencyUnavailable, TaskQueued, Conflict

if not broker_reachable():
    raise DependencyUnavailable(detail={"dependency": "celery"})

task = enqueue_long_running_job.delay(...)
raise TaskQueued(detail={"task_id": task.id})
```

## Backwards compatibility

Existing endpoints that return their own ad-hoc shapes (e.g. `Response({"detail": "..."}, status=400)`) are unaffected — only **raised** exceptions hit the standardized handler. Migrations from ad-hoc shapes to the envelope are scoped per endpoint and tracked alongside the owning AC.

## Tests

`backend/config/tests/test_api_errors.py` mounts a throwaway router and exercises every code in the table above end-to-end through DRF's dispatch path (URL routing → permission check → throttle → action → exception handler). Adding a new code requires extending both the constants in `config.api_errors.ErrorCode` and a test in this file.
