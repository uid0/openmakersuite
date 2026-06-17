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

### notifications

| Task | Trigger | Side effects | Retries | Timeout | Idempotency | Recovery |
| --- | --- | --- | --- | --- | --- | --- |
| `notifications.tasks.send_new_device_alert_email` | `transaction.on_commit` from the new-device branch of `notifications.device_login.track_device_login` — fires when a staff/privileged account signs in from an unrecognized device (oms-1crmp, FP2; email companion to FP1's in-app alert) | Sends one templated email (Postmark via django-anymail in prod) to the signing-in user with the new device's User-Agent, approximate IP, time, and a link to `/account/devices`. Honors `NotificationPreference.email_enabled` only when the module constant `NEW_DEVICE_ALERT_BYPASS_EMAIL_OPT_OUT=False`; by default this security alert is sent regardless of the email opt-out. | None (`@shared_task` defaults) | 30 min global | **Duplicate-safe** — re-running for the same `(user_id, device_id)` re-sends the same alert; a deleted user/device returns `{"sent": 0, "reason": ...}` rather than raising | Re-call `send_new_device_alert_email.delay(user_id, device_id)` from a shell. |

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
| `forgekey.tasks.process_mqtt_reading` | `.delay()` from `ForgeKeyMQTTWebhook._dispatch` when topic matches `<prefix>/<mac>/<sensor>/reading` | Inserts a `TemperatureReading` row (server-stamped `recorded_at`, since the device timestamp is an uptime counter); touches `ESP32Device.last_seen` + `is_online=True`. | None | 30 min global | **No** — append-only; replay inserts a duplicate `TemperatureReading`. | If duplicates ship, delete offending rows by `recorded_at`. Avoid replaying this task. |
| `forgekey.tasks.process_mqtt_device_capabilities` | `.delay()` from `ForgeKeyMQTTWebhook._dispatch` when topic matches `<prefix>/<mac>/capabilities` (retained QoS-1 boot announcement) | Overwrites `ESP32Device.capabilities` from the announcement and stamps `capabilities_announced_at`, `last_seen`, `is_online`; opportunistically updates `firmware_version` when present. | None | 30 min global | **Yes** — last-write-wins; firmware is source of truth so a replay of the same announcement is a no-op. | Replay from MQTT log if needed. |
| `forgekey.tasks.process_mqtt_power_reading` | `.delay()` from MQTT power-reading handler | Inserts a `PowerMeterReading` row | None | 30 min global | **No** — append-only; replay creates duplicate readings | If duplicate readings ship, delete the offending rows by `received_at`. Avoid replaying this task. |
| `forgekey.tasks.process_mqtt_firmware_update_response` | `.delay()` from MQTT firmware response handler | Updates `DeviceFirmwareUpdate` row + `ESP32Device.firmware_version` | None | 30 min global | **Yes** — idempotent state transition keyed on `update_id` + `device` | Replay from MQTT log if needed. |
| `forgekey.tasks.trigger_ota` | `.delay()` from the `FirmwareVersion` admin "Deploy OTA to fleet" action — one task per matching active device | Inserts a new `DeviceFirmwareUpdate` row (audit trail), records a `firmware_request` audit event, MQTT-publishes (QoS 1) the OTA trigger payload to `forgekey/<mac>/ota/trigger`. Lookup failures are terminal (no retry, no audit row). | `max_retries=3`, `default_retry_delay=30` s — only on broker rejection (`OTADispatchError`). Lookup failures are terminal. | 30 min global | **No** — each retry/replay inserts a new `DeviceFirmwareUpdate` row. The device, however, deduplicates on `update_id`, so multiple rows do not cause multiple OTAs to apply. | Replay from the admin action or `trigger_ota.delay(device_id, firmware_id)` in a shell; expect a fresh `DeviceFirmwareUpdate` row per attempt. |
| `forgekey.tasks.prune_device_photos` | Beat: every 86,400 s (daily) — see `CELERY_BEAT_SCHEDULE["forgekey-prune-device-photos"]`. The `retention_days` kwarg is sourced from `FORGEKEY_PHOTO_RETENTION_DAYS` (default 30). | Deletes `ESP32DevicePhoto` rows older than `retention_days` | None | 30 min global | **Yes** — re-running with the same cutoff is a no-op | Re-run manually: `celery -A config call forgekey.tasks.prune_device_photos`. |
| `forgekey.tasks.mark_stale_devices_offline` | Beat: every 1,800 s (30 min) — see `CELERY_BEAT_SCHEDULE["forgekey-mark-stale-devices-offline"]`. The `threshold_hours` kwarg is sourced from `FORGEKEY_DEVICE_OFFLINE_THRESHOLD_HOURS` (default 5). | Updates `ESP32Device.is_online=False` for rows whose `last_seen` is older than the threshold (gh #349) | None | 30 min global | **Yes** — re-running with the same cutoff is a no-op (already-offline rows are filtered at the WHERE clause) | Run manually: `celery -A config call forgekey.tasks.mark_stale_devices_offline`. |
| `forgekey.tasks.advance_firmware_rollouts` | Beat: every 300 s (5 min) — see `CELERY_BEAT_SCHEDULE["forgekey-advance-firmware-rollouts"]`. Also invoked synchronously by the rollout `start` / `advance` API actions. | For each ACTIVE `FirmwareRollout` past its `interval_minutes`, dispatches the next wave (`ceil(target_total * batch_size_percent / 100)` devices) via `publish_ota_trigger` — creating `DeviceFirmwareUpdate` rows + MQTT OTA triggers — and flips the rollout to COMPLETED once the fleet is drained. | None | 30 min global | **Yes** — devices already dispatched by the rollout (linked `DeviceFirmwareUpdate` rows) are excluded from the next wave, so a re-run only picks up genuinely-remaining devices. | Pause the rollout in the UI; re-run manually: `celery -A config call forgekey.tasks.advance_firmware_rollouts`. |
| `forgekey.tasks.advance_epaper_firmware_rollouts` | Beat: every 300 s (5 min) — see `CELERY_BEAT_SCHEDULE["forgekey-advance-epaper-firmware-rollouts"]`. Also invoked synchronously by the ePaper rollout `start` / `advance` API actions. | For each ACTIVE `EpaperFirmwareRollout` past its `interval_minutes`, **promotes** the next wave of `EPaperDisplay` rows by setting their `target_firmware_version` (no MQTT publish — ePaper panels are HTTPS-pull; the actual install happens when each promoted panel next wakes and hits `/firmware-check/`). Flips rollout to COMPLETED once every panel is on the target. | None | 30 min global | **Yes** — panels already promoted (`target_firmware_version` set to the rollout's version) are excluded from the next wave. | Pause the rollout in the UI; re-run manually: `celery -A config call forgekey.tasks.advance_epaper_firmware_rollouts`. |
| `forgekey.tasks.build_firmware` | `.delay()` from `FirmwareBuildViewSet.perform_create` (POST `/forgekey/firmware-builds/`, staff). Routed to the dedicated **`builds`** queue (`CELERY_TASK_ROUTES`) — only the self-hosted `firmware-builder` worker (git + PlatformIO) runs it; the app image has no toolchain. | Clones the ForgeKey repo at `source_ref`, overwrites the firmware security headers with the active OMS CA + command pubkey, runs `pio run -e <pio_env>`, and uploads the resulting binary as a signed `FirmwareVersion`; records status / log / commit / CA-fingerprint on the `FirmwareBuild` row. | None (`max_retries=0`) — a failed build is recorded with its log, not retried. | 30 min global | **No** — each run creates a new `FirmwareVersion` (unique `version`); re-running the same version errors on the unique constraint. | Re-queue from the UI / API with a corrected `version`; inspect the build's `log` / `error_message`. Requires the `firmware-builder` worker + a ForgeKey deploy key. |

### analytics

| Task | Trigger | Side effects | Retries | Timeout | Idempotency | Recovery |
| --- | --- | --- | --- | --- | --- | --- |
| `analytics.send_monthly_pulse_email` | Beat: `crontab(minute=0, hour=9, day_of_month=1)` — see `CELERY_BEAT_SCHEDULE["analytics-send-monthly-pulse"]`. Covers the prior calendar month. | Resolves recipients (env `BOARD_REPORT_EMAILS` ∪ `analytics-recipients` Django group), renders the HTML+text monthly-pulse email with two inline matplotlib PNG charts, sends via the configured email backend (Postmark via `django-anymail` in prod). Short-circuits with a logged warning when no recipients are configured. | None (`@shared_task` defaults) | 30 min global | **No** — running twice in the same window double-emails recipients. | Manual replay: `python manage.py send_monthly_pulse --month=YYYY-MM` (with `--dry-run` to preview without sending). The management command shares the same body so it stays in lockstep with the Beat task. |
| `analytics.emit_metric_snapshot` | Beat: every 5 min (`CELERY_BEAT_SCHEDULE["analytics-emit-metric-snapshot"]`). | Counts the gauges in `analytics.tasks.METRIC_SNAPSHOT_NAMES` (users, staff, active memberships, inventory items / assets / locations, ForgeKey devices total + online, last-24h location + occupancy check-ins) and emits each as a `sentry_sdk.logger.info(name, attributes={value, metric.name="…", metric.kind="gauge"})` to Sentry Logs. Supported replacement for the retired sentry-sdk Metrics SDK. | None (`@shared_task` defaults) | 2 min (`max_runtime` in the `@sentry_sdk.crons.monitor` config) | **Yes** — every run reports the *current* count; a missed run just gaps the chart, never double-counts. | Missed runs recover on the next 5-min Beat fire. Sustained outages are caught by Sentry Crons (`failure_issue_threshold=3`). |

### backups

| Task | Trigger | Side effects | Retries | Timeout | Idempotency | Recovery |
| --- | --- | --- | --- | --- | --- | --- |
| `backups.daily_postgres_backup` | Beat: `crontab(minute=0, hour=2)` — 02:00 UTC daily (`CELERY_BEAT_SCHEDULE["backups-daily-postgres"]`). | Runs `pg_dump -Fc --no-owner --no-acl` against `DATABASE_URL`, writes `db-YYYY-MM-DD.dump` into `OMS_BACKUP_DIR` (default `/var/backups/oms`, mounted as the `backups_volume` named volume on the `celery` container). Prunes any `db-*.dump` older than `OMS_BACKUP_RETENTION_DAYS` (default 14). Emits an `oms.backup.daily_postgres` log entry to Sentry Logs with the byte size + pruned count. SQLite/dev envs short-circuit cleanly. | None (`@shared_task` defaults) | 30 min (`max_runtime` in the `@sentry_sdk.crons.monitor` config) | **Yes** — the per-day filename means a same-day re-run overwrites the prior dump; nothing accumulates duplicates. | Missed day surfaces as a Sentry Cron failure (`failure_issue_threshold=1`). Manual run: `docker compose -f docker-compose.prod.yml exec celery celery -A config call backups.daily_postgres_backup`. Operator-driven backup also available as `scripts/backup-db.sh`. Restore via `scripts/restore-db.sh` against the `-Fc` archive. |

### config (debug)

| Task | Trigger | Side effects | Retries | Timeout | Idempotency | Recovery |
| --- | --- | --- | --- | --- | --- | --- |
| `config.celery.debug_task` | Manual only | Prints request metadata (`ignore_result=True`) | None | 30 min global | **Yes** | N/A |

### storage_vision

| Task | Trigger | Side effects | Retries | Timeout | Idempotency | Recovery |
| --- | --- | --- | --- | --- | --- | --- |
| `storage_vision.process_capture` | Enqueued from `VisionCaptureViewSet.create` after a phone or fixed-camera upload (AC-9, AC-10). | Loads the capture, decodes the original via Pillow, runs OpenCV `QRCodeDetector.detectAndDecodeMulti` (AC-14), classifies the crop below each marker with the `heuristic-v1` brightness-mean model (AC-16), and creates / dedupes `VisionObservation` rows (AC-17/18/19). Transitions the capture through `queued → processing → processed`. Unknown markers stay in `capture.markers_detected` without spawning observations. AC-15 stamps `failure_code=no_markers_detected` when nothing decodes; AC-12 keeps every failure path's `failure_reason` sanitized (no tracebacks, no filesystem paths). | None (`@shared_task` defaults) | 30 min global | **Yes** — early-returns if the row is already past `queued`, so retries / re-enqueues never re-stamp a processed capture; the partial unique constraint on `(slot, suggested_action) WHERE status=pending` makes duplicate observations bump `duplicate_count` instead of inserting again. | Failed rows surface in Django admin → `VisionCapture` (status=`failed`, `failure_reason`/`failure_code` populated). Replay by flipping the row back to `queued` and re-enqueueing manually until the slice-7 management command lands. |
| `storage_vision.prune_original_captures` | Beat: daily at 03:30 UTC (`CELERY_BEAT_SCHEDULE["storage-vision-prune-originals"]`). Lands after the 02:00 Postgres backup so deleted originals still live in the freshest dump. | Deletes `VisionCapture.original_image` files older than `STORAGE_VISION_RETENTION_DAYS` (default 30). Preserves every `VisionCapture` row, every `VisionObservation` + `evidence_crop` thumbnail, every `VisionReviewAction`, and the linked `StockReconciliation` / `ReorderRequest` rows (AC-26). Setting `STORAGE_VISION_RETENTION_DAYS=0` disables the prune entirely. Logs `deleted`, `freed_bytes`, `errors`, and the `cutoff` ISO timestamp. | None (`@shared_task` defaults) | 30 min global | **Yes** — second-run inside the same window finds no candidates (FieldFile reference is cleared on success). | Smoke / manual run: `python manage.py prune_storage_vision_captures` (add `--dry-run` to preview, `--days N` to override the setting for one run). |

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
