#!/bin/bash
# Snapshot the deployment configuration that is *not* in git: the active .env
# file plus any operator-edited compose overrides. The snapshot is written
# with 0600 permissions because .env contains live secrets.
#
# Usage:
#   scripts/backup-config.sh                                  # writes ./config-backups/oms-config-<ts>.tar.gz
#   BACKUP_DIR=/var/backups/oms scripts/backup-config.sh
#   ENV_FILE=.env.prod scripts/backup-config.sh               # alternate env path
#
# The archive will only contain files that exist; missing optional files are
# skipped silently so this works on a fresh operator workstation.
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./config-backups}"
ENV_FILE="${ENV_FILE:-.env}"
EXTRA_FILES=(
    "${ENV_FILE}"
    "docker-compose.override.yml"
    "scripts/emqx/bootstrap-admins.txt"
)
RETENTION_DAYS="${RETENTION_DAYS:-30}"

usage() {
    cat <<EOF
Usage: $0 [--retention-days=N] [--help]

Captures host-side deployment config that is NOT checked into git:
  - \$ENV_FILE (default: .env)
  - docker-compose.override.yml (if present)
  - scripts/emqx/bootstrap-admins.txt (if present)

Environment overrides:
  BACKUP_DIR        Output directory (default: ./config-backups)
  ENV_FILE          Path to .env (default: .env)
  RETENTION_DAYS    Days of history to keep (default: 30, 0 disables pruning)
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --retention-days=*) RETENTION_DAYS="${1#--retention-days=}"; shift ;;
        --help|-h)          usage; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; usage >&2; exit 1 ;;
    esac
done

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR" || true

# Only include files that actually exist. tar -T - reads the list from stdin.
present=()
for f in "${EXTRA_FILES[@]}"; do
    if [ -e "$f" ]; then
        present+=("$f")
    fi
done

if [ "${#present[@]}" -eq 0 ]; then
    echo "ERROR: no config files found to archive (looked for: ${EXTRA_FILES[*]})" >&2
    echo "       set ENV_FILE if your active env is at a non-default path." >&2
    exit 1
fi

TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT="$BACKUP_DIR/oms-config-$TS.tar.gz"

tar -czf "$OUT" "${present[@]}"
chmod 600 "$OUT"

if ! gunzip -t "$OUT" 2>/dev/null; then
    echo "ERROR: $OUT failed gzip integrity check" >&2
    rm -f "$OUT"
    exit 2
fi

SIZE=$(du -h "$OUT" | cut -f1)
echo "Config backup written: $OUT ($SIZE, ${#present[@]} files)"
echo "  permissions: $(stat -c '%a' "$OUT" 2>/dev/null || stat -f '%Lp' "$OUT" 2>/dev/null || echo '?')"
echo "  files included:"
for f in "${present[@]}"; do
    echo "    - $f"
done

if [ "$RETENTION_DAYS" -gt 0 ]; then
    find "$BACKUP_DIR" -name 'oms-config-*.tar.gz' -mtime "+$RETENTION_DAYS" -delete
    echo "Retention complete (${RETENTION_DAYS}-day window)"
fi
