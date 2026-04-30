# OMS Helm chart

Deploys Open Makers Suite (Django + Celery + frontend + nginx) on Kubernetes
with optional in-cluster PostgreSQL, Redis, and EMQX MQTT broker. A single
`values.yaml` controls every common deployment choice.

## Values overview

| Group | Key knobs |
| --- | --- |
| Data plane | `postgres.internal`, `redis.internal`, `emqx.internal` (each toggles in-cluster vs external; external configured via `*.external.host` / `*.external.url`) |
| Persistence | `postgres.persistence.{enabled,size,storageClass,accessMode}`, `emqx.persistence.*`, `redis.persistence.*`, `backend.mediaPersistence.*` |
| Replicas | `backend.replicaCount`, `celery.replicaCount`, `frontend.replicaCount`, `nginx.replicaCount` (and `backend.hpa.*` for autoscaling) |
| Resources | `*.resources.requests`/`limits` on every workload |
| Image tags | `*.image.{registry,repository,tag,pullPolicy}`, plus chart-wide `imageRegistry` and `imagePullSecrets` |
| Ingress | `ingress.enabled`, `ingress.className`, `ingress.hosts[]`, `ingress.annotations` |
| TLS | `ingress.tls.enabled`, `ingress.tls.existingSecret`, `ingress.tls.clusterIssuer`, `ingress.extraTls` |
| Observability | `observability.enabled` master switch + `serviceMonitor`, `podMonitor`, `prometheusRules` |
| Optional UI | `flower.enabled` for Celery monitoring |

## Common deployment combinations

### All-in-one (defaults)

```bash
helm install oms ./helm/oms \
  --set secrets.djangoSecretKey=... \
  --set secrets.postgresPassword=... \
  --set secrets.emqxDashboardPassword=Strong1Pw
```

In-cluster Postgres + Redis + EMQX, ingress on with TLS via cert-manager.

### Managed Postgres + Redis (e.g. RDS + ElastiCache)

```yaml
postgres:
  internal: false
  external:
    host: oms-prod.cluster-xyz.us-east-1.rds.amazonaws.com
    port: 5432
redis:
  internal: false
  external:
    url: redis://master.oms-prod.abc.use1.cache.amazonaws.com:6379/0
```

Backend env auto-rewires; no in-cluster Postgres/Redis manifests are rendered.

### Behind an existing LB (no ingress, no TLS at the chart)

```yaml
ingress:
  enabled: false
```

### Bring your own TLS secret

```yaml
ingress:
  tls:
    enabled: true
    existingSecret: oms-tls
    clusterIssuer: ""
```

### Observability on

```yaml
observability:
  enabled: true
  serviceMonitor:
    enabled: true
  podMonitor:
    enabled: true
```

Renders `ServiceMonitor` and `PodMonitor` (Prometheus Operator CRDs) for the
backend Service and Celery pods.

## Linting and rendering

```bash
helm lint helm/oms
helm template oms helm/oms -f helm/oms/ci/<file>.yaml
```

CI value files under `helm/oms/ci/` exercise the toggle matrix:

- `default-values.yaml`           — everything internal, ingress + TLS on
- `external-data-values.yaml`     — external Postgres + Redis
- `no-ingress-values.yaml`        — ingress off
- `no-tls-values.yaml`            — ingress on, TLS off
- `observability-values.yaml`     — Prometheus monitors enabled

## Secrets

Set `secrets.create=true` (default) and provide the required values inline,
or set `secrets.create=false` and point `secrets.existingSecret` at a Secret
that contains these keys:

- `django-secret-key`
- `postgres-password`
- `emqx-dashboard-password` (only required when `emqx.internal=true`)
- `emqx-api-key`, `emqx-api-secret` (optional)
- `mqtt-broker-username`, `mqtt-broker-password` (optional)
- `forgekey-firmware-signing-key` (optional)
- `sentry-dsn` (optional)
