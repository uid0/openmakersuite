#!/usr/bin/env bash
# Developer-machine setup: pre-commit hooks, frontend Husky hooks, deps.
# Idempotent — safe to re-run after pulling new commits or rebuilding worktrees.

set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"

# --- Backend: venv + pre-commit -----------------------------------------------

if [ -z "${VIRTUAL_ENV:-}" ] && [ ! -d "backend/.venv" ]; then
  echo "→ Creating backend virtualenv (backend/.venv)"
  python3 -m venv backend/.venv
fi

if [ -d "backend/.venv" ]; then
  # shellcheck disable=SC1091
  source backend/.venv/bin/activate
fi

echo "→ Installing pre-commit (and hook environments)"
pip install --quiet --upgrade pip
pip install --quiet pre-commit

pre-commit install --install-hooks
pre-commit install --hook-type commit-msg

# --- Frontend: npm install (also wires Husky via the prepare script) ----------

if [ -d "frontend" ] && command -v npm >/dev/null 2>&1; then
  echo "→ Installing frontend deps (also runs husky setup via prepare script)"
  (cd frontend && npm install --no-audit --no-fund)
else
  echo "→ Skipping frontend npm install (npm not found or no frontend/ dir)"
fi

cat <<'EOF'

✅ Dev setup complete.

Hooks installed:
  • pre-commit (black, isort, flake8, bandit, etc.)   — runs on git commit
  • commit-msg (conventional-pre-commit)              — validates commit message

Override an emergency hook with: git commit --no-verify   (use sparingly)

To re-run all checks across the whole tree:
  pre-commit run --all-files
EOF
