# Deployment Smoke Tests

Post-deploy checks for OpenMakerSuite. Run these after every deploy (Docker
Compose, raw Kubernetes, or Helm) before declaring the release healthy. Each
check is independent — none of them depend on application data, so they work
against a freshly-migrated database.

`HOST` below is the public hostname (or `kubectl port-forward` target) for the
deployment. For the bundled `deploy/k8s/base` and `deploy/helm/openmakersuite`
defaults, that is `openmakersuite.local`. For a port-forward against the
backend `Service`, use `localhost:8000` and skip the ingress-only checks.

```bash
export HOST=openmakersuite.local           # or your real domain
export SCHEME=https                         # http if TLS isn't wired up yet
```

The cluster commands assume the Helm/raw-manifest defaults: `Namespace`
`openmakersuite`, release name `oms` (so `oms-backend`, `oms-frontend`,
`oms-postgresql`, `oms-redis`, `oms-celery`). Adjust if your release uses
different names.

## 1. Frontend serves the SPA shell

```bash
curl -fsSL "$SCHEME://$HOST/" | grep -q '<div id="root">'
```

- **Pass:** HTTP 200 and the response contains `<div id="root">` (the React
  mount point that `frontend/index.html` ships).
- **Fail modes:** 502/504 (frontend Pod not ready, ingress misrouted),
  default nginx welcome page (build assets weren't copied into the volume),
  HTML missing the root div (wrong image tag, stale cached build).

## 2. Backend health endpoint

```bash
curl -fsS "$SCHEME://$HOST/api/dashboard/health/" | jq .
```

- **Pass:** HTTP 200 with JSON `{"status":"healthy", ...}` including
  `active_messages`, `maintenance_mode`, and `last_config_update`.
- **Why this endpoint:** `dashboard.views.dashboard_health` reads
  `DashboardConfig` and counts `DashboardMessage` rows, so a green response
  proves the backend is up, the database is reachable, and migrations ran far
  enough to create those tables. This is the same path the Kubernetes
  liveness/readiness probes hit.
- **Fail modes:** `{"status":"error", ...}` (DB unreachable or migrations
  pending), 502 (backend Pod crash-looping), 404 (ingress not routing
  `/api/...` to backend).

## 3. API documentation (OpenAPI + Swagger UI)

```bash
curl -fsS "$SCHEME://$HOST/api/schema/" | head -1                # raw OpenAPI
curl -fsS "$SCHEME://$HOST/api/docs/"  | grep -qi 'swagger'      # Swagger UI
```

- **Pass:** `/api/schema/` returns an OpenAPI document (YAML by default,
  JSON with `Accept: application/json`); `/api/docs/` returns HTML for the
  Swagger UI.
- **Why:** `drf-spectacular` is wired into `backend/config/urls.py`. If the
  schema view 500s, a serializer or view introspection failure has slipped
  through and other API endpoints are likely affected.

## 4. Admin login page reachable

```bash
curl -fsS -o /dev/null -w '%{http_code}\n' "$SCHEME://$HOST/admin/login/"
```

- **Pass:** HTTP 200. Visiting `$SCHEME://$HOST/admin/` in a browser shows
  the Django admin login form (it 302s to `/admin/login/` when not signed in).
- **Why:** Confirms `/admin/` is wired through the ingress / nginx to the
  backend. Both deployment paths serve admin from the backend container:
  - Docker Compose: `nginx/templates/default.conf.template` has a
    `location /admin/` block that proxies to the backend.
  - Kubernetes: add an `/admin` rule to the `Ingress` (alongside `/api`,
    `/static`, `/media`) if your environment needs admin externally —
    the bundled `deploy/k8s/base/ingress.yaml` only exposes `/api`,
    `/static`, `/media`, and `/`, so admin must be reached via
    `kubectl port-forward svc/oms-backend 8000:8000` unless the rule is
    added.
- **Sign-in:** create the first superuser with
  `kubectl -n openmakersuite exec deploy/oms-backend -- python manage.py createsuperuser`
  (or `docker compose -f docker-compose.prod.yml exec backend python manage.py createsuperuser`).
  Logging in successfully proves session cookies, CSRF, and `SECRET_KEY`
  are intact across restarts.

## 5. Database reachable and migrated

The dashboard health endpoint (#2) implicitly proves the DB is reachable.
Two extra checks catch problems it won't:

```bash
# Postgres accepting connections (bundled deploy)
kubectl -n openmakersuite exec statefulset/oms-postgresql -- \
    pg_isready -U makerspace -d makerspace_inventory

# No pending migrations
kubectl -n openmakersuite exec deploy/oms-backend -- \
    python manage.py showmigrations --plan --no-color | grep -E '^\[ \]' && \
    echo "PENDING MIGRATIONS — DEPLOY IS NOT HEALTHY" || \
    echo "All migrations applied"
```

For the external-services overlay or Helm with `postgresql.enabled=false`,
swap the first command for a `psql` against your managed database, or trust
the migrations Job's exit status (`kubectl -n openmakersuite get job oms-migrate
-o jsonpath='{.status.succeeded}'` should print `1`).

For Docker Compose:

```bash
docker compose -f docker-compose.prod.yml exec -T db \
    pg_isready -U makerspace -d makerspace_inventory
docker compose -f docker-compose.prod.yml exec -T backend \
    python manage.py showmigrations --plan --no-color | grep -E '^\[ \]' && \
    echo "PENDING MIGRATIONS" || echo "All migrations applied"
```

## 6. Redis reachable

```bash
# Bundled Redis (Helm/raw with redis.enabled=true)
kubectl -n openmakersuite exec deploy/oms-redis -- redis-cli ping
# → PONG

# Celery worker can reach the broker
kubectl -n openmakersuite exec deploy/oms-celery -- \
    celery -A config inspect ping
# → -> celery@<pod>: OK
```

The Celery `inspect ping` is the higher-confidence check — it proves the
worker has the right `REDIS_URL` from the canonical `oms-redis` Secret and
can both connect to Redis and round-trip a control message. If Redis is
healthy but Celery can't ping, the broker URL or worker config is wrong.

For Docker Compose:

```bash
docker compose -f docker-compose.prod.yml exec -T redis redis-cli ping
docker compose -f docker-compose.prod.yml exec -T celery_worker \
    celery -A config inspect ping
```

## 7. Static assets and media serving

Static files (`/static/...`) are produced by `python manage.py collectstatic`
during image build / migrations Job and served by the backend in production
configs. Media (`/media/...`) is user-uploaded content backed by the
`oms-media` PVC.

```bash
# Static — pick any file Django collects unconditionally:
curl -fsS -o /dev/null -w '%{http_code}\n' \
    "$SCHEME://$HOST/static/admin/css/base.css"
# → 200

# Media — only meaningful once a file has been uploaded. After uploading
# something through the admin or API, fetch its URL and confirm 200.
# A blank deploy returns 404 here; that's expected.
curl -fsS -o /dev/null -w '%{http_code}\n' "$SCHEME://$HOST/media/"
# → 404 on a fresh deploy is OK; what matters is that the route exists.
```

- **Pass (static):** 200 with `Content-Type: text/css`. A 404 here means
  `collectstatic` didn't run or the static volume isn't mounted into the
  serving container.
- **Pass (media):** any non-5xx response. A 5xx means the media PVC isn't
  mounted (Pod will usually fail to start) or permissions are wrong.

## 8. Unauthenticated public makerspace workflow

The product exposes a small set of endpoints that work without a session —
public displays, kiosks, and site branding. A green response from each of
these proves the public-facing parts of the deploy work end-to-end without
needing to seed users or memberships.

```bash
# Site branding / theme used by the React shell
curl -fsS "$SCHEME://$HOST/api/customization/settings/" | jq .

# Active dashboard messages (returns [] on a fresh install)
curl -fsS "$SCHEME://$HOST/api/dashboard/messages/" | jq .

# Public inventory summary used by lobby displays
curl -fsS "$SCHEME://$HOST/api/dashboard/inventory-summary/" | jq .
```

- **Pass:** Each call returns HTTP 200 and a JSON body. Empty lists / zero
  counts are fine on a fresh install — what matters is that no endpoint
  returns 401/403 (auth misconfiguration), 500 (backend bug), or HTML
  (request fell through to the SPA catch-all because `/api/...` routing
  is wrong).
- **Why this set:** all three views are decorated with
  `@permission_classes([AllowAny])`, so they exercise the full request path
  (ingress → backend → DB → JSON response) without any user session, CSRF
  token, or membership state. If a real visitor would see a broken lobby
  display or an unbranded login page, one of these calls will fail first.

## Browser walk-through

After the curl checks pass, do a quick manual pass in a browser:

1. `$SCHEME://$HOST/` — the SPA loads, no console errors, branding from
   `/api/customization/settings/` is applied.
2. `$SCHEME://$HOST/admin/` — Django admin login form renders; sign in with
   the superuser created above.
3. `$SCHEME://$HOST/api/docs/` — Swagger UI lists endpoints and "Try it out"
   works for `GET /api/dashboard/health/`.

If all eight automated checks and the three browser checks pass, the deploy
is smoke-clean. Anything else is a fail — surface the failing check before
declaring the release healthy.

For upgrades (not first-time installs), run these checks both **before** the
upgrade (to confirm a clean baseline) and **after** the upgrade per the flow
in [`UPGRADE_ROLLBACK.md`](./UPGRADE_ROLLBACK.md). A red post-upgrade check
is the rollback trigger documented there.
