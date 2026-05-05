# Frontend Journey Inventory

This document inventories the critical frontend journeys in OpenMakerSuite. It is the
companion to the [Product Proficiency Roadmap](PRODUCT_PROFICIENCY_ROADMAP.md) and
exists to satisfy AC-13: every critical workflow is named, owned, and pinned to a
concrete entry point so resilience work (mobile, offline, auth expiration, role gating,
empty/error states, accessibility, e2e) can be planned per journey rather than per page.

Each journey row lists:

- **Journey** — the workflow as a user would describe it.
- **Entry routes** — primary URLs in `frontend/src/App.tsx`.
- **Auth** — `public`, `member`, `staff`, or `admin`. Backend permissions remain authoritative.
- **Resilience expectations** — what "proficient" means for that journey (mobile, offline,
  duplicates, auth expiration, role gating, empty/error states, accessibility).
- **Tests** — which suite (`*.test.tsx`, `*.spec.ts`) verifies the journey today.

Journeys are grouped by workspace.

---

## Public scan and report

Unauthenticated members scan QR codes (or enter the 6-character access code) and submit
makerspace operational reports. These journeys never require login and must stay
mobile-first.

| Journey | Entry route(s) | Auth | Key resilience expectations |
| --- | --- | --- | --- |
| Scan inventory item, auto-submit reorder | `/inventory/scan/:itemId`, `/scan/:itemId` (`ScanPage.tsx`) | public | Camera-denied → code-entry fallback (`/inventory/scan`); duplicate-tap guard; pending-reorder shown instead of new request; offline shows actionable error |
| Scan fixture, request refill | `/inventory/scan/fixture/:fixtureId` (`FixtureScanPage.tsx`) | public | Same as above; preserves form state on retry |
| Scan asset, see info or report problem | `/inventory/scan/asset/:assetId` (`AssetScanPage.tsx`) | public | Public read of asset info; problem report submits without login; member-mode fields appear only when `localStorage.token` present |
| Scan location, view problems / report problem | `/inventory/scan/location/:locationId` (`LocationScanPage.tsx`) | public | Code-entry fallback; problem-report duplicate guard; image-upload offline error |
| Scan donation item, view info / pickup | `/inventory/scan/donation-item/:itemId` (`DonationItemScanPage.tsx`) | public | Public donation flow; tax-receipt lookup is a separate authenticated flow |
| Scan MakerBox, lookup contents | `/facilities/maker-boxes/scan` (`MakerBoxScanPage.tsx`) | public | Camera-denied → code-entry fallback within page |
| Manual code entry (camera-free fallback) | `/inventory/scan` (`CodeEntryPage.tsx`) | public | Camera-free path; client-side validation of 6-char code; shows specific not-found error |
| Public reorder thanks confirmation | `/thanks` (`ThanksPage.tsx`) | public | Final confirmation surface — submitted scans should land here, not on a half-submitted form |
| Public transparency dashboard | `/inventory/transparency` (`TransparencyPage.tsx`) | public | Public read-only; no member data; offline shows informative error |
| Tax receipt self-service lookup | `/settings/tax-receipt/lookup` (`TaxReceiptLookupPage.tsx`) | public | Public donor lookup; never lists other donors |

Tests: `e2e/asset-scan.spec.ts`, `e2e/code-entry-fallback.spec.ts`,
`__tests__/pages/ScanPage.test.tsx`,
`__tests__/pages/AssetScanPage.test.tsx`,
`__tests__/pages/LocationScanPage.test.tsx`,
`__tests__/pages/TransparencyPage.test.tsx`.

Coverage gaps: `FixtureScanPage`, `DonationItemScanPage`, `MakerBoxScanPage`,
`TaxReceiptLookupPage`, and `ThanksPage` have no dedicated unit test today —
manual verification only. `CodeEntryPage` is covered by
`e2e/code-entry-fallback.spec.ts`.

---

## Inventory browse / search / triage

Authenticated staff and members manage the inventory catalog and reorder workflow.

