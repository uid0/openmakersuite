# Upgrade and Rollback Runbook

Production upgrade and rollback flow for OpenMakerSuite. Covers the three
shipped deployment paths — Docker Compose, raw Kubernetes manifests under
`deploy/k8s/`, and the Helm chart under `deploy/helm/openmakersuite/`.

The flow is the same for all three: **back up data → pick the new version →
run migrations → verify health → roll back if anything fails**. The commands
differ; the checklist does not.

> **When does this apply?** Any time you change a backend or frontend image
> tag, change the Helm chart version, or apply schema-changing manifests on a
> cluster that already has data. Fresh installs follow `docs/DEPLOYMENT.MD`
> instead.

## 0. Before you start

- You have a clean working copy of the repo at the commit that builds the
  target image tag (so the tag, the migrations, and the manifests all agree).
- The smoke-test runbook in [`SMOKE_TESTS.md`](./SMOKE_TESTS.md) is open in
  another window — you will run it twice (once before, once after).
- You know the **previous good version** — the image tag (and Helm revision,
  if applicable) you can roll back to. Write it down before you change
  anything; the rollback steps below assume you have it.
- For Docker Compose: you have shell access on the host running the stack and
  permission to run `docker compose`.
- For Kubernetes/Helm: your `kubectl` context points at the right cluster
  (`kubectl config current-context`) and the right namespace
  (`kubectl config view --minify --output 'jsonpath={..namespace}'`). The
  examples below use namespace `openmakersuite` and Helm release `oms` — adjust
  to match your install.

```bash
# Capture the rollback target up front:
export PREV_TAG=1.2.2          # previous backend/frontend tag
export NEW_TAG=1.2.3           # tag you are upgrading to
helm -n openmakersuite history oms   # Helm: note the current REVISION
```

## 1. Back up data

The two stateful pieces are PostgreSQL and the `media/` volume (uploaded
files). Static assets and the Redis queue are reproducible from the image and
do not need backups.

### Docker Compose

```bash
# Database — full pg_dump to a timestamped file on the host.
docker compose -f docker-compose.prod.yml exec -T db \
    pg_dump -U "${POSTGRES_USER:-makerspace}" \
            -d "${POSTGRES_DB:-makerspace_inventory}" \
            --format=custom --no-owner --no-acl \
    > "backup-db-$(date -u +%Y%m%dT%H%M%SZ).dump"

# Media volume — tar the named volume contents.
docker run --rm \
    -v $(docker compose -f docker-compose.prod.yml config --volumes | grep media):/media:ro \
    -v "$PWD":/out \
    alpine tar czf "/out/backup-media-$(date -u +%Y%m%dT%H%M%SZ).tgz" -C / media
```

Verify the dump is non-zero and readable (`pg_restore --list backup-db-*.dump`
should print the table-of-contents).

### Raw Kubernetes / Helm

Use the bundled `postgres-statefulset` (or the managed Postgres your
`externalDatabase.url` points at) and the `media` PVC.

```bash
# Bundled postgres — exec pg_dump and stream the dump back to your laptop.
kubectl -n openmakersuite exec -it sts/oms-postgresql -- \
    pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-acl \
    > "backup-db-$(date -u +%Y%m%dT%H%M%SZ).dump"

# Managed postgres — run pg_dump from a host that has DATABASE_URL.
PGPASSWORD=… pg_dump "$DATABASE_URL" --format=custom --no-owner --no-acl \
    > "backup-db-$(date -u +%Y%m%dT%H%M%SZ).dump"

# Media PVC — tar it from a temporary pod that mounts the same claim.
kubectl -n openmakersuite run media-backup --rm -it --restart=Never \
    --image=alpine \
    --overrides='{"spec":{"containers":[{"name":"media-backup","image":"alpine","command":["sh","-c","tar czf - -C / media"],"volumeMounts":[{"name":"media","mountPath":"/media"}]}],"volumes":[{"name":"media","persistentVolumeClaim":{"claimName":"oms-media"}}]}}' \
    > "backup-media-$(date -u +%Y%m%dT%H%M%SZ).tgz"
```

Store both files outside the cluster/host before continuing. **Do not proceed
to step 2 until you have a verified backup** — the rollback path for a
mis-applied destructive migration is "restore from backup," and that has to
exist before you need it.

## 2. Select the new version

