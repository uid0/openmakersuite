# Product proficiency roadmap

## Context
OpenMakerSuite has grown into a broad makerspace operations product spanning public QR workflows, inventory, purchasing, assets, maintenance, kiosks, ForgeKey devices, donations, notifications, workers, and self-hosted deployment. The next product step is to bring the whole application to a proficient operating baseline: mobile-friendly public flows, explicit API boundaries, secure defaults, resilient task workers, observable production health, and meaningful tests.

## Scope
- In: Backend API boundaries, public QR workflows, frontend workflow resilience, application risk register, security/data-protection controls, Celery task proficiency, Docker Compose deployment hardening, deployment runbooks, smoke checks, CI validation, and testing expectations.
- Out: Replacing Django, replacing React, changing the makerspace default of open public reporting, implementing Kubernetes/Helm beyond the existing API-first deployment automation criteria, adding a required paid hosting provider, and rewriting product domains unrelated to proficiency.

## Criteria

### AC-1: Roadmap document exists
- **Given** a maintainer wants to understand the product proficiency work
- **When** they open `docs/PRODUCT_PROFICIENCY_ROADMAP.md`
- **Then** they see deficiencies, severity, and step-by-step remediation plans grouped by backend, frontend, application risks, task workers, and deployment/hosting

### AC-2: Backend endpoint permission matrix exists
- **Given** the backend exposes APIs across all installed product apps
- **When** maintainers review the backend proficiency work
- **Then** every endpoint is classified as public, authenticated member, staff/logistics, admin/superuser, token-authenticated device, or webhook-secret protected

### AC-3: Public endpoint contract is documented
- **Given** a public QR, kiosk, donation, transparency, health, or integration endpoint is intentionally unauthenticated
- **When** maintainers review API documentation or the endpoint matrix
- **Then** the endpoint is explicitly listed as public with its supported method, purpose, expected payload, and abuse-control expectation

### AC-4: Protected endpoint permissions are tested
- **Given** an endpoint mutates financial, safety, device, vendor, donation, maintenance, user, settings, webhook, or administrative state
- **When** backend tests run
- **Then** anonymous and insufficiently privileged users are denied, while the appropriate authorized role can complete the action

### AC-5: Public makerspace workflows stay open
- **Given** a non-authenticated makerspace member scans or enters a valid public code
- **When** they submit an allowed report or request
- **Then** the workflow succeeds without requiring login, and does not expose staff-only data or actions

### AC-6: Public write endpoints have abuse controls
- **Given** an unauthenticated client repeatedly submits to public write endpoints
- **When** the client exceeds the documented limit or duplicate-submission rule
- **Then** the API returns an observable throttling or duplicate response without creating unbounded duplicate operational work

### AC-7: API error responses are consistent
- **Given** the API returns validation, permission, not-found, rate-limit, dependency-unavailable, or async-task-queued responses
- **When** frontend or integration clients inspect the response body
- **Then** the response uses a documented shape that includes a user-safe message and machine-readable status or error code

### AC-8: API list behavior is consistent
- **Given** clients request high-volume list endpoints for inventory, assets, locations, suppliers, reorders, purchase orders, work orders, donations, screens, devices, or notifications
- **When** those endpoints return results
- **Then** pagination, ordering, and supported filters are documented and covered by tests

### AC-9: Critical API queries avoid N+1 regressions
- **Given** representative fixture data includes related categories, locations, suppliers, assets, purchase orders, work orders, and notifications
- **When** tests request critical list and detail endpoints
- **Then** query-count assertions prove the endpoints do not regress into N+1 behavior

### AC-10: API schema is validated
- **Given** CI runs for backend changes
- **When** the OpenAPI schema is generated
- **Then** schema generation succeeds, public/private workflow expectations are represented, and schema validation failures fail CI

### AC-11: Liveness and readiness are distinct
- **Given** the application is deployed in production
- **When** an operator or container health check calls the liveness endpoint
- **Then** it reports process availability without requiring every dependency to be healthy

