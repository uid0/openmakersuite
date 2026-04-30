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
| POST | `auth/logout/` | member | Invalidates session/token. |
| POST | `auth/refresh/` | public | Refresh access token using a refresh token. **Abuse control:** required (refresh throttle). |
| POST | `auth/test-membership/` | admin | Test fixture only; must not be enabled in production. |
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
| any  | `inventory/items/...` (CRUD) | member/staff | Item CRUD; staff for write. | n/a |
| any  | `inventory/purchase-orders/...` | staff | Purchasing — never public. | n/a |
| any  | `inventory/work-orders/...` (CRUD) | staff | Maintenance — never public. | n/a |
| any  | `inventory/admin/...` | admin | Settings, deletes, and overrides. | n/a |

## Membership (`/api/membership/`)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| GET | `membership/me/` | member | Current user's profile. |
| any | `membership/profiles/...` | member/staff | Profile CRUD; staff manages other users. |
| any | `membership/groups/...` | admin | Group/role administration. |

## Reorder queue (`/api/reorders/`)

All endpoints are staff-only — reorders move money. Public read of the
queue would expose vendor pricing.

## Index cards (`/api/index-cards/`)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| GET | `index-cards/<id>/pdf/` | member | Print an index card. |

## Dashboard (`/api/dashboard/`)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| GET | `dashboard/health/` | public | (See "Health and infrastructure".) |
| any | `dashboard/widgets/...` | member | Per-user dashboard widget layout. |
| any | `dashboard/messages/...` | staff | Site-wide messages — staff post, all users read. |

## ForgeKey (`/api/forgekey/`)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| any | `forgekey/devices/...` | staff | Device registration and lifecycle. |
| any | `forgekey/firmware/...` | admin | Firmware uploads and signing — admin only. |
| any | `forgekey/lockouts/...` | staff | Device lockout state. |
| POST | `forgekey/mqtt-bridge/...` | device-token | MQTT bridge ingestion. |
| POST | `forgekey/webhooks/...` | webhook-secret | Inbound webhook receiver. |

## Customization (`/api/customization/`)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| GET | `customization/site-settings/` | public | Public branding (logo, colours, site name). |
| PUT/PATCH | `customization/site-settings/` | admin | Site settings updates. |

## Location check-ins (`/api/location-checkins/`)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| POST | `location-checkins/checkin/` | public | Anonymous check-in for a location. **Abuse control:** required (per-IP throttle). |
| any | `location-checkins/admin/...` | staff | Manage check-in policy and review history. |

## Checklists (`/api/checklists/`)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| GET | `checklists/public/...` | public | Read-only checklists exposed for kiosks. |
| any | `checklists/admin/...` | staff | Author and edit checklists. |

## Donations (`/api/donations/`)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| GET | `donations/receipts/<token>/` | public | Receipt lookup by signed token. **Abuse control:** unguessable token. |
| POST | `donations/public-intake/` | public | Member submits a donation. **Abuse control:** required (per-IP throttle). |
| any | `donations/admin/...` | staff | Donation administration, refunds, receipts. |

## Search (`/api/search/`)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| GET | `search/global/` | member | Cross-app search — member-only to avoid leaking inventory cost data. |

## Notifications (`/api/notifications/`)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| any | `notifications/me/...` | member | Per-user notification settings and history. |
| any | `notifications/admin/...` | admin | Notification template administration. |

## Screens / kiosks (`/api/screens/`)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| GET | `screens/<id>/payload/` | public | Read-only kiosk content; never includes cost or member PII. |
| any | `screens/admin/...` | staff | Kiosk configuration. |

## Maker boxes (`/api/maker-boxes/`)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| any | `maker-boxes/...` | member/staff | Member browse + staff edit. |

## Vendors (`/api/vendors/`)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| any | `vendors/...` | staff | Vendor compliance is staff-only — never public. |

## Maintenance orders (`/api/maintenance-orders/`)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| any | `maintenance-orders/...` | staff | Maintenance state is staff-only. |

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
| any | `flower/...` | admin | Flower proxy is restricted to superusers via `config.flower_proxy`. |

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