Pick the tag, then update the source of truth for your deploy path. Pin to an
**immutable tag** (a release tag like `1.2.3`, a commit SHA tag like
`sha-7f743a2`, or an OCI digest) — never `latest` — so the rollback target is
unambiguous.

### Docker Compose

Edit the relevant service stanzas in `docker-compose.prod.yml` (or your
override file) and bump the image tags. Pull the new images before stopping
anything:

```bash
docker compose -f docker-compose.prod.yml pull backend frontend celery
```

### Raw Kubernetes

Update the `image:` field in each Deployment under `deploy/k8s/base/` (or in
the kustomize overlay you use) and the migrations Job:

```bash
# Render the new manifests but do not apply yet — review the diff:
kubectl diff -k deploy/k8s/base/
```

If the diff looks right, leave it staged. The actual `kubectl apply` happens
in step 3.

### Helm

Pick a chart version *and* image tag. The chart version (`Chart.yaml:version`)
controls the templates and defaults; the image tags control the running
binaries. They are independent — bumping one does not require bumping the
other.

```bash
# Show what will change before doing it (helm-diff plugin recommended):
helm -n openmakersuite diff upgrade oms ./deploy/helm/openmakersuite \
    --set backend.image.tag="$NEW_TAG" \
    --set frontend.image.tag="$NEW_TAG"
```

If your registry serves the chart over OCI (see CI's
`publish-helm-chart-oci` job), pull and pin a chart version too:

```bash
helm pull oci://ghcr.io/<org>/charts/openmakersuite --version 0.2.0
```

## 3. Run migrations and roll out the new version

The pattern is the same everywhere: **migrations first, then traffic.** The
backend must never serve requests against an un-migrated schema (see the gate
documented in `docs/DEPLOYMENT.MD` and bead `oms-p2x`).

### Docker Compose

`./deploy.sh` already enforces "migrations before backend traffic." Just run
it:

```bash
./deploy.sh
tail -50 deploy.log    # confirm `migrate` ran cleanly with no errors
```

If you prefer manual control, the equivalent is:

```bash
docker compose -f docker-compose.prod.yml up -d db redis
docker compose -f docker-compose.prod.yml run --rm backend \
    python manage.py migrate --noinput
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml exec -T backend \
    python manage.py collectstatic --noinput
```

### Raw Kubernetes

The `oms-migrate` Job is a one-shot `manage.py migrate`. Re-apply it ahead of
the Deployments:

```bash
# Recreate the migrations Job under a new name so kubectl keeps history.
kubectl -n openmakersuite create -f deploy/k8s/base/migrations-job.yaml \
    --dry-run=client -o yaml \
    | sed "s/oms-migrate/oms-migrate-$(date -u +%Y%m%d%H%M%S)/" \
    | kubectl apply -f -

# Wait for it to finish before rolling Deployments.
kubectl -n openmakersuite wait --for=condition=complete --timeout=10m \
    job -l app.kubernetes.io/component=migrations

# Now apply the rest (new Deployment specs / image tags).
kubectl apply -k deploy/k8s/base/
kubectl -n openmakersuite rollout status deploy/oms-backend --timeout=5m
kubectl -n openmakersuite rollout status deploy/oms-frontend --timeout=5m
kubectl -n openmakersuite rollout status deploy/oms-celery --timeout=5m
```

If `kubectl wait` on the migrations Job times out or returns non-zero: stop.
Do not roll the Deployments. Read the Job logs (`kubectl -n openmakersuite
logs job/oms-migrate-<ts>`) and follow the rollback flow in §5 — the existing
Deployments are still running on `$PREV_TAG`, which is the safe state.

### Helm

`migrations.useHooks=true` (the chart default) wires migrations as a
`pre-upgrade` hook, so a single `helm upgrade` does the right thing in the
right order: render → run migrations → roll Deployments. If the hook Job
fails, Helm aborts the upgrade *before* touching the Deployments, and the
release stays on the previous revision.

```bash
helm -n openmakersuite upgrade oms ./deploy/helm/openmakersuite \
    --set backend.image.tag="$NEW_TAG" \
    --set frontend.image.tag="$NEW_TAG" \
    --wait --timeout 10m

helm -n openmakersuite history oms | tail -5
```

`--wait` blocks until every Deployment is ready, so a failed rollout will
fail the `helm upgrade` itself rather than silently leaving half a cluster
upgraded.

## 4. Verify health

Run the eight checks in [`SMOKE_TESTS.md`](./SMOKE_TESTS.md) end-to-end. The
release is healthy only if **all eight pass** — partial passes are not "good
enough."

