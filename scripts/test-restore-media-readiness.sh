#!/usr/bin/env bash
# Acceptance evidence for the worker-readiness wait in scripts/restore-media.sh.
#
# `docker compose start` returns when the command is issued, not when the
# containers report running. restore-media.sh restarted the workers as its last
# act and returned; restore-drill.sh then gated on
# `ps --status running --quiet | grep -q .` roughly 0.4s later and could
# legitimately observe zero running services, failing an innocent PR in the
# "Prod Stack Smoke" job with `no running compose services for
# docker-compose.prod.yml`. main has been winning that race by luck (op-fa1).
#
# These checks are docker-free: a bash test double named `docker` is put first
# on PATH and driven by env vars, so the whole stop -> restore -> start -> wait
# sequence can be exercised — including states a real stack only reaches
# intermittently — without a daemon.
#
# Mapped to .criteria/drill-readiness.md:
#
#   AC-1: restore waits for restarted workers to be running.
#   AC-2: readiness failure is bounded and explicit.
#   AC-3: worker stop failures fail at restore-media.
#   AC-4: worker start failures fail at restore-media.
#   AC-5: the DB drill precondition no longer sees the media-restore race.
#
# AC-6..AC-10 are verification commands (pytest, npm test, pre-commit,
# makemigrations --check, check_permission_matrix), not behaviours under test.
#
# Usage: scripts/test-restore-media-readiness.sh
# Exits 0 when every check passes, 1 otherwise.

# No `-e`: a failed assertion should be reported with the others, not abort the
# run on the first one.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESTORE_MEDIA="$REPO_ROOT/scripts/restore-media.sh"
RESTORE_DRILL="$REPO_ROOT/scripts/restore-drill.sh"

# The script's own defaults, restated here so a change to either side is a
# visible diff rather than a silently diverging check.
WORKER_SERVICES="backend celery celery_beat flower mqtt_consumer"
MEDIA_SERVICE="backend"

# The exact precondition restore-drill.sh runs before the DB drill. AC-5 is
# about this command seeing a running stack once restore-media.sh returns.
DRILL_PRECONDITION='docker compose -f "$COMPOSE_FILE" ps --status running --quiet | grep -q .'

BASE_PATH="/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin"

TESTS_RUN=0
TESTS_FAILED=0
CURRENT_TEST=""
CURRENT_FAILED=0
FAILED_NAMES=()

# ---------------------------------------------------------------------------
# Test double for the docker CLI. Records every call (and, for readiness polls,
# the verdict it handed back) so a check can assert on the *sequence* the script
# drove, not just its exit code.
# ---------------------------------------------------------------------------
write_docker_double() {
    cat >"$1" <<'DOUBLE_EOF'
#!/bin/bash
set -u

log() { printf '%s\n' "$*" >> "$DOUBLE_LOG"; }

log "docker $*"

[ "${1:-}" = "compose" ] || { echo "double: unexpected docker call: $*" >&2; exit 99; }
shift
[ "${1:-}" = "-f" ] || { echo "double: expected -f <compose-file>, got: $*" >&2; exit 99; }
shift 2

cmd="${1:-}"
shift || true

case "$cmd" in
    ps)
        case " $* " in
            *" --status running "*)
                # Readiness poll. Stay empty for DOUBLE_PS_EMPTY_POLLS polls so
                # the caller has to actually wait, then report a running worker.
                polls=0
                [ -f "$DOUBLE_STATE/polls" ] && polls=$(cat "$DOUBLE_STATE/polls")
                polls=$((polls + 1))
                printf '%s' "$polls" > "$DOUBLE_STATE/polls"
                if [ "${DOUBLE_PS_NEVER_RUNNING:-0}" = "1" ] ||
                   [ "$polls" -le "${DOUBLE_PS_EMPTY_POLLS:-0}" ]; then
                    log "  -> poll $polls: none running"
                    exit 0
                fi
                log "  -> poll $polls: 1 running"
                echo "container-worker-1"
                exit 0
                ;;
            *)
                # `ps -a --quiet <service>`: does this service have a container?
                for svc in ${DOUBLE_SERVICES_WITHOUT_CONTAINERS:-}; do
                    case " $* " in
                        *" $svc "*) log "  -> no container for $svc"; exit 0 ;;
                    esac
                done
                echo "container-existing"
                exit 0
                ;;
        esac
        ;;
    stop)
        rc="${DOUBLE_STOP_RC:-0}"
        [ "$rc" = 0 ] || echo "Error response from daemon: cannot stop container" >&2
        exit "$rc"
        ;;
    start)
        # One argument matching MEDIA_SERVICE is the pre-exec media start; any
        # other shape is the post-restore worker start.
        if [ "$#" -eq 1 ] && [ "${1:-}" = "${DOUBLE_MEDIA_SERVICE:-backend}" ]; then
            rc="${DOUBLE_START_MEDIA_RC:-0}"
        else
            rc="${DOUBLE_START_WORKERS_RC:-0}"
        fi
        [ "$rc" = 0 ] || echo "Error response from daemon: cannot start container" >&2
        exit "$rc"
        ;;
    exec)
        cat > /dev/null   # drain the archive streamed in on stdin
        exit "${DOUBLE_EXEC_RC:-0}"
        ;;
