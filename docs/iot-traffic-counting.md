# IoT Traffic Counting

OpenMakerSuite supports anonymous traffic-count ingestion from IoT devices
(ForgeKey, ESP32, motion sensors, RFID readers, etc.) via a single webhook.
Each ping is recorded as a `LocationCheckIn` with `source="device"`. SIG
leaders can then view hourly/daily/weekly/monthly traffic per location and
identify high-traffic rooms for cleaning, maintenance, or equipment buying.

## Configure the shared secret

The webhook is gated by a shared secret (`LOCATION_PING_TOKEN`). Set it in
your environment before exposing the endpoint:

```
LOCATION_PING_TOKEN=<long-random-string>
```

If the value is empty the webhook returns `503 Service Unavailable`.

## Endpoint

```
POST https://dms.openmakersuite.net/api/location-checkins/webhook/
```

The token may be supplied either way:

- Header: `X-Location-Ping-Token: <token>`
- Query string: `?token=<token>`

### Request body

```json
{
  "location_id": 7,
  "device_id": "esp32-machineroom",
  "event_id": "evt-2026-04-25T20:00:00Z-7",
  "occurred_at": "2026-04-25T20:00:00Z"
}
```

| Field         | Required | Notes                                                                 |
| ------------- | -------- | --------------------------------------------------------------------- |
| `location_id` | yes\*    | Integer Location PK. May also send `access_code` instead.             |
| `device_id`   | no       | Identifier of the device, useful for debugging and per-device stats.  |
| `event_id`    | no       | Idempotency key. Duplicate `event_id` within 60s is silently deduped. |
| `occurred_at` | no       | ISO 8601 timestamp. Defaults to server time on receipt.               |

\* one of `location_id` or `access_code` is required.

### Responses

| Status | Meaning                                                                  |
| ------ | ------------------------------------------------------------------------ |
| 204    | Recorded (or silently deduped via `event_id` within 60 seconds).         |
| 400    | Body missing `location_id`/`qr_code`/`access_code` or location not found.|
| 401    | Token missing or did not match `LOCATION_PING_TOKEN`.                    |
| 503    | `LOCATION_PING_TOKEN` is not configured on the server.                   |

## Example: ESP32 / curl

```bash
curl -X POST \
  "https://dms.openmakersuite.net/api/location-checkins/webhook/?token=$LOCATION_PING_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"location_id": 7, "device_id": "esp32-machineroom"}'
```

For ESP32 firmware authors, plain HTTP POST is the simplest path — most
ESP32 dev kits ship with `WiFiClientSecure` + `HTTPClient` out of the box.

## Reporting endpoints

Once devices are sending pings, two report endpoints surface the data
(authenticated):

- `GET /api/location-checkins/reports/traffic/?location=<id>&start=<date>&end=<date>&bucket=hour|day|week|month`
  Returns time-bucketed counts: `[{"bucket_start": "...", "count": N}, ...]`
- `GET /api/location-checkins/reports/top/?start=<date>&end=<date>&limit=10`
  Returns the top locations ordered by check-in count for the window.

Both endpoints are wired into the Logistics dashboard ("Top locations by
traffic this week") and the per-location detail page ("Traffic" panel).

## MQTT bridge (deferred)

For devices that prefer MQTT over HTTP, a future worker will subscribe to
the topic schema `oms/locations/<location_id>/entry` and POST to the
webhook above. The HTTP endpoint is the source of truth — MQTT will be a
thin bridge so the rest of the system only has to know about one ingest
path.