### AC-12: Readiness checks critical dependencies
- **Given** the application is deployed in production
- **When** an operator calls the readiness endpoint
- **Then** it checks database, Redis/cache, Celery broker, worker reachability where feasible, configured email/webhook dependencies, and configured EMQX/MQTT dependencies without exposing secrets

### AC-13: Frontend critical journey inventory exists
- **Given** maintainers review frontend proficiency work
- **When** they inspect the documented workflow inventory
- **Then** scan/report, inventory browse/search, reorder triage, purchasing, asset maintenance, logistics dashboard, kiosk/screens, ForgeKey devices, settings, and webhooks are listed as critical journeys

### AC-14: Public scan flows are mobile resilient
- **Given** an unauthenticated user opens a public scan workflow on a phone-sized viewport
- **When** camera access is denied, unavailable, or the code cannot be scanned
- **Then** the user can still complete the workflow through a code-entry fallback

### AC-15: Public scan submissions prevent accidental duplicates
- **Given** an unauthenticated user submits a public report or request
- **When** they tap repeatedly, refresh after success, or briefly lose connectivity
- **Then** the UI prevents obvious duplicate submissions and shows a clear final state

### AC-16: Frontend offline and poor-network states are actionable
- **Given** a critical frontend journey loses network access or receives a dependency failure
- **When** the user attempts to continue
- **Then** the page shows an actionable state that explains whether to retry, wait, use code entry, or contact staff

### AC-17: Auth expiration preserves user context
- **Given** an authenticated staff user is working in a critical admin workflow
- **When** their session or token expires
- **Then** the UI prompts for recovery and returns them to the attempted workflow after successful login where feasible

### AC-18: Protected frontend actions match backend permissions
- **Given** a user lacks permission for a staff, admin, safety, financial, device, settings, or webhook action
- **When** they view the related frontend page
- **Then** the action is hidden or disabled with a clear recovery path, and direct API calls remain denied by backend tests

### AC-19: Frontend loading, empty, and error states are consistent
- **Given** a critical frontend journey is loading, has no data, is forbidden, is missing data, or fails to save
- **When** the state occurs
- **Then** the page renders a consistent, accessible state rather than a blank screen or raw exception

### AC-20: Frontend accessibility is tested for critical controls
- **Given** users navigate scan flows, forms, tables, modals, command palette, workspace navigation, and kiosks
- **When** frontend tests and e2e tests run
- **Then** keyboard access, visible focus, labels, modal focus management, table semantics, and readable kiosk states are verified for critical controls

### AC-21: Playwright covers a public-to-staff loop
- **Given** the frontend e2e suite runs
- **When** it executes the product proficiency scenario
- **Then** it completes at least one public QR report or request and verifies that an authorized staff/admin user can triage or resolve the resulting work

### AC-22: Application risk register exists
- **Given** maintainers review project risk documentation
- **When** they open the risk register
- **Then** security, data integrity, operational reliability, user safety, privacy, dependency health, and physical-world makerspace workflow risks are listed with severity, likelihood, affected subsystem, owner role, detection method, and mitigation status

### AC-23: Production environment validation rejects unsafe defaults
- **Given** an operator runs production deployment validation
- **When** DEBUG, SECRET_KEY, ALLOWED_HOSTS, CSRF/CORS origins, cookie security, Sentry DSNs, ForgeKey secrets, EMQX credentials, webhook tokens, email settings, or signing keys contain unsafe placeholders or missing required values
- **Then** validation fails with an actionable message before deployment proceeds

### AC-24: Production configuration avoids hardcoded project-specific defaults
- **Given** a new operator reviews production examples and deployment files
- **When** they configure a self-hosted installation
- **Then** domains, credentials, Sentry projects, device secrets, webhook tokens, and external service values are neutral examples or environment-driven values rather than Dallas-specific operational defaults

