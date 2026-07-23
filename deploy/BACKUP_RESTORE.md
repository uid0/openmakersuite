# Backup and Restore

Operational playbook for backing up and restoring an OpenMakerSuite deploy.
Covers every piece of state the application owns:

- PostgreSQL (the only system of record for application data)
- Media uploads (user-attached files, photos, generated PDFs)
- Environment config and secrets (`.env`, `oms-env`, `oms-secrets`)
- Kubernetes Persistent Volumes (when not using app-level dumps)
- External storage references (S3 buckets, when media is offloaded)

The same steps work for the bundled stacks in `docker-compose.prod.yml`,
`deploy/k8s/base/`, and `deploy/helm/openmakersuite/`. Pick the column that
matches how you deployed.

> **Restore order matters.** Always restore in this order: secrets → config →
> PostgreSQL → media. Backend Pods/containers should be stopped (or replicas
> scaled to zero) for the duration of the restore. Bringing up workers against
> a partially restored database will write inconsistent rows that the dump
> can't recover from.

## What needs backing up

| State                  | Source                                      | Restore tool        | Lose-able? |
|------------------------|---------------------------------------------|---------------------|------------|
| PostgreSQL data        | `oms-postgresql` StatefulSet / `db` service | `pg_restore`        | No — system of record |
| Media uploads          | `oms-media` PVC / `media_volume`            | `tar` / `rsync`     | No — user files |
| Static assets          | `oms-static` PVC / `static_volume`          | `collectstatic`     | Yes — regenerated on deploy |
| Frontend bundle        | `frontend_build` volume / frontend image    | image pull          | Yes — baked into image |
| Application secrets    | `.env` / `oms-secrets`                      | re-apply manifest   | No — losing `SECRET_KEY` invalidates sessions |
| Application config     | `.env` / `oms-env` ConfigMap                | re-apply manifest   | Yes (re-derivable) but losing it means a long redeploy |
| Redis state            | `oms-redis` Pod / `redis` service           | none — ephemeral    | Yes — Celery queue, cache only |
| EMQX state             | `emqx_data` volume                          | re-bootstrap        | Mostly — bootstrap users render from `EMQX_DASHBOARD_PASSWORD` |

Redis and EMQX hold operational state that is regenerated on the next message
or session, so they are intentionally excluded from the backup paths below.
Back them up only if you have a specific reason (e.g. preserving long Celery
ETA tasks across a maintenance window).

## Scripts (Docker Compose path)

For the bundled Compose stack, the runbook commands below are wrapped by
shell scripts in `scripts/`. The scripts default to safe choices and are the
supported way to run an unattended drill (`scripts/restore-drill.sh`) — the
raw `docker compose exec` recipes in the sections that follow are kept for
operators on Kubernetes, managed Postgres, or anyone who wants to inspect
exactly what the wrapper is doing.

| Script                          | Purpose                                                                                                              |
|---------------------------------|----------------------------------------------------------------------------------------------------------------------|
| `scripts/backup-db.sh`          | Dump PostgreSQL via `docker compose exec db pg_dump`. Plain SQL `.sql.gz` by default; `--format=custom` writes `-Fc`. |
| `scripts/restore-db.sh`         | Restore from a `.sql.gz` or `.dump`. Auto-detects format. `--force` skips the interactive `YES` prompt.              |
| `scripts/backup-media.sh`       | Stream the `media_volume` contents into a gzipped tar via the backend container.                                     |
| `scripts/restore-media.sh`      | Wipe the in-container media tree and unpack an archive back into it. Stops workers around the restore.               |
| `scripts/backup-config.sh`      | Archive the live `.env` (+ optional `docker-compose.override.yml`, EMQX bootstrap file) with 0600 permissions.       |
| `scripts/smoke.sh`              | Scripted form of `deploy/SMOKE_TESTS.md` post-restore checks; `--json` for evidence capture.                         |
| `scripts/restore-drill.sh`      | Orchestrator that captures fresh dumps, restores them, waits for the backend, and runs `smoke.sh --json`.            |

