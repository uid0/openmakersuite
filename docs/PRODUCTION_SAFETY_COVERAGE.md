# Production Safety Coverage (gh-455 / R-01, R-02)

This document is the safety-baseline coverage table referenced by the
[product proficiency roadmap](./PRODUCT_PROFICIENCY_ROADMAP.md) for
acceptance criteria **AC-5, AC-6, AC-22, AC-23, AC-24, AC-37, AC-38**
and by the [risk register](./RISK_REGISTER.md) for rows **R-01** and
**R-02**.

It maps the two "production safety baseline" concerns from gh-455
("unsafe defaults" and "public write abuse controls") to the controls
that exist in the codebase today, the evidence that proves they exist,
and the remaining gaps that follow-up PRs will close one safety class
at a time per the iterative compliance plan on the parent issue.

Coverage status uses the same vocabulary as the risk register:

- **Mitigated** — control is in place, tested, and exercised by CI or
  a documented operator drill.
- **Partial** — control exists but does not cover the whole risk
  surface or lacks automated verification.
- **Gap** — no control yet.

## R-01 — Unsafe production defaults (AC-23, AC-24)

The goal: production deployments cannot ship with placeholder secrets,
plaintext-cookie overrides, missing observability sinks, or
project-specific hardcoded defaults.

### Configuration class coverage

All sections of `scripts/validate-prod-env.sh` listed below are
covered by parametrized cases in
`backend/config/tests/test_deployment_validation.py::TestProductionEnvValidatorAC23`,
which drives the validator with synthesised env files and asserts the
expected pass / fail outcome. Specific test names are linked where
they isolate a single rule.

| Safety class | Expectation | Status | Validator section | Test evidence |
| --- | --- | --- | --- | --- |
| `DEBUG=False` enforced | Deploy refuses to start when `DEBUG` is truthy. | Mitigated | `scripts/validate-prod-env.sh:105-111` | `test_unsafe_value_rejected` (parametrized over `DEBUG=1`, etc.). |
| `SECRET_KEY` strength | Set, non-placeholder, ≥ 50 chars. | Mitigated | `scripts/validate-prod-env.sh:113-119` | `test_unsafe_value_rejected` (parametrized). |
| `ALLOWED_HOSTS` + `CSRF_TRUSTED_ORIGINS` + `CORS_ALLOWED_ORIGINS` | `ALLOWED_HOSTS` non-empty and not loopback-only; both origins lists are `https://`. | Mitigated | `scripts/validate-prod-env.sh:121-142` (helper `is_https_csv()` at lines 88-103) | `test_unsafe_value_rejected` (parametrized). |
| Letsencrypt / domain identity | `DOMAIN`, `LETSENCRYPT_EMAIL`, `LETSENCRYPT_DOMAINS` set + non-placeholder. | Mitigated | `scripts/validate-prod-env.sh:144-154` | `test_unsafe_value_rejected` (parametrized). |
| `POSTGRES_PASSWORD` strength | Non-placeholder, ≥ 16 chars. | Mitigated | `scripts/validate-prod-env.sh:156-164` | `test_unsafe_value_rejected` (parametrized). |
| Redis + Celery broker URLs | Both required. | Mitigated | `scripts/validate-prod-env.sh:166-169` | `test_unsafe_value_rejected` (parametrized). |
| `EMQX_DASHBOARD_PASSWORD` complexity | 8+ chars, mixed case, digit, non-placeholder. | Mitigated | `scripts/validate-prod-env.sh:171-195` | `test_unsafe_value_rejected` (parametrized). |
| `FORGEKEY_FIRMWARE_SIGNING_KEY` PEM shape | When set, must look like a PEM block. | Mitigated | `scripts/validate-prod-env.sh:197-204` | `test_unsafe_value_rejected` (parametrized). |
| `FORGEKEY_PROVISIONING_TOKEN`, webhook tokens | Warned (non-fatal) when empty / placeholder. | Mitigated | `scripts/validate-prod-env.sh:206-213`, `315-324` | `test_warnings_do_not_fail_deploy`. |
| `EMAIL_BACKEND` not console in prod | Refuses console backend; postmark backend requires token. | Mitigated | `scripts/validate-prod-env.sh:230-247` | `test_postmark_backend_requires_token`. |
| Observability sink | Sentry DSN configured, https-only. | Mitigated | `scripts/validate-prod-env.sh:248-280` | `test_sentry_https_value_allowed`, `test_empty_sentry_value_allowed`, `test_observability_warning_when_sentry_unset`, `test_observability_warning_absent_when_sentry_present`. |
| Cookie security overrides | Warns when `SESSION_COOKIE_SECURE=0` or `CSRF_COOKIE_SECURE=0` while `DEBUG=0`. | Mitigated | `scripts/validate-prod-env.sh:292-313` | `test_cookie_secure_falsy_override_warns`, `test_cookie_secure_unset_has_no_warning`. |
| Quoted env values parse safely | Single- and double-quoted values are unwrapped, not eval'd. | Mitigated | `scripts/validate-prod-env.sh:49-70` | `test_validator_handles_quoted_values`. |
| Neutral production examples (AC-24) | `.env.example`, `.env.prod.example`, `backend/env.production.example` do not encode Dallas-specific or maintainer-local values. | Mitigated | n/a (asserted directly against example files) | `TestNoOperatorSpecificDefaultsAC24::test_no_dallas_specific_strings`, `::test_settings_defaults_are_neutral`. |
| Example files include every key the validator requires | Adding a fatal validator check without updating the example would mask the regression. | Mitigated | n/a | `TestDeploymentArtifactsAC36::test_env_example_covers_validator_required_keys`. |
| Permission matrix drift | Live URL conf must match `backend/config/api_permission_matrix.yaml`. | Mitigated | n/a | `backend/config/tests/test_permission_matrix.py`; `backend/config/management/commands/check_permission_matrix.py`. |

