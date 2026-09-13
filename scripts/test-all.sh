#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Lint through scripts/ci-lint.sh, the script CI's Backend Lint and Frontend
# Lint jobs call. Running black/isort/flake8 from the requirements-dev install
# below instead let flake8 load the flake8-bugbear plugin that install brings,
# so it reported B findings CI never sees, and skipped eslint and tsc entirely.
run_lint() {
  echo "== Lint (scripts/ci-lint.sh) =="
  "$ROOT_DIR/scripts/ci-lint.sh"
}

run_backend() {
  echo "== Backend tests and coverage =="
  cd "$ROOT_DIR/backend"

  export DEBUG="${DEBUG:-1}"
  export SECRET_KEY="${SECRET_KEY:-test-secret-key}"
  export ALLOWED_HOSTS="${ALLOWED_HOSTS:-localhost,127.0.0.1}"
  export REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"

  python -m pip install -r requirements.txt -r requirements-dev.txt
  pytest
}

run_frontend() {
  echo "== Frontend tests, coverage, build, and E2E =="
  cd "$ROOT_DIR/frontend"

  export NODE_OPTIONS="${NODE_OPTIONS:---dns-result-order=ipv4first}"

  npm ci
  npm ls @mantine/core @mantine/modals html5-qrcode recharts react-grid-layout
  npm run test:ci
  npm run test:ci:coverage
  npm run build

  if [[ "${SKIP_E2E:-0}" == "1" ]]; then
    echo "Skipping Playwright E2E because SKIP_E2E=1"
  else
    python3 "$ROOT_DIR/scripts/serve-spa.py" "$ROOT_DIR/frontend/build" 3000 &
    server_pid=$!
    trap 'kill "$server_pid" 2>/dev/null || true' RETURN
    sleep 2
    env -u CI npm run test:e2e
  fi
}

run_lint
run_backend
run_frontend