Every script supports `--help` and obeys these overrides:

| Variable          | Effect                                              | Default                  |
|-------------------|-----------------------------------------------------|--------------------------|
| `COMPOSE_FILE`    | Compose file the script execs against              | `docker-compose.prod.yml`|
| `BACKUP_DIR`      | Where backups land (script-specific subdirectory)  | `./db-backups`, `./media-backups`, `./config-backups` |
| `POSTGRES_USER`   | Postgres role                                       | `makerspace`             |
| `POSTGRES_DB`     | Database name                                       | `makerspace_inventory`   |
| `RETENTION_DAYS`  | Days of history to keep (0 disables pruning)        | `30`                     |

`scripts/restore-drill.sh --dry-run` parses every dependency without touching
the live stack — use it to confirm a worktree is ready for an upcoming drill
window before the maintenance start.

## 1. PostgreSQL — Docker Compose

The bundled `db` service stores its data in the named volume `postgres_data`
and exposes itself only on the compose network. Use `pg_dump` inside the
container — never copy the raw `pgdata` directory while the server is
running.

### Back up

```bash
# Full custom-format dump (compressed, supports parallel restore)
docker compose -f docker-compose.prod.yml exec -T db \
  pg_dump -U "${POSTGRES_USER:-makerspace}" \
          -d "${POSTGRES_DB:-makerspace_inventory}" \
          -Fc \
  > "oms-$(date -u +%Y%m%dT%H%M%SZ).dump"
```

- `-Fc` writes PostgreSQL's custom format. It compresses well and supports
  `pg_restore --jobs=N` for fast parallel restore.
- `-T` (no TTY) keeps the dump bytestream clean; without it `docker compose`
  will inject control characters that corrupt the file.
- Store the dump somewhere off the host — these are tiny but losing them
  loses every order, asset, membership, and audit row.

Verify the dump opens:

```bash
pg_restore -l "oms-*.dump" | head -20
```

A valid dump prints a TOC starting with `; Archive created at ...`. An empty
or truncated file errors out on this command.

### Restore

```bash
# Stop workers so they don't write while we're loading
docker compose -f docker-compose.prod.yml stop backend celery flower

# Drop and recreate the database (DESTRUCTIVE — only on the target host)
docker compose -f docker-compose.prod.yml exec -T db \
  psql -U "${POSTGRES_USER:-makerspace}" -d postgres -c \
  "DROP DATABASE IF EXISTS ${POSTGRES_DB:-makerspace_inventory}; \
   CREATE DATABASE ${POSTGRES_DB:-makerspace_inventory} OWNER ${POSTGRES_USER:-makerspace};"

# Load the dump
docker compose -f docker-compose.prod.yml exec -T db \
  pg_restore -U "${POSTGRES_USER:-makerspace}" \
             -d "${POSTGRES_DB:-makerspace_inventory}" \
             --no-owner --clean --if-exists \
  < oms-20260430T053000Z.dump

# Bring workers back up
docker compose -f docker-compose.prod.yml start backend celery flower
```

`--clean --if-exists` makes the restore idempotent against a partially loaded
database. `--no-owner` skips reassigning object owners, which matters when
the source dump was taken under a different role name than the target.

## 2. PostgreSQL — Kubernetes (raw manifests or Helm)