### Integration of the validator

| Integration point | Status | Evidence |
| --- | --- | --- |
| `scripts/validate-prod-env.sh` runs against a synthesised good env in CI | Mitigated | `.github/workflows/ci.yml` step "Run prod env validator against happy-path env". |
| `scripts/validate-prod-env.sh` is asserted to **reject** `.env.prod.example` (so example files cannot accidentally become deployable) | Mitigated | `.github/workflows/ci.yml` step "Validator rejects shipped placeholder env". |
| Validator is invoked by the deploy script before bringing the stack up | Mitigated | Referenced from `docs/DEPLOYMENT.MD`; called from `scripts/reset-and-deploy.sh`. |
| Runtime defense-in-depth — `manage.py validate_production` runs against the LIVE Django settings on every container start | Mitigated (gh-710) | `backend/docker-entrypoint.sh`; `backend/config/validators/django_core.py`; `backend/config/tests/test_validate_production.py`. Catches drift between `.env` and the loaded settings (overrides in `settings.py`, env-var clobbers at container start, etc.). |

### R-01 gaps

None that block AC-23 / AC-24 acceptance. Future work that may surface
new safety classes (additional integrations, new device-class secrets)
must add the new key to the validator, add a parametrized test case to
`test_deployment_validation.py`, and add a row to the table above in
the same PR.

## R-02 — Public unauthenticated write abuse controls (AC-5, AC-6)

The goal: public makerspace workflows remain open by design (AC-5)
while unauthenticated *write* endpoints have observable throttling,
deduplication, or signed-token guards proportional to their risk
(AC-6). The
[API permission matrix](./API_PERMISSION_MATRIX.md) is the
authoritative declaration of which public endpoints exist and what
abuse-control class each one expects. This table maps each declared
expectation to the implementation in code and identifies the gaps.

### Public write endpoints — declared vs. implemented controls

