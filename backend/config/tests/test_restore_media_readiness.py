"""Contract tests for the worker-readiness wait in scripts/restore-media.sh.

`docker compose start` returns when the command is issued, not when the
containers report running. restore-media.sh restarted the workers as its last
act and returned; restore-drill.sh then gated on
`ps --status running --quiet | grep -q .` roughly 0.4s later and could
legitimately observe zero running services, failing an innocent PR in the
"Prod Stack Smoke" job with `no running compose services for
docker-compose.prod.yml`. main has been winning that race by luck (op-fa1).

These tests are docker-free: a bash test double named `docker` is put first on
PATH and driven by env vars, so the whole stop → restore → start → wait
sequence can be exercised — including states a real stack only reaches
intermittently — without a daemon.

Mapped to `.criteria/drill-readiness.md`:

- AC-1: restore waits for restarted workers to be running.
- AC-2: readiness failure is bounded and explicit.
- AC-3: worker stop failures fail at restore-media.
- AC-4: worker start failures fail at restore-media.
- AC-5: the DB drill precondition no longer sees the media-restore race.

AC-6..AC-10 are verification commands (pytest, npm test, pre-commit,
makemigrations --check, check_permission_matrix), not behaviours under test.
"""

from __future__ import annotations

import shutil
import subprocess
import tarfile
import time
from pathlib import Path

import pytest


def _find_repo_root() -> Path | None:
    for p in Path(__file__).resolve().parents:
        if (p / ".criteria").is_dir() and (p / "docker-compose.prod.yml").is_file():
            return p
    return None


REPO_ROOT = _find_repo_root()
pytestmark = [
    pytest.mark.skipif(
        REPO_ROOT is None,
        reason="deploy artifacts not present (running inside backend container)",
    ),
    pytest.mark.skipif(shutil.which("bash") is None, reason="bash required"),
]

# The script's own defaults, restated here so a change to either side is a
# visible diff rather than a silently diverging test.
WORKER_SERVICES = "backend celery celery_beat flower mqtt_consumer"
MEDIA_SERVICE = "backend"

# The exact precondition restore-drill.sh runs before the DB drill. AC-5 is
# about this command seeing a running stack once restore-media.sh returns.
DRILL_PRECONDITION = 'docker compose -f "$COMPOSE_FILE" ps --status running --quiet | grep -q .'

# Test double for the docker CLI. Records every call (and, for readiness
# polls, the verdict it handed back) so a test can assert on the *sequence*
# the script drove, not just its exit code.
DOCKER_DOUBLE = r"""#!/bin/bash
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
"""


@pytest.fixture(scope="module")
def repo_root() -> Path:
    assert REPO_ROOT is not None
    return REPO_ROOT