`deploy/k8s/base/postgres-statefulset.yaml` runs PostgreSQL with a
`volumeClaimTemplates` PVC named `data-oms-postgresql-0`. The Helm chart
keeps the same StatefulSet name (`oms-postgresql`) when
`postgresql.enabled=true`. For external/managed databases (Cloud SQL, RDS,
Crunchy, etc.) skip to [Managed databases](#managed-databases).

### Back up

```bash
NS=openmakersuite      # change if you deployed elsewhere
DB_USER=makerspace
DB_NAME=makerspace_inventory

kubectl exec -n "$NS" -i statefulset/oms-postgresql -- \
  pg_dump -U "$DB_USER" -d "$DB_NAME" -Fc \
  > "oms-$(date -u +%Y%m%dT%H%M%SZ).dump"
```

For unattended backups, run the same `pg_dump` from a `CronJob` that mounts
a `PersistentVolumeClaim` you can rsync off the cluster, or from an
out-of-cluster job using a Service of type `ClusterIP` + `kubectl
port-forward`. The bundled manifests do not ship a CronJob — wire one up to
your usual artifact storage (S3, GCS, off-site rsync target).

### Restore

```bash
NS=openmakersuite

# Scale workers to zero so nothing writes mid-restore
kubectl -n "$NS" scale deploy/oms-backend --replicas=0
kubectl -n "$NS" scale deploy/oms-celery  --replicas=0
kubectl -n "$NS" scale deploy/oms-flower  --replicas=0 || true

# Recreate the database
kubectl exec -n "$NS" -i statefulset/oms-postgresql -- \
  psql -U makerspace -d postgres -c \
  "DROP DATABASE IF EXISTS makerspace_inventory; \
   CREATE DATABASE makerspace_inventory OWNER makerspace;"

# Load the dump
kubectl exec -n "$NS" -i statefulset/oms-postgresql -- \
  pg_restore -U makerspace -d makerspace_inventory \
             --no-owner --clean --if-exists \
  < oms-20260430T053000Z.dump

# Bring workers back
kubectl -n "$NS" scale deploy/oms-backend --replicas=1
kubectl -n "$NS" scale deploy/oms-celery  --replicas=1
kubectl -n "$NS" scale deploy/oms-flower  --replicas=1 || true
```

If the StatefulSet itself is gone (e.g. the namespace was deleted), apply the
manifests again first — the StatefulSet must exist and its Pod must be
`Ready` before `kubectl exec` can stream the dump in.

### Managed databases

When `postgresql.enabled=false` and you point `externalDatabase.url` at a
managed Postgres, ownership of backup/restore moves to that provider. Use
the provider's snapshot tooling (RDS automated snapshots, Cloud SQL
on-demand backups, etc.) and confirm:

- Point-in-time recovery (PITR) is on, with a retention window long enough
  to cover your detection-to-recovery time.
- A test restore has been performed at least once into a non-production
  database — provider snapshots that have never been restored are not yet
  proven backups.
- The resulting database is reachable from the cluster on the same hostname
  and port that `DATABASE_URL` resolves to today; otherwise update the
  Secret and roll the backend.

## 3. Media uploads

Django writes to `MEDIA_ROOT=/app/media` (set in `backend/config/settings.py`).
That path is mounted from `media_volume` in compose and from PVC `oms-media`
in Kubernetes. Lose this and you lose every uploaded photo, attachment, and
generated PDF — `pg_restore` cannot bring them back.

### Docker Compose

```bash
# Back up — stream a tar of the media volume directly to a file on the host
docker run --rm \
  -v oms_media_volume:/media:ro \
  -v "$PWD":/backup \
  alpine \
  tar -C /media -czf "/backup/oms-media-$(date -u +%Y%m%dT%H%M%SZ).tgz" .

# Restore — wipe and reload (DESTRUCTIVE)
docker compose -f docker-compose.prod.yml stop backend celery
docker run --rm \
  -v oms_media_volume:/media \
  -v "$PWD":/backup \
  alpine \
  sh -c 'rm -rf /media/* && tar -C /media -xzf /backup/oms-media-20260430T053000Z.tgz'
docker compose -f docker-compose.prod.yml start backend celery
```

The volume name in the compose project is the project prefix plus
`media_volume`; `docker volume ls` shows the exact name (`oms_media_volume`
above is illustrative — substitute yours). `:ro` on backup is intentional —
it prevents a stray write while the tar is streaming.

### Kubernetes

Media-PVC backups in Kubernetes have two shapes depending on the
`StorageClass`:

