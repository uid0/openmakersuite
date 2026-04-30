# Product Proficiency Roadmap

## Context

OpenMakerSuite is a practical makerspace operations product: public QR workflows help members report supply, fixture, asset, donation, and location needs, while authenticated staff workflows manage inventory, purchasing, maintenance, kiosks, ForgeKey devices, vendor compliance, and notifications. The repository already has meaningful Django, React, Celery, Docker Compose, CI, and e2e foundations. The remaining gap is not a single missing feature; it is product proficiency across the whole operating surface.

This roadmap interprets modern "web 4.0" pragmatically: mobile-first public workflows, resilient APIs, safe unauthenticated actions, observable background jobs, secure production defaults, self-hostable deployment, and test coverage that proves the physical-world makerspace workflows still work.

## Severity Model

- **Critical**: A deficiency can expose sensitive data, create unsafe device or facility behavior, lose operational data, or prevent recovery from a failed production deployment.
- **High**: A deficiency can block important users, corrupt workflow state, hide failed work, or make production operation unreliable.
- **Medium**: A deficiency creates friction, uneven behavior, blind spots, or maintenance risk that should be corrected before broad adoption.
- **Low**: A deficiency is polish, documentation, consistency, or future-proofing work.

## Backend

### Deficiencies

| Severity | Deficiency | Impact |
| --- | --- | --- |
| Critical | Public, member, staff, and admin API boundaries are spread across viewsets and function views without a single permission matrix. | It is hard to prove anonymous QR flows stay open while financial, safety, device, and administrative writes stay protected. |
| High | Public endpoint behavior is not documented as an explicit API contract. | Frontend, kiosk, QR, and integration clients can accidentally depend on endpoints that later change or become protected. |
| High | Health checks prove basic HTTP availability, but readiness is not clearly separated for database, Redis, Celery broker, Celery workers, EMQX/MQTT, email, and external services. | Operators can see a green container while critical dependencies or worker paths are broken. |
| Medium | Error response shape, pagination, filters, and serializer validation are inconsistent across subsystems. | Frontend workflows need one-off handling and users see uneven failure states. |
| Medium | N+1 query protection exists in many places but is not enforced as a regression standard for high-volume list/detail endpoints. | Inventory, dashboard, purchasing, and asset pages can slow down as real makerspace data grows. |
| Medium | API schema coverage exists through drf-spectacular, but public/private workflow expectations are not validated against the schema. | Integrations and deployers cannot confidently treat the API as the product contract. |

### Remediation Plan

1. Create a backend permission and endpoint inventory covering inventory, assets, purchasing, maintenance, donations, screens, ForgeKey, search, notifications, membership, and customization.
2. Mark every endpoint as public, authenticated member, staff/logistics, admin/superuser, token-authenticated device, or webhook-secret protected.
3. Add permission tests for each endpoint class, especially public QR flows, purchase/order actions, asset safety/device actions, vendor compliance, donations, and settings.
4. Standardize API error payloads for validation, permission, not-found, rate-limit, dependency-unavailable, and async-task-queued responses.
5. Standardize list pagination and filter documentation for high-volume resources.
6. Add query-count regression tests for critical list/detail endpoints with representative fixture volume.
7. Add readiness endpoints that report component status without exposing secrets, and document which endpoint is for liveness versus readiness.
8. Validate the OpenAPI schema in CI and document public workflow endpoints as supported external contracts.

## Frontend

### Deficiencies

| Severity | Deficiency | Impact |
| --- | --- | --- |
| High | Public QR workflows need consistent mobile-first handling for loading, camera denial, offline/poor network, duplicate scans, and post-submit confirmation. | Members may abandon reports or accidentally create repeated work. |
| High | Auth expiration and API failures are handled centrally in the API client, but user-facing recovery is inconsistent across pages. | Staff can lose context during purchasing, maintenance, or admin triage. |
| High | Route visibility and route protection are mostly UI-driven, while backend permissions remain the final control. | Users may see actions they cannot complete, causing confusion and support burden. |
| Medium | Workspace navigation spans many domains, but consistent empty/loading/error states and breadcrumbs are not guaranteed across every page. | New operators and committee members need more cognitive effort to run routine workflows. |
| Medium | Accessibility expectations are not expressed as acceptance criteria for scan, dashboard, forms, tables, modals, command palette, and kiosk flows. | Keyboard users, screen reader users, and mobile users can be blocked by otherwise working features. |
| Medium | Playwright coverage exists but does not yet prove the full public-to-staff operating loop. | Regressions can ship in scan-to-triage-to-resolution workflows. |

