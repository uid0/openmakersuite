# ForgeKey device integration

ForgeKey is the OpenMakerSuite subsystem that registers, monitors, and
delivers OTA firmware to ESP32 devices. This document covers the device
provisioning protocol, the MQTT topic schema, JWT auth, and the
periodic-photo retention policy. It pairs with the firmware-side bead
**fo-0z9**.

> The device + firmware models live in the existing `backend/forgekey/`
> Django app — `ESP32Device`, `ESP32DevicePhoto`, `FirmwareVersion`,
> `DeviceFirmwareUpdate`. There is no separate `forgekey_devices/` app;
> the photo + sensor-kind + MQTT-dispatch capabilities introduced for
> oms-dlg extend the existing schema.

## Device sensor kinds

Sensor devices register under one of three new `DeviceType.code` values
alongside the existing badge-reader / relay / power-meter codes:

| Code               | Purpose                                     |
| ------------------ | ------------------------------------------- |
| `people_counter`   | Foot-traffic counter (location-checkins)    |
| `env_sensor`       | Environmental telemetry (temp/humidity/CO₂) |
| `door_counter`     | Door-cycle counter                          |

A `DeviceType` row with the matching `code` must exist before a device of
that kind can register.

## Provisioning protocol

```
ESP32 (boot)              OMS backend
    |                          |
    | POST /api/forgekey/      |
    |   devices/register/      |
    |   X-ForgeKey-            |
    |     Provisioning-Token   |
    |   multipart: photo +     |
    |     metadata JSON        |
    | -----------------------> |
    |                          |  upsert ESP32Device by mac
    |                          |  store enrollment_photo
    |                          |  generate_device_jwt(mac)
    |   { device_id, jwt,      |
    |     mqtt_topic_for_*,    |
    |     assigned_location }  |
    | <----------------------- |
    |                          |
    | (subscribe to            |
    |  mqtt_topic_for_firmware)|
```

### Request

`POST /api/forgekey/devices/register/`

Headers:

* `X-ForgeKey-Provisioning-Token` — must equal `FORGEKEY_PROVISIONING_TOKEN`.
  Empty server config returns 401 unconditionally.

Body (`multipart/form-data`):

* `photo` — JPEG file (camera capture used by staff to identify the
  physical device). Stored as `ESP32Device.enrollment_photo`.
* `metadata` — JSON string with at least `mac_address`. Recognized keys:
  * `mac_address` (required, any of `AA:BB:..`, `aa-bb-..`, `aabb..`)
  * `device_type` (required on first registration; one of the
    `DeviceType.code` values above or any existing code)
  * `firmware_version`
  * `boot_count`, `free_heap`, `ip` — last-known device metadata
  * `sensor_kind` — accepted as an alias for `device_type`

Top-level multipart fields are also accepted in lieu of a JSON `metadata`
blob; useful when the firmware HTTP client doesn't compose JSON.

### Response

```json
{
  "device_id": "f3c1...",
  "assigned_location_id": null,
  "mqtt_topic_for_firmware": "forgekey/AA-BB-CC-DD-EE-FF/firmware",
  "mqtt_topic_for_pings":    "forgekey/AA-BB-CC-DD-EE-FF/ping",
  "jwt_token": "eyJ...",
  "created": true
}
```

* `201 Created` on first registration; `200 OK` for subsequent calls
  (idempotent on `mac_address`).
* `401 Unauthorized` for missing/wrong provisioning token.
* `400 Bad Request` for malformed JSON, missing `mac_address`, or unknown
  `device_type` code.

### JWT auth

The JWT returned at registration is signed with
`sha256(mac + ":" + FORGEKEY_SHARED_SECRET)` and expires after
`FORGEKEY_JWT_EXPIRATION_SECONDS` (default 1 h). Devices use it in the
`Authorization: Bearer …` header to call the photo-upload endpoint.

If the JWT has expired the device may fall back to the provisioning
token — re-registering rotates the JWT.

## Periodic photo upload

`POST /api/forgekey/devices/<mac>/photo/`

Headers:

