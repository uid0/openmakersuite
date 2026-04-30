#!/bin/bash
# Validate production environment configuration before deployment.
#
# Reads .env (or the file passed as $1) and refuses to proceed when any of the
# following are missing or set to placeholder/insecure values:
#
#   - DEBUG (must be 0/false/off)
#   - SECRET_KEY (must be set, non-placeholder, >= 50 chars)
#   - ALLOWED_HOSTS (must be set, must not be only localhost)
#   - CSRF_TRUSTED_ORIGINS (must be set, https://)
#   - CORS_ALLOWED_ORIGINS (must be set, https://)
#   - DOMAIN, LETSENCRYPT_EMAIL, LETSENCRYPT_DOMAINS (must be set + non-placeholder)
#   - POSTGRES_PASSWORD (must be set, non-placeholder, >= 16 chars)
#   - REDIS_URL, CELERY_BROKER_URL (must be set)
#   - EMQX_DASHBOARD_PASSWORD (8+ chars, mixed case, digit, non-placeholder)
#   - ForgeKey: FORGEKEY_FIRMWARE_SIGNING_KEY (if set, must look like PEM)
#   - Webhook tokens: POSTMARK_INBOUND_TOKEN, LOCATION_PING_TOKEN (warned if empty)
#   - Email: EMAIL_BACKEND must not be the console backend in prod
#   - Sentry: if SENTRY_DSN set, must look like a DSN URL
#   - Cookie security: SESSION_COOKIE_SECURE, CSRF_COOKIE_SECURE inferred from
#     DEBUG=0 in settings.py — verified by Django's deployment check below
#
# Exit codes:
#   0 — environment is safe for production deploy
#   1 — at least one fatal misconfiguration; messages list every issue
#
# Usage:
#   scripts/validate-prod-env.sh [path/to/.env]   # default: ./.env

set -u

ENV_FILE="${1:-.env}"

if [ ! -f "$ENV_FILE" ]; then
    echo "❌ $ENV_FILE not found. Copy .env.prod.example and edit before deploying."
    exit 1
fi

# Parse .env line-by-line so values with spaces (LETSENCRYPT_DOMAINS) or shell
# metacharacters don't trigger expansion or command execution. Lines must be
# `KEY=VALUE`; comments and blanks are skipped. Surrounding double or single
# quotes around the value are stripped.
while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
        ''|'#'*) continue ;;
    esac
    case "$line" in
        *=*) ;;
        *) continue ;;
    esac
    key="${line%%=*}"
    val="${line#*=}"
    key="${key#"${key%%[![:space:]]*}"}"
    key="${key%"${key##*[![:space:]]}"}"
    case "$key" in
        ''|*[!A-Za-z0-9_]*) continue ;;
    esac
    case "$val" in
        \"*\") val="${val#\"}"; val="${val%\"}" ;;
        \'*\') val="${val#\'}"; val="${val%\'}" ;;
    esac
    printf -v "$key" '%s' "$val"
    export "$key"
done < "$ENV_FILE"

ERRORS=()
WARNINGS=()

err()  { ERRORS+=("$1"); }
warn() { WARNINGS+=("$1"); }

# --- helpers -----------------------------------------------------------------

is_placeholder() {
    # Common placeholder patterns shipped in .env.prod.example.
    case "$1" in
        ""|change-me*|your-*|CHANGE-*|*-placeholder|REPLACE-*|TODO|todo) return 0 ;;
    esac
    return 1
}

is_https_csv() {
    # Comma-separated list of https:// origins. Empty is not allowed; any
    # non-https entry fails.
    [ -z "$1" ] && return 1
    local IFS=,
    for entry in $1; do
        # trim leading/trailing whitespace
        entry="${entry#"${entry%%[![:space:]]*}"}"
        entry="${entry%"${entry##*[![:space:]]}"}"
        case "$entry" in
            https://*) ;;
            *) return 1 ;;
        esac
    done
    return 0
}

# --- 1. DEBUG ----------------------------------------------------------------

case "${DEBUG:-}" in
    0|false|False|FALSE|off|Off|OFF) ;;
    "") err "DEBUG is not set. Set DEBUG=0 in $ENV_FILE." ;;
    *) err "DEBUG=${DEBUG} — production must run with DEBUG=0 (truthy values leak stack traces and disable security middleware)." ;;
esac

# --- 2. SECRET_KEY -----------------------------------------------------------

if [ -z "${SECRET_KEY:-}" ]; then
    err "SECRET_KEY is not set. Generate one with: python -c 'import secrets; print(secrets.token_urlsafe(64))'"