### Remediation Plan

1. Define critical frontend journeys: public scan/report, inventory browse/search, reorder triage, purchase order creation/receipt, asset problem reporting, maintenance work order review, logistics dashboard, kiosk display, ForgeKey device management, and settings/webhooks.
2. Add consistent loading, empty, permission-denied, expired-session, dependency-failure, and offline states for every critical journey.
3. Make public QR flows mobile-first and resilient: code entry fallback, duplicate-submit prevention, camera permission fallback, and clear confirmation.
4. Ensure staff/admin-only actions are hidden or disabled with clear recovery paths, while backend permissions remain authoritative.
5. Add accessibility checks for keyboard navigation, visible focus, form labels, modal focus management, scanner fallback, table semantics, and kiosk readability.
6. Expand Playwright e2e tests to cover one complete public QR report through staff/admin triage and resolution.
7. Keep UI language operational and direct; avoid marketing-like surfaces for routine work tools.

## Application Risks

### Risk Register

| Severity | Risk | Affected Area | Owner Role | Detection |
| --- | --- | --- | --- | --- |
| Critical | Production uses placeholder or hardcoded secrets, DSNs, hostnames, device tokens, signing keys, or default passwords. | Backend, deployment, ForgeKey, Sentry, EMQX | Operator/admin | Environment validation, CI secret scans, deployment smoke checks |
| Critical | Public unauthenticated endpoints allow unsafe write volume, sensitive data access, or administrative side effects. | Backend, frontend public QR flows | Maintainer | Permission matrix tests, rate-limit tests, abuse-path e2e |
| Critical | Backups exist but restore is not verified. | Database, media, deployment | Operator | Scheduled restore drill, documented recovery check |
| Critical | Device control or lockout workflows fail silently. | ForgeKey, MQTT, Celery, EMQX | Facilities/admin | Task monitoring, device status tests, operator alerts |
| High | Celery tasks fail, retry forever, or duplicate external side effects. | Reorders, webhooks, donations, vendors, ForgeKey, location check-ins | Maintainer/operator | Task result dashboard, retry/idempotency tests |
| High | Safety or financial actions lack audit trails. | Purchasing, maintenance, donations, ForgeKey, vendor compliance | Staff/admin | Audit-log tests, admin review pages |
| High | Deployment health is green while worker, Redis, database, media, certificate, or broker paths are unhealthy. | Hosting | Operator | Readiness checks and smoke tests |
| Medium | Dependency vulnerabilities or unsupported runtime versions accumulate. | Backend, frontend, images | Maintainer | CI audits, Dependabot or equivalent, scheduled review |
| Medium | Users cannot complete workflows on phones, kiosks, or low-connectivity networks. | Frontend | Product maintainer | Playwright mobile tests, manual smoke checklist |
| Medium | Observability captures too much PII or too little operational context. | Sentry, logs, API, Celery | Maintainer/operator | Sentry/log review, privacy checklist |

### Proficiency Project Plan

1. **Risk baseline**: Add a tracked risk register to project docs with severity, likelihood, owner role, detection method, and mitigation status.
2. **Security baseline**: Require production environment validation for secrets, DEBUG, allowed hosts, CSRF/CORS origins, cookie settings, Sentry DSNs, ForgeKey keys, EMQX credentials, webhook tokens, and email settings.
3. **Abuse protection**: Add rate limiting or equivalent abuse controls to public write endpoints while preserving the makerspace default of open reporting.
4. **Data protection**: Document and test backup/restore for PostgreSQL, media uploads, configuration, and deployment state.
5. **Auditability**: Add or verify audit trails for safety, financial, device, vendor, donation, and maintenance state changes.
6. **Operational proficiency**: Define production smoke checks for frontend, backend, API docs, admin access, public QR flows, database, Redis, Celery, media, EMQX, and webhook/email paths.
7. **Maturity metrics**: Track critical-path e2e coverage, permission coverage, worker health, backup restore age, dependency age, and deployment smoke status.

