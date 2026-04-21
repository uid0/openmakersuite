#!/bin/bash
set -euo pipefail

BACKUP_FILE="${1:-}"
POSTGRES_USER="${POSTGRES_USER:-makerspace}"
POSTGRES_DB="${POSTGRES_DB:-makerspace_inventory}"

if [ -z "$BACKUP_FILE" ]; then
    echo 'Usage: restore-db.sh <backup.sql.gz>'
    exit 1
fi

if [ ! -f "$BACKUP_FILE" ]; then
    echo "File not found: $BACKUP_FILE"
    exit 1
fi

read -p "This will DROP and recreate the database. Type 'YES' to continue: " confirm
if [ "$confirm" != 'YES' ]; then
    echo 'Aborted.'
    exit 1
fi

docker compose exec -T db psql -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE IF EXISTS $POSTGRES_DB;"
docker compose exec -T db psql -U "$POSTGRES_USER" -d postgres -c "CREATE DATABASE $POSTGRES_DB;"
gunzip -c "$BACKUP_FILE" | docker compose exec -T db psql -U "$POSTGRES_USER" "$POSTGRES_DB"
echo 'Restore complete'