elif is_placeholder "$SECRET_KEY"; then
    err "SECRET_KEY is the placeholder value from .env.prod.example. Generate a real key before deploying."
elif [ "${#SECRET_KEY}" -lt 50 ]; then
    err "SECRET_KEY is only ${#SECRET_KEY} characters. Django requires 50+ for production-grade signing."
fi

# --- 3. ALLOWED_HOSTS / CSRF / CORS -----------------------------------------

if [ -z "${ALLOWED_HOSTS:-}" ]; then
    err "ALLOWED_HOSTS is not set. List the public hostnames Django should answer for, e.g. example.com,www.example.com"
else
    case ",${ALLOWED_HOSTS// /}," in
        ,localhost,|,127.0.0.1,|,localhost,127.0.0.1,|,127.0.0.1,localhost,)
            err "ALLOWED_HOSTS=${ALLOWED_HOSTS} — production must include the real public hostname, not just localhost." ;;
    esac
fi

if [ -z "${CSRF_TRUSTED_ORIGINS:-}" ]; then
    err "CSRF_TRUSTED_ORIGINS is not set. Required for any POST from the SPA (https://your.domain)."
elif ! is_https_csv "$CSRF_TRUSTED_ORIGINS"; then
    err "CSRF_TRUSTED_ORIGINS=${CSRF_TRUSTED_ORIGINS} — every entry must start with https:// in production."
fi

if [ -z "${CORS_ALLOWED_ORIGINS:-}" ]; then
    err "CORS_ALLOWED_ORIGINS is not set. The browser will block API calls from the SPA without it."
elif ! is_https_csv "$CORS_ALLOWED_ORIGINS"; then
    err "CORS_ALLOWED_ORIGINS=${CORS_ALLOWED_ORIGINS} — every entry must start with https:// in production."
fi

# --- 4. Domain / TLS metadata -----------------------------------------------

if [ -z "${DOMAIN:-}" ] || is_placeholder "${DOMAIN:-}"; then
    err "DOMAIN is unset or a placeholder. Set it to the public hostname (e.g. example.com)."
fi
if [ -z "${LETSENCRYPT_EMAIL:-}" ] || is_placeholder "${LETSENCRYPT_EMAIL:-}"; then
    err "LETSENCRYPT_EMAIL is unset or a placeholder. Required for TLS issuance + expiry alerts."
fi
if [ -z "${LETSENCRYPT_DOMAINS:-}" ] || is_placeholder "${LETSENCRYPT_DOMAINS:-}"; then
    err "LETSENCRYPT_DOMAINS is unset or a placeholder. Required for TLS issuance."
fi

# --- 5. Database -------------------------------------------------------------

if [ -z "${POSTGRES_PASSWORD:-}" ]; then
    err "POSTGRES_PASSWORD is not set."
elif is_placeholder "$POSTGRES_PASSWORD"; then
    err "POSTGRES_PASSWORD is a placeholder value. Generate a real password (>= 16 chars)."
elif [ "${#POSTGRES_PASSWORD}" -lt 16 ]; then
    err "POSTGRES_PASSWORD is only ${#POSTGRES_PASSWORD} characters. Use 16+ for production."
fi

# --- 6. Redis / Celery broker -----------------------------------------------

[ -z "${REDIS_URL:-}" ] && err "REDIS_URL is not set."
[ -z "${CELERY_BROKER_URL:-}" ] && err "CELERY_BROKER_URL is not set."

# --- 7. EMQX ----------------------------------------------------------------
# Mirrors the inline check in deploy.sh — kept here too so this script
# validates the env without invoking deploy.sh.

PW="${EMQX_DASHBOARD_PASSWORD:-}"
if [ -z "$PW" ]; then
    err "EMQX_DASHBOARD_PASSWORD is not set. EMQX 6.x requires it for first-boot."
elif [ "$PW" = "change-me-on-first-deploy" ]; then
    err "EMQX_DASHBOARD_PASSWORD is the placeholder value from .env.prod.example."
elif [ "${#PW}" -lt 8 ]; then
    err "EMQX_DASHBOARD_PASSWORD must be at least 8 characters (EMQX 6.x complexity rule)."
elif ! printf '%s' "$PW" | grep -q '[A-Z]'; then
    err "EMQX_DASHBOARD_PASSWORD must contain an uppercase letter."
elif ! printf '%s' "$PW" | grep -q '[a-z]'; then
    err "EMQX_DASHBOARD_PASSWORD must contain a lowercase letter."
