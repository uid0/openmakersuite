#!/bin/sh
set -eu

DOMAIN="${DOMAIN:-dallas.openmakersuite.net}"
LETSENCRYPT_DOMAINS="${LETSENCRYPT_DOMAINS:-}"

if [ -z "$LETSENCRYPT_DOMAINS" ]; then
    LETSENCRYPT_DOMAINS="$DOMAIN"
    export LETSENCRYPT_DOMAINS
fi

PRIMARY_DOMAIN=$(printf '%s\n' $LETSENCRYPT_DOMAINS | awk 'NR==1')
CERT_PATH="/etc/letsencrypt/live/${PRIMARY_DOMAIN}"
WEBROOT="/var/www/certbot"

mkdir -p "$WEBROOT"

if [ ! -f "$CERT_PATH/fullchain.pem" ] || [ ! -f "$CERT_PATH/privkey.pem" ]; then
    echo "[letsencrypt] Generating temporary self-signed certificate for $PRIMARY_DOMAIN"
    mkdir -p "$CERT_PATH"
    openssl req -x509 -nodes -newkey rsa:2048 \
        -days 1 \
        -keyout "$CERT_PATH/privkey.pem" \
        -out "$CERT_PATH/fullchain.pem" \
        -subj "/CN=$PRIMARY_DOMAIN" >/dev/null 2>&1
fi

request_cert() {
    domain_args=""
    for domain in $LETSENCRYPT_DOMAINS; do
        domain_args="$domain_args -d $domain"
    done

    if [ -z "${LETSENCRYPT_EMAIL:-}" ]; then
        echo "[letsencrypt] LETSENCRYPT_EMAIL is not set; skipping automatic certificate request." >&2
        return 1
    fi

    echo "[letsencrypt] Requesting certificates for: $LETSENCRYPT_DOMAINS"
    if certbot certonly --webroot -w "$WEBROOT" $domain_args \
        --email "${LETSENCRYPT_EMAIL}" \
        --agree-tos --no-eff-email \
        --keep-until-expiring \
        --rsa-key-size 4096 \
        --deploy-hook "nginx -s reload"; then
        echo "[letsencrypt] Certificate request completed"
        return 0
    else
        echo "[letsencrypt] Initial certificate request failed; continuing with existing certificates." >&2
        return 1
    fi
}

(
    sleep 5
    request_cert || true
) &

if [ -n "${LETSENCRYPT_EMAIL:-}" ]; then
    printf '0 */12 * * * certbot renew --webroot -w %s --quiet --deploy-hook "nginx -s reload"\n' "$WEBROOT" > /etc/crontabs/root
    crond -b -l 2
fi
