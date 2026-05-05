# API Permission Matrix (AC-2, AC-3)

This document classifies every public-facing OpenMakerSuite API
endpoint by access level so maintainers can prove anonymous QR flows
stay open while financial, safety, device, and administrative writes
stay protected.

It is the authoritative source for AC-2 (every endpoint is classified)
and AC-3 (each public endpoint lists its purpose, supported method,
expected payload, and abuse-control expectation).

## Access classes

| Class | Description |
| --- | --- |
| **public** | Unauthenticated access permitted by design. Used for QR scan workflows, kiosk readouts, donation receipts, public transparency surfaces, and health probes. |
| **member** | Requires an authenticated Django user (any active member). |
| **staff** | Requires an authenticated user in the Logistics or Staff group, or `is_staff=True`. |
| **admin** | Requires `is_staff=True` or `is_superuser=True` and is intended for administrative state changes. |
| **device-token** | Authentication via a device-specific token or signed payload (ForgeKey devices, MQTT bridge). |
| **webhook-secret** | Authentication via a shared secret carried in the request (typically a header or signed body). |

## Maintenance rules

- When a new endpoint is added, **also add it to this matrix**. PRs that
  add or change `permission_classes` without updating this file should
  be revised before merge.
- Public write endpoints **MUST** declare an abuse-control expectation
  (rate limit, dedupe key, IP throttle, captcha, etc.) per AC-6.
- The matrix lists endpoints by app. URL paths are relative to the
  `/api/` prefix unless otherwise noted.

### Drift detection (gh #328)

The machine-readable source of truth for the
`permission_classes` enforced by every URL-routed view lives in
[`backend/config/api_permission_matrix.yaml`](../backend/config/api_permission_matrix.yaml).
The pytest in `backend/config/tests/test_permission_matrix.py` introspects
the live URL conf and fails when the YAML drifts from code, so a PR that
changes a view's `permission_classes` cannot land without also refreshing
the snapshot.

```bash
cd backend && python manage.py check_permission_matrix          # verify
cd backend && python manage.py check_permission_matrix --write  # regenerate
```

The Markdown table below is the human-readable contract. Each row should
match the YAML; when they disagree, the YAML wins and this document is
fixed in the same PR.

## Health and infrastructure

| Method | Path | Class | Purpose | Abuse Control |
| --- | --- | --- | --- | --- |
| GET | `health/livez/` | public | Liveness probe — process up; no I/O performed. | Not required (read-only, no DB). |
| GET | `health/readyz/` | public | Readiness probe — DB, cache, broker reachable; reports failures. | Not required (cheap, idempotent). |
| GET | `dashboard/health/` | public | Legacy dashboard health view. | Not required. |
| GET | `schema/` | public | OpenAPI schema (drf-spectacular). | Not required. |
| GET | `docs/` | public | Swagger UI. | Not required. |

## Authentication and accounts (`/api/auth/`)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| POST | `auth/register/` | public | New user signup. **Abuse control:** required (account creation throttle). |
| POST | `auth/login/` | public | Issues session/token. **Abuse control:** required (login attempt throttle). |
| POST | `auth/logout/` | public | Destroys the Django session; safe no-op for anonymous callers. |
| POST | `auth/refresh/` | public | Refresh access token using a refresh token. **Abuse control:** required (refresh throttle). |
| POST | `auth/test-membership/` | public (DEBUG only) | Test fixture used by E2E suites. The view is `AllowAny` but returns 403 unless `settings.DEBUG` is true; production deployments MUST set `DEBUG=False`. |
| any | `auth/passkey/...` | public/member | Passkey (WebAuthn) registration and assertion flows. **Abuse control:** required on registration; assertion is signed. |

## Inventory and reporting (`/api/inventory/`)

The inventory app exposes the largest set of public scan/report
endpoints. Generic CRUD on items, locations, suppliers, and assets is
member/staff/admin; only the explicit scan and public-report actions
below are public.

