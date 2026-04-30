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
| API permission matrix (AC-1, AC-2)                             | unit       | `backend/config/tests/test_*_permissions.py` per app; matrix doc at `docs/API_PERMISSION_MATRIX.md`.           |
| Public QR / report endpoints stay anonymous-writable (AC-5)    | integration| `backend/inventory/tests/test_api.py::TestAssetProblemAPI`, `backend/checklists/tests/test_photo.py`.          |
| Public write abuse controls (AC-6)                             | integration| `backend/inventory/tests/test_*_rate_limit*` + `inventory/utils/rate_limiting.py` unit tests.                  |
| Standardized API error envelope (AC-7)                         | integration| `backend/config/tests/test_api_errors.py` (this is the contract enforcement point).                            |
| List behavior — pagination, ordering, filtering (AC-8)         | integration| `backend/config/tests/test_list_contract.py::Test{Supplier,InventoryItem,Asset}ListContract`.                  |
| N+1 query bounds (AC-9)                                        | integration| `backend/config/tests/test_list_contract.py::TestQueryCountBounds`.                                            |
| OpenAPI schema validity + workflow paths (AC-10)               | unit + CI  | `backend/config/tests/test_schema.py` + ci.yml `Validate OpenAPI schema (AC-10)` step.                         |
| Liveness / readiness split (AC-11, AC-12)                      | unit       | `backend/config/tests/test_health.py`.                                                                          |

### Public workflows

| Critical path                                              | Coverage    | Owner test(s)                                                                                                |
| ---------------------------------------------------------- | ----------- | ------------------------------------------------------------------------------------------------------------ |
| Public QR scan → checklist completion (anonymous)          | integration | `backend/checklists/tests/test_public_scan.py`, `backend/checklists/tests/test_photo.py`.                    |
| Public asset problem report                                | integration | `backend/inventory/tests/test_asset_problem_*.py`.                                                           |
| Public location problem report                             | integration | `backend/inventory/tests/test_location_problem.py`.                                                          |
| Public donation submission                                 | integration | `backend/donations/tests/test_*public*.py`.                                                                  |
| Public-to-staff loop (scan → triage → resolution)          | e2e         | `frontend/e2e/public-to-staff.spec.ts` (see `docs/frontend-e2e-playwright.md`).                              |

### Frontend journeys

| Critical path                                              | Coverage | Owner test(s)                                                                                                |
| ---------------------------------------------------------- | -------- | ------------------------------------------------------------------------------------------------------------ |
| Inventory browse + search                                  | unit + e2e | `frontend/src/pages/Inventory*.test.tsx`, `frontend/e2e/inventory-browse.spec.ts`.                         |
| Reorder triage                                             | unit + e2e | `frontend/src/pages/Reorder*.test.tsx`, `frontend/e2e/reorder-triage.spec.ts`.                             |
| Asset problem reporting (staff side)                       | unit + e2e | `frontend/src/pages/AssetProblem*.test.tsx`.                                                                 |
| Maintenance work order review                              | e2e        | `frontend/e2e/work-order-review.spec.ts`.                                                                    |
| Logistics dashboard                                        | unit       | `frontend/src/pages/Dashboard*.test.tsx`.                                                                    |
| Kiosk display                                              | manual     | Documented checklist in `docs/FRONTEND_JOURNEY_INVENTORY.md`. Hardware-dependent.                            |
| ForgeKey device management                                 | unit + e2e | `frontend/src/pages/ForgeKey*.test.tsx`, `frontend/e2e/forgekey.spec.ts`.                                    |
| Settings + webhooks                                        | unit       | `frontend/src/pages/Settings*.test.tsx`.                                                                     |

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
| Backup / restore scripts (AC-25)                           | CI + manual| `.github/workflows/ci.yml` `Lint shell scripts` step + manual quarterly drill in `deploy/BACKUP_RESTORE.md`. |
| Production smoke checks (AC-35)                            | CI + manual| `scripts/smoke.sh` (CI lints; operator runs against staging post-deploy).                                    |
| Deployment artifact validation (AC-36)                     | CI         | `.github/workflows/ci.yml` `Render docker-compose.prod.yml`, `helm lint`, `kubeconform` jobs.                |

## AC-38: Proficiency metrics

The metrics table is the operator-facing dashboard summary. Each metric has a target, a current observed value, and a measurement source so anyone can re-derive it.

| Metric                                            | Target           | Current          | Measurement source                                                                                                                  |
| ------------------------------------------------- | ---------------- | ---------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| Critical-path e2e coverage                        | 100% of `docs/FRONTEND_JOURNEY_INVENTORY.md` rows have a Playwright spec | Partial — public-to-staff loop covered (oms-0uw); kiosk + ForgeKey hardware paths remain manual | Run `frontend/e2e` and cross-reference inventory.                          |
| Permission coverage                               | Every API permission row in `docs/API_PERMISSION_MATRIX.md` has at least one passing test | Partial — covered for public/anonymous + staff write boundaries; SIG-admin matrix in progress | Run `pytest -k permission` and reconcile against matrix.                            |
| Worker health (broker reachability)               | `/api/health/readyz/` returns 200 with `broker: ok` for all configured workers              | Mitigated — readyz probe covers default broker (AC-12)                                          | `curl /api/health/readyz/` from the deployed cluster.                                  |
| Backup restore age                                | Most recent verified restore drill ≤ 90 days                                                | Open — drill cadence not yet enforced (R-03 in risk register)                                   | Operator log; tracked in `deploy/BACKUP_RESTORE.md`.                                   |
| Dependency age                                    | Backend Python deps ≤ 6 months out of date; frontend deps ≤ 12 months                       | Partial — `pip-audit` runs in pre-commit, no enforced cadence on frontend                       | `pip-audit` + `npm audit --audit-level=high`.                                          |
| Deployment smoke status                           | `scripts/smoke.sh` green within 24h of every production deploy                              | Partial — script exists, post-deploy enforcement is operator-driven                             | `scripts/smoke.sh` artifacts captured in operator runbook.                             |
| OpenAPI schema validity                           | `manage.py spectacular --validate` exit 0 on every PR                                       | Mitigated — enforced as a CI step on the backend job (AC-10)                                    | `.github/workflows/ci.yml` `Validate OpenAPI schema (AC-10)` step.                     |
| API list-endpoint query bounds                    | All endpoints in `docs/API_LIST_CONTRACT.md` stay within the documented query budget        | Mitigated — `test_list_contract.py::TestQueryCountBounds` enforces (AC-9)                        | `pytest backend/config/tests/test_list_contract.py`.                                   |

## Process

- **Adding a new critical path**: append a row to the relevant section of AC-37 with the owner test reference. If no automated test exists yet, the row's coverage column reads `manual` and the team must file a bead to back-fill an automated owner.
- **Moving a metric from "Open"/"Partial" to "Mitigated"**: update the current column, link the controlling test or runbook, and reference the AC number in the PR description.
- **Removing a critical path**: requires a paired update to `docs/RISK_REGISTER.md` to confirm no risk row depends on it.
