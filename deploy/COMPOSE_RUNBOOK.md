# Docker Compose Operations Runbook

Manual Docker Compose commands for day-2 operations on an OpenMakerSuite host.
This is the command-level reference for operators running the
`docker-compose.prod.yml` stack. For first-time setup see
[`docs/DEPLOYMENT.MD`](../docs/DEPLOYMENT.MD); for prerequisites see
[`PREREQUISITES.md`](PREREQUISITES.md); for what each port exposes and how
to harden the host firewall see [`NETWORK_EXPOSURE.md`](NETWORK_EXPOSURE.md);
for post-deploy validation see [`SMOKE_TESTS.md`](SMOKE_TESTS.md); for
backup/restore see [`BACKUP_RESTORE.md`](BACKUP_RESTORE.md); for upgrades
and rollbacks see [`UPGRADE_ROLLBACK.md`](UPGRADE_ROLLBACK.md).

> **`docker compose` (v2), not `docker-compose` (v1).** v1 is end-of-life and
> does not parse the bundled `docker-compose.prod.yml` correctly. Every
> snippet below uses the v2 plugin form.

## Conventions

All commands assume:

- You are in the repository root (the directory containing
  `docker-compose.prod.yml`).
- `.env` exists alongside the compose file (copied from `.env.prod.example`
  and filled in). The compose file reads it via `env_file:`.
- `POSTGRES_USER` defaults to `makerspace` and `POSTGRES_DB` defaults to
  `makerspace_inventory`. Override via `.env` if you changed them.

For brevity, snippets reference `$COMPOSE` — set it once per shell:

```bash
export COMPOSE='docker compose -f docker-compose.prod.yml'
```

## 1. Build images

Build all service images defined in the compose file:

```bash
$COMPOSE build
```

Build a single service (faster iteration when only one Dockerfile changed):

```bash
$COMPOSE build backend
$COMPOSE build frontend
```

Force a clean rebuild (ignore the layer cache — use when a base image was
re-pulled or a build arg silently shifted):

```bash
$COMPOSE build --no-cache backend
```

The frontend build bakes `GIT_HASH` and Sentry DSN in as `REACT_APP_*` build
args. If you build by hand without going through `deploy.sh`, export
`GIT_HASH` first so the version banner reflects the right commit:

```bash
export GIT_HASH=$(git rev-parse --short HEAD)
$COMPOSE build frontend
```

## 2. Database migrations

`deploy.sh` runs the pre-deploy migration gate automatically. The manual
equivalent — useful when applying a hotfix migration without a full deploy
or when investigating drift — is:

```bash
# 1. Ensure the database is up.
$COMPOSE up -d db
$COMPOSE exec db pg_isready -U "${POSTGRES_USER:-makerspace}"

# 2. Inspect pending migrations against the live DB. The --no-deps flag
#    keeps this from accidentally starting backend/celery/etc. SKIP_DB_MIGRATIONS=1
#    prevents the entrypoint from re-running migrate before the command.
$COMPOSE run --rm --no-deps -e SKIP_DB_MIGRATIONS=1 backend \
    python manage.py showmigrations --plan --no-color

# 3. Apply them. Aborts non-zero on failure — never run the backend against a
#    half-migrated DB.
$COMPOSE run --rm --no-deps -e SKIP_DB_MIGRATIONS=1 backend \
    python manage.py migrate --noinput
```

Roll back a single migration (rare, only when reverting code that shipped a
schema change):

```bash
$COMPOSE run --rm --no-deps -e SKIP_DB_MIGRATIONS=1 backend \
    python manage.py migrate <app_label> <previous_migration_name>
```

`<previous_migration_name>` is the migration you want to land on (e.g.
`0015_add_packages_column`), not the one you want to undo. Confirm the target
with `showmigrations` first.

## 3. Static assets

The backend image collects static files into the `static_volume`; nginx
mounts that volume read-only and serves `/static/`. Re-run collectstatic
whenever you ship Django admin changes, new admin themes, or DRF browseable
API tweaks:

```bash
$COMPOSE exec backend python manage.py collectstatic --noinput
```

Verify the volume is populated and visible to nginx:

```bash
$COMPOSE exec nginx ls -la /app/staticfiles | head
$COMPOSE exec nginx ls -la /app/frontend | head
```

If `/app/frontend` is empty, the frontend build didn't write into
`frontend_build`. Rebuild the frontend (`$COMPOSE build frontend`) and
recreate it (`$COMPOSE up -d --force-recreate frontend`).

