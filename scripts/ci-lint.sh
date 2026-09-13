#!/usr/bin/env bash
# The lint, format and type checks CI's "Backend Lint" and "Frontend Lint"
# jobs enforce, defined once. Both of those jobs run each check through this
# script, and `.no-mistakes.yaml` points the validation pipeline's lint step
# (`commands.lint`) here, so a tree that fails CI's linters fails the local
# gate too, before anything is pushed.
#
# Without it the pipeline linted by asking an agent to discover the project's
# linters. That pass let an F402 and black drift through to CI: flake8 is not
# installed on the gate host, and flake8 run from the repository root misses
# backend/.flake8 (it lints at 79 columns instead). So nothing here depends on
# what happens to be on PATH or on the caller's working directory.
#
# Usage: scripts/ci-lint.sh [check|group ...]   (default: all)
#   checks: black isort flake8 eslint typecheck derivation-guard
#   groups: backend (black isort flake8), frontend (eslint typecheck
#           derivation-guard), all
#
# Backend tools: CI pip-installs the pins itself and sets CI_LINT_TOOLS=path
# to use them. Anywhere else, black/isort/flake8 run from a throwaway uv
# environment holding exactly the versions pinned in
# backend/requirements-dev.txt, selected with the same `==` anchor CI uses:
# flake8 alone, without the flake8-docstrings/flake8-bugbear plugins a full
# requirements-dev install adds, which would change what it reports.
#
# Every requested check runs even after one fails, and the exit status is
# non-zero if any failed, so one run names every problem.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
PYTHON_VERSION="3.14"

# Run a backend lint tool from backend/, where its config lives.
backend_tool() {
  if [[ "${CI_LINT_TOOLS:-}" == "path" ]]; then
    (cd "$BACKEND_DIR" && "$@")
    return
  fi
  if ! command -v uv >/dev/null 2>&1; then
    echo "ci-lint: uv is required to provision the pinned backend lint tools" >&2
    return 127
  fi
  local pins=()
  local pin
  while IFS= read -r pin; do
    pins+=(--with "$pin")
  done < <(grep -E '^(black|isort|flake8)==' "$BACKEND_DIR/requirements-dev.txt")
  if [[ ${#pins[@]} -ne 6 ]]; then
    echo "ci-lint: expected black, isort and flake8 pins in backend/requirements-dev.txt" >&2
    return 1
  fi
  (cd "$BACKEND_DIR" && uv run --quiet --no-project --isolated --no-config \
    --python "$PYTHON_VERSION" "${pins[@]}" -- "$@")
}

# Install frontend dependencies the way CI does (`npm ci`), unless
# node_modules already holds exactly the versions package-lock.json pins.
# `npm ls` is not enough: it accepts any version inside package.json's ranges,
# so a stale install could lint with a different eslint or tsc than CI.
frontend_deps() {
  if [[ -f "$FRONTEND_DIR/node_modules/.package-lock.json" ]] &&
    (cd "$FRONTEND_DIR" && node -e '
      const want = require("./package-lock.json").packages;
      const have = require("./node_modules/.package-lock.json").packages;
      for (const k of Object.keys(want)) {
        if (!k) continue;
        if (!have[k] ? !want[k].optional : have[k].version !== want[k].version) process.exit(1);
      }
      for (const k of Object.keys(have)) if (!want[k]) process.exit(1);
    ' 2>/dev/null); then
    return 0
  fi
  (cd "$FRONTEND_DIR" &&
    NODE_OPTIONS="${NODE_OPTIONS:---dns-result-order=ipv4first}" npm ci --ignore-scripts)
}

run_check() {
  case "$1" in
  black) backend_tool black --check . ;;
  isort) backend_tool isort --check-only . ;;
  flake8) backend_tool flake8 . ;;
  eslint) frontend_deps && (cd "$FRONTEND_DIR" && npm run lint) ;;
  typecheck) frontend_deps && (cd "$FRONTEND_DIR" && npm run typecheck) ;;
  derivation-guard)
    # Stdlib-only, but it must parse a tree written for Python 3.14.
    if [[ "${CI_LINT_TOOLS:-}" == "path" ]]; then
      (cd "$ROOT_DIR" && python3 backend/reorder_queue/settlement_sites.py)
    else
      (cd "$ROOT_DIR" && uv run --quiet --no-project --isolated --no-config \
        --python "$PYTHON_VERSION" -- python backend/reorder_queue/settlement_sites.py)
    fi
    ;;
  *)
    echo "ci-lint: unknown check '$1'" >&2
    return 2
    ;;
  esac
}

checks=()
for arg in "${@:-all}"; do
  case "$arg" in
  all) checks+=(black isort flake8 eslint typecheck derivation-guard) ;;
  backend) checks+=(black isort flake8) ;;
  frontend) checks+=(eslint typecheck derivation-guard) ;;
  *) checks+=("$arg") ;;
  esac
done

failed=()
for check in "${checks[@]}"; do
  echo "== ci-lint: $check"
  if ! run_check "$check"; then
    failed+=("$check")
  fi
done

if [[ ${#failed[@]} -gt 0 ]]; then
  echo "ci-lint: FAILED: ${failed[*]}" >&2
  exit 1
fi
echo "ci-lint: all passed: ${checks[*]}"
