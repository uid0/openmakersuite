# Proficiency Metrics (AC-37, AC-38)

This document is the single source of truth for OpenMakerSuite's product proficiency dashboard. It maps each critical product path to the tests that prove it works (AC-37) and tracks the target/current status of every proficiency metric the project commits to (AC-38).

It is a working document. When a critical path changes, a metric target moves, or a new owner accepts a path, update this file in the same PR. The purpose is to make "is the product proficient yet?" a question we can answer by reading one file.

## Severity model

Same severity model as `docs/RISK_REGISTER.md`. Critical paths must have at least one automated owner (unit, integration, e2e) — manual is acceptable only for paths that are physically inseparable from hardware or a person.

## AC-37: Critical path coverage map

Each row maps a critical journey or surface to the test owner(s) responsible for keeping it green. "Coverage" is the lowest-cost layer that protects the path; higher layers may also exist.

### Backend permissions

| Critical path                                                  | Coverage   | Owner test(s)                                                                                                  |
| -------------------------------------------------------------- | ---------- | -------------------------------------------------------------------------------------------------------------- |
| API permission matrix (AC-1, AC-2)                             | unit       | `backend/config/tests/test_permission_matrix.py`; matrix doc at `docs/API_PERMISSION_MATRIX.md`.               |
| Public QR / report endpoints stay anonymous-writable (AC-5)    | integration| `backend/inventory/tests/test_asset_problem_photo.py::TestAssetProblemPhotoUpload`, `backend/checklists/tests/test_photo.py`. |
| Public write abuse controls (AC-6)                             | integration| `backend/inventory/tests/test_rate_limiting.py` (covers `inventory/utils/rate_limiting.py`).                   |
| Standardized API error envelope (AC-7)                         | integration| `backend/config/tests/test_api_errors.py` (this is the contract enforcement point).                            |
| List behavior — pagination, ordering, filtering (AC-8)         | integration| `backend/config/tests/test_list_contract.py::Test{Supplier,InventoryItem,Asset}ListContract`.                  |
| N+1 query bounds (AC-9)                                        | integration| `backend/config/tests/test_list_contract.py::TestQueryCountBounds`.                                            |
| OpenAPI schema validity + workflow paths (AC-10)               | unit + CI  | `backend/config/tests/test_schema.py` + ci.yml `Validate OpenAPI schema (AC-10)` step.                         |
| Liveness / readiness split (AC-11, AC-12)                      | unit       | `backend/config/tests/test_health.py`.                                                                          |
| Audit trail for safety/financial/device/vendor/donation/webhook/settings actions (AC-26, R-06) | integration | Per-domain emission: `backend/forgekey/tests/test_audit_events.py`, `backend/reorder_queue/tests/test_audit_events.py`, `backend/reorder_queue/tests/test_webhook_audit_events.py`, `backend/donations/tests/test_audit_events.py`, `backend/inventory/tests/test_audit_events.py`, `backend/vendors/tests/test_audit_events.py`, `backend/customization/tests.py::TestSiteSettingsAudit`, `backend/maintenance_orders/tests/test_phase5.py`. Unified staff-only feed + cross-domain integration + SET_NULL survival + secret redaction: `backend/dashboard/tests/test_audit_feed.py`, `backend/dashboard/tests/test_audit_feed_integration.py`. |

### Public workflows

| Critical path                                              | Coverage    | Owner test(s)                                                                                                |
| ---------------------------------------------------------- | ----------- | ------------------------------------------------------------------------------------------------------------ |
| Public QR scan → checklist completion (anonymous)          | integration | `backend/checklists/tests/test_api.py`, `backend/checklists/tests/test_photo.py`.                            |
| Public asset problem report                                | integration | `backend/inventory/tests/test_asset_problem_notification.py`, `test_asset_problem_photo.py`.                 |
| Public location problem report                             | integration | `backend/inventory/tests/test_location_problem.py`.                                                          |
| Public donation submission                                 | integration | `backend/donations/tests/test_signals.py`, `test_email_service.py`, `test_tasks.py`.                         |
| Public-to-staff loop (scan → triage → resolution)          | e2e         | `frontend/e2e/public-to-staff.spec.ts` (see `docs/frontend-e2e-playwright.md`).                              |

### Frontend journeys

