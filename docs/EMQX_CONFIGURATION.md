# EMQX Configuration for ForgeKey

## Why this matters

ForgeKey devices authenticate to EMQX using ES256-signed JWTs issued by OMS at
device registration time. EMQX verifies those JWTs against the public key
served from `/api/forgekey/jwks/`. Until EMQX is configured with that JWKS
URL, **every device connection is rejected** — the EMQX dashboard shows zero
clients no matter how many devices are deployed.

This guide configures EMQX so devices can connect.

## Prerequisites

- EMQX is running and the dashboard is reachable on port `18083`.
- You have logged in to the dashboard once and replaced the default
  `admin/public` credentials.
- Under **System** → **API Keys**, you have created a key/secret pair and
  written the values into OMS's environment as `EMQX_API_KEY` and
  `EMQX_API_SECRET`.
- The OMS instance is reachable from the EMQX broker on the URL you will
  pass as `--jwks-url`. EMQX needs to reach `/api/forgekey/jwks/` over
  HTTP(S) to fetch the verification key.

## One-shot configuration

From the backend container (or any host with `manage.py` access):

```bash
python manage.py configure_emqx_jwt_auth \
    --jwks-url https://oms.example/api/forgekey/jwks/
```

The command:

1. Connects to `EMQX_API_URL` (default `http://emqx:18083/api/v5`) using HTTP
   basic auth derived from `EMQX_API_KEY` / `EMQX_API_SECRET`.
2. Fetches the existing authentication chain. If a JWT authenticator is
   already present, the create step is skipped — re-runs are safe.
3. POSTs a JWT-via-JWKS authenticator with `verify_claims = {iss, aud}`
   matching `FORGEKEY_JWT_ISSUER` / `FORGEKEY_JWT_AUDIENCE` and
   `acl_claim_name = "acl"` so EMQX honors the per-device pub/sub grants
   embedded in each JWT.
4. Disables `mqtt.allow_anonymous` so EMQX cannot fall back to insecure
   anonymous connections. Pass `--keep-anonymous` to skip that step in dev
   rigs.

Inspect what would happen without making changes:

```bash
python manage.py configure_emqx_jwt_auth \
    --jwks-url https://oms.example/api/forgekey/jwks/ --dry-run
```

## Manual configuration via the dashboard

If you prefer the dashboard, you can do the same by hand:

1. **Authentication → Authentication → Create**.
2. Pick **JWT**.
3. **Use JWKS**: yes.
4. **JWKS URL**: `https://oms.example/api/forgekey/jwks/`.
5. **Refresh interval**: `30` (seconds). Doubles as the post-failure retry
   cadence — see "Recovery from JWKS fetch failure" below for why we keep
   this short.
6. **JWT From**: `password`.
7. **Verify Claims**: add `iss = openmakersuite` and `aud = forgekey`.
8. **ACL Claim Name**: `acl`.
9. Save and **Enable** the authenticator.
10. **Settings → MQTT → General**: set **Allow Anonymous** to `false`.

## Verifying

After configuration:

1. Re-flash a device, or wait for a registered device to re-issue its JWT.
2. In the EMQX dashboard, **Monitoring → Clients** should list the device's
   MAC address. **Monitoring → Subscriptions** should show its
   `forgekey/<mac>/{command,firmware,config,ota/trigger}` subscriptions.
3. The OMS backend should start receiving occupancy webhooks from the EMQX
   webhook bridge (see `oms-2zo`).

If the device fails to connect:

- Check the device serial log for `connect failed rc=4` (auth failure).
- Visit `<jwks-url>` from a browser — it must return JSON (`{"keys":[...]}`).
- In the EMQX dashboard, **Diagnose → Log** filters by client; the JWT
  validation failure reason is logged there.

## Recovery from JWKS fetch failure (oms-4jw)

The JWKS authenticator's `refresh_interval` (30 seconds by default) doubles as
a retry cadence. If EMQX boots before the backend's container is reachable on
the docker network — common with `docker compose up` from cold — the initial
JWKS fetch hits `nxdomain` and EMQX caches that failure. Subsequent device
auth attempts fail with "JWKS endpoint unreachable" until the next refresh
tick fetches successfully.

With the 30s default, recovery is automatic within ~30 seconds of the backend
becoming reachable. If you raised the interval (or you are running an EMQX
that was provisioned with the legacy 300s value), the recovery window grows
to match. Two ways to force-recover:

1. **Restart EMQX**: `docker compose restart emqx`. EMQX re-fetches JWKS on
   startup. Use this when devices need to connect right now and you don't
   want to wait for the next refresh tick.
2. **Re-run `configure_emqx_jwt_auth` after deleting the existing
   authenticator** (Authentication → Authentication → trash icon → re-run the
   management command). Required if you want to update an existing
   authenticator's `refresh_interval`; the command's idempotent path
   intentionally skips creating a duplicate but does **not** rewrite an
   existing authenticator's settings.

See `docs/INCIDENTS/emqx-jwks-nxdomain.md` for the full runbook.

## Backend authentication (oms-y5p)

The OMS backend itself also speaks MQTT — it publishes commands (blink,
restart, capture, OTA triggers) to devices and consumes status messages. With
`mqtt.allow_anonymous = false` the backend needs a credential too, and EMQX's
JWT authenticator does not accept literal username/password pairs.

The backend solves this by **issuing its own server JWT**, signed with the
same `FORGEKEY_JWT_SIGNING_KEY` that signs device JWTs. EMQX verifies it via
the same JWKS endpoint with no extra configuration:

- **Username**: `oms-backend`
- **Password**: a self-signed ES256 JWT with `sub=oms-backend`, the standard
  `iss` / `aud` claims, and an `acl` claim granting `pub`/`sub` on the entire
  `MQTT_TOPIC_PREFIX/#` namespace so the backend can talk to every device.
- **Caching**: the JWT is cached in-process for
  `FORGEKEY_SERVER_JWT_CACHE_SECONDS` (default 24 hours) so we don't re-sign
  on every MQTT connect. The token's own `exp` is
  `FORGEKEY_SERVER_JWT_EXPIRATION_SECONDS` (default 1 year) and the cache is
  refreshed shortly before expiry. On any MQTT connect failure the cache is
  invalidated so the next reconnect mints a fresh credential.

There is **no separate user-management** for the backend — no Built-in DB
entry, no extra password env var to rotate. As long as
`FORGEKEY_JWT_SIGNING_KEY` is set (which it must be for device auth to work
at all), the backend can authenticate.

If `MQTT_BROKER_USERNAME` / `MQTT_BROKER_PASSWORD` are set in the environment,
the backend's `forgekey.W008` system check warns at startup — those literal
credentials are ignored by the JWT authenticator and silently break every
command publish. Unset them.

## Key rotation

To rotate the device-JWT signing key:

1. Generate a new keypair (Python REPL or a one-off script):
   ```python
   from forgekey.services.jwt_signing import generate_jwt_signing_keypair
   priv, pub = generate_jwt_signing_keypair()
   ```
2. Set `FORGEKEY_JWT_SIGNING_KEY` to the new private PEM and bump
   `FORGEKEY_JWT_KEY_ID`.
3. Restart OMS. The JWKS endpoint immediately advertises the new key under
   the new `kid`.
4. EMQX picks up the new JWKS on its next refresh (default 5 minutes); no
   restart required.
5. Existing devices re-register at JWT expiry (default 30 days) and receive
   tokens signed with the new key.