**Snapshots (preferred when supported).** If your `StorageClass` exposes
`VolumeSnapshotClass` (most cloud providers, longhorn, openebs-jiva, etc.),
take a `VolumeSnapshot` of `oms-media`:

```yaml
apiVersion: snapshot.storage.k8s.io/v1
kind: VolumeSnapshot
metadata:
  name: oms-media-20260430
  namespace: openmakersuite
spec:
  volumeSnapshotClassName: csi-snapclass   # whatever your class is called
  source:
    persistentVolumeClaimName: oms-media
```

To restore: create a new PVC with `dataSource` pointing at the snapshot,
then patch the backend Deployment to reference the new claim, or rename it
back to `oms-media` after deleting the original.

**Tar streams (works on any cluster).** When snapshots aren't available,
copy the data out through a sidecar Pod:

```bash
NS=openmakersuite

# Back up
kubectl exec -n "$NS" deploy/oms-backend -c backend -- \
  tar -C /app/media -cf - . \
  | gzip > "oms-media-$(date -u +%Y%m%dT%H%M%SZ).tgz"

# Restore
kubectl -n "$NS" scale deploy/oms-backend --replicas=0
kubectl -n "$NS" scale deploy/oms-celery  --replicas=0

# Spin up a temporary Pod that mounts the same PVC and unpack into it
kubectl -n "$NS" run media-restore --rm -i --restart=Never \
  --image=alpine:3.20 \
  --overrides='{"spec":{"containers":[{"name":"r","image":"alpine:3.20","stdin":true,"command":["sh","-c","rm -rf /media/* && tar -C /media -xzf -"],"volumeMounts":[{"name":"media","mountPath":"/media"}]}],"volumes":[{"name":"media","persistentVolumeClaim":{"claimName":"oms-media"}}]}}' \
  < oms-media-20260430T053000Z.tgz

kubectl -n "$NS" scale deploy/oms-backend --replicas=1
kubectl -n "$NS" scale deploy/oms-celery  --replicas=1
```

Don't try to `kubectl cp` a multi-gigabyte media tree — it buffers in
memory and frequently truncates. Prefer the streaming `tar | gzip` form
above.

## 4. Environment config and secrets

Application config has two flavours: things that are safe to keep in
version control (`oms-env` ConfigMap, `.env.example`) and things that are
not (`oms-secrets`, real `.env`).

### Docker Compose

The host's `.env` file is the source of truth. Back it up the same way you
back up any sensitive on-host config:

```bash
# Snapshot the live .env into a sealed location
sudo install -m 0600 .env "/var/backups/oms/.env-$(date -u +%Y%m%dT%H%M%SZ)"
```

Anything you regenerate (image tags pinned in `docker-compose.prod.yml`,
nginx config rendered from the repo) is already in git — back up only the
secrets that are not.

### Kubernetes

```bash
NS=openmakersuite

# Save current state of every Secret and the ConfigMap, including the
# resourceVersion so you can spot drift on restore
kubectl -n "$NS" get configmap oms-env -o yaml > oms-env.yaml
kubectl -n "$NS" get secret oms-secrets oms-postgresql oms-database -o yaml \
  > oms-secrets.yaml
```

`oms-secrets.yaml` is a plaintext dump of every secret value — treat it
exactly like `.env`: 0600 permissions, encrypted at rest, off-host.

If you use SealedSecrets / External Secrets / Vault, the upstream system is
the backup. In that case, back up the upstream (the `SealedSecret` CR is
already in git, the ESO `SecretStore` config is already in git) and skip
this step. What you must verify is that the upstream is itself backed up,
not just trusted.

To restore:

```bash
kubectl apply -f oms-env.yaml
kubectl apply -f oms-secrets.yaml
# Roll Pods so they pick up the new Secret / ConfigMap values
kubectl -n "$NS" rollout restart deploy/oms-backend deploy/oms-celery
```

