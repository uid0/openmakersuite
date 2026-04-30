# Raw Kubernetes Manifests

Plain Kubernetes manifests for OpenMakerSuite — same topology as the Helm
chart in `../helm/openmakersuite/`, no templating. Use these when you want
to read the YAML straight, fork it for an unusual environment, or apply it
on a cluster where Helm isn't available.

```
deploy/k8s/
├── base/                          # bundled Postgres + Redis (default)
│   ├── kustomization.yaml
│   ├── namespace.yaml
│   ├── serviceaccount.yaml
│   ├── configmap.yaml
│   ├── secret-app.yaml            # SECRET_KEY + Sentry/MQTT/email creds
│   ├── secret-postgres.yaml       # bundled postgres password
│   ├── secret-database.yaml       # canonical DATABASE_URL
│   ├── secret-redis.yaml          # canonical REDIS_URL
│   ├── pvc-static.yaml            # collected static assets
│   ├── pvc-media.yaml             # uploaded media
│   ├── postgres-statefulset.yaml
│   ├── postgres-service.yaml
│   ├── redis-deployment.yaml
│   ├── redis-service.yaml
│   ├── backend-deployment.yaml    # gunicorn + Django, /api/dashboard/health/
│   ├── backend-service.yaml
│   ├── frontend-deployment.yaml   # nginx + built React assets
│   ├── frontend-service.yaml
│   ├── celery-deployment.yaml     # background worker
│   ├── flower-deployment.yaml     # opt-in dashboard (commented in kustomization)
│   ├── flower-service.yaml
│   ├── migrations-job.yaml        # one-shot `manage.py migrate`
│   └── ingress.yaml
└── overlays/
    └── external-services/         # use managed Postgres + Redis instead
        ├── kustomization.yaml
        ├── secret-database-patch.yaml
        └── secret-redis-patch.yaml
```

## Quick start (bundled DB + Redis)

```bash
# 1. Set real credentials.
$EDITOR deploy/k8s/base/secret-app.yaml         # SECRET_KEY, optional integrations
$EDITOR deploy/k8s/base/secret-postgres.yaml    # postgres-password
$EDITOR deploy/k8s/base/secret-database.yaml    # match the password above
$EDITOR deploy/k8s/base/configmap.yaml          # ALLOWED_HOSTS / FRONTEND_URL
$EDITOR deploy/k8s/base/ingress.yaml            # host + TLS

# 2. Apply.
kubectl apply -k deploy/k8s/base

# 3. Run migrations once Postgres is ready.
kubectl -n openmakersuite wait --for=condition=ready pod \
    -l app.kubernetes.io/component=postgresql --timeout=120s
kubectl apply -f deploy/k8s/base/migrations-job.yaml
kubectl -n openmakersuite wait --for=condition=complete job/oms-migrate --timeout=600s
```

`kubectl apply -k` requires kustomize built into kubectl 1.14+. If you can't
use kustomize, apply each file in `base/` with `kubectl apply -f` in the
order they're listed in `base/kustomization.yaml` — the resources have no
hidden ordering requirements beyond Postgres being ready before the
migrations Job runs.

## Managed database / Redis

Use the overlay:

```bash
$EDITOR deploy/k8s/overlays/external-services/secret-database-patch.yaml
$EDITOR deploy/k8s/overlays/external-services/secret-redis-patch.yaml
kubectl apply -k deploy/k8s/overlays/external-services
```

The overlay deletes the bundled `oms-postgresql` StatefulSet/Service/Secret
and the `oms-redis` Deployment/Service, then patches the canonical
`oms-database` and `oms-redis` Secrets with your real connection strings.

## Images

The Deployments default to `ghcr.io/openmakersuite/backend:latest` and
`ghcr.io/openmakersuite/frontend:latest`. Pin a real tag with:

```bash
kubectl -n openmakersuite set image \
    deployment/oms-backend backend=ghcr.io/openmakersuite/backend:1.2.3
kubectl -n openmakersuite set image \
    deployment/oms-frontend frontend=ghcr.io/openmakersuite/frontend:1.2.3
kubectl -n openmakersuite set image \
    deployment/oms-celery celery=ghcr.io/openmakersuite/backend:1.2.3
```

For a kustomize-managed pin, add `images:` to a thin overlay:

```yaml
# deploy/k8s/overlays/prod/kustomization.yaml
resources:
  - ../../base
images:
  - name: ghcr.io/openmakersuite/backend
    newTag: "1.2.3"
  - name: ghcr.io/openmakersuite/frontend
    newTag: "1.2.3"
```

## Health probes

| Component  | Probe                                             |
| ---------- | ------------------------------------------------- |
| Backend    | HTTP `GET /api/dashboard/health/` (live + ready)  |
| Frontend   | HTTP `GET /` against nginx                        |
| Celery     | `celery -A config inspect ping` exec             |
| Flower     | TCP socket on the flower port                     |
| Postgres   | `pg_isready` against the configured user/database |
| Redis      | `redis-cli ping`                                  |

## When to use this vs the Helm chart

- **Helm chart** (`deploy/helm/openmakersuite/`) — preferred for installs
  that need values overrides, multiple environments, or release management.
- **Raw manifests** (this directory) — preferred when you want to read the
  YAML without rendering, when Helm isn't available on the target cluster,
  or as a starting point for a GitOps repo.

Both layouts render the same resource topology; pick whichever fits your
operational model.
