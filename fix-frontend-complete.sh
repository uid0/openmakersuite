#!/bin/bash
set -e

echo "🔧 Complete Frontend Fix - API URLs & Sentry CSP"
echo ""
echo "This script fixes:"
echo "  1. API URL doubling (/api/api/... → /api/...)"
echo "  2. Frontend admin route (/admin → /orderadmin)"
echo "  3. Sentry CSP errors (added proper Content-Security-Policy)"
echo ""
echo "⚠️  This will completely rebuild the frontend container"
echo ""

# Stop all containers
echo "⏹️  Stopping all containers..."
docker-compose -f docker-compose.prod.yml down

# Remove old frontend volume to ensure clean rebuild
echo "🗑️  Removing old frontend volume..."
docker volume rm openmakersuite_frontend_build 2>/dev/null || echo "   Volume doesn't exist or already removed"

# Remove old frontend images to force clean rebuild
echo "🗑️  Removing old frontend images..."
docker-compose -f docker-compose.prod.yml rm -f frontend 2>/dev/null || true
docker rmi $(docker images -q '*frontend*' 2>/dev/null) 2>/dev/null || echo "   No old images to remove"

# Rebuild frontend with no cache
echo "🏗️  Rebuilding frontend (this may take a few minutes)..."
docker-compose -f docker-compose.prod.yml build --no-cache frontend

# Rebuild backend to collect static files with new STATIC_URL
echo "🏗️  Rebuilding backend..."
docker-compose -f docker-compose.prod.yml build --no-cache backend

# Start all services
echo "🚀 Starting all services..."
docker-compose -f docker-compose.prod.yml up -d

# Wait for services
echo "⏳ Waiting for services to initialize..."
sleep 15

# Collect Django static files
echo "📁 Collecting Django static files..."
docker-compose -f docker-compose.prod.yml exec -T backend python manage.py collectstatic --noinput

# Restart nginx to pick up changes
echo "🔄 Restarting nginx..."
docker-compose -f docker-compose.prod.yml restart nginx

# Verify the build
echo ""
echo "🔍 Verifying frontend build..."
if docker-compose -f docker-compose.prod.yml exec -T nginx test -f /app/frontend/index.html; then
    echo "   ✅ Frontend index.html found"

    # Check if Sentry script is present with CSP
    if docker-compose -f docker-compose.prod.yml exec -T nginx grep -q "sentry-cdn" /app/frontend/index.html; then
        echo "   ✅ Sentry script present"
        if docker-compose -f docker-compose.prod.yml exec -T nginx grep -q "Content-Security-Policy" /app/frontend/index.html; then
            echo "   ✅ CSP header configured for Sentry"
        else
            echo "   ⚠️  WARNING: CSP header missing"
        fi
    else
        echo "   ⚠️  WARNING: Sentry script not found"
    fi
else
    echo "   ❌ Frontend files NOT found!"
    exit 1
fi

echo ""
echo "✅ Frontend rebuild complete!"
echo ""
echo "🌐 Test your site:"
echo "   Homepage:           http://${DOMAIN:-dms.openmakersuite.net}/"
echo "   Order Admin:        http://${DOMAIN:-dms.openmakersuite.net}/orderadmin"
echo "   Django Admin:       http://${DOMAIN:-dms.openmakersuite.net}/admin/"
echo ""
echo "🔍 Open browser console and verify:"
echo "   ✓ API calls go to /api/inventory/... (NOT /api/api/...)"
echo "   ✓ No Sentry CSP errors (Sentry should load successfully)"
echo "   ✓ Admin dashboard accessible at /orderadmin"
echo "   ✓ Django admin accessible at /admin/"
echo ""
echo "💡 If you still see old content:"
echo "   1. Hard refresh: Ctrl+Shift+R (Windows/Linux) or Cmd+Shift+R (Mac)"
echo "   2. Clear browser cache"
echo "   3. Try incognito/private window"
echo ""
