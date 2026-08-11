# Drill Readiness

## Context
The production compose restore drill can fail in `restore-drill.sh` immediately after `restore-media.sh` restarts worker services because the restart command can return before any service is reported running. The restore path needs to wait for worker readiness explicitly so innocent PRs are not reddened by the approximately 0.4 second race window that main has been winning by luck.

## Scope
- In: `scripts/restore-media.sh` restart handling, a bounded readiness poll after worker services are started, clear failure output when readiness never arrives, and script-level evidence that the poll blocks through a not-yet-running state before succeeding.
- Out: `.github/workflows/ci.yml`, `docker-compose.prod.yml`, backend code, frontend code, migrations, repository tests outside `scripts/`, changing restore-drill precondition semantics, changing smoke-test health endpoints, and folding this work into `op-0v4` or branch `op-0v4-wo-tools`.

## Criteria

### AC-1: Restore waits for restarted workers to be running
- **Given** `scripts/restore-media.sh` is run with `RESTORE_MEDIA_CONFIRM=YES` and a docker compose test double that makes `docker compose -f "$COMPOSE_FILE" ps --status running --quiet` return no running containers for at least one poll after the worker start command
- **When** a later readiness poll reports at least one worker service running before the configured deadline
- **Then** `restore-media.sh` exits 0 only after that successful running-state observation, prints the normal media restore completion line, and the captured evidence shows the poll observed a not-yet-running state before it succeeded

### AC-2: Restore readiness failure is bounded and explicit
- **Given** `scripts/restore-media.sh` is run with `RESTORE_MEDIA_CONFIRM=YES` and a docker compose test double that never reports any restarted worker service as running
- **When** the readiness deadline expires
- **Then** `restore-media.sh` exits non-zero within the bounded deadline and writes an error that names the compose file, the worker services being waited on, and the fact that no restarted worker service became running

### AC-3: Worker stop failures fail at restore-media
- **Given** `scripts/restore-media.sh` is run with `RESTORE_MEDIA_CONFIRM=YES` and a docker compose test double where `docker compose -f "$COMPOSE_FILE" stop $WORKER_SERVICES` exits non-zero
- **When** the script reaches the worker stop step
- **Then** `restore-media.sh` exits non-zero at that step, does not continue to media restore or worker restart, and does not hide the docker compose failure with `|| true`

### AC-4: Worker start failures fail at restore-media
- **Given** `scripts/restore-media.sh` is run with `RESTORE_MEDIA_CONFIRM=YES` and a docker compose test double where either `docker compose -f "$COMPOSE_FILE" start "$MEDIA_SERVICE"` or `docker compose -f "$COMPOSE_FILE" start $WORKER_SERVICES` exits non-zero
- **When** the script reaches the failing start step
- **Then** `restore-media.sh` exits non-zero from that failing command, does not print the media restore completion line after a failed final worker start, and does not defer the failure to a later `restore-drill.sh` precondition check

### AC-5: DB drill precondition no longer sees the media-restore race
- **Given** the production compose stack is already up and the CI drill sequence runs `scripts/restore-media.sh`, then `scripts/restore-config.sh`, then `scripts/restore-drill.sh --skip-media --skip-smoke`
- **When** `restore-media.sh` restarts worker services during the media restore step
- **Then** control returns to the following config and DB drill steps only after the restarted worker services have reported running or after `restore-media.sh` has failed with its own readiness error, so the DB drill does not fail with `no running compose services for $COMPOSE_FILE - bring the stack up first` due to the post-media-restore race

### AC-6: Backend test command is documented and green
- **Given** the implementer has completed the script change
- **When** they run `cd backend && pytest`
- **Then** the command exits 0

### AC-7: Frontend test command is documented and green
- **Given** the implementer has completed the script change
- **When** they run `cd frontend && npm test`
- **Then** the command exits 0

### AC-8: Pre-commit command is documented and green
- **Given** the implementer has completed the script change
- **When** they run `pre-commit run --all-files`
- **Then** the command exits 0

### AC-9: Migration check command is documented and green
- **Given** the implementer has completed the script change
- **When** they run `cd backend && python manage.py makemigrations --check`
- **Then** the command exits 0 and reports no model changes requiring migrations

### AC-10: Permission matrix command is documented and green
- **Given** the implementer has completed the script change
- **When** they run `cd backend && python manage.py check_permission_matrix`
- **Then** the command exits 0
