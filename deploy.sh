#!/bin/bash
set -e

git pull

echo "🚀 Deploying Dallas Makerspace Inventory Management System..."

# Check if .env file exists
if [ ! -f .env ]; then
    echo "❌ Error: .env file not found!"
    echo "Please create a .env file from .env.prod.example"
    exit 1
fi

# Load environment variables
export $(cat .env | grep -v '^#' | xargs)

# Get git hash for build
export GIT_HASH=$(git rev-parse --short HEAD 2>/dev/null || echo "dev")
echo "📝 Building with git hash: $GIT_HASH"

# Verify GIT_HASH is set
if [ -z "$GIT_HASH" ] || [ "$GIT_HASH" = "dev" ]; then
    echo "⚠️  Warning: GIT_HASH is not set or is 'dev'. Using current commit."
    export GIT_HASH=$(git rev-parse --short HEAD 2>/dev/null || echo "dev")
fi

COMPOSE="docker compose -f docker-compose.prod.yml"
DEPLOY_LOG="${DEPLOY_LOG:-deploy.log}"

deploy_log() {
    echo "$@" | tee -a "$DEPLOY_LOG"
}

# Render the EMQX bootstrap admin file. EMQX 6.x enforces password complexity
# (8+ chars, mixed case, digit) when the dashboard user is created, and the
# legacy EMQX_DASHBOARD__DEFAULT_PASSWORD env var is bootstrap-only — if it
# fails complexity or is missing, EMQX silently falls back to its built-in
# `public` default and there is no way to recover without nuking emqx_data.
# bootstrap_users_file is re-applied on every boot, so we render it from the
# validated env var before bringing the broker up (oms-f9z).
echo "🔐 Rendering EMQX bootstrap admin file..."
if [ -z "${EMQX_DASHBOARD_PASSWORD:-}" ]; then
    echo "❌ EMQX_DASHBOARD_PASSWORD is not set in .env — refusing to deploy."
    echo "   Set it to a value with 8+ chars, mixed case, and at least one digit."
    exit 1
fi

