#!/bin/bash
# Disposable backup → restore → smoke drill for the OMS Docker Compose stack.
#
# Walks the AC-25 happy path end-to-end without putting the production data at
# risk: takes a fresh DB and media backup from the *current* compose stack,
# restores them back into the same stack (proving the dumps load), waits for
# the backend to come back, and runs scripts/smoke.sh to verify the restored
# surfaces respond. The dumps and a JSON evidence record are left in
# DRILL_DIR so the run can be attached to docs/PROFICIENCY_METRICS.md.
#
# This is the orchestrator that wraps backup-db.sh, backup-media.sh,
# restore-db.sh, restore-media.sh, and smoke.sh. It is NOT a clean-namespace
# DR drill — for that, follow deploy/BACKUP_RESTORE.md §8.
#
# Usage:
#   scripts/restore-drill.sh                          # full drill against ./docker-compose.prod.yml
#   scripts/restore-drill.sh --dry-run                # parse + show what would run, exit 0
#   scripts/restore-drill.sh --skip-media             # DB-only drill (faster)
#   DRILL_DIR=/tmp/oms-drill scripts/restore-drill.sh # custom evidence dir
#
# Exit codes:
#   0  drill completed, smoke checks green
#   1  drill failed (a step errored, or smoke checks red)
#   2  internal scripting / prereq error
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DRILL_DIR="${DRILL_DIR:-$REPO_ROOT/.restore-drill}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
SMOKE_HOST="${SMOKE_HOST:-localhost}"
SMOKE_SCHEME="${SMOKE_SCHEME:-http}"
PG_FORMAT="${PG_FORMAT:-custom}"
DRY_RUN=0
SKIP_MEDIA=0
SKIP_SMOKE=0

usage() {
    cat <<EOF
Usage: $0 [--dry-run] [--skip-media] [--skip-smoke] [--help]

Environment overrides:
  DRILL_DIR        Where dumps + evidence land (default: <repo>/.restore-drill)
  COMPOSE_FILE     Compose file (default: docker-compose.prod.yml)
  SMOKE_HOST       Host smoke.sh probes (default: localhost)
  SMOKE_SCHEME     http or https (default: http)
  PG_FORMAT        plain or custom (default: custom — matches the runbook)

Flags:
  --dry-run        Print the plan + verify scripts parse; touch nothing live
  --skip-media     Only exercise the DB backup/restore path
  --skip-smoke     Run backup + restore but stop short of smoke.sh
  --help           Show this help
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run)    DRY_RUN=1; shift ;;
        --skip-media) SKIP_MEDIA=1; shift ;;
        --skip-smoke) SKIP_SMOKE=1; shift ;;
        --help|-h)    usage; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; usage >&2; exit 1 ;;
    esac
done

TS=$(date -u +%Y%m%dT%H%M%SZ)
LOG_DIR="$DRILL_DIR/$TS"
mkdir -p "$LOG_DIR"

EVIDENCE="$LOG_DIR/evidence.json"
DB_LOG="$LOG_DIR/db.log"
MEDIA_LOG="$LOG_DIR/media.log"
SMOKE_LOG="$LOG_DIR/smoke.json"

cd "$REPO_ROOT"

log() { printf '[drill %s] %s\n' "$(date -u +%H:%M:%SZ)" "$*"; }
fail() { log "ERROR: $*"; exit 1; }

require_script() {
    local rel="$1"
    if [ ! -f "$rel" ]; then
        fail "missing required script: $rel"
    fi
    if ! bash -n "$rel" 2>/dev/null; then
        fail "syntax check failed for $rel"
    fi
}

# Sanity: every script we depend on must parse before we touch anything.
require_script scripts/backup-db.sh
require_script scripts/restore-db.sh
require_script scripts/smoke.sh
if [ "$SKIP_MEDIA" = 0 ]; then
    require_script scripts/backup-media.sh
    require_script scripts/restore-media.sh
fi

if [ "$DRY_RUN" = 1 ]; then
    log "dry-run: all required scripts present and parse clean"
    log "  evidence dir would be: $LOG_DIR"
    log "  compose file:          $COMPOSE_FILE"
    log "  pg format:             $PG_FORMAT"
    log "  skip media:            $SKIP_MEDIA"
    log "  skip smoke:            $SKIP_SMOKE"
    exit 0
fi

if ! command -v docker >/dev/null 2>&1; then
    fail "docker CLI not on PATH"
fi
if [ ! -f "$COMPOSE_FILE" ]; then
    fail "compose file not found: $COMPOSE_FILE (set COMPOSE_FILE to override)"
fi
if ! docker compose -f "$COMPOSE_FILE" ps --status running --quiet | grep -q .; then
    fail "no running compose services for $COMPOSE_FILE — bring the stack up first"
fi

