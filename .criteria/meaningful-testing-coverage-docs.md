# Meaningful testing, coverage, and documentation

## Context
OpenMakerSuite already has pytest, Jest, Playwright, coverage artifacts, and testing docs, but the current setup is inconsistent: backend coverage settings differ between config files, frontend coverage gates are low and not enforced on every pull request, and documentation describes goals that do not fully match CI. This work makes the project's testing posture trustworthy for contributors and maintainers.

## Scope
- In: Backend test and coverage configuration, frontend Jest and Playwright test coverage, CI coverage enforcement, contributor-facing testing documentation, and coverage reporting hygiene.
- Out: Product feature changes, schema changes not required by tests, replacing the existing test frameworks, adding paid third-party services as a hard dependency, and broad unrelated refactors.

## Criteria

### AC-1: Backend coverage configuration is single-source and complete
- **Given** the backend test configuration files exist
- **When** a contributor compares `backend/pytest.ini`, `backend/pyproject.toml`, and `backend/.coveragerc`
- **Then** there is one documented source of truth for pytest coverage options, it includes every first-party Django app that has production code, and the other config files do not define conflicting coverage thresholds or app lists

### AC-2: Backend coverage gate is enforced locally and in CI
- **Given** backend dependencies are installed
- **When** a contributor runs the documented backend coverage command locally
- **Then** pytest fails if total backend coverage is below the documented minimum and writes XML plus terminal missing-line reports

### AC-3: Backend coverage excludes only justified files
- **Given** `backend/.coveragerc` controls backend coverage omission
- **When** a contributor reviews omitted production modules
- **Then** each omitted production module has a specific documented reason, and serializers, services, tasks, views, URLs, and management commands are not omitted merely because they were previously untested

### AC-4: Critical backend workflows have integration tests
- **Given** the backend test suite is run with integration tests enabled
- **When** pytest executes
- **Then** there are passing integration tests for anonymous inventory or QR-triggered reorder submission, authenticated administrative reorder handling, issue or maintenance reporting, and at least one notification/webhook dispatch path with external delivery mocked

### AC-5: Backend service and model behavior has focused unit tests
- **Given** the backend test suite is run with unit tests enabled
- **When** pytest executes
- **Then** there are passing unit tests for reorder decision logic, supplier or vendor selection data, membership or access-control defaults, maintenance/order state transitions, and validation failures for invalid user input

### AC-6: Frontend coverage gate is raised and enforced
- **Given** frontend dependencies are installed
- **When** `npm run test:ci:coverage` is run from `frontend/`
- **Then** Jest enforces documented global thresholds of at least 60% for statements, lines, functions, and branches, writes `coverage/lcov.info`, and fails when the thresholds are not met

### AC-7: Frontend critical screens have user-centered tests
- **Given** the frontend unit test suite is run
- **When** Jest executes React Testing Library tests
- **Then** there are passing tests for loading, success, error, and user-action states on the primary dashboard, scan or QR workflow, reorder workflow, and issue or maintenance reporting workflow

### AC-8: Playwright covers the unauthenticated member path
- **Given** the backend and frontend are running in the documented local or CI test environment
- **When** `npm run test:e2e` is run from `frontend/`
- **Then** Playwright verifies that an unauthenticated user can open a public scan/reporting URL, submit the expected action, and see a completion or acknowledgement state without logging in

### AC-9: CI runs coverage on every pull request
- **Given** a pull request targets `main` or `develop`
- **When** the GitHub Actions CI workflow runs
- **Then** backend and frontend coverage commands both run with their configured fail-under thresholds, coverage artifacts are verified, and coverage upload remains non-blocking if the external upload service is unavailable

### AC-10: CI has a documented local equivalent
- **Given** a contributor wants to reproduce CI locally
- **When** they follow the repository documentation
- **Then** there is a documented command or script for running the backend quality checks, frontend quality checks, coverage checks, and Playwright checks in the same order as CI

### AC-11: Testing documentation matches reality
- **Given** a contributor opens the testing documentation
- **When** they compare it to the committed scripts and CI workflow
- **Then** the documented commands, thresholds, report locations, required services, test markers, and troubleshooting notes match the repository's actual configuration

### AC-12: Coverage artifacts are kept out of source control
- **Given** tests with coverage have been run locally
- **When** a contributor checks `git status`
- **Then** generated coverage outputs such as XML reports, HTML reports, `coverage/`, `.coverage`, and Playwright reports are ignored unless a specific tracked fixture or documentation artifact is intentionally named
