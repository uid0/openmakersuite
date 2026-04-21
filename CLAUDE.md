# Claude Code

Project conventions and role assignment for Claude Code in openmakersuite. Also read `AGENTS.md` for shared project standards that apply to all agents.

## Project Layout

- `backend/` — Python 3.11, Django 5.1 + Django REST Framework, Celery, PostgreSQL.
  - Virtualenv from `backend/requirements.txt` + `backend/requirements-dev.txt`.
  - Tests: `cd backend && pytest`.
- `frontend/` — React + TypeScript + Vite, npm.
  - Tests: `cd frontend && npm test`.
- `.github/workflows/ci.yml` is the source of truth for what CI enforces.

## Before committing

Run `pre-commit run --all-files`. If that's not installed, at minimum:

```
cd backend && isort --profile black . && black --check . && flake8 .
```

CI fails on `isort`, `black`, and `flake8` — all three must be green locally before you push, or CI will reject the PR. Pre-commit install once per worktree:

```
pip install pre-commit && pre-commit install
```

## "Tests pass" means more than pytest

A green `pytest` run is necessary but not sufficient. Before declaring a task done:

1. `cd backend && pytest` — backend test suite green.
2. `cd frontend && npm test` — frontend test suite green.
3. `pre-commit run --all-files` — all lint/format hooks green.
4. No new warnings in the diff.

If any of the above fails, the work is not done — surface the failure, don't paper over it.

## Workflow Roles (Codex vs Claude Code)

Two coding agents share this repo with split responsibilities:

- **Codex** — acceptance criteria author. Given a feature request, writes `.criteria/<slug>.md` in the format described in `.criteria/README.md`. Does not modify `backend/`, `frontend/`, migrations, or tests.
- **Claude Code** — implementer. Reads `.criteria/*.md` and writes code + tests to satisfy every AC. Does not edit `.criteria/`.

## Your job (Claude Code)

Given a `.criteria/<slug>.md` file, implement code changes in `backend/` and/or `frontend/` to satisfy every criterion. Write tests that map one-to-one to the Given/When/Then blocks.

### Boundaries

- Do not write, edit, or delete files under `.criteria/`. Those are Codex's output.
- If a criterion is ambiguous, underspecified, or contradicts existing code, stop and surface the conflict — do not invent the intent.
- If acceptance requires changes beyond what the criteria describe (schema migration, config, infra), call them out before implementing rather than silently expanding scope.

### Done means

- All criteria tests pass.
- `pre-commit run --all-files` is clean (or at minimum: `black --check`, `isort --check-only`, `flake8` on backend).
- Both test suites green (`pytest`, `npm test`).
- PR description references AC-N for each change so reviewers can trace intent.
