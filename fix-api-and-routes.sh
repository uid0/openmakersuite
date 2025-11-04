#!/bin/bash
set -e

echo "🔧 Fixing API URL Doubling and Admin Route Conflict..."
echo ""
echo "This script fixes:"
echo "  1. API URL doubling (/api/api/... → /api/...)"
echo "  2. Frontend admin route conflict (/admin → /orderadmin)"
echo ""

# Stop containers
echo "⏹️  Stopping containers..."
docker-compose -f docker-compose.prod.yml down

# Rebuild frontend with fixes
echo "🏗️  Rebuilding frontend with API and route fixes..."
docker-compose -f docker-compose.prod.yml build --no-cache frontend

# Start all services
echo "🚀 Starting services..."
docker-compose -f docker-compose.prod.yml up -d

# Wait for services to start
echo "⏳ Waiting for services to initialize..."
sleep 10

# Restart nginx to ensure it picks up changes
echo "🔄 Restarting nginx..."
docker-compose -f docker-compose.prod.yml restart nginx

echo ""
echo "✅ Fix complete!"
echo ""
echo "📋 Changes Applied:"
echo "   ✓ Fixed API URL doubling (removed /api prefix from frontend API calls)"
echo "   ✓ Changed frontend admin route from /admin to /orderadmin"
echo ""
echo "🌐 Test your site:"
echo "   Homepage:           http://${DOMAIN:-dms.openmakersuite.net}/"
echo "   Order Admin (NEW):  http://${DOMAIN:-dms.openmakersuite.net}/orderadmin"
echo "   Django Admin:       http://${DOMAIN:-dms.openmakersuite.net}/admin/"
echo "   API:                http://${DOMAIN:-dms.openmakersuite.net}/api/"
echo ""
echo "🔍 API calls should now go to:"
echo "   /api/inventory/assets/  (not /api/api/inventory/assets/)"
echo ""