esac

echo "double: unhandled compose subcommand: $cmd" >&2
exit 98
DOUBLE_EOF
    chmod 0755 "$1"
}

# ---------------------------------------------------------------------------
# Assertions. Each records against the running check rather than exiting.
# ---------------------------------------------------------------------------
note_failure() {
    CURRENT_FAILED=1
    printf '    FAIL: %s\n' "$1" >&2
}

assert_eq() {
    [ "$1" = "$2" ] && return 0
    note_failure "$3 (expected '$1', got '$2')"
}

assert_ne() {
    [ "$1" != "$2" ] && return 0
    note_failure "$3 (expected something other than '$1')"
}

assert_contains() {
    case "$1" in
        *"$2"*) return 0 ;;
    esac
    note_failure "$3 (missing: '$2')"
}

assert_not_contains() {
    case "$1" in
        *"$2"*) note_failure "$3 (unexpectedly present: '$2')" ; return 0 ;;
    esac
}

# assert_before <text> <needle-a> <needle-b> <message>
# Asserts needle-a appears on an earlier line than needle-b.
assert_before() {
    local text="$1" a="$2" b="$3" msg="$4" ia ib
    ia=$(printf '%s\n' "$text" | grep -n -F -- "$a" | head -1 | cut -d: -f1)
    ib=$(printf '%s\n' "$text" | grep -n -F -- "$b" | head -1 | cut -d: -f1)
    if [ -z "$ia" ]; then note_failure "$msg (never saw: '$a')"; return; fi
    if [ -z "$ib" ]; then note_failure "$msg (never saw: '$b')"; return; fi
    [ "$ia" -lt "$ib" ] && return 0
    note_failure "$msg ('$a' at line $ia did not precede '$b' at line $ib)"
}

begin_test() {
    CURRENT_TEST="$1"
    CURRENT_FAILED=0
    TESTS_RUN=$((TESTS_RUN + 1))
    printf '  - %s\n' "$CURRENT_TEST"
}

end_test() {
    if [ "$CURRENT_FAILED" -ne 0 ]; then
        TESTS_FAILED=$((TESTS_FAILED + 1))
        FAILED_NAMES+=("$CURRENT_TEST")
    fi
}

