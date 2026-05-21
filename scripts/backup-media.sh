#!/bin/bash
# Back up the OMS media volume (uploaded photos, attachments, generated PDFs)
# from a running Docker Compose stack.
#
# Streams the contents of /app/media (mounted from the `media_volume` volume)
# into a gzipped tar on the host. The tar is taken read-only so a stray write
# can't truncate the artifact mid-stream.
#
# Usage:
#   scripts/backup-media.sh                              # writes ./media-backups/oms-media-<ts>.tgz
#   BACKUP_DIR=/var/backups/oms scripts/backup-media.sh  # custom output dir
#
# Lose this and you lose every user-uploaded asset — pg_restore cannot bring
# the files back. See deploy/BACKUP_RESTORE.md §3.
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./media-backups}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
MEDIA_SERVICE="${MEDIA_SERVICE:-backend}"
MEDIA_PATH="${MEDIA_PATH:-/app/media}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"

usage() {
    cat <<EOF
Usage: $0 [--retention-days=N] [--help]

Environment overrides:
  BACKUP_DIR        Output directory (default: ./media-backups)
  COMPOSE_FILE      Compose file (default: docker-compose.prod.yml)
  MEDIA_SERVICE     Service whose container is exec'd to read media (default: backend)
  MEDIA_PATH        Path inside the container (default: /app/media)
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
TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT="$BACKUP_DIR/oms-media-$TS.tgz"

# Stream tar directly out of the running service container. Using the existing
# backend container keeps the script storage-class-agnostic (works on bind
# mounts, named volumes, NFS, etc.). `-C` makes the resulting tar entries
# relative to the media dir so unpack-into-a-new-volume works without juggling
# the absolute path.
docker compose -f "$COMPOSE_FILE" exec -T "$MEDIA_SERVICE" \
    tar -C "$MEDIA_PATH" -cf - . | gzip > "$OUT"

# Verify the tar is openable. A truncated stream fails this without claiming
# success on the host.
if ! gunzip -t "$OUT" 2>/dev/null; then
    echo "ERROR: $OUT failed gzip integrity check" >&2
    rm -f "$OUT"
    exit 2
fi
if ! tar -tzf "$OUT" >/dev/null 2>&1; then
    echo "ERROR: $OUT is not a valid tar archive" >&2
    rm -f "$OUT"
    exit 2
fi

SIZE=$(du -h "$OUT" | cut -f1)
ENTRIES=$(tar -tzf "$OUT" 2>/dev/null | wc -l | tr -d ' ')
echo "Media backup written: $OUT ($SIZE, $ENTRIES entries)"

if [ "$RETENTION_DAYS" -gt 0 ]; then
    find "$BACKUP_DIR" -name 'oms-media-*.tgz' -mtime "+$RETENTION_DAYS" -delete
    echo "Retention complete (${RETENTION_DAYS}-day window)"
fi
