# Service Catalog

What a working OMS deployment actually needs. Read alongside
[`DEPLOYMENT.MD`](DEPLOYMENT.MD) (procedures), [`NETWORK_EXPOSURE.md`](../deploy/NETWORK_EXPOSURE.md)
(inbound surface), and [`BACKUP_RESTORE.md`](../deploy/BACKUP_RESTORE.md)
(what to back up + how to restore).

The canonical source for image versions is [`docker-compose.prod.yml`](../docker-compose.prod.yml).
When a number here disagrees with the compose file, the compose file is right.

---

## Host requirements

| Resource | Minimum | Notes |
|---|---|---|
| CPU | 2 cores | 4+ for a sustained 20–50 user makerspace |
| RAM | 8 GB | Backend 4G + Celery 1G + Postgres ~1G + Redis 512M + EMQX ~1G + nginx/frontend negligible. Headroom matters under image-processing bursts (gh oms-9t2). |
| Disk | 50 GB | Postgres data + media uploads + EMQX logs + Docker layer cache. Plan for 5–10 GB/year media growth. |
| Docker | 24+ | Compose v2 plugin required (`docker compose`, not `docker-compose`). |
| TLS | Caddy or nginx in front | Terminates TLS, proxies to the internal nginx container on port 9000. Production reference: Caddy on the deploy host. |
| Outbound HTTPS | github.com, ghcr.io, registry.npmjs.org, pypi.org | For `git pull` + image pulls during `deploy.sh`. |

Frontend build host (CI runner) also needs **Node 24+** and **Python 3.14** for `pre-commit` hooks; these are not needed at runtime on the deploy host.

---

## Required runtime services

These five must be healthy for the app to serve any request.

### `db` — PostgreSQL 18.4 (alpine)