# 1. Capture fresh dumps.
log "capturing fresh DB dump (format=$PG_FORMAT) into $LOG_DIR"
BACKUP_DIR="$LOG_DIR" PG_FORMAT="$PG_FORMAT" \
    RETENTION_DAYS=0 \
    bash scripts/backup-db.sh > "$DB_LOG" 2>&1 \
    || { cat "$DB_LOG"; fail "backup-db.sh failed"; }

if [ "$PG_FORMAT" = "custom" ]; then
    DB_ARTIFACT=$(ls -1 "$LOG_DIR"/oms-*.dump 2>/dev/null | tail -n 1)
else
    DB_ARTIFACT=$(ls -1 "$LOG_DIR"/backup-*.sql.gz 2>/dev/null | tail -n 1)
fi
[ -n "$DB_ARTIFACT" ] || fail "backup-db.sh succeeded but no artifact landed in $LOG_DIR"

if [ "$SKIP_MEDIA" = 0 ]; then
    log "capturing fresh media archive into $LOG_DIR"
    BACKUP_DIR="$LOG_DIR" RETENTION_DAYS=0 \
        bash scripts/backup-media.sh > "$MEDIA_LOG" 2>&1 \
        || { cat "$MEDIA_LOG"; fail "backup-media.sh failed"; }
    MEDIA_ARTIFACT=$(ls -1 "$LOG_DIR"/oms-media-*.tgz 2>/dev/null | tail -n 1)
    [ -n "$MEDIA_ARTIFACT" ] || fail "backup-media.sh succeeded but no artifact landed in $LOG_DIR"
fi

# 2. Restore from the fresh dumps. This proves the dump is loadable — running
#    the restore back into the same stack is intentional: we're verifying the
#    backup chain, not migrating data.
log "restoring DB from $DB_ARTIFACT"
RESTORE_DB_CONFIRM=YES bash scripts/restore-db.sh "$DB_ARTIFACT" >> "$DB_LOG" 2>&1 \
    || { cat "$DB_LOG"; fail "restore-db.sh failed"; }

if [ "$SKIP_MEDIA" = 0 ]; then
    log "restoring media from $MEDIA_ARTIFACT"
    RESTORE_MEDIA_CONFIRM=YES bash scripts/restore-media.sh "$MEDIA_ARTIFACT" \
        >> "$MEDIA_LOG" 2>&1 \
        || { cat "$MEDIA_LOG"; fail "restore-media.sh failed"; }
fi

# 3. Wait for backend to come back. After restore-db.sh the backend was
#    restarted; we tolerate up to 60s before declaring red.
if [ "$SKIP_SMOKE" = 0 ]; then
    log "waiting up to 60s for backend liveness at $SMOKE_SCHEME://$SMOKE_HOST/api/health/livez/"
    deadline=$(( $(date +%s) + 60 ))
    live=""
    while [ "$(date +%s)" -lt "$deadline" ]; do
        if curl -fsS -o /dev/null --max-time 5 \
            "$SMOKE_SCHEME://$SMOKE_HOST/api/health/livez/" 2>/dev/null; then
            live=ok
            break
        fi
        sleep 2
    done
    if [ "$live" != "ok" ]; then
        log "backend did not return livez=200 within 60s"
    fi

    log "running smoke.sh against $SMOKE_SCHEME://$SMOKE_HOST"
    set +e
    HOST="$SMOKE_HOST" SCHEME="$SMOKE_SCHEME" \
        bash scripts/smoke.sh --json > "$SMOKE_LOG"
    smoke_rc=$?
    set -e
else
    smoke_rc=0
    printf '{"skipped":true}\n' > "$SMOKE_LOG"
fi

# 4. Write evidence document.
db_size=$(du -h "$DB_ARTIFACT" | cut -f1)
media_size="-"
media_path="-"
if [ "$SKIP_MEDIA" = 0 ]; then
    media_size=$(du -h "$MEDIA_ARTIFACT" | cut -f1)
    media_path="$MEDIA_ARTIFACT"
fi
status="passed"
[ "$smoke_rc" = 0 ] || status="failed"

cat > "$EVIDENCE" <<EOF
{
  "drill_id": "$TS",
  "started_at": "$TS",
  "compose_file": "$COMPOSE_FILE",
  "pg_format": "$PG_FORMAT",
  "db_artifact": "$DB_ARTIFACT",
  "db_artifact_size": "$db_size",
  "media_artifact": "$media_path",
  "media_artifact_size": "$media_size",
  "smoke_skipped": $([ "$SKIP_SMOKE" = 1 ] && echo true || echo false),
  "smoke_exit_code": $smoke_rc,
  "status": "$status"
}
EOF

log "evidence written to $EVIDENCE"
if [ "$smoke_rc" != 0 ] && [ "$SKIP_SMOKE" = 0 ]; then
    log "smoke checks failed — see $SMOKE_LOG"
fi

exit $smoke_rc
