# EMQX → Django MQTT WebHook Bridge

The ForgeKey Django backend exposes `POST /api/forgekey/mqtt-webhook/`,
which receives every published MQTT message that EMQX is configured to
forward and dispatches it to the matching Celery processor task. This is
**one of two** production ingest paths for device telemetry; the other
is the long-running `mqtt_consumer` management command (run as the
`mqtt_consumer` service in `docker-compose.prod.yml`, see the role table
in `COMPOSE_RUNBOOK.md`).

Pick exactly one. Running both at the same time will double-process
every device message — `ESP32Device.last_seen` updates remain
idempotent, but `PowerMeterReading` and `OccupancyEvent` rows are
append-only and will duplicate. Choose:

- **Webhook bridge (this doc)** when EMQX is the system of record and
  you want the OMS backend to be a passive HTTP receiver. Requires EMQX
  rule + connector configuration.
- **`mqtt_consumer` service** when you want OMS to maintain its own
  MQTT subscription with the JWT-authenticated server identity (oms-y5p)
  and don't want to provision EMQX rules. Simpler operationally for a
  single-replica deployment.

## Required Django settings

Set these in the deployment environment (e.g. `.env`, Helm values,
docker-compose env):

| Variable | Required | Purpose |
| --- | --- | --- |
| `FORGEKEY_WEBHOOK_SECRET` | yes | Shared secret EMQX must send in `X-ForgeKey-Webhook-Secret`. The view fails closed (401) when this is empty. |
| `FORGEKEY_WEBHOOK_ALLOWED_IPS` | no | Comma-separated IP allowlist. When set, the request's `REMOTE_ADDR` must match one of the entries. Leave empty to skip IP filtering. |

Generate a secret with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.

## EMQX 5.x WebHook bridge configuration

In the EMQX dashboard:

1. **Integration → Connectors → Create → HTTP Server**
   - URL: `https://<your-host>/api/forgekey/mqtt-webhook/`
   - Headers:
     - `Content-Type: application/json`
     - `X-ForgeKey-Webhook-Secret: <FORGEKEY_WEBHOOK_SECRET value>`
   - Connection pool: defaults are fine.
   - **Resilience knobs (oms-4jw)** — set these so the connector recovers
     from a transient backend outage without needing a manual restart:
     - **Connect timeout**: `5s` (short, since the backend is on the same
       docker network).
     - **Request timeout**: `10s`.
     - **Health check interval**: `15s`. EMQX re-resolves the URL and
       re-establishes the resource on each tick, which clears any cached
       nxdomain from a backend bounce.
     - **Auto restart interval**: leave at the EMQX default; the resource
       manager retries with backoff when the health check fails.
     If your EMQX was provisioned with a longer health-check interval and
     the connector is currently stuck on a cached nxdomain, see
     `docs/INCIDENTS/emqx-jwks-nxdomain.md` for force-recovery procedures.

2. **Integration → Rules → Create**
   - SQL:
     ```sql
     SELECT
       topic,
       payload,
       clientid,
       username,
       qos,
       retain,
       timestamp
     FROM
       "forgekey/+/+/occupancy",
       "forgekey/+/status",
       "forgekey/+/power",
       "forgekey/+/+/power",
       "forgekey/+/firmware/response"
     ```
   - Action: forward to the HTTP connector created above.

3. Test the rule from the dashboard's SQL tester with a sample message;
   confirm the backend returns 204 in EMQX's action history.

## Topic → task routing

The view extracts the MAC from the second topic segment (lowercase, no
separators) and routes by suffix:

| Topic shape | Celery task |
| --- | --- |
| `forgekey/<mac>/<sensor>/occupancy` | `process_mqtt_occupancy(mac, sensor_kind, payload)` |
| `forgekey/<mac>/status` | `process_mqtt_status_message(mac, payload)` |
| `forgekey/<mac>/power` *or* `forgekey/<mac>/<sensor>/power` | `process_mqtt_power_reading(mac, payload["asset_id"], payload)` |
| `forgekey/<mac>/firmware/response` | `process_mqtt_firmware_update_response(mac, payload["update_id"], payload["status"], payload["error_message"])` |

Unknown suffixes are logged and dropped with 204 so EMQX does not retry.

## Operational notes

- The view returns 204 immediately after queueing the Celery task so
  EMQX never blocks on processor work.
- The secret check uses `hmac.compare_digest`. The IP allowlist runs
  first, then the secret check, both before any payload parsing — so
  unauthenticated traffic cannot consume CPU on JSON decode or DB I/O.
- Payload may arrive as either a JSON-encoded string (default EMQX
  rule output) or a parsed object; the view accepts both shapes.
- If a request fails authentication, the response is `401` with a
  short JSON detail. EMQX will retry per its action retry policy.