# ---------------------------------------------------------------------------
# A fake compose stack: docker double on PATH, a real archive, a state dir.
# ---------------------------------------------------------------------------
setup_stack() {
    STACK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/restore-media-check.XXXXXX")"
    STACK_DIRS+=("$STACK_DIR")

    mkdir -p "$STACK_DIR/bin" "$STACK_DIR/state"
    write_docker_double "$STACK_DIR/bin/docker"

    # A real gzip archive: restore-media.sh gunzip -t's it up front and
    # tar -tzf's it at the end to count entries.
    printf 'media-bytes' >"$STACK_DIR/photo.jpg"
    ARCHIVE="$STACK_DIR/oms-media-20260811T000000Z.tgz"
    tar -czf "$ARCHIVE" -C "$STACK_DIR" ./photo.jpg

    # Point COMPOSE_FILE at a throwaway path so nothing can reach a real stack
    # even if the double were somehow bypassed.
    COMPOSE_FILE="$STACK_DIR/docker-compose.test.yml"
    printf 'services: {}\n' >"$COMPOSE_FILE"

    DOUBLE_LOG="$STACK_DIR/docker-calls.log"
    : >"$DOUBLE_LOG"

    STDOUT_FILE="$STACK_DIR/stdout"
    STDERR_FILE="$STACK_DIR/stderr"

    # Shared stack state: every process that polls sees the same coming-up
    # sequence, which is what makes the AC-5 handoff from restore-media.sh to
    # the drill precondition meaningful.
    #
    # Keep the readiness budget short; the point of each check is the
    # sequencing, not the wall clock.
    STACK_ENV=(
        "PATH=$STACK_DIR/bin:$BASE_PATH"
        "RESTORE_MEDIA_CONFIRM=YES"
        "COMPOSE_FILE=$COMPOSE_FILE"
        "MEDIA_SERVICE=$MEDIA_SERVICE"
        "WORKER_SERVICES=$WORKER_SERVICES"
        "DOUBLE_MEDIA_SERVICE=$MEDIA_SERVICE"
        "DOUBLE_LOG=$DOUBLE_LOG"
        "DOUBLE_STATE=$STACK_DIR/state"
        "DOUBLE_PS_EMPTY_POLLS=0"
        "WORKER_READY_TIMEOUT=10"
        "WORKER_READY_INTERVAL=0.05"
    )
}

# run_restore_media [VAR=VAL ...] -> sets RC, OUT, ERR
run_restore_media() {
    env -i "${STACK_ENV[@]}" "$@" \
        bash "$RESTORE_MEDIA" "$ARCHIVE" >"$STDOUT_FILE" 2>"$STDERR_FILE"
    RC=$?
    OUT="$(cat "$STDOUT_FILE")"
    ERR="$(cat "$STDERR_FILE")"
}

# run_drill_precondition [VAR=VAL ...] -> sets PRE_RC
run_drill_precondition() {
    env -i "${STACK_ENV[@]}" "$@" bash -c "$DRILL_PRECONDITION" >/dev/null 2>&1
    PRE_RC=$?
}

calls() { cat "$DOUBLE_LOG"; }

STACK_DIRS=()
cleanup() {
    local d
    for d in "${STACK_DIRS[@]:-}"; do
        [ -n "$d" ] && [ -d "$d" ] && rm -rf "$d"
    done
}
trap cleanup EXIT

# ===========================================================================
# AC-1: exit 0 only after a poll observes a worker running.
# ===========================================================================
echo "AC-1: restore waits for restarted workers to be running"

begin_test "waits through not-yet-running polls, then completes"
setup_stack
run_restore_media DOUBLE_PS_EMPTY_POLLS=2
assert_eq 0 "$RC" "restore-media.sh must exit 0: $OUT $ERR"
assert_contains "$OUT" "Media restore complete from" "must print the completion line"
# The evidence AC-1 asks for: the poll saw a not-yet-running state, then a
# running one, and only then did the script finish.
assert_before "$OUT" "Waiting for worker services to report running" \
    "Worker services running after 3 poll(s)" "must wait before observing running"
assert_before "$OUT" "Worker services running after 3 poll(s)" \
    "Media restore complete from" "must observe running before completing"
assert_contains "$OUT" "poll 1, none running yet" "first poll must report none running"
CALLS="$(calls)"
assert_before "$CALLS" "start $WORKER_SERVICES" "-> poll 1: none running" \
    "workers must be started before the first readiness poll"
assert_before "$CALLS" "-> poll 1: none running" "-> poll 3: 1 running" \
    "the empty poll must precede the running poll"
end_test

begin_test "returns on the first poll when workers are already running"
setup_stack
# The common case must not pay for the fix: no empty polls, no sleep.
run_restore_media DOUBLE_PS_EMPTY_POLLS=0
assert_eq 0 "$RC" "restore-media.sh must exit 0: $OUT $ERR"
assert_contains "$OUT" "Worker services running after 1 poll(s)" "must succeed on poll 1"
assert_not_contains "$OUT" "none running yet" "must not wait when already running"
end_test

