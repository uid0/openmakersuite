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
| **daemon-token** | Authentication via a dedicated shared token for a headless on-prem daemon (e.g. the claim-printer Pi interlock executor). Distinct from **public**: the token is mandatory and the endpoint is fail-closed when it is unconfigured. |

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
| POST | `auth/test-invite-code/` | public (DEBUG only) | Test fixture used by mobile invite-redeem E2E. Mints an open `InviteCode` so Playwright can drive the full public redeem path. Returns 403 unless `settings.DEBUG` is true. |
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
| GET  | `inventory/items/<id>/metrics/` | public | Computed stock + cost metrics row (QOH/QOO/QA/QC/QIT/RP/Lead/Cost + trend). Mirrors `retrieve`'s exposure — includes cost. | Not required (read). |
| GET  | `inventory/items/by-qr/<qr>/` | public | Resolve a QR code to a public item view. | Not required (read). |
| POST | `inventory/items/<id>/report-need/` | public | Member reports the bin needs restocking. | Required: per-IP throttle + dedupe per (item, day). |
| POST | `inventory/items/<id>/report-problem/` | public | Member reports a problem with the item. | Required: per-IP throttle + dedupe per (item, day). |
| POST | `inventory/locations/<id>/report-problem/` | public | Member reports a problem at a location. | Required: per-IP throttle + dedupe per (location, day). |
| POST | `inventory/assets/<id>/report-problem/` | public | Member reports a problem with an asset. | Required: per-IP throttle + dedupe per (asset, day). |
| POST | `inventory/work-orders/<id>/upload/` | public | Upload a completed paper work-order PDF for ingest. | Required: PDF is signed by AcroForm field; rate-limit per-IP. |
| GET  | `inventory/health/` | public | Inventory app health summary. | Not required. |
| any  | `inventory/items/...` (CRUD) | member-rw | `IsAuthenticatedOrReadOnly` — list/retrieve open to anonymous callers, write requires login. Staff-level gating is enforced inside `perform_create/_update/_destroy` via `_check_staff()`. |
| GET  | `inventory/items/<id>/stock_history/` | member | `IsAuthenticated` on `stock_history` (op-izy5) — weekly `StockLevelSnapshot` series plus reorder-request and cycle-count (`StockReconciliation`) event overlays and the reorder-point/desired thresholds, powering the item Stock-History chart. Auth-required (unlike the public `metrics`/`retrieve` reads) because it surfaces reorder + reconciliation history. | Not required (read). |
| POST | `inventory/items/<id>/pack-container/` | member | `IsAuthenticated` on `pack_container` (op-ev14) — the two container moves of an `open_closed` item: `{"transition": "open"}` breaks into a sealed pack (stock down one pack's base units, open tally up one, a `UsageLog` written) and `{"transition": "finish"}` retires the emptied one (open tally down one, stock untouched). Auth-required rather than following the public `log_usage` path, since it is a new capability and `log_usage` remains the anonymous way to record consumption. 400 for a non-`open_closed` item, no sealed pack left, or no open pack. | Not required (mutates one item's own stock; no cross-item effects). |
| GET  | `inventory/items/<id>/purchase_history/` | member | `IsAuthenticated` on `purchase_history` (op-96uo) — the item's order + receipt provenance: one `order_costs` row per `PurchaseOrderItem` (unit cost the order was placed at) and one `deliveries` row per `DeliveryItem` (tracking number, carrier, quantity, receipt notes). Auth-required (unlike the public `metrics`/`retrieve` reads) because it surfaces supplier pricing and shipment history. | Not required (read). |
| GET  | `inventory/work-orders/...` | member | `IsAuthenticatedOrStaffSigAdminWrite` on `WorkOrderViewSet` — any authenticated user can read open + completed standard PM work orders (gh #374). |
| POST/PATCH/PUT/DELETE | `inventory/work-orders/...` | staff-or-sig-admin | `IsAuthenticatedOrStaffSigAdminWrite` denies writes to volunteers; staff and SIG leaders may add or modify (gh #374). |
| POST/DELETE | `inventory/work-orders/<id>/materials/` + `…/materials/<usage_id>/` | staff-or-sig-admin | `add_material` / `remove_material` (op-768w) inherit the viewset gate, same as the sibling `materials/<usage_id>/toggle/`. They record the materials **actually** used or bought — real `unit_cost` and an optional `receipt_image` for out-of-pocket buys — so they carry purchasing-sensitive data and are deliberately not loosened to plain `IsAuthenticated`. `remove_material` deletes ad-hoc lines only, and never one with a live stock decrement. |
| any  | `inventory/work-order-attachments/...` (CRUD, `?work_order=`, `?kind=`) | member / staff-or-sig-admin | `IsAuthenticatedOrStaffSigAdminWrite` on `WorkOrderAttachmentViewSet` (op-7pjj) — the standard work order's generic attachments list (multipart upload of photos/documents hung off one WO). Read follows the parent `WorkOrderViewSet` so a volunteer who can see a work order can open its paperwork; upload and delete stay staff / Logistics / SIG-admin. The third-party equivalent is read-gated instead (`IsStaffOrSigAdmin`), because third-party WOs are hidden from volunteers entirely (gh #374). |
| any  | `inventory/supplier-agreements/...` (CRUD, `?supplier=`, `?is_active=`) | member-rw | `IsAuthenticatedOrReadOnly` on `SupplierAgreementViewSet` (op-yoos) — mirrors `SupplierViewSet`, since a purchase/pricing agreement is supplier reference data. Holds a name, terms notes and an optional uploaded document; a purchase order points at the agreement it was placed under. Reads are open like the rest of the supplier catalog; create/update/delete need login. |
| any  | `inventory/maintenance-*` (CRUD) | member-rw | `IsAuthenticated` for log/task/dashboard; `IsAuthenticatedOrReadOnly` for the material and tool catalogs. |
| GET  | `inventory/assets/<id>/maintenance-history/` | member | `IsAuthenticated` — unified per-asset history (backdated `MaintenanceRecord` rows + closed third-party work orders) with `since`/`until`/`source` filters. |
| any  | `inventory/maintenance-records/...` (CRUD) | staff-or-sig-admin | `IsAuthenticatedOrStaffSigAdminWrite` — anyone authenticated can read; staff and SIG leaders can create/update; staff-only delete. |
| POST | `inventory/assets/<id>/log-hours/` | staff-or-sig-admin | `IsStaffOrSigAdmin` — atomically increments `Asset.hours_used` for utilization metrics + maintenance forecast. |
| POST | `inventory/assets/set_cost_recoverable_by_category/` | staff | `IsAdminUser` on `set_cost_recoverable_by_category` (op-9ho2) — bulk-sets `Asset.is_cost_recoverable` for every asset in one category (PK or slug), the REST twin of the `AssetAdmin` bulk actions. The flag decides whether in-house repair cost is billed to the landlord, so it is staff-only even though the single-asset PATCH follows the usual `AssetViewSet` edit perms. |
| GET  | `inventory/locations/<id>/safety-sheet/` | staff | `LocationSafetySheetView` — printable Safety Sign payload (lights / outlets / thermostats + deduped kill-breaker list for the room). |
| any  | `inventory/asset-reservations/...` (CRUD) | member | `IsAuthenticated` on `AssetReservationViewSet`. Mutations enforce staff-or-SIG-admin inside the view via `asset.is_user_group_admin(user)`; destroy soft-cancels via `cancelled_at`. |
| any  | `inventory/asset-out-of-service/...` (CRUD + `restore/`) | member | `IsAuthenticated` on `AssetOutOfServiceViewSet`. Mutations and `restore/` enforce staff-or-SIG-admin inside the view; only one open row per asset (single-open invariant). |
| any  | `inventory/asset-documents/...` (CRUD + `supersede/`) | member-rw | `IsAuthenticatedOrReadOnly` on `AssetDocumentViewSet` (mirrors `AssetViewSet` edit perms) — the per-asset document library (manuals, CAD, wiring, cut-sheets, cut-ready DXF/SVG/G-code/STL). List/retrieve open to anonymous; upload/update/delete + the `supersede/` new-version action require login. `supersede/` bumps `version` and flips the prior doc's `is_current` to False. |
| any  | `inventory/serialized-components/...` (CRUD + `receive/install/remove/consume/retire/dispose` + `scan_receive`) | staff-or-sig-admin | `IsAuthenticatedOrStaffSigAdminWrite` on `SerializedComponentViewSet` — any authenticated user can read; staff and SIG leaders create/update/delete and drive lifecycle transitions. Transitions are validated against the item's `serial_tracking_mode` and each writes a `ComponentUsageEvent`. `scan_receive` (POST, detail=False) idempotently create-and-receives a scanned serial (no PO required) so batch web/ScanTTY scanning tolerates double-scans. |
| GET  | `inventory/component-usage-events/...` | member | `IsAuthenticatedOrStaffSigAdminWrite` on the read-only `ComponentUsageEventViewSet` — authenticated read of the per-unit usage/audit log (written as a side effect of lifecycle actions). |
| GET  | `inventory/reports/inventory/...` (`stock_by_category`, `reorder_frequency`, `value_by_location`, `serialized_forecast`, `export`) | member | `IsAuthenticated` on `InventoryReportViewSet` — read-only analytics. `serialized_forecast` is the mode-aware consumption forecast + low-stock report for serialized components (consumables deplete on `consume`; reusables only on `retire`/`dispose`), exposing `avg_daily_use`, `days_until_stockout`, and `reorder_point` (lead time from `LeadTimeLog`) for the inventory + purchasing overview dashboards. |

## Membership (`/api/membership/`)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| any | `membership/profile/...` | member | `IsAuthenticated` — every authed user can hit the endpoint; the queryset filters to records the caller can see and writes go through `perform_*` checks. |
| any | `membership/sigs/...`, `membership/sig-admins/...` | member | `IsAuthenticated` on `SIGViewSet`/`SIGAdminViewSet`/`SIGMemberViewSet`; staff-only writes are guarded inside `perform_*`. |
| GET | `membership/users/...` | admin | `IsAdminUser` on `UserDirectoryViewSet` (read-only list/retrieve) — staff user lookup for the access-control badge-enrollment UI. |
| POST | `membership/register/validate-token/` | public | Token validation for the registration QR flow (no `permission_classes` set; falls back to the per-environment default — `AllowAny` in dev, `IsAuthenticatedOrReadOnly` in prod, which still answers GET-style POSTs). |
| POST | `membership/register/complete/` | public | Token-gated user registration; the token (carried in the body) gates access. |
| POST | `membership/change-password/` | member | Password change for the authenticated user. |
| any | `membership/invite-codes/...` (CRUD) | admin | `IsAdminUser` on `InviteCodeViewSet`. Staff mint single-use codes; the `code` field is server-generated, never client-supplied. |
| POST | `membership/invite-codes/<id>/revoke/` | admin | Flip `is_active` so an open code can no longer be redeemed (no-op on already-redeemed codes). |
| GET | `membership/invite/preview/?code=<code>` | public | Anonymous probe: returns `intended_label` + group names for a valid+open code, 404 for invalid/expired/redeemed (does not leak existence). |
| POST | `membership/invite/redeem/` | public | Anonymous self-signup: creates a fresh `User`, adds them to `intended_groups`, marks the code redeemed. Single-use, transactional, password-validated, captures `redeemed_ip`. **Abuse control:** required — per-IP throttle on the redeem path; codes themselves are ~132-bit entropy + single-use + bounded expiry. |

## Reorder queue (`/api/reorders/`)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| POST | `reorders/requests/` (create only) | public | `AllowAny` on `ReorderRequestViewSet.create` — required for the QR-scan reorder flow on printed shelf labels. The create input + response use `ReorderRequestCreateSerializer`, which only exposes `id`, `item`, `quantity`, `requested_by`, `request_notes`, `priority`, `status` — admin / cost / invoice / supplier-URL fields cannot be set or read by an anonymous caller. |
| any | `reorders/requests/...` (everything else: list, retrieve, update, workflow `@action`s) | member | `IsAuthenticated` on `ReorderRequestViewSet` since gh #327 / PR #341 — every read or admin action rejects anonymous callers because `ReorderRequestSerializer` carries purchasing-sensitive fields (`actual_cost`, `invoice_number`, `supplier_url`). |
| any | `reorders/purchase-orders/...` | member-rw | `IsAuthenticatedOrReadOnly` — anonymous reads exist for the queue dashboard; writes need login. Cost data is filtered in the serializer. The per-line-item receive action (`purchase-orders/<id>/receive/`, POST) is a write and requires login, like whole-PO `mark_delivered/`. The order-pad export (`purchase-orders/<id>/export-order/`, GET) is a login-required read — it exposes every line's supplier part number, so it is gated like the other non-list `@action`s rather than left anonymous. |
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
| any | `forgekey/badge-enrollment/...` | admin | `IsAdminUser` on `BadgeEnrollmentViewSet` (list/arm/cancel/set_badge) — staff-only badge↔member enrollment for the access-control interlock. |
| GET | `forgekey/access-log/` | admin | `IsAdminUser` on `ForgeKeyAuditEventViewSet` (list/retrieve) — staff-only access grant/deny/session audit events. |
| POST | `forgekey/devices/enroll/` | device-token | `AllowAny` at DRF; the view validates the provisioning token (`X-ForgeKey-Provisioning-Token` → `FORGEKEY_PROVISIONING_TOKEN`), validates the CSR, signs it, and links the resulting cert to a `DeviceIdentity`. Replaces the prior `/devices/register/` endpoint. |
| POST | `forgekey/devices/<id>/photo/` | device-token | `AllowAny` at DRF; signed device payload is validated in the view body. |
| GET | `forgekey/firmware/<id>/download/` | device-token | `AllowAny` + signed download URL. |
| GET | `forgekey/firmware/public-key/` | public | Returns the firmware-signing public key (required for OTA). |
| GET | `forgekey/oms-command-public-key.pem` | public | Returns the OMS command-verification public key (PEM). Used by firmware to verify signed commands. |
| GET | `forgekey/ca/crl.pem` | public | OMS-internal CA's Certificate Revocation List for the device client-certificate PKI. |
| GET | `forgekey/.well-known/jwks.json` | public | JWKS endpoint for issued device JWTs. |
| POST | `forgekey/mqtt-webhook/` | webhook-secret | `AllowAny` + HMAC validation in the view body. |
| GET | `forgekey/epaper/<display_id>/image.png` | public | XIAO 7.5" ePaper panel fetches the latest PM-status PNG. AllowAny because the device has no persistent JWT; the image contains nothing not already visible on the panel mounted on the asset. Supports `If-None-Match` → 304. |
| POST | `forgekey/epaper/<display_id>/battery/` | public | Panel reports its battery percent (0..100). AllowAny for the same reason as the image endpoint. Below `FORGEKEY_EPAPER_LOW_BATTERY_PERCENT` emits a Sentry warning so ops can prep a charged swap. |
| POST | `forgekey/epaper/<display_id>/set-rotation/` | member | `IsAuthenticated` on `EPaperDisplaySetRotationView` — staff tunes the per-panel event-face / pm-face weighting from the e-paper panels admin. Values clamped to `[0, 100]`. |
| any | `forgekey/asset-authorizations/...`, `forgekey/operational-modes/...`, `forgekey/room-operational-modes/...`, `forgekey/indicator-bindings/...`, `forgekey/power-meter-readings/...` | member-rw | `IsAuthenticatedOrReadOnly` ViewSets feeding the device control panel. `IndicatorBindingViewSet` (incl. its `sync` `@action`) binds an indicator device to an asset/room; `RoomOperationalModeViewSet` sets a room's manual status. The `devices/<id>/indicator/test/` preview `@action` elevates to `IsAdminUser`. |

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

## Preventive maintenance (`/api/preventive-maintenance/`)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| GET | `preventive-maintenance/schedules/board/` | public | `AllowAny` so the Inkplate kiosk can poll without a JWT. Read-only projection of every active PM schedule sorted by urgency. |
| any | `preventive-maintenance/schedules/...` (CRUD) | staff-or-readonly | `PMSchedulePermissions`: read for anyone (so the React admin can show the board on a regular browser); write requires `is_staff`. |
| POST | `preventive-maintenance/schedules/<id>/log_service/` | staff | `PMSchedulePermissions` — staff-only record "I just performed this task now" shortcut. |
| any | `preventive-maintenance/service-logs/...` (CRUD) | staff-or-readonly | Same `PMSchedulePermissions` rule as schedules — anyone reads, staff writes. |

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

## Scanner (`/api/scanner/`)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| POST | `scanner/dispatch/` | public | `AllowAny` so the workshop kiosk can scan without a JWT. Read-only: never mutates state — side effects happen via the per-entity endpoints (reorder / asset.scan / location-checkins.create) which enforce their own auth. |

## Notifications (`/api/notifications/`)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| any | `notifications/notifications/...` (CRUD) | member | `IsAuthenticated` on `NotificationViewSet`; the queryset filters to the current user's notifications. |
| GET, PATCH | `notifications/preferences/` | member | `IsAuthenticated` on `NotificationPreferenceView`. |

## Account device management (`/api/account/`)

Known-device list + "this wasn't me" revoke-all (notifications FP3, oms-ltqs3).
The view class lives in `notifications.account_views`.

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| GET | `account/devices/` (list, retrieve) | member | `IsAuthenticated` on `KnownDeviceViewSet`; `get_queryset` filters to the requesting user's own devices — owner-scoped, no cross-user access. |
| DELETE | `account/devices/<id>/` (forget) | member | Owner-scoped; removing a device is audited (`AccountSecurityAuditEvent`). |
| POST | `account/devices/revoke-all/` | member | "This wasn't me": deletes the user's Django sessions and advances `User.tokens_valid_after`, forcing re-auth across REST, the browsable API, and admin. Audited. |

## Screens / kiosks (`/api/screens/`)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| GET | `screens/kiosk/<slug>/payload/` | public | Read-only kiosk content; never includes cost or member PII. |
| POST | `screens/kiosk/<slug>/heartbeat/` | public | Kiosk liveness heartbeat. **Abuse control:** required (per-slug throttle). |
| GET | `screens/weather/current/` | public | OpenWeather-backed current conditions for kiosks. Server-side cache (`OPENWEATHER_CACHE_SECONDS`, default 600s) bounds upstream quota. |
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
| GET | `vendors/...` | member | `IsAuthenticated` on `VendorViewSet`. The queryset still filters cost/contact data for non-staff readers; staff-only writes are enforced via `IsAdminUser` on non-safe methods. |
| POST/PATCH/PUT/DELETE | `vendors/...` | admin | `IsAdminUser` only — staff manage vendor records. SIG leaders cannot create or modify vendors (operator-set rule, gh #374). |

## Maintenance orders (`/api/maintenance-orders/`)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| any | `maintenance-orders/third-party-work-orders/...` (CRUD) | staff-or-sig-admin | `IsStaffOrSigAdmin` on `ThirdPartyWorkOrderViewSet` and the related `…Asset/Attachment/AuditLog/Quote` ViewSets. Reads gated alongside writes (gh #374). |
| any | `maintenance-orders/asset-warranties/...` | staff-or-sig-admin | `IsStaffOrSigAdmin`. Volunteer surface is the standard PM work-order list (gh #374). |
| any | `maintenance-orders/emergency-authorizations/...` | staff-or-sig-admin | `IsStaffOrSigAdmin`. |
| any | `maintenance-orders/recovery-tasks/...` | staff-or-sig-admin | `IsStaffOrSigAdmin`. |
| GET | `maintenance-orders/assets/<id>/work-order-status/` | member | `IsAuthenticated` on `asset_wo_status` — surfaces compact status data, not vendor relationships. |

> **gh #374 (operator-set rule, 2026-05):** third-party work orders are
> intentionally hidden from volunteers — they would expose vendor
> relationships, NTE amounts, and quote data. Standard PM work orders
> (see `inventory/work-orders/`) remain readable by all authenticated
> users. Staff and SIG leaders may add or modify both kinds; vendors
> themselves are staff-only writes.

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
| any | `electrical-circuits/disconnects/...` | staff | `DisconnectViewSet` — full CRUD on Disconnect rows for hardwired loads. Supports `?circuit=`, `?location=`, `?disconnect_type=`, and `?needs_review=` filters on list. |
| GET | `electrical-circuits/reports/panel-directory.pdf` | member | Printable panel directory PDF. |
| GET | `electrical-circuits/reports/network-drop-list.pdf` | member | Printable network drop list PDF. |

## Power-topology safety queries (`/api/electrical/`, `/api/assets/<id>/power-chain/`)

Read-only endpoints that surface the resolvers in
`electrical_circuits.services.power_chain` so a maintainer can answer
"what loses power if I trip this?" / "what feeds this asset?" before
opening a panel. All staff-gated (oms-b25 AC-5).

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| GET | `electrical/panels/` | staff | `PowerPanelListView` — directory of every panel with breaker counts and the migration `needs_review` flag. |
| GET | `electrical/breakers/<id>/trip-impact/` | staff | `PowerBreakerTripImpactView` — assets fed by the breaker, split critical vs. not. |
| GET | `electrical/circuits/<id>/load/` | staff | `PowerCircuitLoadView` — connected devices, estimated nameplate draw, NEC-derated capacity utilization. |
| GET | `electrical/panels/<id>/topology/` | staff | `PowerPanelTopologyView` — full panel → breaker → circuit → outlet tree. |
| GET | `assets/<id>/power-chain/` | staff | `AssetPowerChainView` — every hop from an asset back to its panel. |
| any  | `electrical/panels-crud/...` | staff | `PowerPanelViewSet` — full CRUD on PowerPanel rows so the frontend can create and edit panels without Django admin. |
| any  | `electrical/breakers-crud/...` | staff | `PowerBreakerViewSet` — full CRUD on PowerBreaker rows. Supports `?panel=<id>` filter on list. New `is_critical` + `critical_category` + `critical_note` fields flag life-safety circuits (fire alarm, emergency lighting, exit signs, egress door). |
| GET  | `electrical/breakers-crud/critical/` | staff | `PowerBreakerViewSet.critical` — every active critical breaker, optional `?location=<id>` filter. Feeds the Location Safety Sign and LOTO planning warning. |
| any  | `electrical/circuits-crud/...` | staff | `PowerCircuitViewSet` — full CRUD on PowerCircuit rows. Supports `?breaker=<id>` filter on list. `max_load_amps` defaults to 80% of the breaker amperage per NEC continuous-load rule when omitted. |
| any  | `electrical/outlets-crud/...` | staff | `PowerOutletViewSet` — full CRUD on PowerOutlet rows. Supports `?circuit=<id>` filter on list. |

## Climate (`/api/climate/`)

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| any | `climate/thermostats/...` | member | `IsAuthenticated` on `ThermostatViewSet` — CRUD on per-room Thermostat records. Supports `?location=` and `?controls_location=` filters on list. |

## Interlocks (`/api/interlocks/`)

Control plane + credential vault for remotely enabling/disabling DMS
RFID-KeyMaster tool interlocks. OMS never reaches the interlocks directly;
the claim-printer Pi polls the command-queue and SSH-executes on the target.
SSH passwords are encrypted at rest (Fernet) and are **write-only** in the
operator API (`has_credentials` boolean is exposed instead of the secret).

| Method | Path | Class | Notes |
| --- | --- | --- | --- |
| any | `interlocks/`, `interlocks/{id}/` | staff | `IsStaffUser` on `InterlockViewSet` CRUD. `ssh_password` is write-only; never returned. |
| POST | `interlocks/{id}/enable/`, `interlocks/{id}/disable/` | staff | Sets `desired_state` and enqueues an `InterlockCommand`. |
| POST | `interlocks/{id}/status/` | staff | Enqueues a status poll (does not change `desired_state`). |
| GET | `interlocks/command-queue/` | daemon-token | `IsInterlockDaemon` — returns pending commands **with decrypted SSH creds** for the Pi executor and marks them claimed. **NOT public** (unlike the project-storage print-queue) because it exposes plaintext credentials; requires the `X-Interlock-Token` header matching `INTERLOCK_DAEMON_TOKEN`, fail-closed when unset. |
| POST | `interlocks/commands/{id}/report/` | daemon-token | `IsInterlockDaemon` — ingests the executor's result and refreshes device telemetry (`last_reported_state`, `in_use`, `online`, `last_seen_at`). |

## Analytics (`/api/analytics/`)

Read-only aggregate metrics for the executive dashboard and the monthly
board email. The aggregation layer reads completed `WorkOrder`s, closed
`ThirdPartyWorkOrder`s, and `Asset.hours_used` to expose ROI, utilization,
category spend, and a maintenance forecast.

| Method | Path | Class | Purpose | Notes |
| --- | --- | --- | --- | --- |
| GET | `analytics/pulse/` | staff-or-sig-admin **or** signed-URL token | `IsAnalyticsViewer` — returns the full aggregate JSON used by both the dashboard and the monthly email. Authenticated staff/SIG admins are allowed; anyone presenting a valid `?token=...` minted by `analytics/share/` is also allowed (board-member bypass). | 60-min django-redis cache keyed by `(start, end, bucket)`. Token verifies signature + embedded TTL; rotating `ANALYTICS_SHARE_SALT` invalidates all outstanding tokens. |
| POST | `analytics/share/` | staff-or-sig-admin | `IsAnalyticsSharer` — mints a signed token (`{"ttl_days": 1..365}`, defaults to 30). Returns the token + ttl; the frontend composes the shareable URL. | Pure HMAC-signed token (no DB row); authoritative kill-switch is the salt setting. |

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
