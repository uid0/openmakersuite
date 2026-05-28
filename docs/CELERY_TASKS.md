# Celery task inventory (AC-28, AC-31)

This document inventories every Celery task registered by OpenMakerSuite,
together with its trigger, queue/worker expectation, side effects, retry
policy, timeout, idempotency posture, owning workflow, and recovery path.
Update it in the same PR whenever a task is added, removed, or its policy
changes — this register is the source of truth for the task-worker
proficiency baseline.

For deployment of the worker and beat processes, see
[`deploy/COMPOSE_RUNBOOK.md`](../deploy/COMPOSE_RUNBOOK.md) §10 and
[`deploy/SMOKE_TESTS.md`](../deploy/SMOKE_TESTS.md) §10.

For the failure-recovery surface, see [§ Failed task recovery](#failed-task-recovery-ac-31).

## Queue/worker model

OMS runs a single Celery worker against the Redis broker
(`docker-compose.prod.yml` `celery` service, command
`celery -A config worker -l info`) plus a single beat scheduler
(`celery_beat` service, command
`celery -A config beat -l info --schedule=/app/.beat-state/celerybeat-schedule`).
The beat schedule file is shelved on a named volume (`celery_beat_state`) so
`last_run_at` survives container restarts — without it the 90-day donor
task would never fire on a stack that gets re-deployed weekly. Run exactly
one beat replica per environment; two schedulers against the same broker
double-fire every periodic task.

All tasks share the **default** queue — no per-task queue routing is
configured. Tasks compete for the same worker pool. If a high-volume
webhook task starves a maintenance task, that's a signal to introduce
queues; until then keep all tasks on `celery` (default) to keep operator
surface area minimal.

The hard global timeout is `CELERY_TASK_TIME_LIMIT = 30 * 60` (30 min). No
soft time limit is configured. Per-task `time_limit` overrides are not in
use today.

Task results land in `django-db` (`django_celery_results.TaskResult`) and
the Django admin at `/admin/django_celery_results/taskresult/` is the
operator surface for inspecting status, args, and tracebacks
(`backend/config/celery_admin.py`).

## Tasks by app

> **Idempotency column legend**
>
> - **Yes** — safe to retry; replays produce no duplicate external side effect.
> - **Duplicate-safe** — replay may emit the side effect again, but downstream consumers are expected to deduplicate (HMAC-signed webhooks with a stable payload `id`).
> - **At-most-once on success** — the in-process check prevents duplicate work after a successful prior run; a retry that crosses a worker restart could emit twice.
> - **No** — replay produces a duplicate external side effect; retries must be avoided or guarded at the call site.

### donations

| Task | Trigger | Side effects | Retries | Timeout | Idempotency | Recovery |
| --- | --- | --- | --- | --- | --- | --- |
| `donations.tasks.send_quarterly_donor_updates` | Beat: every 7,776,000 s (~90 days) — see `CELERY_BEAT_SCHEDULE["send-quarterly-donor-updates"]` | Emails every donor with `tax_receipt_issued=True` and a non-empty `donor_email` (one email per donation row) | None (`@shared_task` defaults) | 30 min global | **No** — per-run loop sends each email unconditionally; running twice in the same window double-emails donors | Manual re-run is destructive. To replay for a single donor, call `DonationEmailService.send_quarterly_update(donation)` from `manage.py shell` rather than re-invoking the task. |

### vendors

| Task | Trigger | Side effects | Retries | Timeout | Idempotency | Recovery |
| --- | --- | --- | --- | --- | --- | --- |
| `vendors.flag_expiring_compliance` | Beat: every 86,400 s (daily) — see `CELERY_BEAT_SCHEDULE["flag-expiring-vendor-compliance"]` | Single digest email to the Logistics group listing TDLR/COI expired or expiring within 30 days | None | 30 min global | **Duplicate-safe** — re-running the same day re-sends the digest, but content is a snapshot; recipients expect daily delivery. No per-vendor side effect. | Re-run via `celery -A config call vendors.flag_expiring_compliance` in the worker container. |

### inventory

| Task | Trigger | Side effects | Retries | Timeout | Idempotency | Recovery |
| --- | --- | --- | --- | --- | --- | --- |
| `inventory.tasks.download_image_from_url` | `.delay()` from item create/update flows that supply an image URL | HTTP GET to the supplied URL (30 s timeout); writes `InventoryItem.image` | None | 30 min global | **Yes** — early-returns `"Image already exists for {item.name}"` when `item.image` is already set | Clear `item.image` and re-queue, or re-call directly. |
| `inventory.tasks.generate_qr_code` | `.delay()` from item create/save | Writes QR PNG to `InventoryItem.qr_code` field via `QRCodeService` | None | 30 min global | **Yes** — overwrites the existing QR; re-running produces the same artifact for the same item | Re-queue. |
| `inventory.tasks.generate_index_card` | `.delay()` (currently called only in tests) | Generates a PDF in memory; not persisted (TODO in code) | None | 30 min global | N/A | Not user-recoverable; this task does not yet have a storage destination. |
| `inventory.tasks.update_average_lead_times` | Manual / not currently scheduled | Updates `ItemSupplier.average_lead_time` for items with completed reorders in the last 180 days | None | 30 min global | **Yes** — recomputes from scratch; idempotent on a stable history | Re-queue from `manage.py shell`. |

### reorder_queue (webhooks + email)

| Task | Trigger | Side effects | Retries | Timeout | Idempotency | Recovery |
| --- | --- | --- | --- | --- | --- | --- |
| `reorder_queue.tasks.send_webhook_notification` | `.delay()` from the per-event trigger tasks below | HTTP POST to every active `WebHook` row matching the event type, optionally HMAC-signed | `max_retries=3`, `default_retry_delay=60` s, `autoretry_for=(requests.exceptions.RequestException,)` | 30 min global | **Duplicate-safe** — payload includes an `id` and a stable `timestamp`, so subscribers can dedupe; `WebHook.record_failure`/`record_success` accumulate counts on every attempt | Failed deliveries surface in the admin (`/admin/reorder_queue/webhook/`, columns `failure_count`, `last_error`). Re-fire by replaying the trigger task with the original ID, or call `send_webhook_notification.delay(event_type, payload)` from `manage.py shell`. |
| `reorder_queue.tasks.send_reorder_request_notification_email` | `.delay()` from `ReorderRequest.save` notification flow | Sends in-app + email notification for the request | None | 30 min global | **Duplicate-safe** — the notification layer dedupes on `request_id`; missing rows return `{"sent": 0, "reason": "not-found"}` rather than raising | Re-call with the same `request_id`. |
| `reorder_queue.tasks.trigger_reorder_request_webhook` | `.delay()` from `ReorderRequest` post-save signal | Builds payload, hands off to `send_webhook_notification` | None at this layer; downstream task retries | 30 min global | **Duplicate-safe** | Re-call with the same `request_id`. |
| `reorder_queue.tasks.run_webhook_test` | `.delay()` from the admin "Test webhook" action | Single HTTP POST to the configured URL with a test payload (`"test": True`) | None | 30 min global | **Yes** — clearly marked test payload; re-running emits another test event | Re-trigger from the admin. |
| `reorder_queue.tasks.send_fixture_refill_webhook` | `.delay()` from `FixtureRefillRequest` create | Builds payload, hands off to `send_webhook_notification` | None at this layer; downstream task retries | 30 min global | **Duplicate-safe** | Re-call with the same `refill_request_id`. |
| `reorder_queue.tasks.send_asset_problem_webhook` | `.delay()` from `AssetProblem` create | Builds payload, hands off to `send_webhook_notification` | None at this layer; downstream task retries | 30 min global | **Duplicate-safe** | Re-call with the same `problem_id`. |
| `reorder_queue.tasks.send_location_problem_webhook` | `.delay()` from `Location.report_problem` action | Builds payload, hands off to `send_webhook_notification` | None at this layer; downstream task retries | 30 min global | **Duplicate-safe** | Re-call with the same `problem_id`. |

### location_checkins

| Task | Trigger | Side effects | Retries | Timeout | Idempotency | Recovery |
| --- | --- | --- | --- | --- | --- | --- |
| `location_checkins.tasks.send_location_checkin_webhook` | `.delay()` from `LocationCheckIn` create | Builds payload, hands off to `send_webhook_notification` | None at this layer; downstream task retries | 30 min global | **Duplicate-safe** | Re-call with the same `checkin_id`. |
| `location_checkins.tasks.send_location_feedback_webhook` | `.delay()` from `LocationFeedback` create | Builds payload, hands off to `send_webhook_notification` | None at this layer; downstream task retries | 30 min global | **Duplicate-safe** | Re-call with the same `feedback_id`. |
| `location_checkins.tasks.send_security_report_webhook` | `.delay()` from `SecurityReport` create | Builds payload, hands off to `send_webhook_notification` | None at this layer; downstream task retries | 30 min global | **Duplicate-safe** | Re-call with the same `report_id`. |

### forgekey (MQTT)

| Task | Trigger | Side effects | Retries | Timeout | Idempotency | Recovery |
| --- | --- | --- | --- | --- | --- | --- |
| `forgekey.tasks.send_mqtt_command` | `.delay()` from device control flows | MQTT publish (QoS 1) to the device command topic | `max_retries=3`, `default_retry_delay=60` s | 30 min global | **Duplicate-safe** — commands are level-triggered (`enable`/`disable`/`status`); device firmware idempotently applies the latest state | Re-call with the same args. |
| `forgekey.tasks.enable_device` | `.delay()` from device control / lockout-release flows | Wraps `send_mqtt_command(mac, "enable")` | `max_retries=3`, `default_retry_delay=60` s | 30 min global | **Duplicate-safe** | Re-call. |
| `forgekey.tasks.disable_device` | `.delay()` from device lockout flows (with optional `delay_seconds`) | Wraps `send_mqtt_command(mac, "disable", {...})` | `max_retries=3`, `default_retry_delay=60` s | 30 min global | **Duplicate-safe** | Re-call. |
| `forgekey.tasks.request_device_status` | `.delay()` from admin / scheduled status checks | Wraps `send_mqtt_command(mac, "status")` | `max_retries=3`, `default_retry_delay=60` s | 30 min global | **Yes** — status request, no state change | Re-call. |
| `forgekey.tasks.process_mqtt_status_message` | `.delay()` from MQTT status message handler | Updates `ESP32Device.is_online`, `last_seen`, `firmware_version`; routes any embedded `cmd_ack` through `apply_command_ack`/`apply_command_ack_by_verb` so command acknowledgements survive a consumer outage (oms-v433rt). | None | 30 min global | **Yes** — last-write-wins on a single device row; cmd_ack application is keyed on `command_id`/verb | Replay from MQTT log if needed. |
| `forgekey.tasks.process_mqtt_occupancy` | `.delay()` from `ForgeKeyMQTTWebhook._dispatch` when topic matches `<prefix>/<mac>/<sensor>/occupancy` | Inserts an `OccupancyEvent` row keyed on the device-supplied timestamp; touches `ESP32Device.last_seen` + `is_online=True`. | None | 30 min global | **No** — append-only; replay inserts a duplicate `OccupancyEvent`. | If duplicates ship, delete offending rows by `event_timestamp_utc`. Avoid replaying this task. |
| `forgekey.tasks.process_mqtt_device_capabilities` | `.delay()` from `ForgeKeyMQTTWebhook._dispatch` when topic matches `<prefix>/<mac>/capabilities` (retained QoS-1 boot announcement) | Overwrites `ESP32Device.capabilities` from the announcement and stamps `capabilities_announced_at`, `last_seen`, `is_online`; opportunistically updates `firmware_version` when present. | None | 30 min global | **Yes** — last-write-wins; firmware is source of truth so a replay of the same announcement is a no-op. | Replay from MQTT log if needed. |
| `forgekey.tasks.process_mqtt_power_reading` | `.delay()` from MQTT power-reading handler | Inserts a `PowerMeterReading` row | None | 30 min global | **No** — append-only; replay creates duplicate readings | If duplicate readings ship, delete the offending rows by `received_at`. Avoid replaying this task. |
| `forgekey.tasks.process_mqtt_firmware_update_response` | `.delay()` from MQTT firmware response handler | Updates `DeviceFirmwareUpdate` row + `ESP32Device.firmware_version` | None | 30 min global | **Yes** — idempotent state transition keyed on `update_id` + `device` | Replay from MQTT log if needed. |
| `forgekey.tasks.trigger_ota` | `.delay()` from the `FirmwareVersion` admin "Deploy OTA to fleet" action — one task per matching active device | Inserts a new `DeviceFirmwareUpdate` row (audit trail), records a `firmware_request` audit event, MQTT-publishes (QoS 1) the OTA trigger payload to `forgekey/<mac>/ota/trigger`. Lookup failures are terminal (no retry, no audit row). | `max_retries=3`, `default_retry_delay=30` s — only on broker rejection (`OTADispatchError`). Lookup failures are terminal. | 30 min global | **No** — each retry/replay inserts a new `DeviceFirmwareUpdate` row. The device, however, deduplicates on `update_id`, so multiple rows do not cause multiple OTAs to apply. | Replay from the admin action or `trigger_ota.delay(device_id, firmware_id)` in a shell; expect a fresh `DeviceFirmwareUpdate` row per attempt. |
| `forgekey.tasks.prune_device_photos` | Beat: every 86,400 s (daily) — see `CELERY_BEAT_SCHEDULE["forgekey-prune-device-photos"]`. The `retention_days` kwarg is sourced from `FORGEKEY_PHOTO_RETENTION_DAYS` (default 30). | Deletes `ESP32DevicePhoto` rows older than `retention_days` | None | 30 min global | **Yes** — re-running with the same cutoff is a no-op | Re-run manually: `celery -A config call forgekey.tasks.prune_device_photos`. |
| `forgekey.tasks.mark_stale_devices_offline` | Beat: every 1,800 s (30 min) — see `CELERY_BEAT_SCHEDULE["forgekey-mark-stale-devices-offline"]`. The `threshold_hours` kwarg is sourced from `FORGEKEY_DEVICE_OFFLINE_THRESHOLD_HOURS` (default 5). | Updates `ESP32Device.is_online=False` for rows whose `last_seen` is older than the threshold (gh #349) | None | 30 min global | **Yes** — re-running with the same cutoff is a no-op (already-offline rows are filtered at the WHERE clause) | Run manually: `celery -A config call forgekey.tasks.mark_stale_devices_offline`. |

### analytics

| Task | Trigger | Side effects | Retries | Timeout | Idempotency | Recovery |
| --- | --- | --- | --- | --- | --- | --- |
| `analytics.send_monthly_pulse_email` | Beat: `crontab(minute=0, hour=9, day_of_month=1)` — see `CELERY_BEAT_SCHEDULE["analytics-send-monthly-pulse"]`. Covers the prior calendar month. | Resolves recipients (env `BOARD_REPORT_EMAILS` ∪ `analytics-recipients` Django group), renders the HTML+text monthly-pulse email with two inline matplotlib PNG charts, sends via the configured email backend (Postmark via `django-anymail` in prod). Short-circuits with a logged warning when no recipients are configured. | None (`@shared_task` defaults) | 30 min global | **No** — running twice in the same window double-emails recipients. | Manual replay: `python manage.py send_monthly_pulse --month=YYYY-MM` (with `--dry-run` to preview without sending). The management command shares the same body so it stays in lockstep with the Beat task. |
| `analytics.emit_metric_snapshot` | Beat: every 5 min (`CELERY_BEAT_SCHEDULE["analytics-emit-metric-snapshot"]`). | Counts the gauges in `analytics.tasks.METRIC_SNAPSHOT_NAMES` (users, staff, active memberships, inventory items / assets / locations, ForgeKey devices total + online, last-24h location + occupancy check-ins) and emits each as a `sentry_sdk.logger.info(name, attributes={value, metric.name="…", metric.kind="gauge"})` to Sentry Logs. Supported replacement for the retired sentry-sdk Metrics SDK. | None (`@shared_task` defaults) | 2 min (`max_runtime` in the `@sentry_sdk.crons.monitor` config) | **Yes** — every run reports the *current* count; a missed run just gaps the chart, never double-counts. | Missed runs recover on the next 5-min Beat fire. Sustained outages are caught by Sentry Crons (`failure_issue_threshold=3`). |

### config (debug)

| Task | Trigger | Side effects | Retries | Timeout | Idempotency | Recovery |
| --- | --- | --- | --- | --- | --- | --- |
| `config.celery.debug_task` | Manual only | Prints request metadata (`ignore_result=True`) | None | 30 min global | **Yes** | N/A |

## Failed task recovery (AC-31)

Failed and retrying tasks are visible to staff and operators through three
surfaces:

1. **Django admin → Celery Task Results** at
   `/admin/django_celery_results/taskresult/`
   (`backend/config/celery_admin.py`). Status is colour-coded
   (`SUCCESS` / `FAILURE` / `PENDING` / `STARTED` / `RETRY` / `REVOKED`),
   list filters cover status / task name / worker / date, and tracebacks
   are persisted in the read-only detail view. Use the `status=FAILURE`
   filter for the failed-task queue.
2. **Webhook delivery counters** on each `WebHook` row
   (`/admin/reorder_queue/webhook/`): `success_count`, `failure_count`,
   `last_triggered_at`, `last_error`. These accumulate across retries and
   survive worker restarts.
3. **Flower**, exposed by the bundled `flower` compose service on port
   5555, gives a live worker / queue / inflight task view. It is **not**
   exposed publicly by default — see
   [`deploy/COMPOSE_RUNBOOK.md`](../deploy/COMPOSE_RUNBOOK.md) §10 for
   the access path.

### Replay paths

For most tasks, the cleanest replay is to re-call the trigger with the
same canonical ID (`request_id`, `problem_id`, `mac_address`, …) so the
task rebuilds its payload from the current database state — never replay
a stale captured payload.

```bash
# In the worker container:
docker compose -f docker-compose.prod.yml exec celery \
    celery -A config call <task_name> --args='[...]' --kwargs='{...}'

# Or from a Django shell:
docker compose -f docker-compose.prod.yml exec backend \
    python manage.py shell -c "
from reorder_queue.tasks import trigger_reorder_request_webhook
trigger_reorder_request_webhook.delay(<request_id>)
"
```

`send_quarterly_donor_updates` is the only task that is **not safe to
replay wholesale** — see its row above for the per-donor recovery path.

## Gaps

The following gaps are tracked here because they affect operator
expectations even though the work to close them has not landed:

- **Per-queue routing is not configured.** A spike in webhook traffic
  can starve maintenance tasks. Introduce queues only when monitoring
  shows it's needed.
