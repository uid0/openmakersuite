#!/bin/sh
# Let's Encrypt lifecycle for the nginx container — a first-class citizen.
#
# nginx serves TLS from a STABLE, entrypoint-managed path:
#
#     /etc/letsencrypt/nginx/fullchain.pem
#     /etc/letsencrypt/nginx/privkey.pem
#
# Those two files are symlinks this script points at whichever certbot lineage
# is actually current on this box — regardless of the archive/suffix certbot
# landed on. certbot renews the lineage in place under a fixed --cert-name, and
# a deploy hook (installed below, in /etc/letsencrypt/renewal-hooks/deploy/)
# re-points these symlinks and reloads nginx on every issuance/renewal. Because
# the nginx config never hard-codes a lineage directory, a renewal — or an
# already-forked "-00NN" lineage from before this was fixed — can no longer
# strand nginx on a stale certificate.
#
# History: nginx used to point straight at /etc/letsencrypt/live/${DOMAIN}-0001,
# a hard-coded lineage number. Renewals marched the real lineage on to -0032
# while nginx stayed pinned to the long-dead -0001, so every deploy required
# hand-editing the cert path (regression from #256; original config was correct).
# The fork itself was caused by seeding a self-signed cert INTO the certbot
# namespace (/etc/letsencrypt/live/<domain>/); certbot refuses to write a lineage
# over a directory it doesn't own and forks "<domain>-00NN" instead. This script
# fixes both: the bootstrap self-signed lives OUTSIDE live/, and nginx follows
# the live lineage via symlink instead of a pinned number.
set -eu

DOMAIN="${DOMAIN:-oms.example.com}"
LETSENCRYPT_DOMAINS="${LETSENCRYPT_DOMAINS:-}"
[ -n "$LETSENCRYPT_DOMAINS" ] || LETSENCRYPT_DOMAINS="$DOMAIN"

# certbot lineage name — stable for the life of the deployment so renewals stay
# in a single lineage. The first domain in the SAN list is the canonical name.
PRIMARY_DOMAIN=$(printf '%s\n' $LETSENCRYPT_DOMAINS | awk 'NR==1')
CERT_NAME="$PRIMARY_DOMAIN"

WEBROOT="/var/www/certbot"
LE_DIR="/etc/letsencrypt"
NGINX_CERT_DIR="$LE_DIR/nginx"          # stable path nginx reads (two symlinks)
BOOTSTRAP_DIR="$LE_DIR/bootstrap"       # self-signed, so nginx can boot pre-LE
DEPLOY_HOOK_DIR="$LE_DIR/renewal-hooks/deploy"

mkdir -p "$WEBROOT" "$NGINX_CERT_DIR" "$BOOTSTRAP_DIR" "$DEPLOY_HOOK_DIR"

# Point the stable nginx cert path at a directory holding fullchain/privkey.
activate() {
    ln -sf "$1/fullchain.pem" "$NGINX_CERT_DIR/fullchain.pem"
    ln -sf "$1/privkey.pem"   "$NGINX_CERT_DIR/privkey.pem"
}