| Critical path                                              | Coverage | Owner test(s)                                                                                                |
| ---------------------------------------------------------- | -------- | ------------------------------------------------------------------------------------------------------------ |
| Inventory browse + search                                  | unit + e2e | `frontend/src/__tests__/pages/InventoryListPage.test.tsx`, `InventoryItemDetailPage.test.tsx`; e2e in `frontend/e2e/inventory-browse.spec.ts`. |
| Reorder triage                                             | unit + e2e | `frontend/src/__tests__/pages/AdminDashboard.test.tsx`; e2e in `frontend/e2e/admin-dashboard-assets.spec.ts` and `public-to-staff.spec.ts`. |
| Asset problem reporting (staff side)                       | unit + e2e | `frontend/src/__tests__/pages/AssetDetailPage.test.tsx`, `AssetScanPage.test.tsx`; e2e in `frontend/e2e/asset-scan.spec.ts`. |
| Maintenance work order review                              | unit       | `frontend/src/__tests__/pages/ThirdPartyWorkOrderPage.test.tsx`, `MaintenanceDashboard.test.tsx`. Dedicated work-order e2e: gap. |
| Logistics dashboard                                        | unit       | `frontend/src/__tests__/pages/LogisticsDashboard.test.tsx`, `DashboardPage.test.tsx`.                        |
| Kiosk display                                              | unit + manual | `frontend/src/__tests__/pages/KioskDisplayPage.test.tsx`; hardware verification per `docs/FRONTEND_JOURNEY_INVENTORY.md`. |
| ForgeKey device management                                 | unit       | `frontend/src/__tests__/pages/ForgeKeyDevicesPage.test.tsx`, `ForgeKeyDeviceDetailPage.test.tsx`. Dedicated e2e: gap. |
| Settings + webhooks                                        | unit       | `frontend/src/__tests__/pages/UserProfilePage.test.tsx`, `WebhookListPage.test.tsx`.                         |

### Task workers

| Critical path                                              | Coverage    | Owner test(s)                                                                                                |
| ---------------------------------------------------------- | ----------- | ------------------------------------------------------------------------------------------------------------ |
| Celery task inventory (AC-28)                              | doc         | `docs/CELERY_TASKS.md` is the source of truth.                                                               |
| Task idempotency / retry behavior (AC-29, AC-30)           | unit        | `backend/config/tests/test_task_idempotency.py`.                                                             |
| Worker readiness probe (AC-32)                             | unit        | `backend/config/tests/test_health.py::test_readyz_*` covers broker reachability.                             |
| Scheduled task registration (AC-31)                        | unit        | `backend/config/tests/test_celery_schedule.py`.                                                              |

### Deployment

| Critical path                                              | Coverage   | Owner test(s)                                                                                                |
| ---------------------------------------------------------- | ---------- | ------------------------------------------------------------------------------------------------------------ |
| Production env validation (AC-23)                          | CI         | `.github/workflows/ci.yml` `Run prod env validator …` step + `scripts/validate-prod-env.sh`.                 |
| Backup / restore scripts (AC-25)                           | CI + manual| `.github/workflows/ci.yml` `Lint shell scripts` step + `backend/config/tests/test_backup_restore_scripts.py` covers parse/help for `scripts/backup-db.sh`, `restore-db.sh`, `backup-media.sh`, `restore-media.sh`, `backup-config.sh`, `smoke.sh`, `restore-drill.sh`; manual quarterly drill in `deploy/BACKUP_RESTORE.md` §8. |
| Production smoke checks (AC-35)                            | CI + manual| `.github/workflows/ci.yml` `Prod Stack Smoke (livez within 60s)` job + manual runbook in `deploy/SMOKE_TESTS.md`. |
| Deployment artifact validation (AC-36)                     | CI         | `.github/workflows/ci.yml` `Render docker-compose.prod.yml`, `helm lint`, `kubeconform` jobs.                |

## AC-38: Proficiency metrics

The metrics table is the operator-facing dashboard summary. Each metric has a target, a current observed value, and a measurement source so anyone can re-derive it.

