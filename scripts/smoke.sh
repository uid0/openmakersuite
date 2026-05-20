#!/bin/bash
# Post-deploy / post-restore smoke checks for OpenMakerSuite.
#
# Scripted form of deploy/SMOKE_TESTS.md. Run this against a deployed stack
# (or a freshly restored DR target) and the exit code tells you whether the
# product surfaces are reachable. Each check is independent — none of them
# depend on application data, so they work against a freshly migrated or
# freshly restored database.
#
# Usage:
#   HOST=oms.example.com SCHEME=https scripts/smoke.sh
#   scripts/smoke.sh --json > /var/log/oms/smoke-$(date -u +%FT%TZ).json
#
# Exit codes:
#   0   every required check passed
#   1   at least one required check failed
#   2   internal scripting error (curl/jq missing, etc.)
set -euo pipefail

HOST="${HOST:-localhost}"
SCHEME="${SCHEME:-http}"
TIMEOUT="${TIMEOUT:-10}"
JSON=0

usage() {
    cat <<EOF
Usage: $0 [--json] [--help]

Environment:
  HOST       Hostname or host:port to probe (default: localhost)
  SCHEME     http or https (default: http)
  TIMEOUT    Per-request timeout in seconds (default: 10)

Flags:
  --json     Emit a JSON result document instead of human output
  --help     Show this help
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --json)   JSON=1; shift ;;
        --help|-h) usage; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; usage >&2; exit 1 ;;
    esac
done

if ! command -v curl >/dev/null 2>&1; then
    echo "smoke.sh requires curl" >&2
    exit 2
fi

base="$SCHEME://$HOST"

# Each check writes one line to a results file as: STATUS|NAME|DETAIL
RESULTS=$(mktemp)
trap 'rm -f "$RESULTS"' EXIT

record() {
    local status="$1" name="$2" detail="$3"
    printf '%s|%s|%s\n' "$status" "$name" "$detail" >> "$RESULTS"
}

http_status() {
    curl -fsS -o /dev/null -w '%{http_code}' --max-time "$TIMEOUT" "$1" 2>/dev/null || echo 000
}

# Like http_status but doesn't fail on non-2xx — we want the code itself.
http_status_any() {
    curl -sS -o /dev/null -w '%{http_code}' --max-time "$TIMEOUT" "$1" 2>/dev/null || echo 000
}

post_status_any() {
    local url="$1" body="${2:-{}}"
    curl -sS -o /dev/null -w '%{http_code}' --max-time "$TIMEOUT" \
        -X POST -H 'Content-Type: application/json' --data "$body" "$url" 2>/dev/null || echo 000
}

# 1. Frontend SPA shell
if curl -fsSL --max-time "$TIMEOUT" "$base/" 2>/dev/null | grep -q '<div id="root">'; then
    record PASS frontend_spa "SPA shell served at $base/"
else
    record FAIL frontend_spa "no <div id=\"root\"> at $base/"
fi

# 2a. Liveness
livez_code=$(http_status_any "$base/api/health/livez/")
if [ "$livez_code" = "200" ]; then
    record PASS backend_livez "200 at /api/health/livez/"
else
    record FAIL backend_livez "got HTTP $livez_code at /api/health/livez/"
fi

# 2b. Readiness
readyz_code=$(http_status_any "$base/api/health/readyz/")
if [ "$readyz_code" = "200" ]; then
    record PASS backend_readyz "200 at /api/health/readyz/"
else
    record FAIL backend_readyz "got HTTP $readyz_code at /api/health/readyz/ (expected 200; 503 means a dep is down)"
fi

# 3. OpenAPI schema + Swagger UI
schema_code=$(http_status_any "$base/api/schema/")
if [ "$schema_code" = "200" ]; then
    record PASS api_schema "200 at /api/schema/"
else
    record FAIL api_schema "got HTTP $schema_code at /api/schema/"
fi

