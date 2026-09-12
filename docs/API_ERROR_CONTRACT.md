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
| `error.code`    | string (stable identifier) | One of the codes in the table below, or an [endpoint-specific code](#endpoint-specific-codes). Safe to switch on; will not change without a deprecation window. |
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

### Endpoint-specific codes

The table above is every code the exception handler can produce: `_classify` resolves an exception to one of those and nothing else. `error_response()` emits the code it is handed, so an endpoint may answer with a narrower one where the generic code would say less than the endpoint knows. Such a code belongs to its endpoint rather than to `ErrorCode`, and a client that does not recognise one must fall back to `error.message`, which carries the same sentence either way.

| HTTP | `code`          | Endpoint                                                     | Meaning                                                                                        | `details` |
| ---- | --------------- | ------------------------------------------------------------ | ----------------------------------------------------------------------------------------------- | --------- |
| 400  | `no_line_items` | `POST /api/reorders/purchase-orders/{id}/send_to_supplier/` | The order carries no line item to order, so it may not go to the supplier. Add one and retry.  | Absent.   |
| 400  | `not_draft`     | `POST /api/reorders/purchase-orders/{id}/send_to_supplier/` | The order is not a draft; only a draft can be sent. The message names the order and its status. | Absent.   |
| 400  | `not_draft`     | `POST /api/reorders/purchase-orders/{id}/items/`            | Lines may only be added while the order is a draft. The message names the order and its status. | Absent.   |
| 400  | `not_found`     | `POST /api/reorders/purchase-orders/{id}/items/`            | The named `item_supplier` or `asset` does not exist.                                            | Absent.   |
| 400  | `supplier_mismatch` | `POST /api/reorders/purchase-orders/{id}/items/`        | The row (or the asset's manufacturer) belongs to a different supplier than this order's.        | Absent.   |
| 400  | `not_supplied`  | `POST /api/reorders/purchase-orders/{id}/items/`            | The identifier names an item this order's supplier does not supply at all.                      | Absent.   |
| 400  | `discontinued`  | `POST /api/reorders/purchase-orders/{id}/items/`            | The supplier supplied the item once and no longer does.                                         | Absent.   |
| 400  | `multiple_unavailable` | `POST /api/reorders/purchase-orders/{id}/items/`     | The identifier matches several items, unavailable for a mix of the two reasons above.           | Absent.   |
| 400  | `no_match`      | `POST /api/reorders/purchase-orders/{id}/items/`            | The identifier matches nothing this supplier carries.                                           | Absent.   |
| 400  | `empty_identifier` | `POST /api/reorders/purchase-orders/{id}/items/`         | The identifier was blank once trimmed, so there was nothing to resolve.                          | Absent.   |
| 400  | `line_voided`   | `POST /api/reorders/purchase-orders/{id}/items/`            | The order already carries a VOIDED line for this target; a void is not silently resurrected.     | Absent.   |
| 400  | `price_conflict` | `POST /api/reorders/purchase-orders/{id}/items/`           | Growing an existing asset/freeform line at a different `unit_cost` than it already carries.      | Absent.   |
| 400  | `no_unit_cost`  | `POST /api/reorders/purchase-orders/{id}/items/`            | No price was supplied and none can be derived (asset and freeform lines have no price to fall back on). | Absent. |
| 400  | `invalid_quantity` / `invalid_unit_cost` / `invalid_description` | `POST /api/reorders/purchase-orders/{id}/items/` | A supplied value is not a usable quantity, price, or description.               | Absent.   |
| 400  | `work_order_conflict` / `owning_group_conflict` | `POST /api/reorders/purchase-orders/{id}/items/` | The existing line already carries a different work order / committee than the request names. | Absent. |
| 400  | `work_order_not_found` / `group_not_found` | `POST /api/reorders/purchase-orders/{id}/items/` | The line-level `work_order` / `owning_group` id does not resolve.                  | Absent.   |
| 409  | `ambiguous`     | `POST /api/reorders/purchase-orders/{id}/items/`            | The identifier matches several items this supplier carries; the server will not pick one.       | `{"candidates": [...]}` — the choice set, strongest match first, capped at 20. |
| 400  | `not_draft`     | `DELETE /api/reorders/purchase-orders/{id}/items/{item_id}/` | The supplier holds a copy (or the order is closed); the message names void as the alternative.  | Absent.   |
| 400  | `line_received` | `DELETE /api/reorders/purchase-orders/{id}/items/{item_id}/` | The line records a received quantity, so it is not destroyed. The message names the quantity.   | Absent.   |
| 503  | `weather_not_configured` | `GET /api/screens/weather/current/`               | `OPENWEATHER_API_KEY` or the location settings are unset. Operator-fixable; the message says which. | Absent. |
| 502  | `weather_upstream` | `GET /api/screens/weather/current/`                     | OpenWeather answered with a non-2xx.                                                            | Absent.   |
| 502  | `weather_unreachable` | `GET /api/screens/weather/current/`                  | OpenWeather could not be reached at all.                                                        | Absent.   |

The same "no line items" refusal on `PATCH /api/reorders/purchase-orders/{id}/` — where setting `status` to `sent`, or attaching a `sales_order_number` to a draft, also sends the order — is **raised** rather than composed, so it arrives under the generic `validation_failed` with that same sentence in `error.message`. A client must not key this refusal on the code alone.

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

Two ad-hoc shapes have existed here, and only one of them is a silent drift risk:

- `Response({"error": "<prose>"})` — a bare sentence, no code. A client that only knows the envelope falls back to `error.message`, finds nothing, and shows whatever it has. Most unconverted endpoints are this one.
- `Response({"error": "<prose>", "code": "<code>"})` — the envelope's own two fields, one level too shallow. This is the worse shape, because the endpoint HAS composed a branchable code and an operator-facing sentence and the client still cannot read either: `error` is a string where a client expects an object, so `error.code` and `error.message` both come back empty and the whole raw body ends up in front of the operator.

No endpoint returns the second shape any more. The line endpoints and the kiosk weather proxy in the table above were its last users; the codes came across unchanged, so a client already branching on `not_draft` or `weather_not_configured` still finds it — one level in, at `error.code`.

## Tests

`backend/config/tests/test_api_errors.py` mounts a throwaway router and exercises every code in the [Codes](#codes) table end-to-end through DRF's dispatch path (URL routing → permission check → throttle → action → exception handler). Adding a code to that table requires extending both the constants in `config.api_errors.ErrorCode` and a test in this file.

An [endpoint-specific code](#endpoint-specific-codes) is pinned by the owning endpoint's own tests instead — `reorder_queue/tests/test_send_requires_lines.py` for the send codes, `reorder_queue/tests/test_po_line_error_envelope.py` for the line endpoints' wire shape, `screens/tests/test_weather.py` for the kiosk proxy — and must be added to that table so a client can find it.