| Journey | Entry route(s) | Auth | Key resilience expectations |
| --- | --- | --- | --- |
| Browse and search inventory list | `/inventory/items` (`InventoryListPage.tsx`) | member | Loading skeleton; empty-state with "Add item"; auth expiration preserves filter context |
| View inventory item detail | `/inventory/items/:id` (`InventoryItemDetailPage.tsx`) | member | Forbidden state for staff-only fields; missing-item state |
| Create / edit inventory item | `/inventory/items/new`, `/inventory/items/:id/edit` (`InventoryItemFormPage.tsx`) | staff | Form save errors surface field-level validation; auth expiration returns to attempted edit |
| Browse and search categories | `/inventory/categories` (`CategoryListPage.tsx`) | staff | Standard list resilience |
| Create / edit category | `/inventory/categories/new`, `/inventory/categories/:id/edit` (`CategoryFormPage.tsx`) | staff | Same as above |
| Browse / view / edit locations | `/inventory/locations`, `/inventory/locations/:id`, `/inventory/locations/:id/edit` (`LocationListPage.tsx`, `LocationDetailPage.tsx`, `LocationFormPage.tsx`) | staff | Detail page exposes problem and traffic panels |
| Reconcile inventory at a location | `/inventory/locations/:id/reconcile` (`InventoryReconciliationPage.tsx`) | staff | Long-running session — must survive auth expiration via re-login banner |
| Reorder triage / pending requests | `/inventory/admin` (`AdminDashboard.tsx`) | staff | Pending reorders surface; duplicate-suppression visible |
| Inventory report (CSV / charts) | `/reports/inventory` (`InventoryReportPage.tsx`) | staff | Empty-state when no data; chart loading state |

Tests: `e2e/inventory-browse.spec.ts`, `e2e/public-to-staff.spec.ts`,
`__tests__/pages/InventoryListPage.test.tsx`,
`__tests__/pages/InventoryItemDetailPage.test.tsx`,
`__tests__/pages/InventoryItemFormPage.test.tsx`,
`__tests__/pages/InventoryReconciliationPage.test.tsx`,
`__tests__/pages/LocationListPage.test.tsx`,
`__tests__/pages/AdminDashboard.test.tsx`.

Coverage gaps: `CategoryListPage`, `CategoryFormPage`, `LocationFormPage`,
`LocationDetailPage`, and `InventoryReportPage` have no dedicated unit test
today.

---

## Purchasing

Staff create purchase orders, receive deliveries, and track supplier relationships.

| Journey | Entry route(s) | Auth | Key resilience expectations |
| --- | --- | --- | --- |
| Browse purchase orders | `/purchasing/orders` (`PurchaseOrderListPage.tsx`) | staff | Filter by status; loading + empty states |
| Create purchase order | `/purchasing/orders/new` (`PurchaseOrderFormPage.tsx`) | staff | Multi-supplier; line-item duplicate guard |
| View / approve / receive purchase order | `/purchasing/orders/:orderId` (`PurchaseOrderPage.tsx`) | staff/admin | Receipt action gated by permission; auth expiration returns to PO |
| Purchasing report (CSV / charts) | `/reports/purchasing` (`PurchasingReportPage.tsx`) | staff | Empty-state when no data |
| Browse / edit suppliers | `/inventory/suppliers`, `/inventory/suppliers/:id`, `/inventory/suppliers/:id/edit` (`SupplierListPage.tsx`, `SupplierDetailPage.tsx`, `SupplierFormPage.tsx`) | staff | Lead-time and price-trend charts have empty-state |

Tests: `__tests__/pages/PurchaseOrderFormPage.test.tsx`,
`__tests__/pages/PurchaseOrderPage.test.tsx`,
`__tests__/pages/SupplierListPage.test.tsx`,
`__tests__/pages/SupplierDetailPage.test.tsx`,
`__tests__/pages/SupplierFormPage.test.tsx`.

Coverage gaps: `PurchaseOrderListPage` and `PurchasingReportPage` have no
dedicated unit test today.

---

## Asset and maintenance

Staff manage assets, preventive maintenance, and work orders for facility safety.

| Journey | Entry route(s) | Auth | Key resilience expectations |
| --- | --- | --- | --- |
| Browse assets | `/assets`, `/inventory/assets` (`AssetsPage.tsx`) | staff | Table + grid views; loading + empty states |
| View asset detail (incl. compliance + safety) | `/assets/:id` (`AssetDetailPage.tsx`) | staff | NFPA diamond + safety panels; problem-report modal |
| Create / edit asset | `/assets/new`, `/assets/:id/edit` (`AssetFormPage.tsx`) | staff | Photo upload offline error; safety-control validation |
| Add / edit asset maintenance item | `/assets/:assetId/maintenance/new`, `/assets/:assetId/maintenance/:id/edit` (`MaintenanceItemFormPage.tsx`) | staff | Lockout/tagout + electrical fields gated by asset traits |
| Asset utilization / TCO report | `/reports/assets` (`AssetReportPage.tsx`) | staff | Empty-state per tab; date-range loading |
| Maintenance dashboards | `/maintenance`, `/maintenance/dashboard` (`MaintenanceDashboardPage.tsx`, `MaintenanceDashboard.tsx`) | staff | Loading per panel; empty-state when no work |
| Work order detail / completion | `/maintenance/work-orders/:id` (`WorkOrderPage.tsx`) | staff | LOTO + electrical validation; CV-review queue; auth expiration returns to attempted save |
| Third-party (paper) work order | `/maintenance/third-party/:id` (`ThirdPartyWorkOrderPage.tsx`) | staff | Compliance banner; vendor expiration warning |
| Location problem detail / resolution | `/maintenance/location-problems/:id` (`LocationProblemDetailPage.tsx`) | staff | Status transitions; assignee picker; comment thread |
| Checklist completion | `/facilities/checklist/:checklistId/complete/:completionId` (`ChecklistCompletionPage.tsx`) | public/member | Long-running form — must survive offline + duplicate submit |