## 4. Admin user creation

Interactive (prompted for username, email, password):

```bash
$COMPOSE exec backend python manage.py createsuperuser
```

Non-interactive (CI / automated bootstrap — do **not** pass the password on
the command line in shared shells; use `.env` or `read -s`):

```bash
$COMPOSE exec -T backend python manage.py createsuperuser \
    --noinput \
    --username admin \
    --email admin@example.com
# Then set the password:
$COMPOSE exec -T backend python manage.py shell -c \
    "from django.contrib.auth import get_user_model; \
     u = get_user_model().objects.get(username='admin'); \
     u.set_password('$ADMIN_PASSWORD'); u.save()"
```

Reset an existing admin's password:

```bash
$COMPOSE exec backend python manage.py changepassword <username>
```

## 5. Backup

The repo ships [`scripts/backup-db.sh`](../scripts/backup-db.sh) — prefer it
over hand-rolled commands. It pipes `pg_dump` through `gzip`, writes a
timestamped file under `./db-backups/`, and prunes anything older than 30
days.

```bash
./scripts/backup-db.sh
# → Backup written: ./db-backups/backup-20260430-143012.sql.gz (4.2M)
```

Override the destination or retention by setting env vars before invoking:

```bash
BACKUP_DIR=/var/backups/oms ./scripts/backup-db.sh
```

Manual equivalent (when the script is unavailable, or you want a
one-shot dump to stdout):

```bash
$COMPOSE exec -T db pg_dump -U "${POSTGRES_USER:-makerspace}" \
    "${POSTGRES_DB:-makerspace_inventory}" | gzip > backup.sql.gz
```

Also back up uploaded media — the database alone is not a full restore:

```bash
docker run --rm \
    -v openmakersuite_media_volume:/data:ro \
    -v "$(pwd)/db-backups:/out" \
    alpine tar czf "/out/media-$(date +%Y%m%d-%H%M%S).tar.gz" -C /data .
```

(Adjust the volume name if your project directory isn't `openmakersuite` —
`docker volume ls | grep media` will show the actual prefix.)

## 6. Restore

Use [`scripts/restore-db.sh`](../scripts/restore-db.sh). It prompts for
`YES` confirmation before dropping and recreating the database, then loads
the gzipped dump:

```bash
./scripts/restore-db.sh ./db-backups/backup-20260430-143012.sql.gz
# This will DROP and recreate the database. Type 'YES' to continue: YES
# → Restore complete
```

> **Stop the backend before restoring.** A live backend writing to the DB
> while you `DROP DATABASE` will spew 500s and may corrupt in-flight work.

```bash
$COMPOSE stop backend celery
./scripts/restore-db.sh <backup>
$COMPOSE start backend celery
```

Restore media:

```bash
$COMPOSE stop backend nginx
docker run --rm \
    -v openmakersuite_media_volume:/data \
    -v "$(pwd)/db-backups:/in:ro" \
    alpine sh -c 'cd /data && rm -rf ./* && tar xzf /in/media-<timestamp>.tar.gz'
$COMPOSE start nginx backend
```

After restore, confirm the schema matches the current code by running the
migration plan check from §2 — if pending migrations appear, the backup
predates your current build and you must `migrate` before serving traffic.

## 7. Logs

Tail everything (Ctrl-C to detach):

```bash
$COMPOSE logs -f
```

Single service, scrollback included:

```bash
$COMPOSE logs -f backend
$COMPOSE logs -f --tail=200 nginx
```

Time-bounded slice (useful for incident review):

```bash
$COMPOSE logs --since=1h backend
$COMPOSE logs --since=2026-04-30T14:00:00 --until=2026-04-30T15:00:00 backend
```

Log files on disk are capped via the `x-logging` block in
`docker-compose.prod.yml` (10 MB × 3 files per container, JSON driver), so
old output rotates out automatically — don't expect indefinite history.
For long-term retention ship to an external log store (Loki, CloudWatch,
etc.) outside the scope of this runbook.

The deploy script also writes a separate audit log to `./deploy.log` —
inspect it for migration history and deploy timing:

```bash
tail -100 deploy.log
```

## 8. Upgrades

Standard upgrade — pull new code, rebuild, run migrations, swap containers:

```bash
./deploy.sh
```

`deploy.sh` is the supported path because it (a) gates the deploy on the
pre-deploy migration check, (b) re-renders the EMQX bootstrap admins file
from `.env`, and (c) records the run to `deploy.log`. Use it unless it's
unavailable.

