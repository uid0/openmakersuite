# Deploy plan: stop rebuilding containers on the production host

## Current state

CI already builds and pushes multi-arch Docker images for every push
to `main` and every tag:

- Workflow: `.github/workflows/docker-build.yml`
- Registry: `ghcr.io/uid0/openmakersuite/{backend,frontend,nginx}`
- Tags: `sha-<short-sha>`, `latest`, and any matching git tag
- Architectures: `linux/amd64` + `linux/arm64`

The deploy script (`deploy.sh`) on the production host then does:

```bash
$COMPOSE build --no-cache frontend
$COMPOSE build --no-cache backend
```

This **rebuilds the same images CI just built** — same base, same
source tree, same Dockerfile. On a Hetzner CCX22 with 4 vCPU and a
warm Docker layer cache the rebuild costs ~3–5 minutes (cold cache
after a `docker system prune`: ~8–12 minutes). It also doubles
toolchain attack surface (the prod host has the full Python build
toolchain + npm + node-gyp), and means a deploy can produce a
*different* binary from what CI tested if the host has a divergent
local layer cache.

## Proposal

Replace the host-side `docker compose build` with a pull of the
CI-built image, pinned to the git SHA the deploy is actually
targeting. The prod host becomes a pure runtime; the only build
on it is whatever transient `prep` containers do (e.g. the frontend
build that populates the `frontend_build` named volume).

### Three changes

1. **`docker-compose.prod.yml`** — replace the local `build:`
   blocks on `backend`, `frontend`, and `nginx` services with
   `image:` references to the GHCR tags CI publishes. Keep the
   `image:` directive on each backend-derived service so
   `celery`, `celery_beat`, `mqtt_consumer` continue to share the
   same image.

   ```yaml
   backend:
     image: ghcr.io/uid0/openmakersuite/backend:${GIT_HASH}
     # build: removed
   frontend:
     image: ghcr.io/uid0/openmakersuite/frontend:${GIT_HASH}
   nginx:
     image: ghcr.io/uid0/openmakersuite/nginx:${GIT_HASH}
   ```

   `${GIT_HASH}` is the 7-char short SHA — the same value the
   Sentry release machinery (`deploy.sh:52`) already exports for
   the rest of the run. With no fallback the deploy is strict by
   design: an undefined GIT_HASH means an aborted deploy rather
   than a silent `:latest` resolution.

2. **`deploy.sh`** — swap the build step for a pull step:

   ```bash
   $COMPOSE pull --include-deps backend frontend nginx
   $COMPOSE up -d --no-build  # --no-build is belt-and-braces
   ```

   `--no-build` makes the run fail loudly if any service still has
   a `build:` directive that didn't get migrated — easier to spot
   than a silent host-side build.

3. **One-time bootstrap on the prod host** — authenticate to GHCR
   so pulls succeed. The relevant secret already exists for CI
   (`GHCR_TOKEN`); add it once to the prod host's `.env` and have
   `deploy.sh` run:

   ```bash
   echo "${GHCR_TOKEN}" | docker login ghcr.io \
       --username "${GHCR_USER}" --password-stdin
   ```

   This is the same login CI does (`docker-build.yml:148`).

### Resulting deploy timeline

| Step                 | Before     | After       |
|----------------------|------------|-------------|
| `git pull`           | ~3s        | ~3s         |
| `docker compose build` (host) | **~5 min** | — removed  |
| `docker compose pull`         | —          | **~30–60s** |
| `up -d --no-build`            | ~20s       | ~20s        |
| `restart nginx` + `restart emqx`              | ~10s       | ~10s        |
| **Total**            | **~6 min** | **~1.5 min**|

End-to-end deploy gets ~4× faster and the artifact is bit-for-bit
identical to what CI tested.

## Tradeoffs

### What we gain

- **Reproducible deploys**: the SHA-tagged image is the exact one
  CI ran tests against — no "works in CI, breaks on host" caused by
  a stale layer or a different `pip-compile` resolution.