PW="$EMQX_DASHBOARD_PASSWORD"
PW_ERR=""
if [ ${#PW} -lt 8 ]; then
    PW_ERR="must be at least 8 characters"
elif ! printf '%s' "$PW" | grep -q '[A-Z]'; then
    PW_ERR="must contain an uppercase letter"
elif ! printf '%s' "$PW" | grep -q '[a-z]'; then
    PW_ERR="must contain a lowercase letter"
elif ! printf '%s' "$PW" | grep -q '[0-9]'; then
    PW_ERR="must contain a digit"
elif [ "$PW" = "change-me-on-first-deploy" ]; then
    PW_ERR="is the placeholder value from .env.prod.example"
fi

if [ -n "$PW_ERR" ]; then
    echo "❌ EMQX_DASHBOARD_PASSWORD ${PW_ERR}."
    echo "   EMQX 6.x rejects weak passwords at bootstrap; pick a stronger value."
    exit 1
fi

mkdir -p scripts/emqx
# Write atomically: chmod first so the password is never world-readable on disk.
BOOTSTRAP_FILE="scripts/emqx/bootstrap-admins.txt"
umask 077
printf 'admin:%s\n' "$PW" > "$BOOTSTRAP_FILE"
chmod 600 "$BOOTSTRAP_FILE"
echo "✅ Wrote $BOOTSTRAP_FILE (admin user)"

# Stop existing containers
echo "⏹️  Stopping existing containers..."
$COMPOSE down

# Build images first — we need the backend image to run the migration check
# before any long-running backend container is started.
echo "🏗️  Building services..."
echo "📝 Using GIT_HASH=$GIT_HASH for build..."
export GIT_HASH
$COMPOSE build --no-cache frontend
$COMPOSE build --no-cache backend

# Start only the database + redis so we can run migrations against the target DB
# BEFORE the backend container starts serving traffic. This prevents the class of
# bug where migrations ship in code but never run in prod (oms-qqn, oms-p2x).
echo "🗄️  Starting database and cache for pre-deploy migration check..."
$COMPOSE up -d db redis

echo "⏳ Waiting for database healthcheck..."
DB_USER="${POSTGRES_USER:-makerspace}"
DB_NAME="${POSTGRES_DB:-makerspace_inventory}"
DB_READY_ATTEMPTS=60
for i in $(seq 1 $DB_READY_ATTEMPTS); do
    if $COMPOSE exec -T db pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; then
        echo "✅ Database is ready"
        break
    fi
    if [ "$i" -eq "$DB_READY_ATTEMPTS" ]; then
        echo "❌ Database did not become ready after ${DB_READY_ATTEMPTS}s"
        exit 1
    fi
    sleep 1
done

# Verify Django migration state against the live database, using the freshly
# built backend image. SKIP_DB_MIGRATIONS=1 disables the image entrypoint's
# auto-migrate so this call is a pure read.
echo "🔍 Verifying Django migration state..."
deploy_log ""
deploy_log "==== Migration check @ $(date -u +"%Y-%m-%dT%H:%M:%SZ") (GIT_HASH=${GIT_HASH}) ===="

MIGRATION_PLAN=$(
    $COMPOSE run --rm --no-deps -T \
        -e SKIP_DB_MIGRATIONS=1 \
        backend python manage.py showmigrations --plan --no-color
)

deploy_log "--- showmigrations --plan (pre-migrate) ---"
deploy_log "$MIGRATION_PLAN"

PENDING=$(echo "$MIGRATION_PLAN" | grep '^\[ \]' || true)

if [ -n "$PENDING" ]; then
    deploy_log ""
    deploy_log "⚠️  Pending migrations detected — auto-applying before backend start:"
    deploy_log "$PENDING"
    deploy_log ""

    if ! $COMPOSE run --rm --no-deps -T \
            -e SKIP_DB_MIGRATIONS=1 \
            backend sh -c \
            "python manage.py ensure_membership_initial_migration && python manage.py migrate --noinput" \
            2>&1 | tee -a "$DEPLOY_LOG"; then
        deploy_log ""
        deploy_log "❌ Migration apply FAILED — aborting deploy. Database may be in a partially-migrated state."
        deploy_log "   Review $DEPLOY_LOG and resolve before re-running deploy.sh."
        exit 1
    fi

    deploy_log ""
    deploy_log "--- showmigrations --plan (post-migrate) ---"
    $COMPOSE run --rm --no-deps -T \
        -e SKIP_DB_MIGRATIONS=1 \
        backend python manage.py showmigrations --plan --no-color 2>&1 | tee -a "$DEPLOY_LOG"

    deploy_log "✅ Pending migrations applied."
else
    deploy_log "✅ No pending migrations — database schema matches code."
fi

# Start the remaining services. Backend's entrypoint will re-run migrate as a
# safety net, which is a no-op since we just applied above.
echo "🚀 Starting services..."
$COMPOSE up -d

# Collect static files
echo "📁 Collecting static files..."
$COMPOSE exec -T backend python manage.py collectstatic --noinput

# Wait for frontend to finish building and ensure volume is populated
echo "⏳ Waiting for frontend build to complete..."
sleep 5

# Restart nginx to pick up any frontend changes
echo "🔄 Restarting nginx to pick up frontend changes..."
$COMPOSE restart nginx

# Verify frontend build
echo "🔍 Verifying frontend build..."
if $COMPOSE exec -T nginx ls -la /app/frontend/index.html >/dev/null 2>&1; then
    echo "✅ Frontend files found!"
else
    echo "❌ Frontend files NOT found in nginx container!"
    echo "   Checking frontend container..."
    $COMPOSE exec -T frontend ls -la /app/frontend/ || echo "   Frontend volume is empty!"
fi

# Verify static files
echo "🔍 Verifying Django static files..."
if $COMPOSE exec -T nginx ls -la /app/staticfiles/ >/dev/null 2>&1; then
    echo "✅ Django static files found!"
else
    echo "❌ Django static files NOT found!"
fi

echo "✅ Deployment complete!"
echo ""
echo "📍 Your application is now running at:"
echo "   http://${DOMAIN:-dallas.openmakersuite.net}"
echo ""
echo "🔧 Useful commands:"
echo "   View logs:    $COMPOSE logs -f"
echo "   Stop:         $COMPOSE down"
echo "   Restart:      $COMPOSE restart"
echo "   Shell:        $COMPOSE exec backend python manage.py shell"