Manual upgrade (no migration gate — only when `deploy.sh` is broken):

```bash
git pull
export GIT_HASH=$(git rev-parse --short HEAD)
$COMPOSE build
$COMPOSE up -d db
$COMPOSE exec db pg_isready -U "${POSTGRES_USER:-makerspace}"
$COMPOSE run --rm --no-deps -e SKIP_DB_MIGRATIONS=1 backend \
    python manage.py showmigrations --plan --no-color
$COMPOSE run --rm --no-deps -e SKIP_DB_MIGRATIONS=1 backend \
    python manage.py migrate --noinput
$COMPOSE up -d
$COMPOSE exec backend python manage.py collectstatic --noinput
```

Restart a single service in place (zero rebuild — picks up env changes
only):

```bash
$COMPOSE restart backend
```

Recreate a single service from its current image (picks up image changes
without restarting the rest of the stack):

```bash
$COMPOSE up -d --no-deps --force-recreate backend
```

Verify health after the swap by running the
[smoke tests](SMOKE_TESTS.md).

## 9. Rollback

The application image is rebuilt every deploy from the checked-out git ref
and tagged via the `GIT_HASH` build arg (baked into both the backend Sentry
release and the frontend version banner). To roll back:

### 9a. Roll back the code (preferred)

```bash
# 1. Identify the last-known-good commit. The frontend banner and
#    backend Sentry releases both record the GIT_HASH, so pick from
#    git log or your release tracker.
git log --oneline -n 20

# 2. Check that ref out (use a tag or short SHA).
git checkout <good-sha>

# 3. Rebuild and redeploy. deploy.sh will refuse to migrate "backwards"
#    if the rolled-back code lacks a migration that's already applied.
./deploy.sh
```

If the rollback target predates a migration that has since been applied,
the rolled-back app will run against a "newer" schema. Django tolerates
this for additive migrations (new columns ignored) but not for destructive
ones (dropped columns the old code still reads). When unsure, restore the
DB from the backup taken immediately before the bad deploy (§6) **before**
starting the rolled-back backend.

### 9b. Roll back the database

When the bad deploy executed a destructive migration, code rollback alone
is not enough — restore the DB from the most recent backup taken before
the deploy:

```bash
$COMPOSE stop backend celery
./scripts/restore-db.sh ./db-backups/backup-<pre-deploy-timestamp>.sql.gz
git checkout <good-sha>
./deploy.sh
```

### 9c. Quick rollback by image tag (advanced)

If you push images to a registry under tags like `oms-backend:<git-hash>`,
you can swap tags without rebuilding:

```bash
# Edit docker-compose.prod.yml (or a compose override) so backend.image
# points at the previous tag, then:
$COMPOSE pull backend
$COMPOSE up -d --no-deps backend
```

The bundled compose file builds locally rather than pulling tagged images,
so this path applies only to deployments that have already wired up a
registry workflow.

### After any rollback

1. Run the [smoke tests](SMOKE_TESTS.md).
2. Confirm the frontend version banner shows the rolled-back `GIT_HASH`.
3. Tail `$COMPOSE logs -f backend` for at least one full health-check
   interval (30 s) to make sure the new container stays healthy.
4. File a bead capturing the cause of the bad deploy so the next attempt
   doesn't repeat it.

## 10. Workers and scheduled tasks

OpenMakerSuite splits the application across three process roles. The full
task inventory and per-task policy lives in
[`docs/CELERY_TASKS.md`](../docs/CELERY_TASKS.md); this section covers the
process-level deployment.