Quick triage targets if the smoke tests are red:

```bash
# Compose
docker compose -f docker-compose.prod.yml logs --tail=200 backend
docker compose -f docker-compose.prod.yml ps

# Kubernetes / Helm
kubectl -n openmakersuite get pods
kubectl -n openmakersuite logs -l app.kubernetes.io/component=backend --tail=200
kubectl -n openmakersuite describe pod -l app.kubernetes.io/component=backend
```

If everything is green, the upgrade is done — record the new tag and Helm
revision somewhere durable (release notes, deploy log, ops channel). Keep the
backup files from §1 for at least one full backup cycle.

If any check is red and you cannot fix it forward in minutes, **roll back
now** — do not leave the system half-upgraded.

## 5. Roll back

The rollback flow inverts step 3. The exact commands depend on whether the
failure is forward-compatible (only the new image is bad — old image still
works against the migrated schema) or destructive (the new migrations made
the schema incompatible with the old image).

### Forward-compatible: re-pin the old image

This covers the common case — bug in the new release, schema is unchanged or
backward-compatible. No backup restore needed.

**Docker Compose:**

```bash
# Edit docker-compose.prod.yml back to $PREV_TAG (or `git checkout` the file
# if the change is in the repo), then redeploy.
git checkout HEAD~1 -- docker-compose.prod.yml   # if the bump was committed
./deploy.sh
```

**Raw Kubernetes:** roll the Deployments back to the previous ReplicaSet —
this is what `kubectl` revision history is for, and it does not require
re-applying YAML:

```bash
kubectl -n openmakersuite rollout undo deploy/oms-backend
kubectl -n openmakersuite rollout undo deploy/oms-frontend
kubectl -n openmakersuite rollout undo deploy/oms-celery
kubectl -n openmakersuite rollout status deploy/oms-backend --timeout=5m
```

**Helm:**

```bash
helm -n openmakersuite history oms              # find the last good REVISION
helm -n openmakersuite rollback oms <REVISION> --wait --timeout 10m
```

After any of these, re-run the §4 smoke tests. If they pass on the rollback
target, the system is back on a known-good version. File a bead with the
failed-tag postmortem before re-attempting the upgrade.

### Destructive: schema is incompatible with the old image

This applies when the new release shipped migrations that drop columns,
rename tables, or otherwise make the schema unreadable to `$PREV_TAG`. The
image-only rollback above will start the old backend, which will then 500
against the new schema.

The recovery is **restore from the §1 backup**, then re-pin the old image.

```bash
# Compose — restore the dump into the running db container.
cat backup-db-<TS>.dump | docker compose -f docker-compose.prod.yml exec -T db \
    pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner

# Kubernetes — restore via the postgres pod.
cat backup-db-<TS>.dump | kubectl -n openmakersuite exec -i sts/oms-postgresql -- \
    pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner
```

Then run the forward-compatible image rollback above. Restore the media
tarball the same way (untar into the volume / PVC) only if the new release
mutated existing files; new uploads can usually be left in place.

> Restoring overwrites data committed since the §1 backup. That is the cost
> of a destructive rollback — the alternative is fixing forward. Decide which
> is cheaper before you start, not midway through.

## 6. Post-rollback or post-success cleanup

- Note the final state in your deploy log: which tag is running, which Helm
  revision is current, and whether you rolled back.
- If you rolled back, open a bead capturing the failure mode (which smoke
  test failed, what the logs said) so the next attempt is informed.
- If the upgrade succeeded, retain the §1 backup files until the next
  successful backup cycle, then they can be rotated out per your normal
  retention policy.

## Cross-references

- [`docs/DEPLOYMENT.MD`](../docs/DEPLOYMENT.MD) — first-time install and the
  Compose-based migration gate (`./deploy.sh`).
- [`SMOKE_TESTS.md`](./SMOKE_TESTS.md) — eight post-deploy health checks
  referenced in §4 and after every rollback.
- [`PREREQUISITES.md`](./PREREQUISITES.md) — host tooling; `pg_dump`,
  `pg_restore`, and `kubectl` come from there.
- [`helm/openmakersuite/README.md`](./helm/openmakersuite/README.md) — full
  list of values, including `migrations.useHooks` and `secrets.existingSecret`
  which interact with the upgrade flow.
- [`k8s/README.md`](./k8s/README.md) — raw manifest topology referenced in
  the Kubernetes-specific commands above.