| Endpoint | Matrix expectation | Implementation | Status |
| --- | --- | --- | --- |
| `POST /api/auth/register/` | Account-creation throttle. | `backend/auth_views.py`'s `register_user` is `@permission_classes([AllowAny])`; no throttle, no dedupe. | Gap |
| `POST /api/auth/login/` | Login-attempt throttle. | `backend/auth_views.py`'s `login_user` is `@permission_classes([AllowAny])`; no throttle. | Gap |
| `POST /api/auth/refresh/` | Refresh throttle. | `backend/auth_views.py`'s `refresh_token` is `@permission_classes([AllowAny])`; no throttle. | Gap |
| `POST /api/auth/passkey/register/` (begin) | Required on registration. | Begin endpoint accepts anonymous callers; assertion step is signed. | Partial — signed assertion mitigates replay; registration begin is unthrottled. |
| `POST /api/inventory/items/<id>/scan/` | Idempotent timestamp update (no business state). | `backend/inventory/views.py:813` updates `last_scanned_at`; no throttle. | Acceptable per matrix — idempotent. |
| `POST /api/inventory/assets/<id>/scan/` | Idempotent timestamp update. | `backend/inventory/views.py:1677` (`@permission_classes([AllowAny])`); idempotent. | Acceptable per matrix. |
| `POST /api/inventory/fixtures/<id>/scan/` | Creates a refill request. | `backend/inventory/views.py:2671` (`@permission_classes([AllowAny])`); no throttle. | Partial — creates business state without throttle; follow-up will add per-fixture/day dedupe. |
| `POST /api/inventory/locations/<id>/generate_qr/` | Per-IP / per-user throttle. | `backend/inventory/views.py:268` uses `QRCodeRateLimiter` (`backend/inventory/utils/rate_limiting.py`). | Mitigated |
| `POST /api/inventory/items/<id>/generate_qr/` | Per-IP / per-user throttle. | `backend/inventory/views.py:708` uses `QRCodeRateLimiter`. | Mitigated |
| `POST /api/inventory/assets/<id>/generate_qr/` | Per-IP / per-user throttle. | `backend/inventory/views.py:1447` (`@permission_classes([AllowAny])`) uses `QRCodeRateLimiter`. | Mitigated |
| `POST /api/inventory/locations/<id>/report_problem/` | Per-IP throttle + dedupe per (location, day). | `backend/inventory/views.py:377-383` is `@permission_classes([AllowAny])`; no throttle / dedupe. | Gap |
| `POST /api/inventory/assets/<id>/report_problem/` | Per-IP throttle + dedupe per (asset, day). | `backend/inventory/views.py:1946-1947` has no per-action `permission_classes` and inherits the `AssetViewSet` default `IsAuthenticatedOrReadOnly` (`backend/inventory/views.py:1168`), so anonymous POSTs are denied in production. Matrix lists this as "public" but the implementation today is authenticated-only. | Matrix drift — denied to anonymous callers in prod; abuse-control class needs reconciliation. |
| `POST /api/inventory/work-orders/<id>/upload-pdf/` | Per-IP rate limit; PDF AcroForm signing enforced. | `backend/inventory/views.py:3456` (`upload_pdf`) validates the signed AcroForm but does not rate-limit raw upload volume. | Partial — signature gates legitimate PDFs; raw upload volume is unthrottled. |
| `POST /api/checklists/.../complete/` | Per-completion-token dedupe. | `backend/checklists/views.py` uses the per-instance completion token as the natural dedupe key. | Mitigated by design (token is single-use). |
| `POST /api/donations/upload-signature/` (public path) | Token-gated, unguessable submission token. | `backend/donations/views.py:468` is `@permission_classes([AllowAny])` over a signed donation token; no rate limit. | Partial — token strength substitutes for throttle. |
| `GET, POST /api/donations/lookup-donation-item/` | Token-gated lookup. | `backend/donations/views.py:559` is `@permission_classes([AllowAny])`; lookup uses an unguessable code. | Partial — token strength substitutes for throttle. |
| `POST /api/membership/register/validate-token/` | Token-gated. | `backend/membership/views.py` — token in body; no throttle. | Partial — token strength substitutes for throttle. |
| `POST /api/membership/register/complete/` | Token-gated. | `backend/membership/views.py` — token gates the action. | Mitigated by token. |
| `POST /api/reorders/requests/` (create only) | `AllowAny` create with serializer field allow-list. | `backend/reorder_queue/views.py` `ReorderRequestViewSet.create` uses `ReorderRequestCreateSerializer`; admin / cost / supplier fields are not exposed. | Mitigated by serializer-field allow-list; no throttle. |
| `POST /api/location-checkins/check-ins/checkin/` | Per-IP throttle. | `backend/location_checkins/views.py:62` (`@action ... permission_classes=[AllowAny]`); no throttle. | Gap |
| `POST /api/location-checkins/check-ins/` (kiosk create) | Per-IP throttle. | `backend/location_checkins/views.py:46-60` (`get_permissions()` returns `[AllowAny()]` for `create`); no throttle. | Gap |
| `POST /api/location-checkins/feedback/` (kiosk create) | Per-IP throttle. | `backend/location_checkins/views.py:113` (`get_permissions()` returns `[AllowAny()]` for `create`); no throttle. | Gap |
| `POST /api/location-checkins/security-reports/submit/` | Per-IP throttle. | `backend/location_checkins/views.py:124` (`@action ... permission_classes=[AllowAny], url_path="submit"`); no throttle. | Gap |
| `POST /api/location-checkins/webhook/` | Webhook secret + HMAC. | `backend/location_checkins/views.py:371` validates the HMAC inside the view body. | Mitigated by signed-secret design. |
| `POST /api/forgekey/devices/enroll/` | Provisioning token + CSR signing. | `backend/forgekey/views.py` validates `X-ForgeKey-Provisioning-Token` and the CSR before signing. | Mitigated by signed-payload design. |
| `POST /api/forgekey/devices/<id>/photo/` | Signed device payload. | `backend/forgekey/views.py` validates the signed payload in the view body. | Mitigated. |
| `POST /api/forgekey/mqtt-webhook/` | Webhook secret + HMAC. | `backend/forgekey/views.py` validates the HMAC. | Mitigated. |
| `POST /api/screens/.../heartbeat/` | Per-slug throttle. | `backend/screens/views.py` heartbeat action accepts anonymous callers; no throttle. | Gap |

