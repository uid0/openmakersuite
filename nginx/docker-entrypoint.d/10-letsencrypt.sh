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

# Check if valid certificates already exist
cert_exists_and_valid() {
    if [ ! -f "$CERT_PATH/fullchain.pem" ] || [ ! -f "$CERT_PATH/privkey.pem" ]; then
        return 1
    fi

    # Check if certificate is valid and not expiring soon (within 30 days)
    # Use openssl to check certificate validity
    if openssl x509 -in "$CERT_PATH/fullchain.pem" -noout -checkend 2592000 >/dev/null 2>&1; then
        # Certificate is valid for at least 30 days (2592000 seconds)
        return 0
    fi

    return 1
}

# Generate temporary self-signed certificate only if no valid certificate exists
if ! cert_exists_and_valid; then
    if [ ! -f "$CERT_PATH/fullchain.pem" ] || [ ! -f "$CERT_PATH/privkey.pem" ]; then
        echo "[letsencrypt] Generating temporary self-signed certificate for $PRIMARY_DOMAIN"
        mkdir -p "$CERT_PATH"
        openssl req -x509 -nodes -newkey rsa:2048 \
            -days 1 \
            -keyout "$CERT_PATH/privkey.pem" \
            -out "$CERT_PATH/fullchain.pem" \
            -subj "/CN=$PRIMARY_DOMAIN" >/dev/null 2>&1
    fi
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

    # Only request if certificate doesn't exist or is expiring soon
    if cert_exists_and_valid; then
        echo "[letsencrypt] Valid certificate already exists for $PRIMARY_DOMAIN; skipping request."
        return 0
    fi

    echo "[letsencrypt] Requesting certificates for: $LETSENCRYPT_DOMAINS"
    if certbot certonly --webroot -w "$WEBROOT" $domain_args \
        --cert-name "$PRIMARY_DOMAIN" \
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

# Only request certificate in background if email is set and certificate doesn't exist or is expiring
if [ -n "${LETSENCRYPT_EMAIL:-}" ]; then
    (
        sleep 5
        request_cert || true
    ) &

    # Set up cron job to renew certificates daily (certbot renew only renews if expiring within 30 days)
    printf '0 3 * * * certbot renew --cert-name %s --webroot -w %s --quiet --deploy-hook "nginx -s reload"\n' "$PRIMARY_DOMAIN" "$WEBROOT" > /etc/crontabs/root
    crond -b -l 2
fi
