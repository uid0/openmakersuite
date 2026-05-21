#!/bin/bash
# Back up the OMS PostgreSQL database from the running Docker Compose stack.
#
# Defaults to a plain SQL dump piped through gzip (backward compatible with the
# original script and `gunzip ... | psql` restores). Pass --format=custom to
# emit PostgreSQL's `-Fc` archive instead — smaller, supports parallel restore,
# and matches the runbook in deploy/BACKUP_RESTORE.md.
#
# Usage:
#   scripts/backup-db.sh                       # plain SQL .sql.gz
#   scripts/backup-db.sh --format=custom       # pg_dump -Fc .dump
#   PG_FORMAT=custom scripts/backup-db.sh      # same, via env
#   BACKUP_DIR=/var/backups/oms ./backup-db.sh # override output dir
#
# Exit codes:
#   0  backup written and verified
#   1  argument or runtime error
#   2  backup written but verification failed
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./db-backups}"
POSTGRES_USER="${POSTGRES_USER:-makerspace}"
POSTGRES_DB="${POSTGRES_DB:-makerspace_inventory}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
PG_FORMAT="${PG_FORMAT:-plain}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"

usage() {
    cat <<EOF
Usage: $0 [--format=plain|custom] [--retention-days=N] [--help]

Environment overrides:
  BACKUP_DIR        Output directory (default: ./db-backups)
  COMPOSE_FILE      Compose file to exec against (default: docker-compose.prod.yml)
  POSTGRES_USER     Postgres role (default: makerspace)
  POSTGRES_DB       Database name (default: makerspace_inventory)
  PG_FORMAT         plain | custom (default: plain)
  RETENTION_DAYS    Days of history to keep (default: 30, 0 disables pruning)

Format notes:
  plain   gzipped SQL — restore with \`gunzip ... | psql\` or restore-db.sh
  custom  pg_dump -Fc archive — restore with \`pg_restore\` or restore-db.sh
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --format=*)         PG_FORMAT="${1#--format=}"; shift ;;
        --retention-days=*) RETENTION_DAYS="${1#--retention-days=}"; shift ;;
        --help|-h)          usage; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; usage >&2; exit 1 ;;
    esac
done

case "$PG_FORMAT" in
    plain|custom) ;;
    *) echo "PG_FORMAT must be plain or custom (got: $PG_FORMAT)" >&2; exit 1 ;;
esac

mkdir -p "$BACKUP_DIR"
TS=$(date -u +%Y%m%dT%H%M%SZ)

if [ "$PG_FORMAT" = "custom" ]; then
    OUT="$BACKUP_DIR/oms-$TS.dump"
    docker compose -f "$COMPOSE_FILE" exec -T db \
        pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc > "$OUT"
else
    OUT="$BACKUP_DIR/backup-$TS.sql.gz"
    docker compose -f "$COMPOSE_FILE" exec -T db \
        pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" | gzip > "$OUT"
fi

SIZE=$(du -h "$OUT" | cut -f1)
echo "Backup written: $OUT ($SIZE)"

# Verify the artifact is openable. A truncated dump fails this without ever
# touching the live database.
verify_status=0
if [ "$PG_FORMAT" = "custom" ]; then
    if ! pg_restore -l "$OUT" >/dev/null 2>&1; then
        echo "WARNING: pg_restore could not list TOC for $OUT (host pg_restore not available or dump corrupt)" >&2
        verify_status=2
    fi
else
    if ! gunzip -t "$OUT" 2>/dev/null; then
        echo "ERROR: $OUT failed gzip integrity check" >&2
        verify_status=2
    fi
fi

# Prune old backups (custom and plain together; both belong to the same drill).
if [ "$RETENTION_DAYS" -gt 0 ]; then
    find "$BACKUP_DIR" \( -name 'backup-*.sql.gz' -o -name 'oms-*.dump' \) \
        -mtime "+$RETENTION_DAYS" -delete
    echo "Retention complete (${RETENTION_DAYS}-day window)"
fi

exit $verify_status
