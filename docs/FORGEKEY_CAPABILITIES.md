# ForgeKey Device Capabilities

ForgeKey devices announce the set of features they expose ("capabilities") so
the backend and UI can adapt without an explicit per-device-model lookup.
This document is the authoritative contract between firmware and OMS.

Companion bead: oms-x3a (OMS side); paired firmware bead lands the publisher.

## MQTT topic

```
<MQTT_TOPIC_PREFIX>/<mac-no-colons-lowercase>/capabilities
```

- **Retained**: yes. EMQX must hand the most-recent message to any new
  subscriber so the backend recovers the device's capability set after a
  Celery worker restart without forcing the firmware to republish.
- **QoS**: 1. At-least-once delivery; the OMS processor is idempotent (it
  overwrites the stored list on every message).
- **Direction**: device → broker → OMS webhook (`/api/forgekey/mqtt-webhook/`)
  via the EMQX HTTP bridge.

## Payload schema

```json
{
  "capabilities": ["people_counter", "status_led"],
  "firmware_version": "1.2.0"
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `capabilities` | `list[str]` | yes | Capability identifiers from the table below. Unknown identifiers are accepted and surfaced in the UI as a generic "Detected" badge. |
| `firmware_version` | `str` | no | If present, OMS opportunistically updates `ESP32Device.firmware_version`. The boot announcement doubles as a status beacon so dashboards reflect the running firmware without waiting for the next periodic status message. |

OMS normalizes the list before persistence: non-strings are dropped, leading
and trailing whitespace is stripped, empty strings are dropped, and
duplicates are removed while preserving first-seen order.

## OMS-side persistence

Two fields on `forgekey.ESP32Device` (migration `0008_add_capabilities`):

- `capabilities` — `JSONField(default=list)`. Replaced wholesale on every
  announcement; firmware is the source of truth.
- `capabilities_announced_at` — `DateTimeField(null=True)`. Stamped with
  `timezone.now()` on every ingest so admins can spot stale rows.

Each announcement also touches `last_seen` and sets `is_online=True`.

## Filtering devices by capability

`GET /api/forgekey/devices/?capability=<id>` returns only devices whose stored
list contains `<id>`. Backed by Postgres `JSONField.contains` — the existing
deployment stack assumption.

The admin device-list page exposes the same filter as a free-text input.

## Known capability identifiers

Identifiers are short snake_case strings. Adding a new identifier requires
nothing on the OMS side beyond optional UI work — unknown identifiers render
as a generic "Detected: &lt;name&gt;" badge until per-capability UI lands.

| Identifier | Meaning | OMS UI status |
|------------|---------|---------------|
| `people_counter` | Bidirectional door/passage counter publishing per-event in/out deltas to `<prefix>/<mac>/people_counter/occupancy`. | Live occupancy chart on device-detail (oms-yyg). |
| `mmwave_presence` | Coarse "is someone in the room" signal from a 24/60 GHz radar module. | Placeholder presence indicator on device-detail; data-plane lands in a follow-up. |
| `button` | Physical button(s) the firmware reports presses for. | Placeholder event log on device-detail; data-plane lands in a follow-up. |
| `status_led` | Onboard indicator the firmware drives. | State indicator + "Blink LED" override on device-detail (reuses the existing `command/blink/` endpoint). |

When extending this list, **register the identifier here first** so the
firmware and OMS sides agree before the topic carries the new value.

## Adding a new capability (OMS-side checklist)

1. Document the identifier in the table above with a one-line meaning.
2. (Optional) Add a per-capability widget to
   `frontend/src/pages/ForgeKeyDeviceDetailPage.tsx` (extend
   `KNOWN_CAPABILITIES` and `CapabilityRow`).
3. (Optional) If the capability has its own data plane (events, readings),
   add a sibling MQTT topic + processor in
   `backend/forgekey/views.py::MqttWebhookView._dispatch` and
   `backend/forgekey/tasks.py`.

## Failure modes

| Situation | Behavior |
|-----------|----------|
| Webhook payload not a JSON object | HTTP 400 (rejected before dispatch). |
| Webhook auth header missing/wrong | HTTP 401. |
| Topic MAC does not match a known device | Task logs `Dropping capabilities event: unknown MAC ...` and returns; no retry. |
| `capabilities` key missing or non-list | Treated as empty list — the stored list is cleared and `capabilities_announced_at` is still updated. |
| Item in `capabilities` not a string | Dropped silently during normalization. |