> **`SECRET_KEY` is sticky.** The Helm chart annotates `oms-secrets` with
> `helm.sh/resource-policy: keep` precisely so that `helm uninstall` does
> not drop it. If you rotate `SECRET_KEY` you invalidate every active
> session and every signed token (password reset links, email verification,
> etc.). Restore the original key unless you intend to log everyone out.

## 5. Kubernetes Persistent Volumes

For most failures, application-level dumps (sections 1 and 3) are the right
recovery path — they're portable across StorageClasses and clusters, and
they exclude orphan data. Use raw PV/PVC backup only when:

- You're cloning the entire namespace into a new cluster.
- Your `StorageClass` supports `VolumeSnapshot` and you want a near-instant
  RPO.
- You need to capture state outside Postgres/media (e.g. a modified EMQX
  bootstrap config that hasn't been baked back into the chart).

### Snapshot-based

Take a `VolumeSnapshot` of every PVC in the namespace:

```bash
NS=openmakersuite
TS=$(date -u +%Y%m%d-%H%M)

for pvc in $(kubectl -n "$NS" get pvc -o name); do
  name=$(basename "$pvc")
  cat <<EOF | kubectl apply -f -
apiVersion: snapshot.storage.k8s.io/v1
kind: VolumeSnapshot
metadata:
  name: ${name}-${TS}
  namespace: ${NS}
spec:
  volumeSnapshotClassName: csi-snapclass
  source:
    persistentVolumeClaimName: ${name}
EOF
done
```

Restore by creating new PVCs with `dataSource` pointing at each snapshot,
then either renaming them back to the original PVC names (after deleting
the originals) or patching the workload to reference the new claims.

### Velero (cluster-wide)

If you're already running [Velero](https://velero.io), back up the whole
namespace:

```bash
velero backup create oms-$(date -u +%Y%m%d) \
  --include-namespaces openmakersuite \
  --snapshot-volumes
velero restore create --from-backup oms-20260430
```

Velero captures both the manifest state (Deployments, Services, Secrets,
ConfigMaps) and PV snapshots in one operation, which is why it's the
recommended path for full-namespace disaster recovery if you can deploy it.
The bundled manifests do not install Velero — wire it up separately.

## 6. External storage references

`backend/env.production.example` documents an optional S3 path:

```
# AWS_ACCESS_KEY_ID=...
# AWS_SECRET_ACCESS_KEY=...
# AWS_STORAGE_BUCKET_NAME=...
# AWS_S3_REGION_NAME=us-east-1
```

When media is offloaded to S3 (or any S3-compatible store like MinIO, R2,
GCS via Interop), section 3 no longer applies — the `oms-media` PVC is
empty and the bucket is the source of truth. In that mode:

- **Versioning** — turn on object versioning on the bucket. Without it,
  any overwrite or delete is unrecoverable.
- **Lifecycle policy** — keep noncurrent versions long enough to cover
  your detection window (30+ days is typical).
- **Cross-region replication** — for production, replicate to a second
  region/account. A single-region snapshot is not a backup against
  account-level loss.
- **Restore drill** — use the provider's bucket-restore tooling (S3 Batch
  Operations, `aws s3 sync` with `--exclude '*' --include` filters) and
  confirm a sample of objects restores into a fresh bucket. Untested
  versioning is not a proven backup.

The application reads `AWS_STORAGE_BUCKET_NAME` and credentials at startup;
to point at the restored bucket, update those values in `oms-secrets` (or
`.env`) and roll the backend.

## 7. Post-restore verification (required)

Every restore — whether a real disaster recovery, an upgrade rollback, or
a quarterly drill — must be followed by the post-restore checklist below.
A backup that "applied without errors" is not a verified restore until
each of these is green. Map directly to the [smoke tests](SMOKE_TESTS.md):

