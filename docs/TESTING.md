# Testing and Code Coverage

This project uses pytest for the Django backend, Jest and React Testing Library for frontend unit and integration tests, and Playwright for browser-level E2E coverage.

## One-command local check

Run the checks in the same order as CI:

```bash
./scripts/test-all.sh
```

The script installs backend and frontend dependencies, runs backend formatting/linting/tests with coverage, verifies key frontend dependencies, runs Jest with and without coverage, builds the frontend, and runs Playwright E2E tests.

Use this when Playwright services or browsers are not available:

```bash
SKIP_E2E=1 ./scripts/test-all.sh
```

## Backend

Install dependencies:

```bash
cd backend
python -m pip install -r requirements.txt -r requirements-dev.txt
```

Use explicit test environment values so local shell settings such as `DEBUG=release` do not break Django settings parsing:

```bash
export DEBUG=1
export SECRET_KEY=test-secret-key
export ALLOWED_HOSTS=localhost,127.0.0.1
export REDIS_URL=redis://localhost:6379/0
```

Run quality checks:

```bash
black --check .
isort --check-only .
flake8 .
```

Run tests with configured coverage:

```bash
pytest
```

`backend/pytest.ini` is the **single** source of truth for pytest options, test
paths, coverage app selection, reports, markers, and the current fail-under
threshold. CI relies on the same config instead of repeating app-specific
`--cov` flags. `backend/pyproject.toml` intentionally does not carry a
`[tool.pytest.ini_options]` section — when both files define pytest config,
pytest emits a "WARNING: ignoring pytest config in pyproject.toml" and silent
drift can hide bugs (see oms-8q38 / gh-460 for the regression this prevents).

The configured test directories cover the product-critical apps, including
LOTO and electrical-circuits safety paths, analytics, climate, devices,
maker_boxes, and notifications. The current backend fail-under threshold is
**85%**; raise it incrementally as new tests land.

Coverage reports:

- Terminal missing-line report: printed by pytest.
- XML report: `backend/coverage.xml`.
- HTML report, when requested manually: `backend/htmlcov/index.html`.

## Frontend

Install dependencies from the lock file:

```bash
cd frontend
npm ci
```

Verify key runtime dependencies are present before interpreting test failures:

```bash
npm ls @mantine/core @mantine/modals html5-qrcode recharts react-grid-layout
```

Run unit and integration tests:

```bash
npm run test:ci
```

Run coverage with the configured Jest thresholds:

```bash
npm run test:ci:coverage
```

The current global Jest thresholds live in `frontend/package.json` under
`jest.coverageThreshold.global`. They were set from the measured baseline on
2026-05-20 (oms-8q38) and the file-level `collectCoverageFrom` exclusions are
limited to the React bootstrap (`index.tsx`), web-vitals reporting, type
declaration packages, and `.d.ts` files. Application pages and components are
not excluded; raise the thresholds incrementally rather than excluding files
that are merely undertested.

Build the production bundle:

```bash
npm run build
```

Coverage reports:

- LCOV: `frontend/coverage/lcov.info`.
- Clover XML: `frontend/coverage/clover.xml`.
- HTML: `frontend/coverage/lcov-report/index.html`.

## Playwright E2E

Install browsers when needed:

```bash
cd frontend
npx playwright install --with-deps
```

Run E2E tests:

```bash
npm run test:e2e
```

For the most CI-like local run, build the frontend and serve it with the repo's SPA static server so Playwright reuses an existing server instead of invoking the development server:

```bash
cd frontend
npm run build
python3 ../scripts/serve-spa.py build 3000
```

Then, in another shell:

```bash
cd frontend
env -u CI npm run test:e2e
```

Backend API availability is still required for flows that call the backend. Configure endpoints with:

```bash
PLAYWRIGHT_BASE_URL=http://localhost:3000
PLAYWRIGHT_API_URL=http://localhost:8000/api
```

Current E2E coverage lives in `frontend/e2e/` and should include public unauthenticated member paths, administrative asset/dashboard paths, and SIG dashboard paths.

## CI and Codecov

GitHub Actions runs on pushes and pull requests targeting `main` and `develop`.

Backend CI:

- Installs `backend/requirements.txt` and `backend/requirements-dev.txt`.
- Runs Black, isort, and flake8.
- Runs migrations against PostgreSQL.
- Runs `pytest` with coverage from `backend/pytest.ini`.
- Verifies `backend/coverage.xml` exists.
- Uploads backend coverage to Codecov with `fail_ci_if_error: false`.

Frontend CI:

- Verifies `package-lock.json` with `npm ci --dry-run --ignore-scripts`.
- Runs `npm ci`.
- Verifies key dependencies with `npm ls`.
- Runs `npm run test:ci:coverage` on every CI run.
- Builds the production bundle.
- Installs Playwright browsers and runs advisory `npm run test:e2e` coverage against the built frontend served by `scripts/serve-spa.py`.
- Verifies `frontend/coverage/clover.xml` and `frontend/coverage/lcov.info`.
- Uploads frontend coverage to Codecov with `fail_ci_if_error: false`.

Coverage thresholds are enforced by pytest/Jest. Codecov upload is reporting-only and must not be the only gate.

The current Playwright CI step is advisory because the specs require a live backend at `PLAYWRIGHT_API_URL`, and the existing frontend dev-server path is incompatible with the current dependency override when run under Node 18. A future frontend/backend implementation pass should make this step blocking by running against a live backend service and the Node 20 frontend runtime.

## Writing Tests

Backend tests should use existing pytest fixtures and factories, mark tests with `unit` or `integration` where useful, and mock external systems such as Celery, webhooks, Sentry, MQTT, payment, or email providers.

Frontend tests should use React Testing Library from the user's perspective: assert loading, success, error, and user-action states; avoid implementation details; and mock API boundaries consistently.

Playwright tests should verify complete browser workflows. Public makerspace paths should remain unauthenticated unless the feature specifically requires admin or member identity.

## Generated Artifacts

Generated reports are ignored and should not be committed:

- `coverage.xml`
- `.coverage`
- `coverage/`
- `htmlcov/`
- `backend/coverage.xml`
- `backend/htmlcov/`
- `frontend/coverage/`
- `frontend/playwright-report/`
- `frontend/test-results/`

If a report file is already tracked, remove it from git tracking in a cleanup commit rather than updating it with local test output.
