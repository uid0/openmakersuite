#!/bin/bash
# Restore the OMS media volume from an archive produced by backup-media.sh.
#
# Backend and Celery services are stopped while the restore runs so the
# unpacked tree is consistent when traffic resumes. Pass --force or set
# RESTORE_MEDIA_CONFIRM=YES to skip the interactive confirmation.
#
# Usage:
#   scripts/restore-media.sh oms-media-20260520T053000Z.tgz
#   scripts/restore-media.sh --force oms-media-*.tgz
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
MEDIA_SERVICE="${MEDIA_SERVICE:-backend}"
MEDIA_PATH="${MEDIA_PATH:-/app/media}"
FORCE="${RESTORE_MEDIA_CONFIRM:-}"
WORKER_SERVICES="${WORKER_SERVICES:-backend celery celery_beat flower mqtt_consumer}"

usage() {
    cat <<EOF
Usage: $0 [--force] <media-archive.tgz>

Restore the media volume from a backup-media.sh archive. Backend and Celery
services are stopped during the restore and started again afterwards.

  --force, -y                  Skip the interactive confirmation prompt
  --help, -h                   Show this help

Environment overrides:
  COMPOSE_FILE                 Compose file (default: docker-compose.prod.yml)
  MEDIA_SERVICE                Service whose container holds the media volume (default: backend)
  MEDIA_PATH                   Path inside the container (default: /app/media)
  RESTORE_MEDIA_CONFIRM=YES    Same effect as --force
  WORKER_SERVICES              Services to stop/start around the restore
EOF
}

ARCHIVE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --force|-y) FORCE=YES; shift ;;
        --help|-h)  usage; exit 0 ;;
        --*)        echo "Unknown argument: $1" >&2; usage >&2; exit 1 ;;
        *)
            if [ -z "$ARCHIVE" ]; then
                ARCHIVE="$1"; shift
            else
                echo "Unexpected positional argument: $1" >&2; exit 1
            fi
            ;;
    esac
done

if [ -z "$ARCHIVE" ]; then
    usage >&2
    exit 1
fi

if [ ! -f "$ARCHIVE" ]; then
    echo "File not found: $ARCHIVE" >&2
    exit 1
fi

if ! gunzip -t "$ARCHIVE" 2>/dev/null; then
    echo "ERROR: $ARCHIVE failed gzip integrity check (cowardly refusing to wipe media)" >&2
    exit 2
fi

if [ "$FORCE" != "YES" ]; then
    printf "This will WIPE %s inside the %s container and restore from %s. Type 'YES' to continue: " \
        "$MEDIA_PATH" "$MEDIA_SERVICE" "$ARCHIVE"
    read -r confirm
    if [ "$confirm" != 'YES' ]; then
        echo 'Aborted.'
        exit 1
    fi
fi

# shellcheck disable=SC2086
docker compose -f "$COMPOSE_FILE" stop $WORKER_SERVICES >/dev/null 2>&1 || true

# Bring the backend container back so we can exec into it for the restore,
# but with the workers off it won't be serving traffic until we restart them.
docker compose -f "$COMPOSE_FILE" start "$MEDIA_SERVICE" >/dev/null 2>&1 || true

# Use sh -c so we can chain the rm + tar inside one container, with the
# archive streamed in over stdin. Trailing `.` keeps tar entries relative.
docker compose -f "$COMPOSE_FILE" exec -T "$MEDIA_SERVICE" \
    sh -c "set -e; rm -rf $MEDIA_PATH/* $MEDIA_PATH/.[!.]* 2>/dev/null || true; tar -C $MEDIA_PATH -xzf -" \
    < "$ARCHIVE"

# shellcheck disable=SC2086
docker compose -f "$COMPOSE_FILE" start $WORKER_SERVICES >/dev/null 2>&1 || true

ENTRIES=$(tar -tzf "$ARCHIVE" 2>/dev/null | wc -l | tr -d ' ')
echo "Media restore complete from $ARCHIVE ($ENTRIES entries)"