## Task Workers

### Deficiencies

| Severity | Deficiency | Impact |
| --- | --- | --- |
| High | Celery worker and beat responsibilities are not presented as a complete operational contract. | Operators may deploy the web app but miss scheduled or async work. |
| High | Retry/idempotency behavior is unevenly documented for webhooks, notifications, ForgeKey/MQTT actions, donations, vendor compliance, photo pruning, and check-in alerts. | Duplicate notifications, missed work, or repeated device commands can occur under transient failures. |
| High | Failed task visibility depends on Flower/admin/task results, but operator recovery paths are not clearly defined. | Failed webhooks or device operations can remain invisible until users complain. |
| Medium | Scheduled task cadence is configured, but not all periodic work has acceptance tests or documented verification. | Daily/quarterly/retention work can drift or stop silently. |

### Remediation Plan

1. Inventory every task, trigger, queue, retry policy, timeout, side effect, and owner workflow.
2. Separate task classes: notification, webhook, device command, scheduled maintenance, report generation, cleanup, and external sync.
3. Add idempotency keys or duplicate-suppression behavior where tasks call external systems or mutate business state.
4. Add retry/backoff policies and failure caps for transient network operations.
5. Add task-result visibility in staff/admin surfaces or documented operator commands for failed task review and replay.
6. Add worker and beat readiness checks, including whether a worker is consuming the expected queue.
7. Add tests for scheduled task registration or documented operator verification for each periodic task.

## Deployment And Hosting

### Deficiencies

| Severity | Deficiency | Impact |
| --- | --- | --- |
| Critical | Production deployment still contains project-specific defaults and examples that can be mistaken for general self-hosting values. | New operators can deploy with wrong domains, DSNs, credentials, or external service assumptions. |
| Critical | Restore, rollback, and smoke-test steps exist in pieces but are not proven as a complete release safety process. | A failed deployment or data incident can take longer to recover from than acceptable. |
| High | EMQX/MQTT, Flower, cert handling, Sentry, and external webhooks need clearer production exposure and credential guidance. | Operational tools or device brokers can be overexposed or misconfigured. |
| High | Docker Compose production is the primary path, while Kubernetes/Helm deployment criteria are already planned but not implemented. | Larger makerspaces or hosted deployments lack a standard path beyond single-host Compose. |
| Medium | CI validates many surfaces, but deployment validation should include rendered configs, env examples, smoke scripts, and backup scripts. | Deployment drift can ship without failing CI. |

### Remediation Plan

1. Convert project-specific deployment defaults to neutral examples or documented sample overlays.
2. Add an environment validation script that fails production deploys on unsafe values.
3. Document production network exposure for nginx, backend, frontend, Flower, EMQX dashboard/API, MQTT ports, media/static volumes, and certificates.
4. Add provider-neutral Docker Compose runbooks for install, upgrade, rollback, backup, restore, logs, worker checks, and smoke tests.
5. Add CI checks for Compose config rendering, env example completeness, smoke script syntax, backup/restore script syntax, and production Docker builds.
6. Implement the existing API-first deployment automation plan for Kubernetes manifests, Helm chart, artifact publishing, and manifest/chart validation.
7. Require every release path to prove migrations, static/media serving, API docs, admin access, public QR workflow, Celery, Redis, database, and nginx health.

## Suggested Delivery Order

1. **Critical safety baseline**: permission matrix, production env validation, public endpoint abuse controls, readiness checks, backup/restore verification.
2. **Workflow proficiency**: mobile QR resilience, staff/admin error states, route/action clarity, critical e2e journeys.
3. **Worker proficiency**: task inventory, retries, idempotency, failed task visibility, beat verification.
4. **Operational proficiency**: release smoke tests, runbooks, CI deployment validation, observability/privacy tuning.
5. **Scale and portability**: query-count regression tests, API schema validation, Kubernetes and Helm support.

## Beads Task Seed