@pytest.fixture
def stack(tmp_path: Path) -> dict:
    """A fake compose stack: docker double on PATH, archive, state dir."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    double = bin_dir / "docker"
    double.write_text(DOCKER_DOUBLE)
    double.chmod(0o755)

    state = tmp_path / "state"
    state.mkdir()

    # A real gzip archive: restore-media.sh gunzip -t's it up front and
    # tar -tzf's it at the end to count entries.
    payload = tmp_path / "photo.jpg"
    payload.write_bytes(b"media-bytes")
    archive = tmp_path / "oms-media-20260811T000000Z.tgz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(payload, arcname="./photo.jpg")

    # Point COMPOSE_FILE at a throwaway path so nothing can reach a real stack
    # even if the double were somehow bypassed.
    compose_file = tmp_path / "docker-compose.test.yml"
    compose_file.write_text("services: {}\n")

    return {
        "archive": str(archive),
        "compose_file": str(compose_file),
        "log": str(tmp_path / "docker-calls.log"),
        "state": str(state),
        "env": {
            "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/local/bin",
            "RESTORE_MEDIA_CONFIRM": "YES",
            "COMPOSE_FILE": str(compose_file),
            "MEDIA_SERVICE": MEDIA_SERVICE,
            "WORKER_SERVICES": WORKER_SERVICES,
            "DOUBLE_MEDIA_SERVICE": MEDIA_SERVICE,
            "DOUBLE_LOG": str(tmp_path / "docker-calls.log"),
            "DOUBLE_STATE": str(state),
            # Shared stack state: every process that polls sees the same
            # coming-up sequence, which is what makes the AC-5 handoff from
            # restore-media.sh to the drill precondition meaningful.
            "DOUBLE_PS_EMPTY_POLLS": "0",
            # Keep the readiness budget short; the point of each test is the
            # sequencing, not the wall clock.
            "WORKER_READY_TIMEOUT": "10",
            "WORKER_READY_INTERVAL": "0.05",
        },
    }


def _restore_media(repo_root: Path, stack: dict, **env) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(repo_root / "scripts" / "restore-media.sh"), stack["archive"]],
        capture_output=True,
        text=True,
        check=False,
        env={**stack["env"], **env},
    )


def _calls(stack: dict) -> list[str]:
    log = Path(stack["log"])
    return log.read_text().splitlines() if log.exists() else []


def _index_of(lines: list[str], needle: str) -> int:
    for i, line in enumerate(lines):
        if needle in line:
            return i
    raise AssertionError(f"{needle!r} not found in:\n" + "\n".join(lines))


class TestWorkerReadinessWaitAC1:
    """AC-1: exit 0 only after a poll observes a worker running."""

    def test_waits_through_not_yet_running_polls_then_completes(self, repo_root, stack):
        result = _restore_media(repo_root, stack, DOUBLE_PS_EMPTY_POLLS="2")

        assert result.returncode == 0, result.stdout + result.stderr
        assert "Media restore complete from" in result.stdout

        out = result.stdout.splitlines()
        first_wait = _index_of(out, "Waiting for worker services to report running")
        became_running = _index_of(out, "Worker services running after 3 poll(s)")
        completed = _index_of(out, "Media restore complete from")

        # The evidence AC-1 asks for: the poll saw a not-yet-running state,
        # then a running one, and only then did the script finish.
        assert first_wait < became_running < completed
        assert "poll 1, none running yet" in out[first_wait]

        calls = _calls(stack)
        worker_start = _index_of(calls, f"start {WORKER_SERVICES}")
        first_poll = _index_of(calls, "-> poll 1: none running")
        running_poll = _index_of(calls, "-> poll 3: 1 running")
        assert worker_start < first_poll < running_poll

    def test_returns_on_the_first_poll_when_workers_are_already_running(self, repo_root, stack):
        # The common case must not pay for the fix: no empty polls, no sleep.
        result = _restore_media(repo_root, stack, DOUBLE_PS_EMPTY_POLLS="0")

        assert result.returncode == 0, result.stdout + result.stderr
        assert "Worker services running after 1 poll(s)" in result.stdout
        assert "none running yet" not in result.stdout


class TestWorkerReadinessTimeoutAC2:
    """AC-2: bounded, explicit failure when readiness never arrives."""

    def test_times_out_with_an_error_naming_compose_file_and_services(self, repo_root, stack):
        started = time.monotonic()
        result = _restore_media(
            repo_root,
            stack,
            DOUBLE_PS_NEVER_RUNNING="1",
            WORKER_READY_TIMEOUT="1",
            WORKER_READY_INTERVAL="0.1",
        )
        elapsed = time.monotonic() - started

        assert result.returncode != 0
        # Bounded: a 1s budget must not turn into an open-ended wait.
        assert elapsed < 15, f"readiness wait was not bounded ({elapsed:.1f}s)"

        assert "no restarted worker service became running" in result.stderr
        assert stack["compose_file"] in result.stderr
        for service in WORKER_SERVICES.split():
            assert service in result.stderr, f"error must name {service}"

        # A failed readiness wait is not a completed restore.
        assert "Media restore complete" not in result.stdout


class TestWorkerStopFailureAC3:
    """AC-3: a failing `docker compose stop` fails right there."""

    def test_stop_failure_aborts_before_restore_and_is_not_swallowed(self, repo_root, stack):
        result = _restore_media(repo_root, stack, DOUBLE_STOP_RC="1")

        assert result.returncode != 0
        assert "stopping worker services failed" in result.stderr
        # The docker error itself must survive — `>/dev/null 2>&1 || true` used
        # to eat both the exit code and the reason.
        assert "Error response from daemon" in result.stderr

        calls = _calls(stack)
        assert any(f"stop {WORKER_SERVICES}" in c for c in calls)
        assert not any(" exec " in c for c in calls), "media restore must not run"
        assert not any(" start " in c for c in calls), "workers must not be restarted"
        assert "Media restore complete" not in result.stdout


class TestWorkerStartFailureAC4:
    """AC-4: a failing `docker compose start` fails at restore-media."""

    def test_media_service_start_failure_aborts_before_exec(self, repo_root, stack):
        result = _restore_media(repo_root, stack, DOUBLE_START_MEDIA_RC="1")

        assert result.returncode != 0
        assert f"starting {MEDIA_SERVICE} failed" in result.stderr
        assert "Error response from daemon" in result.stderr

        calls = _calls(stack)
        assert not any(" exec " in c for c in calls)
        assert "Media restore complete" not in result.stdout

    def test_worker_start_failure_fails_here_not_in_the_drill(self, repo_root, stack):
        result = _restore_media(repo_root, stack, DOUBLE_START_WORKERS_RC="1")

        assert result.returncode != 0
        assert "starting worker services failed" in result.stderr
        assert "Error response from daemon" in result.stderr

        calls = _calls(stack)
        # The restore itself ran; the failure is the restart that followed.
        assert any(" exec " in c for c in calls)
        # Failing at the start means we never reach the readiness poll — the
        # error names the start, not a confusing downstream precondition.
        assert not any("--status running" in c for c in calls)
        assert "Media restore complete" not in result.stdout


class TestWorkerSetResolution:
    """Making the stop/start fatal (AC-3, AC-4) is only safe if the services we
    hand compose actually have containers.

    `docker compose start` fails with `service "x" has no container to start`,
    and the CI "Prod Stack Smoke" job boots only db, redis, emqx and backend —
    never celery, celery_beat, flower or mqtt_consumer. Without this filtering,
    dropping `|| true` would fail that job on every run: the same class of
    false red this change exists to remove.
    """

    def test_only_services_with_containers_are_stopped_and_started(self, repo_root, stack):
        result = _restore_media(
            repo_root,
            stack,
            # Exactly the CI prod-smoke shape: backend is up, the workers were
            # never created.
            DOUBLE_SERVICES_WITHOUT_CONTAINERS="celery celery_beat flower mqtt_consumer",
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert "Media restore complete from" in result.stdout

        # Exactly one stop and two starts (the pre-exec media start and the
        # post-restore worker start), every one of them naming only backend.
        # A container-less service reaching `start` is what compose rejects.
        prefix = f"docker compose -f {stack['compose_file']}"
        calls = _calls(stack)
        assert [c for c in calls if " stop " in c] == [f"{prefix} stop backend"]
        assert [c for c in calls if " start " in c] == [
            f"{prefix} start backend",
            f"{prefix} start backend",
        ]

    def test_no_worker_containers_at_all_fails_with_a_clear_error(self, repo_root, stack):
        result = _restore_media(
            repo_root,
            stack,
            DOUBLE_SERVICES_WITHOUT_CONTAINERS=WORKER_SERVICES,
        )

        assert result.returncode != 0
        assert "no containers found for worker services" in result.stderr
        assert stack["compose_file"] in result.stderr
        # Fail before touching media rather than mid-restore.
        assert not any(" exec " in c for c in _calls(stack))


class TestDrillPreconditionAC5:
    """AC-5: the drill's precondition no longer races the media restore."""

    def _precondition(self, stack: dict, **env) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", "-c", DRILL_PRECONDITION],
            capture_output=True,
            text=True,
            check=False,
            env={**stack["env"], **env},
        )

    def test_test_replicates_the_real_drill_precondition(self, repo_root):
        # If restore-drill.sh's gate changes shape, this test is testing a
        # fiction — fail loudly instead.
        drill = (repo_root / "scripts" / "restore-drill.sh").read_text()
        assert DRILL_PRECONDITION in drill
        assert "no running compose services for" in drill

    def test_precondition_fails_while_workers_are_still_coming_up(self, stack):
        # The race, demonstrated: run the drill's gate against a stack whose
        # workers have been started but are not running yet. This is what
        # restore-media.sh used to hand straight to restore-drill.sh.
        result = self._precondition(stack, DOUBLE_PS_NEVER_RUNNING="1")
        assert result.returncode != 0

    def test_precondition_passes_once_restore_media_returns(self, repo_root, stack):
        # One stack, one coming-up sequence: the workers report running from
        # the third poll onward to *whoever* asks. restore-media.sh must absorb
        # that window so the config and DB drill steps that follow start from a
        # running stack. Without the wait the drill's own gate is the first
        # poll, sees nothing running, and fails an innocent PR.
        stack["env"]["DOUBLE_PS_EMPTY_POLLS"] = "2"

        restore = _restore_media(repo_root, stack)
        assert restore.returncode == 0, restore.stdout + restore.stderr

        precondition = self._precondition(stack)
        assert precondition.returncode == 0, (
            "restore-drill.sh would fail with 'no running compose services' — "
            "restore-media.sh returned before the workers were running"
        )

        # And the run really did pass through a not-yet-running state, so the
        # pass above is the wait working, not the double being lenient.
        calls = _calls(stack)
        assert any("-> poll 1: none running" in c for c in calls)
