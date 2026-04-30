# Supported Deployment Paths

OpenMakerSuite supports four deployment paths. Pick the one that matches your
audience and operating environment — each links to the detailed runbook for
that target.

| Path | Audience | When to use | Detailed doc |
|------|----------|-------------|---------------|
| [Local development](#1-local-development) | Contributors hacking on the code | Running tests, iterating on `backend/` or `frontend/`, devcontainer work | [`docs/DEVELOPMENT_ENVIRONMENT.md`](DEVELOPMENT_ENVIRONMENT.md) |
| [Docker Compose self-hosting](#2-docker-compose-self-hosting) | Single-host operators (one Linux box, one VM) | Small/medium makerspaces, evaluation, on-prem with one server | [`docs/DEPLOYMENT.MD`](DEPLOYMENT.MD) |
| [Raw Kubernetes manifests](#3-raw-kubernetes-manifests) | Cluster operators who avoid Helm | Forking the YAML, GitOps with Kustomize overlays, environments where Helm isn't available | [`deploy/k8s/README.md`](../deploy/k8s/README.md) |
| [Helm chart](#4-helm-chart) | Cluster operators using Helm | Multi-environment installs, value-driven config, OCI chart consumption | [`deploy/helm/openmakersuite/README.md`](../deploy/helm/openmakersuite/README.md) |

Cross-cutting docs apply to all four paths:

- [`deploy/PREREQUISITES.md`](../deploy/PREREQUISITES.md) — host tooling
  (Docker, Compose, kubectl, Helm, Git, Node, Python) by distro family
  (Debian / Red Hat / Arch). Includes a per-path "what you need" matrix.
- [`deploy/SMOKE_TESTS.md`](../deploy/SMOKE_TESTS.md) — post-deploy checks to
  run before declaring a release healthy. The same checks work against
  Compose, raw Kubernetes, and Helm deployments.
- [`.env.prod.example`](../.env.prod.example) — environment template covering
  Django, database, cache, email, MQTT, Sentry, and webhook settings shared
  across the cluster paths.

## 1. Local development

For working on the code itself: running `pytest` and `npm test`, hot-reload,
debugging migrations, or trying a feature branch.

**Prerequisites:** Docker + Compose, Python 3.11+, Node.js 20 LTS, Git. See
[`deploy/PREREQUISITES.md`](../deploy/PREREQUISITES.md) for distro-specific
install steps.

**Quick start:**

```bash
git clone https://github.com/uid0/openmakersuite.git
cd openmakersuite
cp .env.example .env
docker compose up -d
docker compose exec backend python manage.py createsuperuser
```

Frontend at `http://localhost:3000`, API at `http://localhost:8000/api/`,
admin at `http://localhost:8000/admin/`.

**Containerized dev environment.** A VS Code devcontainer is provided in
`.devcontainer/` for an environment that matches CI (Ubuntu 22.04, Python
3.11, Node 18). See [`docs/DEVELOPMENT_ENVIRONMENT.md`](DEVELOPMENT_ENVIRONMENT.md).

**Test suites.** Both must be green before a change ships:

```bash
cd backend && pytest
cd frontend && npm test
pre-commit run --all-files
```

## 2. Docker Compose self-hosting

For deploying to a single Linux host — typical for makerspaces running on
one server or VM. The `docker-compose.prod.yml` topology bundles Postgres,
Redis, Django + Gunicorn, Celery, the React frontend, EMQX, optional Flower,
and an Nginx reverse proxy with healthchecks on every service.

**Prerequisites:** Docker engine + Docker Compose v2, Git. Optional: Python
and Node only if you're also running tooling outside containers.

**Quick start:**

```bash
git clone https://github.com/uid0/openmakersuite.git
cd openmakersuite
cp .env.prod.example .env
$EDITOR .env                        # set SECRET_KEY, POSTGRES_PASSWORD, DOMAIN, ...
./deploy.sh
```

`deploy.sh` builds images, brings up the database, runs the **pre-deploy
migration gate** (`showmigrations --plan` → `migrate --noinput` before any
backend traffic), starts the rest of the stack, runs `collectstatic`, and
appends an audit trail to `deploy.log`.

**Full runbook:** [`docs/DEPLOYMENT.MD`](DEPLOYMENT.MD) — covers manual
deploys, SSL via Let's Encrypt, the migration gate, troubleshooting, and the
update flow.

**Post-deploy checks:** [`deploy/SMOKE_TESTS.md`](../deploy/SMOKE_TESTS.md).

## 3. Raw Kubernetes manifests

For cluster operators who want plain YAML — no Helm, no templating.
Identical topology to the Helm chart. Ships a `base/` kustomization with
bundled Postgres + Redis (good for evaluation) and an
`overlays/external-services/` overlay that swaps in managed Postgres and
Redis via patched `Secret`s.

**Prerequisites:** `kubectl` (within ±1 minor of your control plane), Git.
Docker only if you're also building images instead of pulling published tags.

**Quick start (bundled DB + cache):**

```bash
kubectl apply -k deploy/k8s/base
```

**Quick start (external DB + cache):**

```bash
# Edit deploy/k8s/overlays/external-services/secret-{database,redis}-patch.yaml
kubectl apply -k deploy/k8s/overlays/external-services
```

The `migrations-job.yaml` runs `manage.py migrate` once per apply; the
backend `Deployment` waits on it via `initContainers`. The `Ingress`
expects `openmakersuite.local` by default — adjust for your domain.

**Full runbook:** [`deploy/k8s/README.md`](../deploy/k8s/README.md) — file
inventory, secret rotation, image-tag overrides, and overlay patterns.

## 4. Helm chart

For cluster operators using Helm — value-driven config, multi-environment
installs, and OCI chart consumption from `ghcr.io`. Mirrors the raw-manifest
topology with the same defaults.

**Prerequisites:** `helm` 3, `kubectl`, Git.

**Quick start (bundled DB + cache):**

```bash
helm install oms ./deploy/helm/openmakersuite \
  --namespace oms --create-namespace \
  --set domain=oms.example.com \
  --set ingress.enabled=true \
  --set backend.image.tag=1.2.3 \
  --set frontend.image.tag=1.2.3
```

**Quick start (external DB + cache):**

```bash
helm install oms ./deploy/helm/openmakersuite \
  --set postgresql.enabled=false \
  --set externalDatabase.url=postgresql://oms:hunter2@db.prod.svc:5432/makerspace_inventory \
  --set redis.enabled=false \
  --set externalRedis.url=redis://redis.prod.svc:6379/0 \
  --set secrets.existingSecret=oms-app-secrets
```

**Full runbook:** [`deploy/helm/openmakersuite/README.md`](../deploy/helm/openmakersuite/README.md)
— values reference, internal/external DB/Redis toggles, ingress + TLS,
persistence sizing, replicas, image tags, optional observability.

## Choosing between paths

- **One server, one operator** → Docker Compose. Simplest mental model and
  the only path with `deploy.sh` automation.
- **Existing Kubernetes cluster, no Helm** → raw manifests with the
  `external-services` overlay if you have managed Postgres/Redis.
- **Existing Kubernetes cluster, Helm shop** → Helm chart. Same topology,
  values-driven configuration, easier upgrades.
- **Working on the code** → local development. Don't use the production
  paths for hacking — they're slower to iterate on and require rebuilding
  images.

The cluster paths (raw manifests and Helm) render the same workloads. Pick
based on team tooling, not capability. If you're unsure, start with the
Helm chart — values changes are easier to roll out than re-applying raw
manifests.
