# Incident runbook: backend container OOM

> Tracking bead: **oms-9t2** (P0, user-reported 2026-05-02).
> History: this doc was created alongside the first hardening pass; update it
> after each repeat occurrence with what was actually wrong this time.

## TL;DR

The Django `backend` container in `docker-compose.prod.yml` runs four gunicorn
workers under a 2 GiB memory limit. If the workers grow unbounded — usually
because of a slow leak — the container OOM-kills and `restart: unless-stopped`
brings it back. Symptoms:

- 502s from nginx for tens of seconds at a time.
- `docker ps` shows `backend` recently restarted (`Up X seconds`).
- `dmesg | grep -i 'oom\|killed process'` on the host shows the OOM kill.
- `docker inspect <backend-cid> --format '{{.State.OOMKilled}}'` returns `true`.

The oms-9t2 hardening pass already deployed two safety nets that **bound** the
damage even if a leak is present:

1. **gunicorn `--max-requests 1000 --max-requests-jitter 100`** — each worker
   recycles after roughly 1k requests, releasing whatever memory it accumulated.
2. **`deploy.resources.limits.memory: 2G`** — Docker enforces a hard ceiling
   so a runaway worker can't take down adjacent containers.
3. **celery `--max-tasks-per-child=200`** — same recycle pattern for the celery
   worker container.

If you are paged for a fresh OOM, those two should already be in place. If they
aren't, **deploy them first** before investigating — they are pure safety and
cost nothing in steady state.

## Detect

- **Healthcheck flap.** `docker compose -f docker-compose.prod.yml ps` shows the
  backend healthcheck oscillating, or "Restarting".
- **Kernel log.** On the host: `dmesg -T | grep -i 'killed process' | tail`.
- **Container state.**
  ```bash
  cid=$(docker compose -f docker-compose.prod.yml ps -q backend)
  docker inspect "$cid" --format '{{.State.OOMKilled}} {{.State.ExitCode}} {{.RestartCount}}'
  ```
  `OOMKilled=true` confirms the cause.
- **Worker RSS over time.** Inside the container:
  ```bash
  docker compose -f docker-compose.prod.yml exec backend \
    ps -o pid,rss,etime,cmd -ax | grep gunicorn
  ```
  Growing RSS across worker PIDs over minutes/hours points at a leak.

## Mitigate (ship first, debug second)

Already in `docker-compose.prod.yml` after oms-9t2. Confirm the running config
matches:

```yaml
backend:
  command: >-
    gunicorn config.wsgi:application
    --bind 0.0.0.0:8000
    --workers 4
    --timeout 120
    --max-requests 1000
    --max-requests-jitter 100
    --graceful-timeout 30
  deploy:
    resources:
      limits:
        memory: 2G
      reservations:
        memory: 512M
```

If a leak resurfaces and the recycle window isn't fast enough:

- Drop `--max-requests` to 500 (or 250) to recycle more aggressively.
- Bump `limits.memory` only as a temporary cushion; treat it as evidence of an
  unfixed leak, not a fix.

## Investigate

The bead enumerates five hypotheses; the oms-9t2 fix already addressed #1 and
#2 defensively (see "Mitigations already shipped" below). If the OOM repeats
after that pass, move down the list.

### H1: paho-mqtt connection retry storm (addressed)

`forgekey.tasks.get_mqtt_client()` is a per-process singleton. Before oms-9t2,
a broker outage caused every `send_mqtt_command` call to instantiate a fresh
`paho.Client`, fail in `connect()`, and leak the half-built client's socket +
background thread. The fix:

- A failed connect records a monotonic timestamp and refuses subsequent calls
  for `MQTT_CONNECT_RETRY_COOLDOWN_SECONDS` (default 30s) with
  `MQTTConnectCooldown`.
- On failure, `loop_stop()` and `disconnect()` are called on the partially
  built client to release its socket + thread.
- Test coverage: `forgekey/tests/test_tasks.py::TestMQTTClientCooldown`.

