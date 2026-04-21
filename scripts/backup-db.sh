#!/bin/bash
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./db-backups}"
POSTGRES_USER="${POSTGRES_USER:-makerspace}"
POSTGRES_DB="${POSTGRES_DB:-makerspace_inventory}"

mkdir -p "$BACKUP_DIR"
TS=$(date +%Y%m%d-%H%M%S)
OUT="$BACKUP_DIR/backup-$TS.sql.gz"

docker compose exec -T db pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" | gzip > "$OUT"
echo "Backup written: $OUT ($(du -h "$OUT" | cut -f1))"

# Retention: keep last 30 days
find "$BACKUP_DIR" -name 'backup-*.sql.gz' -mtime +30 -delete
echo 'Retention complete (30-day window)'
