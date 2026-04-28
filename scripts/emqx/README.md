# EMQX Bootstrap Admin

`bootstrap-admins.txt` is **rendered by `deploy.sh`** from
`$EMQX_DASHBOARD_PASSWORD` and mounted into the EMQX container at
`/opt/emqx/etc/bootstrap_users`. It is gitignored.

EMQX 6.x reads this file on every boot and (re-)creates the listed dashboard
users, which makes the admin login deterministic across container restarts —
the older `EMQX_DASHBOARD__DEFAULT_PASSWORD` env var is bootstrap-only and
silently falls back to `public` when the var is empty or fails complexity
checks (oms-f9z).

File format is one `username:password` line per admin; we ship a single
`admin` user driven by the env var. Password complexity is validated by
`deploy.sh` before render.