| Role | Service | Required? | Command | Purpose |
| --- | --- | --- | --- | --- |
| Web | `backend` | **Required** | `gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 4 --timeout 120` | Serves HTTP API and admin. Liveness/readiness probes hit `/api/health/livez/` and `/api/health/readyz/`. |
| Worker | `celery` | **Required** for any feature that uses `.delay()` (webhooks, reorder/donation/vendor/forgekey/location flows) | `celery -A config worker -l info` | Pulls tasks off the Redis broker. The bundled compose file runs a single replica on the default queue; scale with `$COMPOSE up -d --scale celery=N` if throughput requires it. |
| MQTT consumer | `mqtt_consumer` | **Required** if you ingest device telemetry via MQTT subscriptions (versus the EMQX → webhook bridge in `EMQX_WEBHOOK.md`) | `python manage.py mqtt_consumer` | Subscribes to `forgekey/+/status`, `forgekey/+/+/occupancy`, and `forgekey/+/ota/status`. Without this, MQTT-only ingest paths leave `ESP32Device.last_seen` stuck at the last register/photo-upload time and command ACKs never resolve. Run **exactly one replica** — two would race on the JWT-authenticated paho client and double-process every message. Pick **either** this consumer or the EMQX webhook bridge, not both, to avoid duplicate row inserts on `PowerMeterReading`. |
| Beat | *(not bundled)* | **Optional today** — only needed for the scheduled tasks in `CELERY_BEAT_SCHEDULE` (quarterly donor updates, daily vendor compliance digest) | `celery -A config beat -l info` | The bundled `docker-compose.prod.yml` does **not** define a beat service. If you need scheduled tasks, run beat as a sidecar (see below). |
| Flower | `flower` | Optional | `celery -A config flower --port=5555 --broker=$CELERY_BROKER_URL` | Live worker / queue / inflight task UI. **Do not expose port 5555 publicly** — it has no auth in the bundled config. Restrict via the host firewall or a private overlay network and tunnel through SSH for operator access. |

### Bringing up worker + flower

```bash
$COMPOSE up -d celery flower
$COMPOSE ps celery flower
```

### Adding a beat sidecar (when scheduled tasks must fire)

Add a service block to a compose override (e.g. `docker-compose.beat.yml`)
rather than editing `docker-compose.prod.yml` directly so the upstream
file stays clean:

```yaml
services:
  celery_beat:
    build:
      context: ./backend
      dockerfile: Dockerfile.prod
    command: celery -A config beat -l info
    env_file:
      - .env
    environment:
      - DATABASE_URL=postgresql://${POSTGRES_USER:-makerspace}:${POSTGRES_PASSWORD}@db:5432/${POSTGRES_DB:-makerspace_inventory}
      - REDIS_URL=redis://redis:6379/0
      - CELERY_BROKER_URL=redis://redis:6379/0
      - SKIP_DB_MIGRATIONS=1
    depends_on:
      redis:
        condition: service_healthy
    restart: unless-stopped
    networks:
      - app-network
```

Bring it up with both files merged:

```bash
docker compose -f docker-compose.prod.yml -f docker-compose.beat.yml up -d celery_beat
```

> **Run exactly one beat process per environment.** Two beat schedulers
> against the same broker double-fire every periodic task.

### Worker health check

The Celery worker container does not expose an HTTP probe; verify with
`celery inspect ping`, which round-trips a control message through the
broker:

```bash
$COMPOSE exec -T celery celery -A config inspect ping
# → -> celery@<container>: OK
#         pong
```

A non-zero exit or empty output means the worker can't reach Redis or has
deadlocked — restart with `$COMPOSE restart celery` and inspect logs.

### Verifying the beat schedule is loaded

```bash
$COMPOSE exec -T celery_beat celery -A config inspect scheduled
# Lists tasks queued by beat for future execution; empty until a tick fires.
```

To confirm the schedule entries themselves (independent of whether beat is
actually running):

```bash
$COMPOSE exec -T backend python manage.py shell -c "
from django.conf import settings
for name, entry in settings.CELERY_BEAT_SCHEDULE.items():
    print(name, '->', entry['task'], 'every', entry['schedule'], 's')
"
```

### Verification steps after deploy

After any deploy that touches worker, beat, or task code, run these
checks (also covered by [`SMOKE_TESTS.md`](SMOKE_TESTS.md) §6):

1. `$COMPOSE ps celery` — service is `running`.
2. `$COMPOSE exec -T celery celery -A config inspect ping` — returns `pong`.
3. Trigger a known task and confirm it lands as `SUCCESS` in
   `/admin/django_celery_results/taskresult/`. The lowest-impact option
   is `config.celery.debug_task`:
   ```bash
   $COMPOSE exec -T backend python manage.py shell -c \
       "from config.celery import debug_task; debug_task.delay()"
   ```
4. Tail `$COMPOSE logs -f celery` for at least one task lifecycle so that
   `Received task` and `succeeded` lines both appear.

### Recovering failed tasks

Failed tasks show `status=FAILURE` in
`/admin/django_celery_results/taskresult/` with the traceback and the
original args/kwargs. Webhook deliveries also accumulate per-row
`failure_count` / `last_error` on `/admin/reorder_queue/webhook/`. Replay
paths per task are documented in
[`docs/CELERY_TASKS.md`](../docs/CELERY_TASKS.md#failed-task-recovery-ac-31).
