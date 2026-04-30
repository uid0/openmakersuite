# OpenMakerSuite Helm Chart

Helm chart for deploying [OpenMakerSuite](https://github.com/uid0/openmakersuite)
to Kubernetes. Mirrors the production topology from `docker-compose.prod.yml`:
Django backend, React frontend served by nginx, Celery worker, optional Flower
dashboard, optional in-cluster PostgreSQL and Redis (or external instances),
Ingress with TLS hooks, persistence for static/media, secrets, and a one-shot
migrations Job.

## Quick start

```bash
helm install oms ./deploy/helm/openmakersuite \
  --namespace oms --create-namespace \
  --set domain=oms.example.com \
  --set ingress.enabled=true \
  --set backend.image.tag=1.2.3 \
  --set frontend.image.tag=1.2.3
```

The chart bundles PostgreSQL + Redis by default for evaluation. Disable both
and point at managed services for production:

```bash
helm install oms ./deploy/helm/openmakersuite \
  --set postgresql.enabled=false \
  --set externalDatabase.url=postgresql://oms:hunter2@db.prod.svc:5432/makerspace_inventory \
  --set redis.enabled=false \
  --set externalRedis.url=redis://redis.prod.svc:6379/0 \
  --set secrets.existingSecret=oms-app-secrets
```

## What it renders

| Resource                | When                                                                 |
| ----------------------- | -------------------------------------------------------------------- |
| `Deployment` backend    | always                                                               |
| `Deployment` frontend   | always                                                               |
| `Deployment` celery     | `celery.enabled=true` (default)                                      |
| `Deployment` flower     | `flower.enabled=true`                                                |
| `StatefulSet` postgres  | `postgresql.enabled=true` (default)                                  |
| `Deployment` redis      | `redis.enabled=true` (default)                                       |
| `Service` for each app  | always                                                               |
| `Ingress`               | `ingress.enabled=true`                                               |
| `PersistentVolumeClaim` | `persistence.{static,media}.enabled=true` and per-component flags    |
| `Secret` (app)          | unless `secrets.existingSecret` is set                               |
| `Secret` (database)     | always — wraps either bundled or external connection string          |
| `Secret` (redis)        | always — wraps either bundled or external connection string          |
| `Secret` (postgres)     | bundled postgres only, unless `auth.existingSecret` is set           |
| `ConfigMap`             | always — non-secret env (DEBUG, ALLOWED_HOSTS, SENTRY_*, etc.)       |
| `ServiceAccount`        | `serviceAccount.create=true` (default)                               |
| `Job` migrations        | `migrations.enabled=true` (default), pre-install/pre-upgrade hook    |

## Health probes

- **Backend**: HTTP `/api/dashboard/health/` for liveness and readiness
- **Frontend**: HTTP `/` against the nginx container
- **Celery**: `celery inspect ping` exec probe
- **Flower**: TCP socket on the flower port
- **Postgres**: `pg_isready` against the configured user/database
- **Redis**: `redis-cli ping`

All probes are tunable under `<component>.probes.{liveness,readiness,startup}`
and can be disabled by setting `enabled: false` per probe.

## Configuration sources

Where each setting category lives:

| Category                | Helm values path                                                                       | Rendered as                       |
| ----------------------- | -------------------------------------------------------------------------------------- | --------------------------------- |
| Hostnames / CORS / CSRF | `domain`, `extraAllowedHosts`, `extraCsrfTrustedOrigins`, `extraCorsAllowedOrigins`    | ConfigMap `<rel>-openmakersuite-env` |
| Database URL            | `postgresql.*` (bundled) or `externalDatabase.url` / `existingSecret`                  | Secret `<rel>-openmakersuite-database` |
| Redis URL               | `redis.*` (bundled) or `externalRedis.url` / `existingSecret`                          | Secret `<rel>-openmakersuite-redis`    |
| Sentry                  | `env.sentry.{environment,release}` + `secrets.values.SENTRY_DSN`                       | ConfigMap (env) + Secret (DSN)    |
| Email transport         | `env.email.{backend,host,port,useTls,defaultFrom,logisticsAlert}`                      | ConfigMap                         |
| Email credentials       | `secrets.values.{EMAIL_HOST_USER,EMAIL_HOST_PASSWORD,POSTMARK_SERVER_TOKEN}`           | Secret                            |
| Inbound webhooks        | `secrets.values.{POSTMARK_INBOUND_TOKEN,LOCATION_PING_TOKEN}`                          | Secret                            |
| Public iframe URLs      | `env.publicUrls.{traffic,weather,github}`                                              | ConfigMap                         |
| WHMCS API               | `env.whmcs.apiUrl` + `secrets.values.WHMCS_API_{IDENTIFIER,SECRET,ACCESSKEY}`          | ConfigMap (URL) + Secret (creds)  |
| EMQX MQTT               | `env.emqx.*` + `secrets.values.{EMQX_*,MQTT_BROKER_*}`                                 | ConfigMap + Secret                |
| ForgeKey signing key    | `secrets.values.FORGEKEY_FIRMWARE_SIGNING_KEY`                                         | Secret                            |

`backend.extraEnv`, `frontend.extraEnv`, and `celery.extraEnv` accept raw
container env entries for anything outside this list (including
`valueFrom.secretKeyRef` references to externally managed Secrets).

## Secrets handling

Sensitive values (Django `SECRET_KEY`, Sentry DSN, EMQX credentials, mail
credentials, etc.) live in a chart-managed `Secret` named
`<release>-openmakersuite-secrets`. `SECRET_KEY` is generated automatically
on first install and persisted across upgrades via `helm.sh/resource-policy: keep`.

To bring your own Secret instead, populate it with the same keys
(`SECRET_KEY`, `SENTRY_DSN`, …) and set:

```yaml
secrets:
  existingSecret: my-secret-name
```

The bundled Postgres password works the same way: set
`postgresql.auth.existingSecret` to skip the chart-managed Secret.

## Migrations

`python manage.py migrate --no-input` runs as a Helm pre-install/pre-upgrade
hook by default. Set `migrations.useHooks=false` to ship it as a regular Job
instead, or `migrations.enabled=false` to skip it entirely.

## Smoke tests

After `helm install`/`upgrade` finishes and the migrations hook completes,
walk through [`../../SMOKE_TESTS.md`](../../SMOKE_TESTS.md) — eight
curl-based checks plus a short browser pass covering frontend, backend
health, API docs, admin, DB, Redis, static/media, and the unauthenticated
public endpoints. Anything red there is a failed release.

## Validating local changes

```bash
helm lint deploy/helm/openmakersuite
helm template oms deploy/helm/openmakersuite > /tmp/render.yaml
helm template oms deploy/helm/openmakersuite \
  --set postgresql.enabled=false --set redis.enabled=false \
  --set externalDatabase.url=postgresql://u:p@db:5432/oms \
  --set externalRedis.url=redis://r:6379/0 \
  > /tmp/render-external.yaml
```

See `values.yaml` for the full set of overridable values.