| Method | Path | Class | Purpose | Abuse Control |
| --- | --- | --- | --- | --- |
| POST | `inventory/items/<id>/scan/` | public | Increment scan count when a member scans a QR code. | Required: per-IP throttle + duplicate-submit dedupe. |
| GET  | `inventory/items/<id>/public/` | public | Public read of scannable item fields (no cost/supplier data). | Not required (read). |
| GET  | `inventory/items/by-qr/<qr>/` | public | Resolve a QR code to a public item view. | Not required (read). |
| POST | `inventory/items/<id>/report-need/` | public | Member reports the bin needs restocking. | Required: per-IP throttle + dedupe per (item, day). |
| POST | `inventory/items/<id>/report-problem/` | public | Member reports a problem with the item. | Required: per-IP throttle + dedupe per (item, day). |
| POST | `inventory/locations/<id>/report-problem/` | public | Member reports a problem at a location. | Required: per-IP throttle + dedupe per (location, day). |
| POST | `inventory/assets/<id>/report-problem/` | public | Member reports a problem with an asset. | Required: per-IP throttle + dedupe per (asset, day). |
| POST | `inventory/work-orders/<id>/upload/` | public | Upload a completed paper work-order PDF for ingest. | Required: PDF is signed by AcroForm field; rate-limit per-IP. |
| GET  | `inventory/health/` | public | Inventory app health summary. | Not required. |
| any  | `inventory/items/...` (CRUD) | member-rw | `IsAuthenticatedOrReadOnly` — list/retrieve open to anonymous callers, write requires login. Staff-level gating is enforced inside `perform_create/_update/_destroy` via `_check_staff()`. |
| any  | `inventory/work-orders/...` (CRUD) | member | `IsAuthenticated` on `WorkOrderViewSet`; staff-only operations are gated inline. |
| any  | `inventory/maintenance-*` (CRUD) | member-rw | `IsAuthenticated` for log/task/dashboard; `IsAuthenticatedOrReadOnly` for material catalog. |

## Membership (`/api/membership/`)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| any | `membership/profile/...` | member | `IsAuthenticated` — every authed user can hit the endpoint; the queryset filters to records the caller can see and writes go through `perform_*` checks. |
| any | `membership/sigs/...`, `membership/sig-admins/...` | member | `IsAuthenticated` on `SIGViewSet`/`SIGAdminViewSet`/`SIGMemberViewSet`; staff-only writes are guarded inside `perform_*`. |
| POST | `membership/register/validate-token/` | public | Token validation for the registration QR flow (no `permission_classes` set; falls back to the per-environment default — `AllowAny` in dev, `IsAuthenticatedOrReadOnly` in prod, which still answers GET-style POSTs). |
| POST | `membership/register/complete/` | public | Token-gated user registration; the token (carried in the body) gates access. |
| POST | `membership/change-password/` | member | Password change for the authenticated user. |

## Reorder queue (`/api/reorders/`)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| any | `reorders/requests/...` (CRUD + workflow `@action`s) | member | `IsAuthenticated` on `ReorderRequestViewSet` since gh #327 / PR #341 — every action, including `list`, `retrieve`, `create`, and `pending`, rejects anonymous callers because the serializer carries purchasing-sensitive fields (`actual_cost`, `invoice_number`, `supplier_url`). |
| any | `reorders/purchase-orders/...` | member-rw | `IsAuthenticatedOrReadOnly` — anonymous reads exist for the queue dashboard; writes need login. Cost data is filtered in the serializer. |
| any | `reorders/order-receipts/...` | member | `IsAuthenticated` on `OrderReceiptViewSet`. |
| any | `reorders/analytics/...` | member-rw | `IsAuthenticated` for the API; the `kanban-print` and `kanban-multi-print` `@action`s are explicitly `AllowAny` so kiosks can render PDFs without a session. |
| any | `reorders/webhooks/...` | member | `IsAuthenticated` on `WebHookViewSet`. |
| any | `reorders/reports/...` | member | `IsAuthenticated` on `PurchasingReportViewSet`. |

## Index cards (`/api/index-cards/`)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| POST | `index-cards/preview/` | member-rw | `IsAuthenticatedOrReadOnly` on `IndexCardPreviewView`. |
| POST | `index-cards/generate/` | member-rw | `IsAuthenticatedOrReadOnly` on `IndexCardBatchGenerateView`. |
| POST | `index-cards/test-sheet/` | member-rw | `IsAuthenticatedOrReadOnly` on `TestSheetGenerateView`. |

## Dashboard (`/api/dashboard/`)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| GET | `dashboard/health/` | public | (See "Health and infrastructure".) |
| GET | `dashboard/inventory-summary/` | public | Aggregate counts only; no PII or cost data. |
| GET | `dashboard/messages/` | public | Site-wide message feed for unauthenticated kiosks. |
| POST | `dashboard/messages/add/` | member | Staff-only at the data layer; the view delegates to `IsAuthenticated` and inspects `request.user.is_staff` before persisting. |
| GET, POST | `dashboard/widgets/`, `dashboard/widgets/save/` | member | Per-user dashboard layout. |
| GET, POST | `dashboard/config/`, `dashboard/config/update/` | member | Per-user dashboard configuration. |
| GET | `dashboard/widget-data/{low-stock,pending-reorders,asset-problems,qr-scans,deliveries}/` | member | Authenticated dashboard widget data sources. |

