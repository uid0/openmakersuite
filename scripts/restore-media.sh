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
# Budget for "the workers we just started are actually running again". Mirrors
# the 60s/2s shape of the livez waits in restore-drill.sh and CI.
WORKER_READY_TIMEOUT="${WORKER_READY_TIMEOUT:-60}"
WORKER_READY_INTERVAL="${WORKER_READY_INTERVAL:-2}"

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
  WORKER_READY_TIMEOUT         Seconds to wait for restarted workers (default: 60)
  WORKER_READY_INTERVAL        Seconds between readiness polls (default: 2)
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

# Run a docker compose call, keeping its stdout quiet but letting the real
# error text through, and failing here if it errors. These calls used to end in
# `>/dev/null 2>&1 || true`: a stop or start that never happened was invisible
# at its own site and resurfaced two steps later as restore-drill.sh's
# "no running compose services" precondition error, which points the reader at
# the wrong script entirely (op-fa1).
run_compose() {
    local what="$1"
    shift
    local rc=0
    "$@" >/dev/null || rc=$?
    if [ "$rc" -ne 0 ]; then
        echo "ERROR: $what failed (exit $rc): $*" >&2
        exit "$rc"
    fi
}

# Narrow the stop/start set to the worker services this stack actually has
# containers for. `docker compose start` fails with `service "x" has no
# container to start`, so a deployment that runs only some of the workers (the
# CI prod compose smoke boots db, redis, emqx and backend — never celery or
# flower) would otherwise fail the final start for a service that was never
# meant to be up. Resolving first is what makes the start below safe to treat
# as fatal.
resolve_worker_services() {
    local resolved="" svc
    for svc in $WORKER_SERVICES; do
        if docker compose -f "$COMPOSE_FILE" ps -a --quiet "$svc" 2>/dev/null | grep -q .; then
            resolved="${resolved}${resolved:+ }$svc"
        fi
    done
    printf '%s' "$resolved"
}

# `docker compose start` returns when the command is issued, not when the
# containers report running. restore-drill.sh gates on
# `ps --status running --quiet | grep -q .` roughly 0.4s later, so without this
# wait the drill can legitimately observe zero running services and fail an
# innocent PR — main has been winning that race by luck, not by design.
#
# One running worker is the bar because that is exactly what the drill
# precondition checks; requiring every worker would block on services a given
# stack may deliberately not run.
wait_for_workers_running() {
    local deadline attempt=0 running_ids running_count poll_err
    deadline=$(( $(date +%s) + WORKER_READY_TIMEOUT ))
    poll_err=$(mktemp)
    # shellcheck disable=SC2064
    trap "rm -f '$poll_err'" EXIT

    while :; do
        attempt=$((attempt + 1))
        # shellcheck disable=SC2086
        running_ids=$(docker compose -f "$COMPOSE_FILE" ps --status running --quiet \
            $ACTIVE_WORKERS 2>"$poll_err" || true)
        running_count=$(printf '%s' "$running_ids" | grep -c . || true)
        running_count=${running_count:-0}

        if [ "$running_count" -gt 0 ]; then
            echo "Worker services running after $attempt poll(s):" \
                "$running_count container(s) of [$ACTIVE_WORKERS]"
            return 0
        fi

        echo "Waiting for worker services to report running" \
            "(poll $attempt, none running yet): $ACTIVE_WORKERS"

        if [ "$(date +%s)" -ge "$deadline" ]; then
            break
        fi
        sleep "$WORKER_READY_INTERVAL"
    done

    echo "ERROR: no restarted worker service became running for $COMPOSE_FILE" \
        "within ${WORKER_READY_TIMEOUT}s" >&2
    echo "       waited on: $ACTIVE_WORKERS" >&2
    echo "       poll: docker compose -f $COMPOSE_FILE ps --status running --quiet" \
        "$ACTIVE_WORKERS ($attempt polls, all empty)" >&2
    if [ -s "$poll_err" ]; then
        echo "       last docker error: $(tr '\n' ' ' < "$poll_err")" >&2
    fi
    exit 1
}

ACTIVE_WORKERS=$(resolve_worker_services)
if [ -z "$ACTIVE_WORKERS" ]; then
    echo "ERROR: no containers found for worker services in $COMPOSE_FILE" >&2
    echo "       looked for: $WORKER_SERVICES" >&2
    echo "       bring the stack up first, or set WORKER_SERVICES to match it" >&2
    exit 1
fi

# shellcheck disable=SC2086
run_compose "stopping worker services" \
    docker compose -f "$COMPOSE_FILE" stop $ACTIVE_WORKERS

# Bring the backend container back so we can exec into it for the restore,
# but with the workers off it won't be serving traffic until we restart them.
run_compose "starting $MEDIA_SERVICE" \
    docker compose -f "$COMPOSE_FILE" start "$MEDIA_SERVICE"

# Use sh -c so we can chain the rm + tar inside one container, with the
# archive streamed in over stdin. Trailing `.` keeps tar entries relative.
docker compose -f "$COMPOSE_FILE" exec -T "$MEDIA_SERVICE" \
    sh -c "set -e; rm -rf $MEDIA_PATH/* $MEDIA_PATH/.[!.]* 2>/dev/null || true; tar -C $MEDIA_PATH -xzf -" \
    < "$ARCHIVE"

# shellcheck disable=SC2086
run_compose "starting worker services" \
    docker compose -f "$COMPOSE_FILE" start $ACTIVE_WORKERS

wait_for_workers_running

ENTRIES=$(tar -tzf "$ARCHIVE" 2>/dev/null | wc -l | tr -d ' ')
echo "Media restore complete from $ARCHIVE ($ENTRIES entries)"
