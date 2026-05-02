# Incident runbook: backend gunicorn WORKER TIMEOUT at startup

> Tracking bead: **oms-801** (P0, GH #309, user-reported 2026-05-02).
> History: this doc was created alongside the first mitigation pass; update it
> after each repeat occurrence with what was actually wrong this time.

## Symptom

The Django `backend` container starts, every gunicorn worker logs the
`apps.ready()` line ("ForgeKey provisioning token configured ..."), then
**all** workers go silent and are killed by the arbiter at exactly the
configured `--timeout` (120s) without ever serving a request.

Representative log slice from the original incident:

```
backend-1 | [21:22:59] [INFO] Control socket listening at /home/appuser/.gunicorn/gunicorn.ctl
backend-1 | INFO 21:22:59 apps 416 ForgeKey provisioning token configured (length=36, fingerprint=a0467e88)
backend-1 | INFO 21:22:59 apps 418 ForgeKey provisioning token configured (length=36, fingerprint=a0467e88)
backend-1 | INFO 21:22:59 apps 420 ForgeKey provisioning token configured (length=36, fingerprint=a0467e88)
backend-1 | INFO 21:22:59 apps 422 ForgeKey provisioning token configured (length=36, fingerprint=a0467e88)
backend-1 | [21:25:01] [CRITICAL] WORKER TIMEOUT (pid:416)
backend-1 | [21:25:01] [CRITICAL] WORKER TIMEOUT (pid:418)
backend-1 | [21:25:01] [CRITICAL] WORKER TIMEOUT (pid:420)
backend-1 | [21:25:01] [CRITICAL] WORKER TIMEOUT (pid:422)
backend-1 | [21:25:01] [416] [ERROR] Error handling request (no URI read)
```

The "Error handling request (no URI read)" line means a connection was
accepted but no HTTP request line was read before the worker was killed —
typically the docker healthcheck connecting just as the arbiter sent SIGABRT.

All four workers stopping their heartbeat at the same instant means a single
shared blocking call after `apps.ready()` returns — not a per-request stall.

## Mitigations already in place (oms-801 fix)

These changes are now part of the repo; verify they are present on the
target deploy before doing further triage.

1. **Liveness-only container healthcheck.** Both `Dockerfile.prod` and
   `docker-compose.prod.yml` now point the `HEALTHCHECK` at
   `/api/health/livez/` (dep-free; returns 200 if the gunicorn worker is
   alive). The previous endpoint, `/api/dashboard/health/`, executes a DB
   query, so a transient DB hiccup turned the healthcheck red and triggered
   container restarts even when the process was fine. Readiness with full
   dep checks is still exposed at `/api/health/readyz/`.
2. **Backend no longer waits for EMQX healthy.** `docker-compose.prod.yml`
   `backend.depends_on.emqx` is `service_started` (was `service_healthy`).
   MQTT publish from Django is lazy
   (`forgekey.tasks.get_mqtt_client` only opens the broker socket on first
   use), so blocking backend boot on EMQX reaching healthy added 60s+ to
   the dependency chain.
3. **`FORGEKEY_JWT_SIGNING_KEY` is validated at deploy time.** Both
   `scripts/validate-prod-env.sh` and `.env.prod.example` were updated so a
   missing or malformed PEM is surfaced before deploy, instead of as a
   runtime 503 on the EMQX JWKS endpoint.
4. **CI smoke gate.** A new `prod-stack-smoke` job in `.github/workflows/ci.yml`
   boots `db + redis + emqx + backend` from `docker-compose.prod.yml` with a
   minimal env and asserts `/api/health/livez/` returns 200 within 60s. A
   regression that pushes startup past the gunicorn worker timeout fails CI.

## Detect

- **`docker compose ps` shows backend `Restarting`** with the healthcheck
  unhealthy and the same workers being recycled every ~2 minutes.
- **`docker compose logs backend --since 5m`** shows `WORKER TIMEOUT` lines
  in clusters of four (one per worker), all on the same second.
- **No request logs.** A worker that is serving traffic prints either access
  logs (if `--access-logfile -` is set) or at minimum DRF exception logs;
  silent workers + timeouts means the workers never entered their accept
  loop.

## Investigate (if this fires again post-mitigation)

The mitigations above remove the most likely contributors but do not prove
which one was the trigger. If the symptom returns:

1. **Confirm the env.** `docker exec backend env | grep -E 'FORGEKEY|SENTRY'`.
   Empty or malformed values for the required keys produce surprising
   downstream behavior; the shipped validator now catches this at deploy.
2. **Confirm the migrations finished.**
   `docker exec backend python manage.py showmigrations | grep -v '\[X\]'`.
   A migration stuck on a row-by-row data backfill (e.g. the
   `electrical_circuits.0003` legacy-outlet migration) would block the
   entrypoint script before gunicorn starts; that is a *different* failure
   mode than this runbook (no worker logs at all).
3. **Strace one worker.** `docker exec backend sh -c 'apt-get install -y strace
   && strace -p <worker-pid> -tt -T -e trace=network,desc'`. If every
   worker is blocked in the same syscall (e.g. `connect`, `read`,
   `pthread_cond_wait`), grab the call stack with `py-spy dump --pid
   <worker-pid>` from the host.
4. **Check Sentry init.** Set `SENTRY_DSN=` (empty) in `.env` and bounce the
   backend. If workers come up green, the previous DSN is unreachable or
   the network path to it is blocked; switch DSNs or set the env empty.
5. **Bisect the deploy.** The original outage shipped after a five-PR batch
   (#303 OTA, #304 webhook, #306 JWT, #307 OOM hardening, #308 power
   topology). Roll back to the last-known-good tag and re-land one PR at a
   time, gating each on the `prod-stack-smoke` job.

## After-action template

When this incident recurs, append a section like:

```
### YYYY-MM-DD recurrence
- Detected by: ...
- Root cause: ...
- Fix: ...
- Mitigation update: ...
```
