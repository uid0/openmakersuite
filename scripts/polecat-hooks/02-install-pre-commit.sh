#!/usr/bin/env bash
# Install pre-commit hooks in the polecat worktree (hq-ahf / hq-3rl Part B).
#
# CI enforces black, isort, and flake8 via pre-commit. Without the git hook
# installed locally, polecats commit code that fails CI and waste a round trip.
# This hook runs `pre-commit install` in the spawned worktree so `git commit`
# runs the same checks CI does.
#
# Note: in linked worktrees, `pre-commit install` writes to the shared
# commondir hooks path (e.g. .repo.git/hooks/pre-commit), so all worktrees of
# this repo share the installed hook. Running this per-spawn is idempotent.

set -euo pipefail

WORKTREE="$GT_WORKTREE_PATH"

if [[ ! -f "$WORKTREE/.pre-commit-config.yaml" ]]; then
  echo "install-pre-commit: no .pre-commit-config.yaml in $WORKTREE, skipping" >&2
  exit 0
fi

if ! command -v pre-commit >/dev/null 2>&1; then
  echo "install-pre-commit: pre-commit not on PATH, skipping" >&2
  exit 0
fi

cd "$WORKTREE"
pre-commit install >&2
echo "install-pre-commit: installed hook in $WORKTREE" >&2