# ===========================================================================
# AC-2: bounded, explicit failure when readiness never arrives.
# ===========================================================================
echo "AC-2: readiness failure is bounded and explicit"

begin_test "times out with an error naming the compose file and services"
setup_stack
STARTED=$(date +%s)
run_restore_media DOUBLE_PS_NEVER_RUNNING=1 WORKER_READY_TIMEOUT=1 WORKER_READY_INTERVAL=0.1
ELAPSED=$(( $(date +%s) - STARTED ))
assert_ne 0 "$RC" "restore-media.sh must fail when readiness never arrives"
# Bounded: a 1s budget must not turn into an open-ended wait.
if [ "$ELAPSED" -ge 15 ]; then
    note_failure "readiness wait was not bounded (${ELAPSED}s)"
fi
assert_contains "$ERR" "no restarted worker service became running" "must explain the failure"
assert_contains "$ERR" "$COMPOSE_FILE" "error must name the compose file"
for svc in $WORKER_SERVICES; do
    assert_contains "$ERR" "$svc" "error must name $svc"
done
# A failed readiness wait is not a completed restore.
assert_not_contains "$OUT" "Media restore complete" "must not claim completion"
end_test

# ===========================================================================
# AC-3: a failing `docker compose stop` fails right there.
# ===========================================================================
echo "AC-3: worker stop failures fail at restore-media"

begin_test "stop failure aborts before restore and is not swallowed"
setup_stack
run_restore_media DOUBLE_STOP_RC=1
assert_ne 0 "$RC" "restore-media.sh must fail when stop fails"
assert_contains "$ERR" "stopping worker services failed" "must name the failing step"
# The docker error itself must survive — `>/dev/null 2>&1 || true` used to eat
# both the exit code and the reason.
assert_contains "$ERR" "Error response from daemon" "must surface the docker error"
CALLS="$(calls)"
assert_contains "$CALLS" "stop $WORKER_SERVICES" "must have attempted the stop"
assert_not_contains "$CALLS" " exec " "media restore must not run"
assert_not_contains "$CALLS" " start " "workers must not be restarted"
assert_not_contains "$OUT" "Media restore complete" "must not claim completion"
end_test

# ===========================================================================
# AC-4: a failing `docker compose start` fails at restore-media.
# ===========================================================================
echo "AC-4: worker start failures fail at restore-media"

begin_test "media service start failure aborts before exec"
setup_stack
run_restore_media DOUBLE_START_MEDIA_RC=1
assert_ne 0 "$RC" "restore-media.sh must fail when the media start fails"
assert_contains "$ERR" "starting $MEDIA_SERVICE failed" "must name the failing step"
assert_contains "$ERR" "Error response from daemon" "must surface the docker error"
assert_not_contains "$(calls)" " exec " "media restore must not run"
assert_not_contains "$OUT" "Media restore complete" "must not claim completion"
end_test

begin_test "worker start failure fails here, not in the drill"
setup_stack
run_restore_media DOUBLE_START_WORKERS_RC=1
assert_ne 0 "$RC" "restore-media.sh must fail when the worker start fails"
assert_contains "$ERR" "starting worker services failed" "must name the failing step"
assert_contains "$ERR" "Error response from daemon" "must surface the docker error"
CALLS="$(calls)"
# The restore itself ran; the failure is the restart that followed.
assert_contains "$CALLS" " exec " "the media restore should have run"
# Failing at the start means we never reach the readiness poll — the error
# names the start, not a confusing downstream precondition.
assert_not_contains "$CALLS" "--status running" "must not poll after a failed start"
assert_not_contains "$OUT" "Media restore complete" "must not claim completion"
end_test

# ===========================================================================
# Making the stop/start fatal (AC-3, AC-4) is only safe if the services we hand
# compose actually have containers.
#
# `docker compose start` fails with `service "x" has no container to start`, and
# the CI "Prod Stack Smoke" job boots only db, redis, emqx and backend — never
# celery, celery_beat, flower or mqtt_consumer. Without this filtering, dropping
# `|| true` would fail that job on every run: the same class of false red this
# change exists to remove.
# ===========================================================================
echo "Worker set resolution (keeps AC-3/AC-4 safe)"