### AC-25: Data backup and restore are verified
- **Given** an operator follows the backup and restore runbook
- **When** they back up and restore PostgreSQL data, media uploads, and deployment configuration
- **Then** smoke checks prove restored frontend, backend, admin, public QR workflow, and media access are functional

### AC-26: Safety and financial actions are auditable
- **Given** a user changes purchase order, receipt, donation receipt, maintenance, vendor compliance, device authorization, lockout, firmware, webhook, or site settings state
- **When** an authorized reviewer inspects the system
- **Then** they can see who performed the action, when it happened, what changed, and any relevant reason or note

### AC-27: Observability protects privacy
- **Given** API errors, frontend errors, Celery failures, and deployment smoke failures are reported to logs or Sentry
- **When** maintainers review captured events
- **Then** events include enough operational context to debug while avoiding unnecessary secrets, tokens, passwords, or sensitive member data

### AC-28: Celery task inventory exists
- **Given** maintainers review task-worker proficiency work
- **When** they inspect the task inventory
- **Then** every Celery task is listed with trigger, queue or worker expectation, side effects, retry policy, timeout, idempotency expectation, owner workflow, and recovery path

### AC-29: Worker and beat deployment are explicit
- **Given** an operator deploys OpenMakerSuite
- **When** they follow the deployment runbook
- **Then** web, worker, and scheduled-task processes are described as required or optional with commands, health checks, and verification steps

### AC-30: External side-effect tasks are idempotent
- **Given** a task sends webhooks, emails, MQTT commands, device firmware notifications, donation updates, vendor compliance alerts, or location/security alerts
- **When** the task retries after a transient failure
- **Then** duplicate external side effects are prevented or clearly marked as duplicate-safe by tests

### AC-31: Task retries and failures are visible
- **Given** a task exhausts retries or fails permanently
- **When** staff or operators review task status
- **Then** the failure is visible through an admin/staff surface or documented command, and the recovery or replay path is documented

### AC-32: Scheduled tasks are verified
- **Given** Celery beat or scheduled task configuration changes
- **When** tests or documented operator checks run
- **Then** donation updates, vendor compliance checks, cleanup/retention tasks, and other scheduled work are verified for registration and expected cadence

### AC-33: Deployment runbooks are complete
- **Given** a self-hosting operator opens the deployment documentation
- **When** they follow the Docker Compose production path
- **Then** they can install, configure, deploy, upgrade, roll back, back up, restore, inspect logs, check workers, and run smoke tests without relying on undocumented project knowledge

### AC-34: Production network exposure is documented
- **Given** an operator deploys nginx, backend, frontend, Flower, EMQX dashboard/API, MQTT ports, static files, media files, and certificate handling
- **When** they review hosting documentation
- **Then** each service has documented exposure guidance, default exposure state, credential requirements, and production hardening notes

### AC-35: Deployment smoke tests cover the whole product
- **Given** a deployment completes
- **When** the operator runs documented smoke checks
- **Then** frontend availability, backend liveness/readiness, API docs, admin login, public QR workflow, database, Redis, Celery worker, scheduled tasks, static files, media files, nginx routing, and configured EMQX/webhook/email paths are verified

### AC-36: CI validates deployment artifacts
- **Given** a pull request changes deployment, scripts, environment examples, Docker, nginx, or CI files
- **When** CI runs
- **Then** Docker Compose config rendering, production env example completeness, smoke script syntax, backup/restore script syntax, production Docker builds, and relevant deployment documentation checks pass before merge

### AC-37: Test coverage reflects critical product paths
- **Given** maintainers review automated test coverage
- **When** they compare tests to the documented critical journeys and risk register
- **Then** each critical backend permission, public workflow, frontend journey, task behavior, and deployment smoke path has a unit, integration, e2e, or documented manual verification owner

### AC-38: Proficiency metrics are documented
- **Given** maintainers review product health
- **When** they inspect the proficiency metrics
- **Then** they can see the target and current status for critical-path e2e coverage, permission coverage, worker health, backup restore age, dependency age, and deployment smoke status
