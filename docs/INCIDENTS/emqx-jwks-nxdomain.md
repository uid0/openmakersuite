# Incident runbook: EMQX cannot resolve `backend` (JWKS / WebHook nxdomain)

> Tracking bead: **oms-4jw** (P2, user-reported 2026-05-03).
> Cross-references: oms-zad (ALLOWED_HOSTS — same family of issues),
> oms-2zo (the WebHook bridge that's failing as the OpenMakerSuite connector).

## Symptom

EMQX logs one of these on startup or after a backend container restart:

```
failed_to_request_jwks_endpoint, reason:
  {failed_connect, [{to_address,{"backend",8000}}, {inet,[inet],nxdomain}]}

CONNECTOR/WEBHOOK, msg: start_resource_failed,
  reason: nxdomain, resource_id: connector:http:OpenMakerSuite
```

Devices that connect after the failure are rejected at MQTT auth, and any
EMQX rule that publishes to the OpenMakerSuite WebHook stops dispatching. A
manual `curl http://backend:8000/api/forgekey/jwks/` from inside the EMQX
container DOES resolve and return JSON — the docker network is fine. The
problem is that EMQX cached the initial nxdomain in the JWKS authenticator's
state and the connector's resource manager.

## Root cause

Container start ordering during `docker compose up`. EMQX's `depends_on`
chain does not include `backend` (that would create a cycle: backend already
depends on EMQX for `service_started`). When EMQX wins the race, its
authenticator initializes against a hostname that hasn't been published to
the docker DNS yet, the lookup returns nxdomain, and EMQX caches the
failure until the next refresh tick.

The same race fires when the `backend` container is recycled (deploy, OOM
kill, gunicorn worker storm) — backend's IP goes away, EMQX's next JWKS
refresh hits nxdomain, and the failure persists for the rest of the
`refresh_interval`.

## Mitigations in place (oms-4jw fix)

These changes are part of the repo; verify they are present on the target
deploy before doing further triage.

1. **JWKS `refresh_interval` lowered to 30 seconds.** The default for
   `python manage.py configure_emqx_jwt_auth` is now `30` (was `300`).
   EMQX retries the JWKS fetch every refresh tick, so a transient
   nxdomain self-heals within ~30s of the backend becoming reachable.
   See `backend/forgekey/management/commands/configure_emqx_jwt_auth.py`.
2. **Operator-facing docs updated.** `docs/EMQX_CONFIGURATION.md` and
   `deploy/EMQX_WEBHOOK.md` call out the retry behavior so a new operator
   provisioning a fresh EMQX picks the short interval and the
   `start_after_created` / `request_timeout` knobs on the WebHook
   connector.

## Force-recovery procedures

Use these when MQTT auth is broken right now and you cannot wait for the
next refresh tick (e.g. an existing EMQX provisioned with the legacy 300s
interval, or the WebHook connector stuck on a cached nxdomain).

### A. Restart EMQX (fastest, always works)

```bash
docker compose -f docker-compose.prod.yml restart emqx
```

EMQX re-fetches JWKS and re-creates the WebHook connector resource on boot.
Devices reconnect within seconds. Safe to run any time — no data loss; MQTT
sessions are re-established by clients.

### B. Update the JWKS authenticator's refresh interval in place

For an EMQX that was provisioned with the legacy 300s interval, the
management command's idempotent path skips re-creating the authenticator,
so re-running it does NOT update `refresh_interval`. Either:

1. Delete the JWT authenticator in the dashboard
   (Authentication → Authentication → trash icon) and re-run
   `python manage.py configure_emqx_jwt_auth --jwks-url=...` from the
   backend container, or
2. Edit the authenticator in the dashboard and set Refresh Interval to
   `30` directly.

### C. Restart the WebHook connector resource

EMQX 5.x dashboard: Integration → Connectors → OpenMakerSuite → restart.
This clears the cached nxdomain on the connector specifically without
bouncing the broker.

## Detect (proactive)

- **Devices fail to connect** but the EMQX dashboard shows zero auth
  failures from a specific listener — they're being rejected at JWKS
  lookup, not at signature verification.
- **`docker compose logs emqx --since 5m | grep -E 'nxdomain|jwks|connector'`**
  shows the failure lines above.
- **Backend's gunicorn access log shows no JWKS hits**
  (`grep '/api/forgekey/jwks/' backend.log`) for >1× the configured
  refresh interval, even though devices are trying to connect.

## Investigate (if this fires post-mitigation)

1. **Confirm refresh_interval on the live authenticator.**
   ```bash
   curl -s -u "$EMQX_API_KEY:$EMQX_API_SECRET" \
     http://localhost:18083/api/v5/authentication \
     | jq '.[] | select(.mechanism=="jwt") | .refresh_interval'
   ```
   Expect `30`. Anything ≥`300` is a stale configuration — see
   "Force-recovery B" above.
2. **Confirm DNS resolves from inside EMQX.**
   `docker exec oms-emqx getent hosts backend`. Empty output means the
   docker network itself is broken (different problem); non-empty means
   EMQX has the cached failure and needs a kick.
3. **Confirm the JWKS endpoint is healthy.**
   `docker exec oms-emqx curl -sf http://backend:8000/api/forgekey/jwks/`.
   If this returns the JWS JSON, the broker just needs to refresh.
4. **Check the WebHook connector resource state** in the dashboard's
   Integration → Connectors view. A connector stuck in `disconnected` after
   the network is healthy is the same nxdomain-cache symptom on a different
   subsystem.

## After-action template

When this incident recurs, append a section like:

```
### YYYY-MM-DD recurrence
- Detected by: ...
- Root cause: ...
- Fix: ...
- Mitigation update: ...
```