Tests: `__tests__/pages/AssetDetailPage.test.tsx`,
`__tests__/pages/AssetFormPage.test.tsx`,
`__tests__/pages/MaintenanceDashboard.test.tsx`,
`__tests__/pages/MaintenanceDashboardPage.test.tsx`,
`__tests__/pages/ThirdPartyWorkOrderPage.test.tsx`,
`__tests__/pages/ChecklistCompletionPage.test.tsx`,
`e2e/admin-dashboard-assets.spec.ts`.

Coverage gaps: `AssetsPage`, `MaintenanceItemFormPage`, `AssetReportPage`,
`WorkOrderPage`, and `LocationProblemDetailPage` have no dedicated unit test
today.

---

## Logistics dashboard

Staff manage day-to-day operational triage from a single landing dashboard.

| Journey | Entry route(s) | Auth | Key resilience expectations |
| --- | --- | --- | --- |
| Logistics dashboard (overview) | `/facilities/logistics` (`LogisticsDashboard.tsx`) | staff | Summarises pending reorders, low-stock, location problems, recent scans; loading + empty states per panel |
| TV dashboard (read-only display) | `/facilities/tv-dashboard`, `/facilities/tv-dashboard/:location` (`TVDashboard.tsx`) | public/staff | Auto-refresh; readable kiosk state on dependency failure |

Tests: `__tests__/pages/LogisticsDashboard.test.tsx`,
`__tests__/pages/DashboardPage.test.tsx`.

Coverage gap: `TVDashboard` has no dedicated unit test today (kiosk-style
display, primarily verified manually).

---

## Kiosk / screens

Public displays (kiosks, screens, TV dashboards) must run unattended.

| Journey | Entry route(s) | Auth | Key resilience expectations |
| --- | --- | --- | --- |
| Browse / configure screens | `/facilities/screens` (`ScreensListPage.tsx`) | staff | Loading + empty states |
| Edit screen content blocks | `/facilities/screens/:slug` (`ScreenEditPage.tsx`) | staff | Drag-to-reorder; save-failed banner |
| Public kiosk display | `/kiosk/:slug` (`KioskDisplayPage.tsx`) | public | Auto-refresh; offline shows fallback content; no auth required |

Tests: `__tests__/pages/ScreensListPage.test.tsx`,
`__tests__/pages/KioskDisplayPage.test.tsx`,
`__tests__/components/screens/SharedWeatherBlock.test.tsx`.

Coverage gap: `ScreenEditPage` has no dedicated unit test today.

---

## ForgeKey devices

Staff and admins manage networked door / equipment lockout devices.

| Journey | Entry route(s) | Auth | Key resilience expectations |
| --- | --- | --- | --- |
| Browse / authorize ForgeKey devices | `/facilities/forgekey-devices` (`ForgeKeyDevicesPage.tsx`) | staff/admin | Authorize / revoke gated by `is_superuser`; backend rejects insufficient privileges |
| Electrical circuits / breakers / outlets | `/facilities/electrical/*` (`ElectricalCircuitsPage.tsx`, `BreakerFormPage.tsx`, `BreakerTracePage.tsx`, `OutletFormPage.tsx`, `LightSwitchFormPage.tsx`, `NetworkDropFormPage.tsx`) | staff | Loading per panel; tree-view; trace operations gated by permissions |

Tests: `__tests__/pages/ForgeKeyDevicesPage.test.tsx`,
`__tests__/pages/ForgeKeyDeviceDetailPage.test.tsx`,
`__tests__/pages/ElectricalCircuitsPage.test.tsx`,
`__tests__/pages/BreakerTracePage.test.tsx`.

Coverage gap: `BreakerFormPage`, `OutletFormPage`, `LightSwitchFormPage`, and
`NetworkDropFormPage` have no dedicated unit test today.

---

## Settings (profile, site, webhooks)

Authenticated users manage their account, site configuration, and integrations.