- **Smaller / faster prod host**: no Python build toolchain, no
  node-gyp, no npm cache. CCX22 → could downsize one tier.
- **Lower attack surface**: prod host no longer needs `git` history
  reachability for the build (`build:` reads the local working
  copy, which means a compromised host can inject code into the
  next deploy if it can write to `~/openmakersuite`). Pulling a
  signed image from GHCR is harder to subvert silently.
- **Rollback by tag**: `GIT_HASH=<old-sha> ./deploy.sh` becomes a
  pure-pull operation — no need to checkout the old source and
  rebuild it.

### What we lose

- **Local-hotfix deploys**: today an operator can SSH in, edit a
  file, and `./deploy.sh` to apply it. After the change the host
  no longer has a working `build:` path; the hotfix flow is "make
  a branch, push, wait for CI, then pull." That's a deliberate
  trade — local hotfixes are exactly the path that produces drift.
  If the hotfix flow is load-bearing, keep a documented
  `deploy-local.sh` that re-enables `build:` for emergencies only.

- **GHCR availability becomes a deploy dependency**: if GHCR is
  down, a fresh `pull` fails. Mitigation: keep the previous image
  tag locally (which `docker compose pull` does by default), so a
  `up -d` against the prior tag works as a fallback. Documented
  rollback runbook covers this case.

- **First-time host setup**: the `docker login ghcr.io` step needs
  a token. Easy to do, but one more thing in
  `deploy/BACKUP_RESTORE.md` and the bring-up runbook.

### What stays the same

- `nginx`, `backend`, `frontend`, `celery`, `celery_beat`,
  `mqtt_consumer`, `emqx`, `db`, `redis` topology and ports.
- Healthchecks, restart policies, named volumes
  (`frontend_build`, postgres data, media).
- Sentry release tagging + finalize step.
- `restart-drill.sh` and the DB backup/restore flow.

## Migration steps

1. **PR #1** — add `image:` directives next to the existing
   `build:` blocks (compose ignores `build:` when `image:` is
   tagged with a fully-qualified registry and the image is
   present locally). Run a few deploys with both knobs to confirm
   the GHCR image matches what the local build would produce.

2. **PR #2** — flip `deploy.sh` to `pull` instead of `build`. Keep
   the `build:` blocks in compose as a safety net (compose will
   still build them on a `--build` flag, which the new deploy
   script doesn't pass).

3. **PR #3** — remove the `build:` blocks entirely from
   `docker-compose.prod.yml` and require GHCR for prod. Add a
   sibling `docker-compose.build.yml` (or a `--profile build`
   service set) for the local-hotfix escape hatch the runbook
   references.

4. **Runbook updates** —
   - `deploy/BACKUP_RESTORE.md`: note the GHCR login bootstrap.
   - `deploy/SMOKE_TESTS.md`: smoke runs are unchanged but call
     out that a failed `pull` now aborts the deploy.
   - `docs/SERVICE_CATALOG.md`: reflect that the prod host no
     longer needs the Python/Node build toolchain.

5. **Resource-tier review** — once two deploy cycles pass cleanly
   on pulled images, evaluate whether the prod host can drop from
   CCX22 to a smaller tier (no Python build, no node-gyp = less
   RAM headroom needed during deploys).

## Open question

CI's `docker-build.yml` currently builds **only on push to `main`**
and on tag pushes. The deploy is keyed on `GIT_HASH = $(git rev-parse
--short HEAD)`. As long as deploys run on a SHA that's been pushed
to `main` we're fine; if an operator ever wants to deploy a SHA from
a feature branch, the GHCR image won't exist. Options:

- Document that deploys must be from `main` (already the de-facto
  rule).
- Add a manual `workflow_dispatch` trigger to `docker-build.yml`
  that builds + pushes an arbitrary ref.

Recommend option 1 — keeps the deploy story simple and matches the
existing CI cadence.