* `Authorization: Bearer <jwt>` (preferred), or
* `X-ForgeKey-Provisioning-Token` (fallback).

Body (`multipart/form-data`):

* `photo` — JPEG file. The first three bytes are checked against the JPEG
  magic header (`FF D8 FF`); other formats are rejected with 400.
* `captured_at` (optional) — ISO-8601 timestamp the device captured the
  frame.

Each successful upload:

1. Creates an `ESP32DevicePhoto` row.
2. Updates `ESP32Device.last_photo`, `last_seen`, and `is_online=True`.
3. Returns `{ photo_id, received_at }` with `201 Created`.

### Retention

Photos older than `FORGEKEY_PHOTO_RETENTION_DAYS` (default **30 days**)
are deleted by the `forgekey.tasks.prune_device_photos` celery task.
Schedule it via `CELERY_BEAT_SCHEDULE`, e.g. once per day.

## MQTT topic schema

All ForgeKey topics live under the `MQTT_TOPIC_PREFIX` (default
`forgekey`). The MAC address is normalized to uppercase and colons
replaced with dashes so brokers don't choke on `:`:

| Topic                                    | Direction      | Purpose                                       |
| ---------------------------------------- | -------------- | --------------------------------------------- |
| `forgekey/<mac>/firmware`                | server → device | Advertise a new firmware release (retained)   |
| `forgekey/<mac>/ping`                    | device → server | Heartbeat / liveness                          |
| `forgekey/<mac>/command`                 | server → device | Existing enable/disable/status commands       |
| `forgekey/<mac>/status`                  | device → server | Existing status responses                     |
| `forgekey/<mac>/data`                    | device → server | Existing telemetry / power readings           |

`<mac>` is `AA-BB-CC-DD-EE-FF` (uppercase, dash-separated).

### Firmware advertisement payload

```json
{
  "url":     "https://releases.openmakersuite.net/forgekey/2.0.0.bin",
  "sha256":  "8c6976e5b5410415bde908bd4dee15dfb16…",
  "version": "2.0.0",
  "mandatory": true
}
```

* `url` — `FirmwareVersion.binary_url` if set; else the `firmware_file`'s
  storage URL.
* `sha256` — auto-computed on `FirmwareVersion.save()` over the uploaded
  binary.
* `mandatory` — devices must apply before resuming normal operation.

The publish is retained, so a device that was offline at dispatch time
will receive the latest advertisement when it reconnects.

### Server side

`backend/forgekey/services/firmware_dispatch.py` exposes:

* `publish_firmware_update(devices, firmware, *, requested_by=None)` —
  publishes the advertisement to each device's topic and creates a
  pending `DeviceFirmwareUpdate` row per device.
* `dispatch_to_device_type(firmware, *, device_type_code=None,
  requested_by=None)` — bulk variant that selects every active device
  matching `device_type.code` (defaults to the firmware's own
  `device_type`).

The Django admin's `FirmwareVersion` page includes a **Dispatch firmware
update via MQTT** action that calls `dispatch_to_device_type` for the
selected releases.

## Required environment variables

Set these in `.env` (both development and production — `docker-compose.prod.yml`
loads the production stack from `.env`, not `.env.prod`. The `.env.prod.example`
template is copied to `.env` during initial setup, despite the name).