docs_code=$(http_status_any "$base/api/docs/")
if [ "$docs_code" = "200" ]; then
    record PASS api_docs "200 at /api/docs/"
else
    record FAIL api_docs "got HTTP $docs_code at /api/docs/"
fi

# 4. Admin login page reachable
admin_code=$(http_status_any "$base/admin/login/")
if [ "$admin_code" = "200" ]; then
    record PASS admin_login "200 at /admin/login/"
else
    record FAIL admin_login "got HTTP $admin_code at /admin/login/"
fi

# 7. Static assets served (admin css is collected unconditionally)
static_code=$(http_status_any "$base/static/admin/css/base.css")
if [ "$static_code" = "200" ]; then
    record PASS static_assets "200 at /static/admin/css/base.css"
else
    record FAIL static_assets "got HTTP $static_code at /static/admin/css/base.css"
fi

# 7b. Media route exists (404 OK on a fresh restore with no uploads yet)
media_code=$(http_status_any "$base/media/")
case "$media_code" in
    2*|3*|404) record PASS media_route "HTTP $media_code at /media/ (route reachable)" ;;
    *)        record FAIL media_route "got HTTP $media_code at /media/ — 5xx implies PVC/volume not mounted" ;;
esac

# 8. Public makerspace endpoints
for path in /api/customization/settings/ /api/dashboard/messages/ /api/dashboard/inventory-summary/; do
    code=$(http_status_any "$base$path")
    if [ "$code" = "200" ]; then
        record PASS "public$path" "200 at $path"
    else
        record FAIL "public$path" "got HTTP $code at $path"
    fi
done

# 9. Public QR / kiosk workflow — AC-25 gate
lookup_code=$(http_status_any "$base/api/inventory/items/lookup/?qr_code=__smoke_test__")
# 200 OK if a test item exists, 404 if not; either proves routing+serializer+DB.
case "$lookup_code" in
    200|404) record PASS public_qr_lookup "HTTP $lookup_code at /api/inventory/items/lookup/ (AC-25 gate)" ;;
    *)       record FAIL public_qr_lookup "got HTTP $lookup_code at /api/inventory/items/lookup/" ;;
esac

ping_code=$(post_status_any "$base/api/location-checkins/webhook/")
# 401/403/503 are all "intentional gate" responses; 5xx (other than 503) is bug.
case "$ping_code" in
    401|403|503) record PASS public_location_webhook "HTTP $ping_code at /api/location-checkins/webhook/ (auth gate active)" ;;
    *)           record FAIL public_location_webhook "got HTTP $ping_code at /api/location-checkins/webhook/" ;;
esac

# Tally + emit
total=$(wc -l < "$RESULTS" | tr -d ' ')
failed=$(grep -c '^FAIL' "$RESULTS" || true)
passed=$(grep -c '^PASS' "$RESULTS" || true)

if [ "$JSON" = "1" ]; then
    {
        printf '{"host":"%s","scheme":"%s","total":%s,"passed":%s,"failed":%s,"checks":[' \
            "$HOST" "$SCHEME" "$total" "$passed" "$failed"
        sep=""
        while IFS='|' read -r status name detail; do
            printf '%s{"status":"%s","name":"%s","detail":"%s"}' \
                "$sep" "$status" "$name" "$(printf '%s' "$detail" | sed 's/\\/\\\\/g; s/"/\\"/g')"
            sep=","
        done < "$RESULTS"
        printf ']}\n'
    }
else
    printf 'OMS smoke checks against %s\n' "$base"
    printf '  total:  %s\n' "$total"
    printf '  passed: %s\n' "$passed"
    printf '  failed: %s\n' "$failed"
    printf -- '---\n'
    while IFS='|' read -r status name detail; do
        printf '  [%s] %-30s %s\n' "$status" "$name" "$detail"
    done < "$RESULTS"
fi

[ "$failed" = "0" ]