| Journey | Entry route(s) | Auth | Key resilience expectations |
| --- | --- | --- | --- |
| User profile / preferences | `/settings/profile` (`UserProfilePage.tsx`) | member | Form save errors are field-level; password-change confirms before submit |
| Site settings (admin) | `/settings/site` (`SiteSettingsPage.tsx`) | admin | Forbidden state for non-admin |
| Browse webhooks | `/settings/webhooks` (`WebhookListPage.tsx`) | admin | Loading + empty states; test result visible |
| Create / edit webhook | `/settings/webhooks/new`, `/settings/webhooks/:id/edit` (`WebhookFormPage.tsx`) | admin | Test-send button surfaces transient errors |
| View webhook history | `/settings/webhooks/:id` (`WebhookDetailPage.tsx`) | admin | Recent deliveries with retry count |

Tests: `__tests__/pages/UserProfilePage.test.tsx`,
`__tests__/pages/WebhookListPage.test.tsx`.

Coverage gap: `SiteSettingsPage`, `WebhookFormPage`, and `WebhookDetailPage`
have no dedicated unit test today.

---

## Workspace navigation and cross-cutting controls

These are not single-page workflows but underpin every journey above.

| Journey | Entry component | Auth | Key resilience expectations |
| --- | --- | --- | --- |
| Workspace layout (sidebar, breadcrumbs, footer) | `WorkspaceLayout.tsx`, `Sidebar.tsx`, `Breadcrumbs.tsx`, `Footer.tsx` | mixed | Sidebar items hide for unauthorized roles via `requiresStaff` flags; backend remains authoritative |
| Command palette (keyboard-driven nav) | `CommandPalette.tsx` (Ctrl/Cmd+K) | mixed | Keyboard accessible; results filtered by role |
| Notification banner / center | `NotificationBanner.tsx`, `NotificationCenter.tsx` | mixed | Inline error / success surface; respects `prefers-reduced-motion` |
| Auth section (login / register / logout) | `AuthSection.tsx` | mixed | Inline login on home; session-expired prompt returns user to attempted route |
| Offline indicator | `OfflineIndicator.tsx` (driven by `useOnlineStatus`) | mixed | Visible whenever `navigator.onLine` is false |
| Install prompt (PWA) | `InstallPrompt.tsx` | mixed | Dismissible; respects user preference |
| Error fallback (Sentry boundary) | `ErrorFallback.tsx` | mixed | Catches React render exceptions; shows recovery action |

Tests: `__tests__/components/Sidebar.test.tsx`,
`__tests__/components/Breadcrumbs.test.tsx`,
`__tests__/components/Footer.test.tsx`,
`__tests__/components/WorkspaceLayout.test.tsx`,
`__tests__/components/NotificationBanner.test.tsx`,
`__tests__/components/NotificationBadge.test.tsx`,
`__tests__/components/AuthSection.test.tsx`,
`__tests__/components/SessionExpiredBanner.test.tsx`,
`__tests__/components/StatusState.test.tsx`.

Coverage gap: `CommandPalette`, `OfflineIndicator`, `InstallPrompt`, and
`ErrorFallback` have no dedicated unit test today.

---

## Resilience matrix

Each journey above is expected to handle the following states. The shared
`StatusState` component (`frontend/src/components/StatusState.tsx`) and the
`SessionExpiredBanner` component (`frontend/src/components/SessionExpiredBanner.tsx`)
provide the canonical surfaces.

| State | Trigger | Canonical surface |
| --- | --- | --- |
| Loading | Initial fetch in flight | `<StatusState variant="loading">` |
| Empty | Successful fetch returned no rows | `<StatusState variant="empty">` |
| Forbidden | Backend returned 403 (or role check failed client-side) | `<StatusState variant="forbidden">` |
| Missing | Backend returned 404 | `<StatusState variant="missing">` |
| Save failed | Mutation returned 4xx/5xx (other than 401) | `<StatusState variant="error">` plus inline field errors |
| Offline | `navigator.onLine === false` or `error.request` (no response) | `<OfflineIndicator>` plus `<StatusState variant="offline">` for the in-progress action |
| Session expired | API client refresh-token cycle failed | `<SessionExpiredBanner>` (preserves `location.pathname` so user returns after re-login) |

---

## How to use this inventory

- When adding a new page, append it to the appropriate workspace table here so
  resilience expectations are explicit.
- When changing route URLs, update the `Entry route(s)` column.
- When changing role gating, update `Auth` and verify the matching backend
  permission in `docs/API_PERMISSION_MATRIX.md`.
- When opening a Playwright scenario for a journey, add the spec name to the
  workspace's `Tests:` line.

The inventory is intentionally narrative rather than generated. Generated
inventories drift silently when routes change; a human-maintained list keeps
the resilience contract front-and-center during review.