# Echo the current, valid, CA-issued lineage dir for CERT_NAME (highest suffix
# first, then the unsuffixed canonical dir). Skips self-signed leftovers and
# expired lineages. Returns non-zero when none qualifies (fresh box / not yet
# issued), so callers fall back to the bootstrap cert.
resolve_live_dir() {
    _best=""
    for _d in $(ls -d "$LE_DIR/live/$CERT_NAME"-* "$LE_DIR/live/$CERT_NAME" 2>/dev/null | sort -r); do
        [ -f "$_d/fullchain.pem" ] && [ -f "$_d/privkey.pem" ] || continue
        _subj=$(openssl x509 -in "$_d/fullchain.pem" -noout -subject 2>/dev/null) || _subj=""
        _iss=$(openssl x509 -in "$_d/fullchain.pem" -noout -issuer 2>/dev/null) || _iss=""
        [ -n "$_iss" ] || continue
        # A self-signed bootstrap cert has subject == issuer; a real LE cert does
        # not. Strip the "subject="/"issuer=" prefixes and compare.
        _subj=${_subj#subject=}
        _iss=${_iss#issuer=}
        [ "$_subj" != "$_iss" ] || continue
        openssl x509 -in "$_d/fullchain.pem" -noout -checkend 0 >/dev/null 2>&1 || continue
        _best="$_d"
        break
    done
    [ -n "$_best" ] || return 1
    printf '%s\n' "$_best"
}

# A self-signed bootstrap so nginx can start (and serve the ACME http-01
# challenge on :80) before a real certificate exists. Kept OUTSIDE
# /etc/letsencrypt/live/ so it can never collide with certbot's lineage naming.
# 90-day validity means a prolonged LE outage degrades to a browser warning
# rather than an outright expired-cert failure while issuance keeps retrying.
ensure_bootstrap() {
    if [ ! -f "$BOOTSTRAP_DIR/fullchain.pem" ] || [ ! -f "$BOOTSTRAP_DIR/privkey.pem" ] \
       || ! openssl x509 -in "$BOOTSTRAP_DIR/fullchain.pem" -noout -checkend 0 >/dev/null 2>&1; then
        echo "[letsencrypt] generating self-signed bootstrap certificate for $PRIMARY_DOMAIN"
        openssl req -x509 -nodes -newkey rsa:2048 -days 90 \
            -keyout "$BOOTSTRAP_DIR/privkey.pem" \
            -out    "$BOOTSTRAP_DIR/fullchain.pem" \
            -subj "/CN=$PRIMARY_DOMAIN" >/dev/null 2>&1
    fi
}

# Deploy hook — runs on every certbot issuance/renewal (both the boot-time
# `certonly` below and the daily `certbot renew` cron). certbot exports
# $RENEWED_LINEAGE = the live dir of the cert it just wrote. Lives on the
# persisted volume because a file baked into the image would be shadowed by the
# /etc/letsencrypt volume mount at runtime.
install_deploy_hook() {
    cat > "$DEPLOY_HOOK_DIR/10-nginx.sh" <<'HOOK'
#!/bin/sh
set -eu
NGINX_CERT_DIR="/etc/letsencrypt/nginx"
ln -sf "$RENEWED_LINEAGE/fullchain.pem" "$NGINX_CERT_DIR/fullchain.pem"
ln -sf "$RENEWED_LINEAGE/privkey.pem"   "$NGINX_CERT_DIR/privkey.pem"
# Reload only if the master is already running; ignore failure during the very
# first issuance when nginx may still be starting (the boot path activates the
# symlink regardless, so nginx picks the cert up when it comes up).
nginx -s reload 2>/dev/null || true
HOOK
    chmod +x "$DEPLOY_HOOK_DIR/10-nginx.sh"
}

# --- point nginx at the best certificate available right now ------------------
ensure_bootstrap
install_deploy_hook
if live_dir=$(resolve_live_dir); then
    echo "[letsencrypt] activating existing lineage: $live_dir"
    activate "$live_dir"
else
    echo "[letsencrypt] no valid Let's Encrypt certificate yet; serving self-signed bootstrap"
    activate "$BOOTSTRAP_DIR"
fi

# --- request / renew in the background so nginx can serve the ACME challenge ---
request_cert() {
    if [ -z "${LETSENCRYPT_EMAIL:-}" ]; then
        echo "[letsencrypt] LETSENCRYPT_EMAIL unset; skipping automatic issuance." >&2
        return 0
    fi

    domain_args=""
    for d in $LETSENCRYPT_DOMAINS; do
        domain_args="$domain_args -d $d"
    done

    echo "[letsencrypt] requesting/renewing certificate for: $LETSENCRYPT_DOMAINS (cert-name=$CERT_NAME)"
    # --keep-until-expiring makes this a no-op when the cert isn't near expiry.
    # No --deploy-hook here on purpose: the renewal-hooks/deploy/ script fires
    # for both this issuance and the cron renew, so the reload path is identical.
    if ! certbot certonly --webroot -w "$WEBROOT" $domain_args \
        --cert-name "$CERT_NAME" \
        --email "$LETSENCRYPT_EMAIL" \
        --agree-tos --no-eff-email \
        --keep-until-expiring \
        --rsa-key-size 4096 \
        --non-interactive; then
        echo "[letsencrypt] certbot run failed; keeping current certificate." >&2
        return 0
    fi

    # Safety net: if certbot was a no-op (keep-until-expiring) the deploy hook
    # did not fire, so re-resolve and activate in case boot-time resolution ran
    # before the lineage was valid.
    if live_dir=$(resolve_live_dir); then
        activate "$live_dir"
        nginx -s reload 2>/dev/null || true
    fi
}

if [ -n "${LETSENCRYPT_EMAIL:-}" ]; then
    (
        sleep 5
        request_cert || true
    ) &

    # Daily renewal. `certbot renew` renews only lineages within 30 days of
    # expiry and reuses each lineage's stored authenticator/webroot; the
    # deploy hook above handles the nginx symlink swap + reload.
    printf '0 3 * * * certbot renew --quiet\n' > /etc/crontabs/root
    crond -b -l 2
fi
