# API List Contract (AC-8, AC-9)

Every high-volume list endpoint follows the same external contract for pagination, ordering, and filtering. Frontend, kiosk, and integration clients can paginate any endpoint with the same code.

## Pagination

All paginated list endpoints return DRF's standard `PageNumberPagination` envelope:

```json
{
  "count": 137,
  "next": "https://.../api/inventory/items/?page=3",
  "previous": "https://.../api/inventory/items/?page=1",
  "results": [...]
}
```

| Setting           | Value                  | Notes                                                                |
| ----------------- | ---------------------- | -------------------------------------------------------------------- |
| `PAGE_SIZE`       | 50                     | Configured globally in `REST_FRAMEWORK["PAGE_SIZE"]`.                |
| `?page=N`         | int                    | One-indexed. Out-of-range page returns `404` with the standard error envelope. |
| `?page_size=N`    | int (optional)         | Per-view if `PageNumberPagination.page_size_query_param` is enabled. |

Endpoints that intentionally return all rows in one response (e.g. `/api/inventory/locations/` because the location tree is small and the UI renders it as a hierarchy) are documented as **unpaginated** in the table below. Their list response is a bare JSON array, not the envelope.

## Critical list endpoints

| Endpoint                                     | ViewSet                       | Paginated | Default order  | Documented filters                                                                                                                |
| -------------------------------------------- | ----------------------------- | --------- | -------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `GET /api/inventory/items/`                  | `InventoryItemViewSet`        | yes       | `name`         | `category`, `location`, `search`, `low_stock`, `is_active`                                                                        |
| `GET /api/inventory/assets/`                 | `AssetViewSet`                | yes       | `name`         | `category`, `location`, `status`, `inventory_item`, `manufacturer`, `owning_group`, `date_received_after`, `date_received_before`, `age_min_days`, `age_max_days`, `search`, `is_active`, `ordering` (whitelist) |
| `GET /api/inventory/locations/`              | `LocationViewSet`             | no        | `name`         | `search` (anonymous users see only `is_active=True`)                                                                              |
| `GET /api/inventory/suppliers/`              | `SupplierViewSet`             | yes       | model default  | none (paginated browse)                                                                                                           |
| `GET /api/reorders/requests/`                | `ReorderRequestViewSet`       | yes       | `-requested_at`| status / priority filters in `get_queryset`                                                                                       |
| `GET /api/reorders/purchase-orders/`         | `PurchaseOrderViewSet`        | yes       | `-created_at`  | status / supplier filters                                                                                                         |
| `GET /api/maintenance-orders/work-orders/`   | `ThirdPartyWorkOrderViewSet`  | yes       | `-created_at`  | status / vendor / asset filters                                                                                                   |
| `GET /api/donations/donations/`              | `DonationViewSet`             | yes       | `-created_at`  | `status` filter                                                                                                                   |
| `GET /api/screens/screens/`                  | `ScreenViewSet`               | yes       | `name`         | none documented                                                                                                                   |
| `GET /api/forgekey/devices/`                 | `ESP32DeviceViewSet`          | yes       | `serial_number`| status / asset / lockout filters                                                                                                  |
| `GET /api/notifications/`                    | `NotificationViewSet`         | yes       | `-created_at`  | `is_read`, `kind` filters                                                                                                         |

For ViewSets with a hand-rolled `ordering` query param (currently only `AssetViewSet`), the **whitelist** is enforced server-side — unknown ordering values fall back to the default. This is asserted by `test_ordering_whitelist_rejects_unknown_field` in `backend/config/tests/test_list_contract.py`.

## Ordering

The default DRF page envelope guarantees a stable order on subsequent pages. Where a ViewSet does not set an explicit `order_by()`, the model's `Meta.ordering` (or the implicit primary-key order) applies. New endpoints **must** set an explicit default to avoid client-visible flicker.

When a ViewSet exposes user-controllable ordering, it does so via `?ordering=<field>` and validates the value against an explicit allow-list — never against `request.query_params.get("ordering")` directly. See `AssetViewSet.get_queryset()` for the pattern.

## Filtering

Filtering today is hand-rolled inside each `get_queryset()`. The conventions are:

- Use existing query-string param names (`category`, `location`, `status`, `search`) so the frontend can share helpers.
- `?search=...` performs a case-insensitive `OR` over the human-meaningful fields for the resource (name, description, codes).
- Boolean filters accept `true`/`false` (case-insensitive). Anything else is treated as "filter not specified".
- Date range filters accept ISO-8601 strings. Invalid values silently drop the filter rather than returning 400 — this mirrors the legacy frontend's behavior; do not change it without coordinating UI work.

`django-filter` is intentionally not yet a dependency. If a viewset's filter list grows large enough that `get_queryset` becomes hard to read, prefer extracting a `FilterSet` or filter helper rather than continuing to nest conditionals.

## Query-count regression budget (AC-9)

`backend/config/tests/test_list_contract.py::TestQueryCountBounds` exercises critical list and detail endpoints with realistic related-row counts (5+ categories, 5+ locations, multiple suppliers per item, multiple parts per asset) and asserts an upper-bound query count.

The bounds are intentionally **upper bounds**, not targets. They reflect the current observed cost so any regression that increases query count fails CI. Tightening them — by moving `SerializerMethodField` loads into `prefetch_related`, by switching `_get_primary_supplier`-style calls to annotations, etc. — is welcomed; the right move is to lower the constant and let the test enforce the new ceiling.

| Endpoint                                | Current bound | Notes                                                                            |
| --------------------------------------- | ------------- | -------------------------------------------------------------------------------- |
| `GET /api/inventory/items/` (20 items)  | 260 queries   | Dominated by per-item `SerializerMethodField` calls into `ItemSupplier` and reorder lookups. |
| `GET /api/inventory/items/{id}/`        | 40 queries    | Detail serializer fetches per-supplier `PriceHistory`.                           |
| `GET /api/inventory/assets/` (15 items) | 90 queries    | Dominated by per-asset `groups_can_enable`, ForgeKey operational mode, and lockout lookups. |
| `GET /api/inventory/suppliers/` (10)    | 130 queries   | `SupplierSerializer` includes per-supplier `_get_*` helpers that re-query.       |

Tightening these counts is tracked separately — see follow-up bead `oms-0rc:n+1-tightening`.