begin_test "only services with containers are stopped and started"
setup_stack
# Exactly the CI prod-smoke shape: backend is up, the workers were never created.
run_restore_media DOUBLE_SERVICES_WITHOUT_CONTAINERS="celery celery_beat flower mqtt_consumer"
assert_eq 0 "$RC" "restore-media.sh must exit 0: $OUT $ERR"
assert_contains "$OUT" "Media restore complete from" "must complete"
# Exactly one stop and two starts (the pre-exec media start and the post-restore
# worker start), every one of them naming only backend. A container-less service
# reaching `start` is what compose rejects.
PREFIX="docker compose -f $COMPOSE_FILE"
STOPS="$(calls | grep -F -- " stop " || true)"
STARTS="$(calls | grep -F -- " start " || true)"
assert_eq "$PREFIX stop backend" "$STOPS" "only backend may be stopped"
assert_eq "$(printf '%s\n%s' "$PREFIX start backend" "$PREFIX start backend")" "$STARTS" \
    "only backend may be started, twice"
end_test

begin_test "no worker containers at all fails with a clear error"
setup_stack
run_restore_media DOUBLE_SERVICES_WITHOUT_CONTAINERS="$WORKER_SERVICES"
assert_ne 0 "$RC" "restore-media.sh must fail when no worker containers exist"
assert_contains "$ERR" "no containers found for worker services" "must explain the failure"
assert_contains "$ERR" "$COMPOSE_FILE" "error must name the compose file"
# Fail before touching media rather than mid-restore.
assert_not_contains "$(calls)" " exec " "must not touch media"
end_test

# ===========================================================================
# AC-5: the drill's precondition no longer races the media restore.
# ===========================================================================
echo "AC-5: DB drill precondition no longer sees the media-restore race"

begin_test "this check replicates the real drill precondition"
# If restore-drill.sh's gate changes shape, this check is testing a fiction —
# fail loudly instead.
DRILL_TEXT="$(cat "$RESTORE_DRILL")"
assert_contains "$DRILL_TEXT" "$DRILL_PRECONDITION" \
    "restore-drill.sh must still use the precondition this check replicates"
assert_contains "$DRILL_TEXT" "no running compose services for" \
    "restore-drill.sh must still fail with the message this race produced"
end_test

begin_test "precondition fails while workers are still coming up"
setup_stack
# The race, demonstrated: run the drill's gate against a stack whose workers
# have been started but are not running yet. This is what restore-media.sh used
# to hand straight to restore-drill.sh.
run_drill_precondition DOUBLE_PS_NEVER_RUNNING=1
assert_ne 0 "$PRE_RC" "the drill gate must fail while nothing is running"
end_test

begin_test "precondition passes once restore-media returns"
setup_stack
# One stack, one coming-up sequence: the workers report running from the third
# poll onward to *whoever* asks. restore-media.sh must absorb that window so the
# config and DB drill steps that follow start from a running stack. Without the
# wait the drill's own gate is the first poll, sees nothing running, and fails an
# innocent PR.
STACK_ENV+=("DOUBLE_PS_EMPTY_POLLS=2")
run_restore_media
assert_eq 0 "$RC" "restore-media.sh must exit 0: $OUT $ERR"
run_drill_precondition
assert_eq 0 "$PRE_RC" \
    "restore-drill.sh would fail with 'no running compose services' — restore-media.sh returned before the workers were running"
# And the run really did pass through a not-yet-running state, so the pass above
# is the wait working, not the double being lenient.
assert_contains "$(calls)" "-> poll 1: none running" \
    "the run must have observed a not-yet-running state"
end_test

# ===========================================================================
echo
if [ "$TESTS_FAILED" -eq 0 ]; then
    echo "PASS: $TESTS_RUN/$TESTS_RUN restore-media readiness checks"
    exit 0
fi

echo "FAIL: $TESTS_FAILED of $TESTS_RUN restore-media readiness checks failed" >&2
for name in "${FAILED_NAMES[@]}"; do
    echo "  - $name" >&2
done
exit 1