| Variable                          | Description                                                                                    |
| --------------------------------- | ---------------------------------------------------------------------------------------------- |
| `FORGEKEY_PROVISIONING_TOKEN`     | Shared secret devices send in `X-ForgeKey-Provisioning-Token`. Empty disables registration.    |
| `FORGEKEY_SHARED_SECRET`          | Base secret used to sign per-device JWTs.                                                      |
| `FORGEKEY_PHOTO_RETENTION_DAYS`   | Days to keep `ESP32DevicePhoto` rows before pruning. Default 30.                               |
| `MQTT_BROKER_HOST`                | Hostname of the MQTT broker. In production this is `emqx` (the in-stack EMQX service).         |
| `MQTT_BROKER_PORT`                | Port (default 1883).                                                                           |
| `MQTT_BROKER_USERNAME`            | Optional broker auth.                                                                          |
| `MQTT_BROKER_PASSWORD`            | Optional broker auth.                                                                          |
| `MQTT_TOPIC_PREFIX`               | Prefix for all ForgeKey topics. Default `forgekey`.                                            |
| `MQTT_CLIENT_ID`                  | MQTT client id used by the backend publisher.                                                  |
| `MQTT_KEEPALIVE`                  | MQTT keepalive in seconds (default 60).                                                        |
| `EMQX_DASHBOARD_PASSWORD`         | Initial password for the `admin` dashboard user (production only — see EMQX deployment below). |
| `EMQX_API_URL`                    | EMQX REST API base URL. Set to `http://emqx:18083/api/v5` in the docker-compose stack.         |
| `EMQX_API_KEY`                    | API key for backend → EMQX REST calls. Generate via dashboard `System > API Keys`.             |
| `EMQX_API_SECRET`                 | API secret matching `EMQX_API_KEY`.                                                            |

See `.env.example` and `backend/env.production.example` for the templated
versions.

## EMQX deployment

The production stack ships the broker as an EMQX Enterprise service in
`docker-compose.prod.yml`:

```
services:
  emqx:
    image: emqx/emqx-enterprise:6.2.0@sha256:<digest>
    ports:
      - 1883:1883     # MQTT
      - 8083:8083     # MQTT-over-WebSocket
      - 8084:8084     # MQTT-over-WSS
      - 8883:8883     # MQTTS
      - 18083:18083   # Dashboard + REST API
    volumes:
      - emqx_data:/opt/emqx/data
      - emqx_log:/opt/emqx/log
```

The `backend` service depends on `emqx` with `condition: service_healthy`,
publishes via the MAC-aware topics described above, and uses the EMQX
REST API at `http://emqx:18083/api/v5` for management calls. Devices on
the same LAN as the host reach the broker on port `1883` (or `8883` for
TLS); the dashboard is reachable on port `18083`.

### First-deploy checklist

1. Set `EMQX_DASHBOARD_PASSWORD` in `.env` to a strong value before
   `docker compose up -d`. EMQX bakes this into the dashboard's default
   `admin` user on first boot.
2. Open `https://<host>:18083` and log in as `admin`. Rotate the password
   immediately if you reused a placeholder.
3. Generate an API key under **System → API Keys**. Copy the key and
   secret into `EMQX_API_KEY` / `EMQX_API_SECRET` in `.env` and
   restart the `backend` service so it picks up the new values.
4. (Optional) Create a dedicated MQTT user under **Access Control →
   Authentication** and set `MQTT_BROKER_USERNAME` /
   `MQTT_BROKER_PASSWORD` for the backend publisher. Devices use JWT
   auth — see `EMQX_JWT_SETUP.md` for that flow.

### Topics

The broker handles the ForgeKey topics described under [MQTT topic
schema](#mqtt-topic-schema): `forgekey/<mac>/firmware`,
`forgekey/<mac>/ping`, `forgekey/<mac>/command`, `forgekey/<mac>/status`,
and `forgekey/<mac>/data`. Firmware advertisements are retained, so a
device that reconnects after downtime still receives the latest pointer.

### Backups

The `emqx_data` volume holds retained messages, ACL state, and any
configuration set via the dashboard or REST API. Snapshot it alongside
`postgres_data` in your backup routine. The `emqx_log` volume contains
broker logs; rotate it with the rest of the stack's `*_log` volumes.

### Firewall / network

In a single-host deployment, only the dashboard (`18083`) and MQTT
listeners (`1883`, `8083`, `8084`, `8883`) need to be reachable from the
device LAN. If devices live on the same private network as the docker
host, drop the public port mappings for `1883`/`8083`/`8084`/`8883` and
keep traffic inside the `app-network` bridge.

## Out of scope

* Real-time MQTT subscriber for sensor telemetry (handled by the
  location-checkins webhook + planned MQTT bridge).
* Computer-vision processing of uploaded photos.
* A/B firmware rollouts and rollback dispatch.
* Captive-portal Wi-Fi setup (firmware-side concern).
