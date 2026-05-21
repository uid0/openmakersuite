#!/bin/bash
# Restore the OMS PostgreSQL database from a dump produced by backup-db.sh.
#
# Auto-detects the format from the file extension:
#   *.sql.gz  → gunzip | psql        (plain SQL backups)
#   *.dump    → pg_restore --clean   (pg_dump -Fc archives)
#
# Workers are stopped before the restore and restarted after, so the load
# happens against an idle database. Pass --force or set RESTORE_DB_CONFIRM=YES
# to skip the interactive confirmation (used by the restore drill).
#
# Usage:
#   scripts/restore-db.sh backup-20260520T053000Z.sql.gz
#   scripts/restore-db.sh oms-20260520T053000Z.dump
#   scripts/restore-db.sh --force backup-*.sql.gz
set -euo pipefail

POSTGRES_USER="${POSTGRES_USER:-makerspace}"
POSTGRES_DB="${POSTGRES_DB:-makerspace_inventory}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
FORCE="${RESTORE_DB_CONFIRM:-}"
# Services to stop while the restore is in flight. Override if your compose
# project renames them.
WORKER_SERVICES="${WORKER_SERVICES:-backend celery celery_beat flower mqtt_consumer}"

usage() {
    cat <<EOF
Usage: $0 [--force] <backup-file>

Restore PostgreSQL from a dump written by scripts/backup-db.sh. Workers are
stopped during the restore and started again afterwards.

  --force, -y               Skip the interactive confirmation prompt
  --help, -h                Show this help

Environment overrides:
  COMPOSE_FILE              Compose file to exec against (default: docker-compose.prod.yml)
  POSTGRES_USER             Postgres role (default: makerspace)
  POSTGRES_DB               Database name (default: makerspace_inventory)
  RESTORE_DB_CONFIRM=YES    Same effect as --force
  WORKER_SERVICES           Space-separated services to stop/start (default: $WORKER_SERVICES)
EOF
}

BACKUP_FILE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --force|-y) FORCE=YES; shift ;;
        --help|-h)  usage; exit 0 ;;
        --*)        echo "Unknown argument: $1" >&2; usage >&2; exit 1 ;;
        *)
            if [ -z "$BACKUP_FILE" ]; then
                BACKUP_FILE="$1"; shift
            else
                echo "Unexpected positional argument: $1" >&2; exit 1
            fi
            ;;
    esac
done

if [ -z "$BACKUP_FILE" ]; then
    usage >&2
    exit 1
fi

if [ ! -f "$BACKUP_FILE" ]; then
    echo "File not found: $BACKUP_FILE" >&2
    exit 1
fi

case "$BACKUP_FILE" in
    *.sql.gz)  RESTORE_KIND=plain ;;
    *.dump)    RESTORE_KIND=custom ;;
    *)
        echo "Unrecognized backup format: $BACKUP_FILE" >&2
        echo "Supported extensions: .sql.gz, .dump" >&2
        exit 1
        ;;
esac

if [ "$FORCE" != "YES" ]; then
    printf "This will DROP and recreate database '%s'. Type 'YES' to continue: " "$POSTGRES_DB"
    read -r confirm
    if [ "$confirm" != 'YES' ]; then
        echo 'Aborted.'
        exit 1
    fi
fi

# shellcheck disable=SC2086
docker compose -f "$COMPOSE_FILE" stop $WORKER_SERVICES >/dev/null 2>&1 || true

docker compose -f "$COMPOSE_FILE" exec -T db \
    psql -U "$POSTGRES_USER" -d postgres -c \
    "DROP DATABASE IF EXISTS $POSTGRES_DB;"
docker compose -f "$COMPOSE_FILE" exec -T db \
    psql -U "$POSTGRES_USER" -d postgres -c \
    "CREATE DATABASE $POSTGRES_DB OWNER $POSTGRES_USER;"

if [ "$RESTORE_KIND" = "custom" ]; then
    docker compose -f "$COMPOSE_FILE" exec -T db \
        pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
            --no-owner --clean --if-exists \
        < "$BACKUP_FILE"
else
    gunzip -c "$BACKUP_FILE" | \
        docker compose -f "$COMPOSE_FILE" exec -T db \
        psql -U "$POSTGRES_USER" "$POSTGRES_DB"
fi

# shellcheck disable=SC2086
docker compose -f "$COMPOSE_FILE" start $WORKER_SERVICES >/dev/null 2>&1 || true

echo "Restore complete from $BACKUP_FILE (kind=$RESTORE_KIND)"