| Surface              | Smoke test                | What a restore proves                                                        |
|----------------------|---------------------------|------------------------------------------------------------------------------|
| **Frontend SPA**     | §1 in SMOKE_TESTS.md      | The shell loads, branding is restored, frontend bundle is intact.            |
| **Backend liveness** | §2 dashboard `/health/`   | Backend connects to the restored Postgres, migrations are at the dump's level. |
| **Admin login**      | §4 admin login            | `SECRET_KEY` survived (sessions valid), restored user table is queryable.    |
| **Public QR scan**   | §9 public QR / kiosk      | Item lookups resolve against restored inventory data. *AC-25 gate.*          |
| **Media access**     | §7 static + media         | Restored `/media/` files serve over HTTP without 5xx; uploaded images load.  |
| **Database**         | §5 pending migrations     | `showmigrations` reports zero pending — schema matches the running code.     |
| **Workers**          | §6 Celery `inspect ping`  | Worker reaches the restored broker (Redis state is reproducible — only check that the worker is healthy). |

Run the table top-to-bottom. If any row fails, the restore is incomplete:

- Frontend / admin / public QR / media red → restore probably ran but the
  application can't see it. Check that backend Pods/containers were
  scaled back up after the restore (§§ 1–3 above scale them to zero) and
  that the Pod is mounting the same `media` claim/volume the restore
  wrote into.
- Database migrations red → backup predates the running code. Run
  `python manage.py migrate --noinput` *before* re-enabling traffic.
- Public QR returns 5xx → restored inventory rows are missing or the
  schema is drifted. Compare `pg_restore` output for table-level errors
  and reload the dump if needed.

Document the result in the deploy log: backup file, restore start/end
time, which smoke checks passed, and (for drills) the namespace teardown
timestamp.

## 8. Restore drill

A backup that has never been restored is a hypothesis, not a backup.

### 8.1 Automated DB drill (CI-enforced)

The Docker Compose half of the drill runs on every deploy-touching PR via
the `Prod Stack Smoke (livez within 120s)` job in `.github/workflows/ci.yml`.
After the stack boots and `/api/health/livez/` answers 200, the job
invokes [`scripts/restore-drill.sh --skip-media --skip-smoke`](../scripts/restore-drill.sh),
which takes a fresh `pg_dump` of the live `db` service, restores it
back into the same database, and then re-probes `/api/health/livez/`
from inside the backend container so the drill proves the restored stack
still serves requests — not just that `pg_restore` exited 0.

The companion `Deploy Artifacts` job runs `scripts/restore-drill.sh --dry-run`
under `bash -n`–level lint plus a parse-and-verify pass on every dependency
script. That step catches regressions on the operator-facing drill in
environments where Docker is not available.

`--skip-media` is set because the CI compose stack ships an empty
`oms_media_volume` by default; `--skip-smoke` because `smoke.sh` expects
the frontend/nginx surfaces, which the prod-stack-smoke job does not
boot. Media + config restore stay on the manual quarterly cadence below.

Run the same drill locally against a disposable stack:

```bash
# Bring up just the slice the drill exercises
docker compose -f docker-compose.prod.yml --env-file .env up -d --build \
    db redis backend

# Drive the drill (uses the running compose db; tears down its own evidence
# on success). POSTGRES_USER / POSTGRES_DB must match your .env.
POSTGRES_USER=oms POSTGRES_DB=oms bash scripts/restore-drill.sh --skip-media --skip-smoke
```

### 8.2 Manual quarterly drill (Kubernetes / full-stack)

The DB-only CI drill does not exercise media restore, config restore, or
the multi-namespace recovery shape. At least once per quarter, run the
full drill on Kubernetes:

1. Stand up a parallel namespace (`openmakersuite-dr` is the convention).
2. Restore PostgreSQL into it from the most recent dump.
3. Restore media into it.
4. Apply config + secrets manifests pointed at the DR namespace.
5. Run the §7 post-restore verification checklist (every row must pass)
   against `kubectl port-forward` of the DR backend Service.
6. If every smoke check passes, the backup chain is healthy. Tear the DR
   namespace down (`kubectl delete ns openmakersuite-dr`).

If any step fails, treat it as a real production incident — the fact that
it happened in DR doesn't change that the *next* real outage will hit the
same wall. Fix the cause before declaring the drill complete.