## ForgeKey (`/api/forgekey/`)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| any | `forgekey/devices/...` (CRUD) | member-rw | `IsAuthenticatedOrReadOnly` on `ESP32DeviceViewSet`/`AssetDeviceViewSet`/`DeviceTypeViewSet`/`DeviceLockoutViewSet`/`DeviceUsageViewSet`. Custom write `@action`s elevate to `IsAdminUser`. |
| any | `forgekey/firmware/...` | member | `IsAuthenticated` on `FirmwareVersionViewSet` and `DeviceFirmwareUpdateViewSet`. |
| POST | `forgekey/devices/register/` | device-token | `AllowAny` at DRF; the view validates a provisioning token (`FORGEKEY_PROVISIONING_TOKEN`) before any state change. |
| POST | `forgekey/devices/<id>/photo/` | device-token | `AllowAny` at DRF; signed device payload is validated in the view body. |
| GET | `forgekey/firmware/<id>/download/` | device-token | `AllowAny` + signed download URL. |
| GET | `forgekey/firmware/public-key/` | public | Returns the firmware-signing public key (required for OTA). |
| GET | `forgekey/.well-known/jwks.json` | public | JWKS endpoint for issued device JWTs. |
| POST | `forgekey/mqtt-webhook/` | webhook-secret | `AllowAny` + HMAC validation in the view body. |
| any | `forgekey/asset-authorizations/...`, `forgekey/operational-modes/...`, `forgekey/power-meter-readings/...` | member-rw | `IsAuthenticatedOrReadOnly` ViewSets feeding the device control panel. |

## Customization (`/api/customization/`)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| GET | `customization/settings/` | public | Public branding (logo, colours, site name). The view is `AllowAny`. |
| PUT/PATCH | `customization/settings/` | admin | Same view; admin-only writes are enforced via `request.user.is_staff` inside the handler before any update is committed. |

## Location check-ins (`/api/location-checkins/`)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| POST | `location-checkins/checkin/` (`@action`) | public | Anonymous check-in for a location. **Abuse control:** required (per-IP throttle). |
| any | `location-checkins/check-ins/` (CRUD) | member-rw | `IsAuthenticatedOrReadOnly`; create is opened to `AllowAny` via `get_permissions()` for the kiosk path. |
| any | `location-checkins/feedback/` (CRUD) | member-rw | `IsAuthenticatedOrReadOnly`; create is `AllowAny` for kiosks. |
| any | `location-checkins/security-reports/` (CRUD) | member-rw | `IsAuthenticatedOrReadOnly`; the `submit` `@action` is `AllowAny`. |
| any | `location-checkins/tasks/...` | member | `IsAuthenticated`. |
| GET | `location-checkins/reports/{traffic,top}/` | member | `IsAuthenticated` traffic/visit reports. |
| POST | `location-checkins/webhook/` | webhook-secret | `AllowAny` + HMAC verification in the view. |

## Checklists (`/api/checklists/`)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| GET | `checklists/checklists/available/` (`@action`) | public | Available checklists for the kiosk picker. |
| GET | `checklists/checklists/<id>/detail/` (`@action`) | public | Read-only detail used by kiosks. |
| POST | `checklists/checklists/<id>/start/` (`@action`) | public | Begin a checklist completion (kiosk flow). |
| any | `checklists/checklists/...` (CRUD) | member | `IsAuthenticated` for create/update/delete; the public actions above override per-action. |
| any | `checklists/completions/...` | public | `AllowAny` on `ChecklistCompletionViewSet` so kiosks can post answers without a session. **Abuse control:** required (per-completion-token dedupe). |

## Donations (`/api/donations/`)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| GET | `donations/tax-receipt/<token>/` | public | Receipt lookup by signed token. **Abuse control:** unguessable token. |
| GET | `donations/donation-items/by-code/<code>/` | public | Public lookup for QR-printed donation labels. **Abuse control:** unguessable code. |
| any | `donations/donations/...` (CRUD) | member | `IsAuthenticated` on `DonationViewSet`. |
| any | `donations/donation-items/...` (CRUD) | member | `IsAuthenticated` on `DonationItemViewSet`; includes label-print and QR-generation `@action`s. |
| any | `donations/dispositions/...` | member | `IsAuthenticated` on `DispositionViewSet`. |
| any | `donations/tax-receipts/...` (admin CRUD) | admin | `IsAdminUser` on `TaxReceiptViewSet` for management actions; the public single-receipt lookup above stays open. |
| POST | `donations/donations/<id>/upload-signature/` | member | `IsAuthenticated` on `upload_signature`. |

## Search (`/api/search/`)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| GET | `search/` | member | Cross-app search — `IsAuthenticated` to avoid leaking inventory SKUs, asset serials, and supplier names. |
| GET | `search/recent/` | member | Per-user recent search history. |
| POST | `search/recent/save/` | member | Record a recent search hit. |

