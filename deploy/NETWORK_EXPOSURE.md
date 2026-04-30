# Production Network Exposure

What ports each OpenMakerSuite service listens on, what the bundled
`docker-compose.prod.yml` and `deploy/k8s/` topologies expose to the public
internet by default, and what to harden before pointing real traffic at it.

> The compose file binds **host** ports for every service marked "host-bound"
> below. On a multi-tenant host (or a host with no firewall in front of it)
> those host ports are reachable from anywhere on the network the host is
> attached to. Read [§ Hardening checklist](#hardening-checklist) before
> shipping the default compose file to a public IP.

## Service exposure matrix (Docker Compose)

| Service     | Container port | Host bind (default)        | Default exposure | Auth/credential | Production hardening |
|-------------|----------------|----------------------------|------------------|-----------------|----------------------|
| nginx       | 80, 443        | `0.0.0.0:80`, `0.0.0.0:443`| **public**       | none on `/`, sessions on `/admin/` and `/api/`; basic auth on `/flower/` (see below) | Real TLS via Let's Encrypt (`LETSENCRYPT_*` env). Restrict `/admin/`, `/flower/`, `/mqttadmin/` to operator IPs at the host firewall or with an `allow`/`deny` block in `nginx/templates/default.conf.template`. |
| backend (gunicorn) | 8000   | not bound (`expose:`-only) | container-network only | session cookie + CSRF on `/admin/`, DRF auth on `/api/` | Keep unbound; let nginx terminate TLS and proxy `/api/`, `/admin/`, `/auth/passkey/`, `/webhooks/`. |
| frontend (build container) | n/a | not bound          | container-network only | n/a | Static bundle written to `frontend_build` volume; nginx serves it from `/`. |
| celery worker | n/a (Celery) | not bound                | container-network only | broker creds (Redis URL) | Keep unbound. The worker is a pure Redis consumer — no inbound listener. |
| postgres    | 5432           | not bound                  | container-network only | `POSTGRES_USER` + `POSTGRES_PASSWORD` | Keep unbound. If you must expose for managed backups, bind to `127.0.0.1:5432` and SSH-tunnel — never `0.0.0.0`. |
| redis       | 6379           | not bound                  | container-network only | none by default (Redis 7 default config) | Keep unbound. Redis ships with no auth — exposing it is equivalent to `eval`. |
| flower      | 5555           | `0.0.0.0:5555`             | **public** by default — must firewall | none in container; `/flower/` proxy in nginx adds basic auth via `htpasswd` | Either remove the `ports:` block (recommended) and use `/flower/` through nginx, OR restrict `5555/tcp` to operator IPs at the host firewall. |
| EMQX MQTT   | 1883           | `0.0.0.0:1883`             | **public** — must firewall | `MQTT_BROKER_USERNAME` / `MQTT_BROKER_PASSWORD`; ForgeKey JWT for devices | Public is required for off-site IoT clients. Protect with: (a) per-client credentials in EMQX dashboard, (b) ACL rules on EMQX restricting topics, (c) TLS on `8883/tcp` instead of plaintext `1883/tcp` for any client outside the trusted LAN. |
| EMQX MQTTS  | 8883           | `0.0.0.0:8883`             | public            | TLS client cert + creds | Mount your own server cert into the broker if you intend to use this; the bundled image generates a self-signed cert at first boot. |
| EMQX MQTT-WS  | 8083         | `0.0.0.0:8083`             | public            | same as 1883      | Only needed for browser MQTT clients. Remove the host bind if you don't have any. |
| EMQX MQTT-WSS | 8084         | `0.0.0.0:8084`             | public            | same as 8883      | Same as 8083 — drop if unused. |
| EMQX dashboard / REST API | 18083 | `0.0.0.0:18083`        | **public** — must firewall | `admin` / `EMQX_DASHBOARD_PASSWORD` (rotated by `deploy.sh`); API key/secret pair for REST | Treat as a privileged operator surface. Either (a) drop the `18083` host bind and reach the dashboard via nginx `/mqttadmin/`, OR (b) firewall `18083/tcp` to operator IPs. The shipped nginx config already proxies `/mqttadmin/` → `emqx:18083` so removing the host bind does not lose access. |

> "Container-network only" means the port is reachable from other services in
> the same compose project but not from outside the host. `expose:` and the
> default Docker network behaviour both produce this state.

## Service exposure matrix (Kubernetes / Helm)

The bundled `deploy/k8s/base/` and `deploy/helm/openmakersuite/` charts use
`Service`s of type `ClusterIP` for every workload, so the only externally
reachable surface is whatever your `Ingress` / cloud LB points at.

| Service       | k8s Service / port | Exposed externally? | How |
|---------------|--------------------|---------------------|-----|
| oms-backend   | `oms-backend:8000` | only via Ingress    | bundled `ingress.yaml` exposes `/api`, `/static`, `/media`, `/` (catch-all to frontend). Add an `/admin` rule explicitly if you want the Django admin externally — by default it is reachable only via `kubectl port-forward svc/oms-backend 8000:8000`. |
| oms-frontend  | `oms-frontend:80`  | only via Ingress    | catch-all `/` rule. |
| oms-celery    | no Service         | no                  | worker-only Pod. |
| oms-postgresql| `oms-postgresql:5432` | no                | cluster-internal only. Set `postgresql.enabled=false` + `externalDatabase.url` for managed Postgres. |
| oms-redis     | `oms-redis:6379`   | no                  | cluster-internal only. Set `redis.enabled=false` + `externalRedis.url` for managed Redis. |
| oms-emqx (if shipped) | n/a in bundled chart | n/a       | The bundled chart does not ship EMQX. Run it as a separate chart or as a Compose stack reachable from the cluster. |

The bundled `Ingress` does not include `/flower/` or `/mqttadmin/` — add them
explicitly (with auth) if you need operator access through the public LB.

## Static and media files

| Path        | Backed by                            | Served by | Cache                         |
|-------------|--------------------------------------|-----------|-------------------------------|
| `/static/`  | `static_volume` / `oms-static` PVC   | nginx (Compose) or backend (k8s) | `Cache-Control: public, max-age=31536000` is reasonable; static names include hashes. |
| `/media/`   | `media_volume` / `oms-media` PVC     | nginx (Compose) or backend (k8s) | `Cache-Control: private, max-age=300` — uploads include user-attributed photos and PDFs. |
| `/django-static/` | same volume, alias path | nginx | Same as `/static/`. |

Media URLs are guessable but not enumerable through the API. Don't host the
media path on a CDN that allows directory listing. If you offload to S3,
follow §6 of [BACKUP_RESTORE.md](BACKUP_RESTORE.md) for versioning + lifecycle.

## TLS / certificate handling

The Compose stack bundles certbot + Let's Encrypt automation in
`nginx/docker-entrypoint.d/10-letsencrypt.sh`:

- On first boot it generates a 1-day self-signed cert for `$DOMAIN` so nginx
  can listen on `:443` immediately.
- If `LETSENCRYPT_EMAIL` is set, it requests a real certificate via the HTTP-01
  challenge against `/.well-known/acme-challenge/` and reloads nginx.
- A daily `crond` job runs `certbot renew` (no-op until 30 days from expiry).
- Certificates persist in the `letsencrypt_certs` named volume; back it up
  alongside `.env` if you want to avoid re-issuing on a host rebuild.

For Kubernetes, terminate TLS at the Ingress controller (`cert-manager` is the
canonical pattern). The bundled `Ingress` ships without `tls:` entries; add
your own once cert-manager is wired up.

## Hardening checklist

Run through this before pointing real traffic at a Compose deploy:

1. **Firewall the host first.** UFW / iptables / cloud security group should
   default-deny inbound and explicitly allow only:
   - `80/tcp` and `443/tcp` (public web)
   - `1883/tcp` (or `8883/tcp` for TLS) **only** from the IPs of devices you
     own, ideally restricted to a VPN
   - `22/tcp` for SSH from your management network
   Drop `1883`, `8083`, `8084`, `8883`, `5555`, `18083` from the public
   internet unless you have a deliberate reason to expose them.
2. **Remove the host bind for ports you don't need.** Edit
   `docker-compose.prod.yml` and delete the `ports:` block on `flower` (use
   `/flower/` through nginx instead) and on `emqx` (use `/mqttadmin/`).
3. **Rotate `EMQX_DASHBOARD_PASSWORD`** to a strong value before first
   deploy. `scripts/validate-prod-env.sh` and `deploy.sh` enforce 8+ chars,
   mixed case, and a digit, but a weak-but-valid value is still a weak admin
   password.
4. **Enable Redis auth** if Redis is reachable from outside the compose
   network (e.g. you bound it to a host port for debugging). The shipped
   image runs without auth — fine for `expose:`-only, dangerous if exposed.
5. **Set `SECURE_PROXY_SSL_HEADER`** in Django when terminating TLS at
   nginx. The bundled settings derive this from the `X-Forwarded-Proto`
   header that nginx already sets; verify with the smoke test that
   `request.is_secure()` reports `True` on `/admin/login/`.
6. **Restrict admin surfaces.** `/admin/`, `/flower/`, and `/mqttadmin/`
   should never be reachable from the public internet without auth — and
   ideally not from the public internet at all. Either firewall them, lock
   them down with HTTP basic auth in nginx, or move them behind a VPN.
7. **Run [`SMOKE_TESTS.md`](SMOKE_TESTS.md)** to confirm the exposure
   matches what you configured. The smoke tests intentionally hit only
   public endpoints; if `/admin/login/` returns 200 and you didn't intend
   that, your firewall isn't doing what you think it is.

## Default exposure summary

> **Out of the box, the Compose file binds the following to `0.0.0.0`:**
> `80/tcp`, `443/tcp` (nginx), `5555/tcp` (Flower), `1883/8083/8084/8883/tcp`
> (EMQX MQTT family), and `18083/tcp` (EMQX dashboard).
>
> If you don't firewall the host, every one of those is reachable from the
> internet. The Compose defaults assume a host that is **not** on a public
> IP — running this stack on a public-facing VM without first running
> through the hardening checklist will expose Flower (no auth in the
> container), the EMQX dashboard, and the MQTT broker to the world.