The three `backend/auth_views.py` rows cite the FUNCTION rather than a line
number, because line numbers rot: `op-anonymous-read-posture` moved all three
by about 110 lines. The `backend/inventory/views.py` citations still carry
line numbers and several are already stale; that decay predates this table's
last edit and sweeping it is a separate piece of work, deliberately not done
here. Cite a symbol, not a line, when you touch a row.

### Cross-cutting controls

| Control | Status | Evidence |
| --- | --- | --- |
| Global DRF `DEFAULT_THROTTLE_CLASSES` / `DEFAULT_THROTTLE_RATES` | Gap | `backend/config/settings.py:186-203` configures `REST_FRAMEWORK` without any throttle classes or rates. |
| Per-endpoint `throttle_classes` on declared-public write views | Gap (except inventory QR generators via `QRCodeRateLimiter`) | grep `throttle_classes` returns no matches outside tests. |
| Idempotency keys / dedupe envelope helper | Gap | No shared helper exists; each domain implements its own (matrix, donations, checklists). |
| Tests that **prove** an `AllowAny` POST endpoint is throttled | Partial | `backend/inventory/tests/test_rate_limiting.py` covers `QRCodeRateLimiter` only. |

### R-02 gaps and follow-up scope

Per the iterative compliance plan on the parent bead, each follow-up
PR should close one safety class. The recommended ordering is by
*observable abuse surface × declared expectation*:

1. **Authentication endpoints** (`/auth/register/`, `/auth/login/`,
   `/auth/refresh/`) — declared `required` in the matrix, currently
   `Gap`. Recommended: scoped DRF `AnonRateThrottle` subclasses (or
   a global `DEFAULT_THROTTLE_RATES` with `anon`/`user` scopes), plus
   backend tests that prove the 429 path.
2. **Location check-ins** (`check-ins/checkin/`, kiosk `check-ins`
   create, `feedback` create, security `submit`) — per-IP throttle.
   Closing these together makes sense because they share the same
   `get_permissions()` `AllowAny` pattern.
3. **`LocationViewSet.report_problem`** — per-IP throttle + dedupe
   per (location, day).
4. **Asset `report_problem` matrix drift** — reconcile
   `docs/API_PERMISSION_MATRIX.md` with the actual
   `IsAuthenticatedOrReadOnly` permission on `AssetViewSet`. Either
   open the endpoint to `AllowAny` (and then add the abuse control)
   or update the matrix row to reflect the authenticated-only
   contract. The drift test will start failing if a future PR adds
   an explicit override; the matrix row should be fixed first.
5. **`FixtureViewSet.scan`** — creates a refill request; add
   per-(fixture, day) dedupe so a stuck kiosk cannot create
   unbounded duplicate refill requests.
6. **Screens heartbeat** — per-slug throttle to bound kiosk replay
   volume.
7. **Work-order PDF upload** — per-IP upload-volume throttle on top
   of the existing AcroForm signature gate.

Each follow-up PR should also add the throttle assertion test to
`backend/<app>/tests/test_rate_limiting.py` (or extend the existing
matrix test) and update the **Status** column in this document in the
same change. Once every endpoint row reads `Mitigated`, R-02 in
`docs/RISK_REGISTER.md` can be moved from `Partial` to `Mitigated`.

## Maintenance contract

- A PR that adds a new public unauthenticated write endpoint must add
  the endpoint to `docs/API_PERMISSION_MATRIX.md` *and* to the
  "Public write endpoints" table above with an honest `Status`. The
  drift test in `backend/config/tests/test_permission_matrix.py`
  catches the matrix half; reviewers are responsible for catching the
  coverage-table half.
- A PR that closes a `Gap` row must update the row in place and add
  the corresponding throttle / dedupe assertion test. Do not move a
  row to `Mitigated` without test evidence.
- A PR that changes `scripts/validate-prod-env.sh` must add or update
  the corresponding row in the "Configuration class coverage" table
  and the matching parametrized case in
  `backend/config/tests/test_deployment_validation.py`.
