#!/bin/bash
set -e

echo "🔧 Fixing Static Files Conflict..."
echo ""
echo "This script fixes the /static/ URL conflict between Django and React"
echo "by changing Django static files to /django-static/"
echo ""

# Stop containers
echo "⏹️  Stopping containers..."
docker-compose -f docker-compose.prod.yml down

# Rebuild backend with new static URL
echo "🏗️  Rebuilding backend with updated static URL..."
docker-compose -f docker-compose.prod.yml build --no-cache backend

# Rebuild frontend
echo "🏗️  Rebuilding frontend..."
docker-compose -f docker-compose.prod.yml build --no-cache frontend

# Start all services
echo "🚀 Starting services..."
docker-compose -f docker-compose.prod.yml up -d

# Wait for services to start
echo "⏳ Waiting for services to initialize..."
sleep 15

# Collect Django static files
echo "📁 Collecting Django static files..."
docker-compose -f docker-compose.prod.yml exec -T backend python manage.py collectstatic --noinput

# Restart nginx to pick up new configuration
echo "🔄 Restarting nginx..."
docker-compose -f docker-compose.prod.yml restart nginx

# Verify static files
echo ""
echo "🔍 Verifying configuration..."
echo ""

echo "1. Checking React static files..."
if docker-compose -f docker-compose.prod.yml exec -T nginx ls /app/frontend/static/js/ >/dev/null 2>&1; then
    echo "   ✅ React JS files found"
    docker-compose -f docker-compose.prod.yml exec -T nginx ls -lh /app/frontend/static/js/ | head -3
else
    echo "   ❌ React JS files NOT found"
fi

echo ""
echo "2. Checking Django static files..."
if docker-compose -f docker-compose.prod.yml exec -T nginx ls /app/staticfiles/admin/ >/dev/null 2>&1; then
    echo "   ✅ Django admin static files found"
    docker-compose -f docker-compose.prod.yml exec -T nginx ls -lh /app/staticfiles/admin/ | head -3
else
    echo "   ❌ Django static files NOT found"
fi

echo ""
echo "3. Testing React index.html..."
if docker-compose -f docker-compose.prod.yml exec -T nginx test -f /app/frontend/index.html; then
    echo "   ✅ index.html exists"
else
    echo "   ❌ index.html NOT found"
fi

echo ""
echo "✅ Fix complete!"
echo ""
echo "📋 URL Structure:"
echo "   React assets:      /static/js/*, /static/css/*"
echo "   Django admin:      /django-static/*"
echo "   API:               /api/*"
echo "   Django Admin UI:   /admin/"
echo "   Media uploads:     /media/*"
echo ""
echo "🌐 Test your site:"
echo "   Homepage: http://${DOMAIN:-dallas.openmakersuite.net}/"
echo "   Admin:    http://${DOMAIN:-dallas.openmakersuite.net}/admin/"
echo "   API:      http://${DOMAIN:-dallas.openmakersuite.net}/api/"
