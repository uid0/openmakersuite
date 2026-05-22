# Application Risk Register (AC-22)

This register tracks the cross-cutting risks that can affect OpenMakerSuite
in production: security, data integrity, operational reliability, user
safety, privacy, dependency health, and physical-world makerspace
workflow risks.

It is a working document. When a control is added, a review changes the
state of a risk, or a new risk is uncovered, update this file in the same
PR — the register is the source of truth for the proficiency baseline,
not a snapshot.

## Severity model

- **Critical** — can expose sensitive data, create unsafe device or
  facility behaviour, lose operational data, or prevent recovery from a
  failed production deployment.
- **High** — can block important users, corrupt workflow state, hide
  failed work, or make production operation unreliable.
- **Medium** — friction, uneven behaviour, blind spots, or maintenance
  risk that should be corrected before broad adoption.
- **Low** — polish, documentation, consistency, or future-proofing.

## Mitigation status

- **Mitigated** — control is in place, tested, and exercised by CI or
  documented operator drill.
- **Partial** — control exists but does not cover the whole risk surface
  or lacks automated verification.
- **Planned** — work is committed but not yet implemented.
- **Open** — no control yet.

## Register

| ID | Severity | Likelihood | Risk | Affected Subsystem | Owner Role | Detection | Mitigation Status | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| R-01 | Critical | Medium | Production uses placeholder or hardcoded secrets, DSNs, hostnames, device tokens, signing keys, or default passwords. | Backend, deployment, ForgeKey, Sentry, EMQX | Operator/admin | Environment validation, CI secret scans, deployment smoke checks | Mitigated | `scripts/validate-prod-env.sh` rejects unsafe defaults; CI runs the validator against a happy-path env and asserts it rejects `.env.prod.example`. Coverage per safety class in `docs/PRODUCTION_SAFETY_COVERAGE.md` (R-01 section). |
| R-02 | Critical | Medium | Public unauthenticated endpoints allow unsafe write volume, sensitive data access, or administrative side effects. | Backend, frontend public QR flows | Maintainer | Permission matrix tests, rate-limit tests, abuse-path e2e | Partial | `docs/API_PERMISSION_MATRIX.md` declares per-endpoint abuse-control expectations; `docs/PRODUCTION_SAFETY_COVERAGE.md` (R-02 section) maps each declared expectation to current implementation status and lists the remaining `Gap` endpoints in the recommended follow-up order. |
| R-03 | Critical | Medium | Backups exist but restore is not verified. | Database, media, deployment | Operator | Scheduled restore drill, documented recovery check | Partial | Runbook in `deploy/BACKUP_RESTORE.md` covers DB + media + config; scripts shipped (`scripts/backup-db.sh`, `backup-media.sh`, `backup-config.sh`, `restore-db.sh`, `restore-media.sh`) and orchestrated end-to-end by `scripts/restore-drill.sh`; `scripts/smoke.sh --json` captures post-restore evidence. **DB half is CI-enforced**: every deploy-touching PR runs `restore-drill.sh --skip-media --skip-smoke` in the `Prod Stack Smoke` job + a `--dry-run` parse check in `Deploy Artifacts` (BACKUP_RESTORE §8.1). Media + config restore stay on the manual quarterly K8s cadence (§8.2). |
| R-04 | Critical | Low | Device control or lockout workflows fail silently. | ForgeKey, MQTT, Celery, EMQX | Facilities/admin | Task monitoring, device status tests, operator alerts | Partial | Device tests exist; task-result alerting is open. |
| R-05 | High | Medium | Celery tasks fail, retry forever, or duplicate external side effects. | Reorders, webhooks, donations, vendors, ForgeKey, location check-ins | Maintainer/operator | Task result dashboard, retry/idempotency tests | Partial | Idempotency expectations tracked in AC-30; per-task inventory in AC-28 is planned. |
| R-06 | High | Medium | Safety or financial actions lack audit trails. | Purchasing, maintenance, donations, ForgeKey, vendor compliance | Staff/admin | Audit-log tests, admin review pages | Mitigated | Per-domain audit tables (`ForgeKeyAuditEvent`, `PurchaseOrderAuditEvent`, `WebhookAuditEvent`, `DonationAuditEvent`, `MaintenanceAuditEvent`, `VendorAuditEvent`, `SiteSettingsAuditEvent`) plus the pre-existing `ThirdPartyWorkOrderAuditLog` are surfaced through the unified staff-only `/api/dashboard/audit-feed/` endpoint (`dashboard.audit_feed.collect_events`). Per-domain emission, secret redaction, and unauthorized-access denial are covered by each app's `tests/test_audit_events.py`; cross-domain integration, SET_NULL survival on entity teardown, and feed-response secret redaction are covered by `backend/dashboard/tests/test_audit_feed_integration.py` (AC-26). |
| R-07 | High | Medium | Deployment health is green while worker, Redis, database, media, certificate, or broker paths are unhealthy. | Hosting | Operator | Readiness checks and smoke tests | Mitigated | `/api/health/livez/` + `/api/health/readyz/` (AC-11/AC-12); smoke runbook in `deploy/SMOKE_TESTS.md`. |
| R-08 | High | Low | Liveness probe restarts containers when only a downstream dependency is unhealthy. | Hosting | Operator | Liveness/readiness split tests | Mitigated | Liveness performs no I/O; readiness reports per-dependency status (AC-11). |
| R-09 | Medium | Medium | Dependency vulnerabilities or unsupported runtime versions accumulate. | Backend, frontend, images | Maintainer | CI audits, Dependabot or equivalent, scheduled review | Partial | `pip-audit` runs in pre-commit; frontend audit cadence is open. |
| R-10 | Medium | Medium | Users cannot complete workflows on phones, kiosks, or low-connectivity networks. | Frontend | Product maintainer | Playwright mobile tests, manual smoke checklist | Partial | AC-14 / AC-15 / AC-16 tracked separately. |
| R-11 | Medium | Low | Observability captures too much PII or too little operational context. | Sentry, logs, API, Celery | Maintainer/operator | Sentry/log review, privacy checklist | Mitigated | `config.observability_redaction.redact` plus rollout to all four named surfaces (gh #333, #378): DRF error envelopes, webhook `last_error`, Celery `TaskResult.{traceback,result}` via `RedactingDatabaseBackend`, ForgeKey MQTT consumer (`OccupancyEvent.raw_payload`, OTA `error_message`), and the Highlight client (`networkHeadersToRedact`/`networkBodyKeysToRedact` + frontend `redactError`). |
| R-12 | High | Low | Readiness response leaks connection strings, credentials, or internal hostnames. | Hosting | Maintainer | Health probe tests | Mitigated | `test_readyz_response_does_not_leak_connection_strings` enforces. |

## Process

- New risks: add a row with the next ID, severity, likelihood, owner
  role, and an honest detection/mitigation state. Use `Open` instead of
  inventing controls.
- Mitigation work: when a PR adds or strengthens a control, edit the
  matching row's `Detection`/`Mitigation Status`/`Notes` columns in the
  same change.
- Periodic review: maintainers review the register at least once per
  release cycle. Rows that no longer apply are deleted, not silently
  archived; the git history preserves the reasoning.