To confirm whether MQTT was the proximate cause, check
`docker compose -f docker-compose.prod.yml logs backend | grep -i mqtt | tail`.
Repeated "Failed to connect to MQTT broker" lines without intervening
"refusing reconnect" lines means the cooldown isn't deployed.

### H2: ESP32DevicePhoto upload memory spike (addressed)

`ForgeKeyDevicePhotoUploadView` accepts JPEGs up to ~10 MB. Django's default
`FILE_UPLOAD_MAX_MEMORY_SIZE` is 2.5 MiB, so anything below that sat in worker
heap. The fix lowered the threshold to 1 MiB
(`backend/config/settings.py::FILE_UPLOAD_MAX_MEMORY_SIZE`) so device photos
spool to `/tmp` via `TemporaryFileUploadHandler` instead of buffering on the
heap. Test guard:
`forgekey/tests/test_provisioning_and_photo.py::TestPhotoUpload::test_file_upload_threshold_keeps_device_photos_off_the_heap`.

To confirm: trigger a photo upload while watching worker RSS:
```bash
docker compose -f docker-compose.prod.yml exec backend sh -c \
  'while true; do ps -o rss,cmd -ax | grep gunicorn | grep -v grep; sleep 1; done'
```
RSS should stay flat ± a few hundred KB across the upload, not jump by the JPEG
size.

### H3: Sentry SDK event buffer

If `SENTRY_DSN` points at an unreachable Sentry, the SDK buffers events in
memory waiting to flush. Check from inside the container:

```bash
docker compose -f docker-compose.prod.yml exec backend sh -c \
  'echo "DSN=$SENTRY_DSN"; python -c "import urllib.request as u; print(u.urlopen(\"$SENTRY_DSN\", timeout=3).status)" 2>&1 || true'
```

If the DSN is unset or unreachable, either fix the DSN or unset it so
sentry-sdk never initializes.

### H4: Celery worker leak (separate container)

If `docker stats` shows the `celery` container OOMing rather than `backend`,
this is a different problem with the same shape. The oms-9t2 pass added
`--max-tasks-per-child=200` to the celery command, which gives celery the
same recycle behavior as gunicorn. If celery is still OOMing, drop the value
to 50 and look for the offending task with `docker compose ... logs celery`.

### H5: Heavy ML imports at module load time

If anything under `backend/` imports a large library (torch, tflite, opencv,
PIL is fine) at module import time, every worker pays for it. To enumerate
top module sizes:

```bash
docker compose -f docker-compose.prod.yml exec backend python -c "
import sys, importlib
import config.wsgi  # noqa: F401  -- triggers full app import
items = sorted(sys.modules.items(), key=lambda kv: sys.getsizeof(kv[1]), reverse=True)[:20]
for name, mod in items:
    print(sys.getsizeof(mod), name)
"
```

Anything multi-MB at the top of that list is suspect.

## Mitigations already shipped (oms-9t2)

| Change | File | Why |
|---|---|---|
| gunicorn `--max-requests 1000 --max-requests-jitter 100 --graceful-timeout 30` | `docker-compose.prod.yml` | Recycle web workers so any slow leak is bounded. |
| `deploy.resources.limits.memory: 2G` for backend | `docker-compose.prod.yml` | Hard cap so runaway workers can't take down adjacent containers. |
| celery `--max-tasks-per-child=200` + 1G memory limit | `docker-compose.prod.yml` | Same recycle pattern for the celery worker container. |
| MQTT connect cooldown / circuit breaker | `backend/forgekey/tasks.py` | Stop instantiating a fresh `paho.Client` per task during broker outages. |
| `FILE_UPLOAD_MAX_MEMORY_SIZE = 1 MiB` | `backend/config/settings.py` | Force device photo uploads to spool to disk. |

## After-action

When you close out an OOM incident, append a short entry below with:

- Date, suspected root cause, and the change that fixed it.
- A pointer to the bead/PR.
- Whether any of the H1–H5 hypotheses turned out to be wrong (so we can prune
  the runbook over time).

### History

- **2026-05-02** — Initial pass (oms-9t2). Mitigations + H1/H2 defensive code
  shipped without confirmed root cause; runbook authored. Awaiting 24h soak.