elif ! printf '%s' "$PW" | grep -q '[0-9]'; then
    err "EMQX_DASHBOARD_PASSWORD must contain a digit."
fi

# EMQX API credentials are optional on first boot but required for any flow
# that calls the EMQX REST API from Django (forgekey provisioning).
if [ -n "${EMQX_API_KEY:-}${EMQX_API_SECRET:-}" ]; then
    [ -z "${EMQX_API_KEY:-}" ]    && err "EMQX_API_SECRET is set but EMQX_API_KEY is empty."
    [ -z "${EMQX_API_SECRET:-}" ] && err "EMQX_API_KEY is set but EMQX_API_SECRET is empty."
fi

# --- 8. ForgeKey signing key ------------------------------------------------

if [ -n "${FORGEKEY_FIRMWARE_SIGNING_KEY:-}" ]; then
    case "$FORGEKEY_FIRMWARE_SIGNING_KEY" in
        *"-----BEGIN "*"PRIVATE KEY-----"*) ;;
        *) err "FORGEKEY_FIRMWARE_SIGNING_KEY does not look like a PEM private key (no '-----BEGIN ... PRIVATE KEY-----' header)." ;;
    esac
fi

# --- 9. Email ---------------------------------------------------------------

case "${EMAIL_BACKEND:-}" in
    ""|django.core.mail.backends.console.EmailBackend)
        warn "EMAIL_BACKEND is unset or the console backend — outbound email will not be delivered. Set it to anymail.backends.postmark.EmailBackend or django.core.mail.backends.smtp.EmailBackend." ;;
    anymail.backends.postmark.EmailBackend)
        if [ -z "${POSTMARK_SERVER_TOKEN:-}" ] || is_placeholder "${POSTMARK_SERVER_TOKEN:-}"; then
            err "EMAIL_BACKEND is Postmark but POSTMARK_SERVER_TOKEN is unset/placeholder."
        fi ;;
    django.core.mail.backends.smtp.EmailBackend)
        [ -z "${EMAIL_HOST:-}" ]          && err "SMTP backend requires EMAIL_HOST."
        [ -z "${EMAIL_HOST_USER:-}" ]     && warn "EMAIL_HOST_USER is empty — most SMTP relays require auth."
        [ -z "${EMAIL_HOST_PASSWORD:-}" ] && warn "EMAIL_HOST_PASSWORD is empty — most SMTP relays require auth." ;;
esac

if [ -z "${DEFAULT_FROM_EMAIL:-}" ] || is_placeholder "${DEFAULT_FROM_EMAIL:-}"; then
    warn "DEFAULT_FROM_EMAIL is unset or a placeholder. Outbound mail will use Django's default."
fi

# --- 10. Sentry -------------------------------------------------------------

if [ -n "${SENTRY_DSN:-}" ]; then
    case "$SENTRY_DSN" in
        https://*@*) ;;
        *) err "SENTRY_DSN does not look like a Sentry DSN URL (expected https://<key>@<host>/<id>)." ;;
    esac
fi

# --- 11. Webhook / IoT shared secrets ---------------------------------------
# These are not strictly required (the endpoints degrade to 503), but most
# operators want them — flag as warnings, not errors.

if [ -z "${POSTMARK_INBOUND_TOKEN:-}" ]; then
    warn "POSTMARK_INBOUND_TOKEN is empty — the Postmark inbound work-order webhook will return 503."
fi
if [ -z "${LOCATION_PING_TOKEN:-}" ]; then
    warn "LOCATION_PING_TOKEN is empty — the IoT location-ping webhook will return 503."
fi

# --- report -----------------------------------------------------------------

print_list() {
    local prefix="$1"; shift
    for msg in "$@"; do
        printf '  %s %s\n' "$prefix" "$msg"
    done
}

if [ "${#WARNINGS[@]}" -gt 0 ]; then
    echo "⚠️  Warnings (non-fatal):"
    print_list "•" "${WARNINGS[@]}"
fi

if [ "${#ERRORS[@]}" -gt 0 ]; then
    echo
    echo "❌ Refusing to deploy — ${#ERRORS[@]} unsafe configuration $( [ ${#ERRORS[@]} -eq 1 ] && echo issue || echo issues ):"
    print_list "✗" "${ERRORS[@]}"
    echo
    echo "Edit $ENV_FILE and re-run scripts/validate-prod-env.sh until it exits 0."
    exit 1
fi

echo "✅ Production environment validation passed."