## Notifications (`/api/notifications/`)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| any | `notifications/notifications/...` (CRUD) | member | `IsAuthenticated` on `NotificationViewSet`; the queryset filters to the current user's notifications. |
| GET, PATCH | `notifications/preferences/` | member | `IsAuthenticated` on `NotificationPreferenceView`. |

## Screens / kiosks (`/api/screens/`)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| GET | `screens/kiosk/<slug>/payload/` | public | Read-only kiosk content; never includes cost or member PII. |
| POST | `screens/kiosk/<slug>/heartbeat/` | public | Kiosk liveness heartbeat. **Abuse control:** required (per-slug throttle). |
| any | `screens/screens/...` (CRUD) | member | `IsAuthenticated` on `ScreenViewSet`. |
| any | `screens/blocks/...` | member | `IsAuthenticated` on `ScreenContentBlockViewSet`. |
| any | `screens/messages/...` | member | `IsAuthenticated` on `SystemMessageViewSet`. |

## Maker boxes (`/api/maker-boxes/`)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| any | `maker-boxes/...` | member | `IsAuthenticated` on `MakerBoxViewSet`; staff-write enforcement happens inside `_check_staff()` for create/update/destroy. |

## Vendors (`/api/vendors/`)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| any | `vendors/...` | member-rw | `IsAuthenticatedOrReadOnly` on `VendorViewSet`. The queryset filters cost/contact data for non-staff readers; staff-only writes are enforced inline. |

> **Known intent gap (gh #328 follow-up):** the original matrix declared
> vendors staff-only. Today the read surface is open to any authenticated
> user. Tighten to `IsAdminUser` if vendor compliance must be staff-only.

## Maintenance orders (`/api/maintenance-orders/`)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| any | `maintenance-orders/third-party-work-orders/...` (CRUD) | member-rw | `IsAuthenticatedOrReadOnly` on `ThirdPartyWorkOrderViewSet` and the related `…Asset/Attachment/AuditLog/Quote` ViewSets. |
| any | `maintenance-orders/asset-warranties/...` | member-rw | `IsAuthenticatedOrReadOnly` on `AssetWarrantyViewSet`. |
| any | `maintenance-orders/emergency-authorizations/...` | member-rw | `IsAuthenticatedOrReadOnly` on `EmergencyAuthorizationViewSet`. |
| any | `maintenance-orders/recovery-tasks/...` | member-rw | `IsAuthenticatedOrReadOnly` on `RecoveryTaskViewSet`. |
| GET | `maintenance-orders/assets/<id>/work-order-status/` | member | `IsAuthenticated` on `asset_wo_status`. |

> **Known intent gap (gh #328 follow-up):** maintenance state was
> previously documented as staff-only. The current code allows any
> authenticated user to read; staff gating is enforced via the workflow
> transitions in `maintenance_orders.transitions`. Tighten the ViewSet
> permissions if read-side staff-only is required.

## Lockout / tagout (`/api/loto/`)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| any | `loto/devices/...` (CRUD) | member | `IsAuthenticated` on `LOTODeviceViewSet`. |
| any | `loto/asset-energy-sources/...` (CRUD) | member | `IsAuthenticated` on `AssetEnergySourceViewSet`. |
| GET | `loto/assets/<id>/loto-requirements/` | member | `IsAuthenticated` on `AssetLOTORequirementsView` — surfaces required isolation steps for an asset. |

## Electrical circuits (`/api/electrical-circuits/`)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| any | `electrical-circuits/breakers/...` | member | Inventory of breakers. |
| any | `electrical-circuits/outlets/...` | member | Inventory of outlets. |
| any | `electrical-circuits/light-switches/...` | member | Inventory of light switches. |
| any | `electrical-circuits/network-drops/...` | member | Inventory of network drops. |
| GET | `electrical-circuits/reports/panel-directory.pdf` | member | Printable panel directory PDF. |
| GET | `electrical-circuits/reports/network-drop-list.pdf` | member | Printable network drop list PDF. |

## Flower (Celery monitoring)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| any | `flower/...` | admin | Flower proxy. The view falls back to `DEFAULT_PERMISSION_CLASSES`; superuser enforcement happens inside `config.flower_proxy.flower_proxy`. |

## Public endpoint contract (AC-3)

Each row marked **public** is a supported external contract. Changing
the path, method, request shape, or response shape of a public endpoint
requires a deprecation period and matching frontend/kiosk updates.

For every public **write** endpoint, the abuse-control column states the
expectation; the implementation may use any of:

- DRF throttle scopes (`UserRateThrottle`, `AnonRateThrottle`,
  `ScopedRateThrottle`).
- Per-(resource, day) duplicate-submission keys backed by the cache.
- Signed/unguessable tokens for receipt-style endpoints.
- IP-based limits at the reverse proxy.

When a public write endpoint does not yet have an abuse control, file a
follow-up bead under AC-6.