| Metric                                            | Target           | Current          | Measurement source                                                                                                                  |
| ------------------------------------------------- | ---------------- | ---------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| Critical-path e2e coverage                        | 100% of `docs/FRONTEND_JOURNEY_INVENTORY.md` rows have a Playwright spec | Partial — `frontend/e2e/` covers public-to-staff loop UI-complete (`public-to-staff.spec.ts`), inventory browse + search (`inventory-browse.spec.ts`), mobile code-entry fallback (`code-entry-fallback.spec.ts`), asset-scan, admin-dashboard-assets, and SIG dashboard; reorder triage, work-order review, and ForgeKey e2e remain gaps | Run `frontend/e2e` and cross-reference inventory.                          |
| Permission coverage                               | Every API permission row in `docs/API_PERMISSION_MATRIX.md` has at least one passing test | Mitigated — staff/SIG-admin gate proven end-to-end for standard + third-party work orders (`backend/inventory/tests/test_work_order_permissions.py`, `backend/maintenance_orders/tests/test_api.py::test_sig_admin_can_list_third_party_work_orders`) and vendor writes (`backend/vendors/tests/test_api.py`); remaining matrix rows are still proven via the drift detector (`test_permission_matrix.py`) only — no end-to-end coverage gap remains for the rows called out in gh #374 | Run `pytest -k permission` and reconcile against matrix.                            |
| Worker health (broker reachability)               | `/api/health/readyz/` returns 200 with `broker: ok` for all configured workers              | Mitigated — readyz probe covers default broker (AC-12)                                          | `curl /api/health/readyz/` from the deployed cluster.                                  |
| Backup restore age                                | Most recent verified restore drill ≤ 90 days                                                | Partial — drill scripts shipped (`scripts/restore-drill.sh` orchestrates backup → restore → smoke; `scripts/smoke.sh --json` writes evidence) and CI parses them; quarterly cadence enforcement still open (R-03 in risk register) | Operator log; latest verified drill recorded in `deploy/BACKUP_RESTORE.md` §8 / drill output from `scripts/restore-drill.sh`. |
| Dependency age                                    | Backend Python deps ≤ 6 months out of date; frontend deps ≤ 12 months                       | Partial — `pip-audit` runs in pre-commit, no enforced cadence on frontend                       | `pip-audit` + `npm audit --audit-level=high`.                                          |
| Deployment smoke status                           | `deploy/SMOKE_TESTS.md` runbook executed within 24h of every production deploy              | Partial — runbook exists; CI's `Prod Stack Smoke (livez within 60s)` enforces livez within 60s, but post-deploy operator enforcement is manual | CI smoke job in `.github/workflows/ci.yml`; operator-captured outputs of `deploy/SMOKE_TESTS.md`. |
| OpenAPI schema validity                           | `manage.py spectacular --validate` exit 0 on every PR                                       | Mitigated — enforced as a CI step on the backend job (AC-10)                                    | `.github/workflows/ci.yml` `Validate OpenAPI schema (AC-10)` step.                     |
| API list-endpoint query bounds                    | All endpoints in `docs/API_LIST_CONTRACT.md` stay within the documented query budget        | Mitigated — `test_list_contract.py::TestQueryCountBounds` enforces (AC-9)                        | `pytest backend/config/tests/test_list_contract.py`.                                   |
| Audit trail coverage (AC-26, R-06)                | Every safety/financial/device/vendor/donation/webhook/settings workflow listed in `docs/RISK_REGISTER.md` R-06 surfaces through the unified `/api/dashboard/audit-feed/` endpoint with actor + timestamp + entity reference, and audit rows survive entity teardown via SET_NULL | Mitigated — per-domain audit tables exist for forgekey, purchase orders, webhooks, donations, maintenance, vendors, customization, and third-party work orders; `backend/dashboard/tests/test_audit_feed_integration.py` proves cross-domain coverage end-to-end. `ThirdPartyWorkOrderAuditLog` is the explicit accepted gap on SET_NULL (CASCADE by Phase-5 design; application code never deletes a WO). | `pytest backend/dashboard/tests/test_audit_feed_integration.py` and the per-domain `test_audit_events.py` suites. |

## Process

- **Adding a new critical path**: append a row to the relevant section of AC-37 with the owner test reference. If no automated test exists yet, the row's coverage column reads `manual` and the team must file a bead to back-fill an automated owner.
- **Moving a metric from "Open"/"Partial" to "Mitigated"**: update the current column, link the controlling test or runbook, and reference the AC number in the PR description.
- **Removing a critical path**: requires a paired update to `docs/RISK_REGISTER.md` to confirm no risk row depends on it.