- **Image:** `postgres:18.4-alpine` (digest-pinned in compose)
- **Port:** 5432 (internal only)
- **Volume:** `postgres_data` → `/var/lib/postgresql/data` (PGDATA at `/var/lib/postgresql/data/pgdata` per Postgres 18's layout change)
- **Healthcheck:** `pg_isready -U $POSTGRES_USER -d $POSTGRES_DB`
- **Backup priority:** **critical** — primary application data. Verified restore path in [`BACKUP_RESTORE.md`](../deploy/BACKUP_RESTORE.md).
- **Major-version upgrades:** require pg_dump → fresh init → restore. PG18 alpine moved the data dir; don't trust an in-place mount of an older cluster (lesson: 2026-05-25 deploy).

### `redis` — Redis 8.6.3 (alpine)

- **Image:** `redis:8.6.3-alpine` (digest-pinned)
- **Port:** 6379 (internal only)
- **Volume:** none (in-memory; Celery results in postgres via `django_celery_results`)
- **Healthcheck:** `redis-cli ping`
- **Roles:**
  - Celery broker (URL: `redis://redis:6379/0`)
  - Django cache backend (`django-redis`)
  - Per-device rate limit for ForgeKey log forwarding (`forgekey.management.commands.mqtt_consumer`)
- **Backup priority:** **none** — all state is regenerable from postgres + the celery_beat shelve.

### `emqx` — EMQX Enterprise 6.2.0

- **Image:** `emqx/emqx-enterprise:6.2.0` (digest-pinned)
- **Ports (exposed):**
  - `1883` — MQTT (plain) — ForgeKey devices on the LAN
  - `8083` — MQTT-over-WebSocket
  - `8084` — MQTT-over-WSS
  - `8883` — MQTTS
  - `18083` — Dashboard + REST API
- **Volumes:** `emqx_data`, `emqx_log`
- **Bootstrap admin:** rendered by `deploy.sh` from `EMQX_DASHBOARD_PASSWORD` into `scripts/emqx/bootstrap-admins.txt`, mounted read-only and re-applied on every boot (oms-f9z).
- **Healthcheck:** `emqx ctl status` (start_period 60s — EMQX boot is slow)
- **Backup priority:** **low** — broker is essentially stateless from the OMS app's perspective; in-flight messages can be lost without harm. Dashboard ACLs are recreated from bootstrap on every boot.
- **Why it's required:** ForgeKey IoT devices (locker locks, traffic counters, status lights) publish status/occupancy/logs over MQTT. Without EMQX the device fleet appears offline.

### `nginx` — alpine

- **Image:** built from `nginx/Dockerfile`
- **Ports (exposed via Caddy upstream):** 80, 443
- **Volumes:** `static_volume` (Django collectstatic output), `frontend_build` (Vite output), `media_volume` (uploads), `letsencrypt_*` (TLS chain), `certbot_challenges`
- **Healthcheck:** `wget --spider http://127.0.0.1/health`
- **Backup priority:** **none** for the volumes it consumes (all regenerable). The `letsencrypt_*` volumes are easy to regenerate via ACME but cause user-visible TLS errors during the gap.
- **Notes:** all `/admin/`, `/api/`, `/auth/passkey/`, `/webhooks/`, `/flower/`, `/mqttadmin/` paths proxy to the backend container; `/django-static/` and `/media/` are served by nginx off the mounted volumes; `/` falls back to the SPA shell. The vendor-paperwork prefixes under `/media/` are still served here but only after an `auth_request` session check against the backend — see [`API_PERMISSION_MATRIX.md`](API_PERMISSION_MATRIX.md) "Protected media".

### `backend` — Django (gunicorn 4w/8t)

- **Image:** built from `backend/Dockerfile.prod` (Python 3.14)
- **Exposed port (internal):** 8000
- **Volumes:** `static_volume`, `media_volume`
- **Healthcheck:** `GET /api/health/livez/` (dep-free, no DB query — see oms-801)
- **Readiness probe** (operator-side, not docker): `GET /api/health/readyz/` checks db / cache / broker / EMQX / object store / Sentry telemetry.
- **Recycle policy:** gunicorn `--max-requests 1000 --max-requests-jitter 100 --graceful-timeout 30` bounds slow leaks from paho-mqtt / image processing / etc. (oms-9t2).

The `celery`, `mqtt_consumer`, and `celery_beat` services **reuse the backend image** via `image: oms-backend:local` so a single `docker compose build --no-cache backend` rebuilds all four together (fix from BACKEND-4 / oms-8bsc5).

---

## Required workers (backend-derived images)

| Service | Command | Memory limit | Healthcheck |
|---|---|---|---|
| `celery` | `celery -A config worker -l info --max-tasks-per-child=200` | 1 GB | (inherits backend image's HTTP probe — override pending) |
| `celery_beat` | `celery -A config beat ...` with persistent shelve under `celery_beat_state` volume | 256 MB | pidfile check |
| `mqtt_consumer` | `python manage.py mqtt_consumer` | 512 MB | `pgrep -f manage.py mqtt_consumer` |

All three depend on `backend` so Compose runs the image build once.

---

## Optional runtime services

The app boots and runs without these — each is gated on its env-var presence and falls back gracefully.

### Sentry self-hosted — `highlighter.openmakersuite.net`

- **Purpose:** Backend (Django + Celery + logging) and frontend (`@sentry/react` + Session Replay) error reporting.
- **Wired by:** `SENTRY_DSN` (backend init in `backend/config/settings.py`) and `VITE_SENTRY_DSN` (frontend init in `frontend/src/index.tsx`).
- **CSP allowlist:** already in nginx template — `*.sentry-cdn.com`, `*.ingest.sentry.io`, `sentry.io`.
- **Fallback when unset:** errors only appear in container logs (`docker compose logs backend`).
- **Operator tools:** `sentry-mayor` CLI for list / show / comment / assign / resolve issues via REST.
- **CI integration:** every CI build registers the commit SHA as a Sentry release and uploads frontend source maps, gated on the `SENTRY_AUTH_TOKEN` GH secret.

### Postmark — outbound email (via `django-anymail[postmark]`)

- **Purpose:** Transactional mail (donor receipts, vendor compliance digests, reorder notifications, location-problem alerts).
- **Wired by:** `EMAIL_BACKEND=anymail.backends.postmark.EmailBackend` + `POSTMARK_SERVER_TOKEN`. Inbound webhook validation uses `POSTMARK_INBOUND_TOKEN`.
- **Fallback when unset:** `EMAIL_BACKEND` defaults to `django.core.mail.backends.console.EmailBackend` — mail is printed to backend logs, not sent. `scripts/validate-prod-env.sh` refuses to deploy with the console backend in production.

### OpenWeather

- **Purpose:** Weather widget on the TV dashboard.
- **Wired by:** `OPENWEATHER_API_KEY` + `OPENWEATHER_LAT`/`LON` or `OPENWEATHER_ZIP`. Configurable: `OPENWEATHER_UNITS` (default `imperial`), `OPENWEATHER_CACHE_SECONDS` (default 600).
- **Fallback when unset:** widget displays `weather_not_configured` and the TV dashboard renders without the weather block.

### WHMCS — billing/membership lookup

- **Purpose:** Cross-checks member status against the makerspace's WHMCS instance during certain admin flows.
- **Wired by:** `WHMCS_API_URL` + `WHMCS_API_ACCESSKEY`.
- **Fallback when unset:** WHMCS-dependent admin actions return a clear "not configured" response; everything else works normally.

### Slack — release notifications

- **Purpose:** `.github/workflows/release.yml` posts a release ping on successful tag.
- **Wired by:** `SLACK_WEBHOOK_URL` GH secret.
- **Fallback when unset:** release still ships; just no Slack message.

### Codecov

- **Purpose:** PR coverage reporting.
- **Wired by:** `CODECOV_TOKEN` GH secret.
- **Fallback when unset:** upload step is a no-op; CI still passes.

---

## Persistent volumes — backup priority

| Volume | What's in it | Regenerable? | Backup priority |
|---|---|---|---|
| `postgres_data` | Application database | No | **CRITICAL** |
| `media_volume` | Uploaded files (asset photos, WO attachments, device photos) | No | **CRITICAL** |
| `emqx_data` | Broker state, ACL cache, retained messages | Mostly | Low (in-flight messages only) |
| `emqx_log` | Broker logs | Yes | Skip |
| `celery_beat_state` | `celerybeat-schedule` shelve (last_run_at per periodic task) | Yes (timers re-arm) | Skip |
| `static_volume` | Django collectstatic output | Yes (every deploy) | Skip |
| `frontend_build` | Vite output | Yes (every deploy) | Skip |
| `letsencrypt_certs` / `letsencrypt_lib` / `letsencrypt_logs` | ACME chain + state | Yes (ACME on next boot) | Skip — TLS gap during regen |
| `certbot_challenges` | ACME http-01 challenge files | Yes (transient) | Skip |

See [`deploy/BACKUP_RESTORE.md`](../deploy/BACKUP_RESTORE.md) for the actual procedures.

---

## Inbound network exposure

What an internet-facing deploy actually advertises. Source of truth: [`deploy/NETWORK_EXPOSURE.md`](../deploy/NETWORK_EXPOSURE.md).

| Port | Purpose | Audience |
|---|---|---|
| 443 | HTTPS — main app, admin, API, webhooks | public + makerspace LAN |
| 80 | HTTP — ACME challenge + 301 redirect | public |
| 1883 / 8883 | MQTT / MQTTS for ForgeKey devices | makerspace LAN only — do **not** expose to the internet |
| 18083 | EMQX dashboard | operator (Tailscale / SSH tunnel recommended; CSP + reverse proxy if exposed) |
| 5555 | Celery Flower | operator (don't expose; protect behind nginx auth or Tailscale) |

---

## Application-language requirements (build-time)

These don't run on the deploy host; they run in CI and in the Docker build stages.

- **Python 3.14** — backend image (`backend/Dockerfile.prod`). Pinned in CI (`actions/setup-python` → `python-version: "3.14"`).
- **Node 24+** — frontend build (`frontend/Dockerfile.prod`). Pinned in `frontend/package.json` `engines.node`.
- **Vite 8** — frontend bundler (`frontend/vite.config.ts`).

The full dependency manifest lives in:
- `backend/requirements.txt` + `backend/requirements-dev.txt`
- `frontend/package.json` + `frontend/package-lock.json`

[GitHub issue #445](https://github.com/uid0/openmakersuite/issues/445) (Renovate's auto-managed dependency dashboard) is the canonical real-time view of every detected dep across all manifests.

---

## "I'm running this for the first time" checklist

1. Provision a VM that meets the [Host requirements](#host-requirements).
2. Install Docker 24+ with Compose v2 plugin.
3. Stand up a TLS-terminating proxy (Caddy reference config in [`deploy/COMPOSE_RUNBOOK.md`](../deploy/COMPOSE_RUNBOOK.md)).
4. Clone this repo, copy `.env.prod.example` → `.env`, fill in:
   - **Required:** `POSTGRES_PASSWORD`, `SECRET_KEY`, `DOMAIN`, `LETSENCRYPT_EMAIL`, `LETSENCRYPT_DOMAINS`, `EMQX_DASHBOARD_PASSWORD`.
   - **Strongly recommended:** `SENTRY_DSN`, `VITE_SENTRY_DSN`, `POSTMARK_SERVER_TOKEN`, `DEFAULT_FROM_EMAIL`.
   - **Optional:** `OPENWEATHER_API_KEY`, `WHMCS_API_*`.
5. `./scripts/validate-prod-env.sh` — refuses to deploy if anything required is unsafe.
6. `./deploy.sh` — rebuilds backend + frontend images, runs migrations on the live DB before bringing the app up, then `compose up -d`.
7. Visit `https://${DOMAIN}/admin/` and finish Django superuser setup.