Use these `bd` commands to seed implementation work from this roadmap. They create one epic, five implementation tasks, and dependency edges matching the delivery order above. Run them from the repository root after installing Beads and syncing the tracker.

The commands intentionally avoid `bd create --parent` because this repository has had multiple Beads ID prefixes over time. Creating issues first and linking them afterward avoids child-ID prefix mismatches.

```bash
set -euo pipefail

git config beads.role maintainer
bd sync

EPIC=$(
  bd create "Product proficiency roadmap" \
    -t epic \
    -p 1 \
    -l "product,proficiency,roadmap" \
    --description "Bring OpenMakerSuite to a proficient baseline across backend APIs, frontend workflows, application risks, Celery workers, and self-hosted deployment. Source: docs/PRODUCT_PROFICIENCY_ROADMAP.md and .criteria/product-proficiency-roadmap.md." \
    --json | jq -r '.id'
)

SAFETY=$(
  bd create "Critical safety baseline for product proficiency" \
    -t task \
    -p 0 \
    -l "backend,security,risk,deployment" \
    --description "Implement the permission matrix, public endpoint contract, abuse controls, production environment validation, liveness/readiness split, and backup/restore verification. Covers AC-2 through AC-12 and AC-22 through AC-27." \
    --json | jq -r '.id'
)

WORKFLOW=$(
  bd create "Frontend workflow proficiency" \
    -t task \
    -p 1 \
    -l "frontend,accessibility,e2e,public-workflows" \
    --description "Harden public scan/report flows, staff/admin recovery states, route/action clarity, accessibility, and the public-to-staff Playwright loop. Covers AC-13 through AC-21." \
    --json | jq -r '.id'
)

WORKERS=$(
  bd create "Task worker proficiency" \
    -t task \
    -p 1 \
    -l "celery,workers,observability" \
    --description "Inventory Celery tasks, document worker/beat deployment, add idempotency and retry expectations, expose failed task recovery, and verify scheduled tasks. Covers AC-28 through AC-32." \
    --json | jq -r '.id'
)

OPS=$(
  bd create "Deployment and hosting proficiency" \
    -t task \
    -p 1 \
    -l "deployment,hosting,ci,runbooks" \
    --description "Complete Docker Compose production runbooks, service exposure guidance, deployment smoke tests, and CI validation for deployment artifacts. Covers AC-23 through AC-25 and AC-33 through AC-36." \
    --json | jq -r '.id'
)

SCALE=$(
  bd create "API scale and contract proficiency" \
    -t task \
    -p 2 \
    -l "backend,api,performance,openapi" \
    --description "Standardize API errors, list behavior, schema validation, and query-count regression tests for critical high-volume endpoints. Covers AC-7 through AC-10 and AC-37 through AC-38." \
    --json | jq -r '.id'
)

for id in "$EPIC" "$SAFETY" "$WORKFLOW" "$WORKERS" "$OPS" "$SCALE"; do
  test -n "$id"
done

bd dep add "$SAFETY" "$EPIC" -t parent-child
bd dep add "$WORKFLOW" "$EPIC" -t parent-child
bd dep add "$WORKERS" "$EPIC" -t parent-child
bd dep add "$OPS" "$EPIC" -t parent-child
bd dep add "$SCALE" "$EPIC" -t parent-child

bd dep add "$WORKFLOW" "$SAFETY"
bd dep add "$WORKERS" "$SAFETY"
bd dep add "$OPS" "$SAFETY"
bd dep add "$SCALE" "$SAFETY"
bd dep add "$SCALE" "$WORKFLOW"
bd dep add "$SCALE" "$WORKERS"

bd dep tree "$EPIC"
bd ready
```

## Acceptance Signals

OpenMakerSuite should be considered proficient when:

- A new operator can self-host it with production-safe configuration and documented smoke checks.
- A member can complete public QR workflows from a phone without an account, even when camera or network conditions are imperfect.
- Staff can triage and resolve reports, purchases, maintenance, vendor, donation, and device work with clear state, error recovery, and auditability.
- CI proves critical backend permissions, frontend journeys, task behavior, deployment rendering, and backup/restore scripts.
- Operators can identify failed workers, failed integrations, unsafe configuration, and stale backups before users discover the issue.
