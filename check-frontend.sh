#!/bin/bash

echo "🔍 Checking Frontend Build Status..."
echo ""

echo "1. Checking if frontend container is running..."
if docker-compose -f docker-compose.prod.yml ps frontend | grep -q "Up"; then
    echo "   ✅ Frontend container is running"
else
    echo "   ❌ Frontend container is NOT running"
    docker-compose -f docker-compose.prod.yml ps frontend
fi
echo ""

echo "2. Checking frontend files in frontend container..."
docker-compose -f docker-compose.prod.yml exec -T frontend ls -lah /app/frontend/ 2>/dev/null || echo "   ❌ Can't access frontend container"
echo ""

echo "3. Checking frontend files in nginx container..."
docker-compose -f docker-compose.prod.yml exec -T nginx ls -lah /app/frontend/ 2>/dev/null || echo "   ❌ Can't access nginx container"
echo ""

echo "4. Checking if index.html exists..."
if docker-compose -f docker-compose.prod.yml exec -T nginx test -f /app/frontend/index.html 2>/dev/null; then
    echo "   ✅ index.html found!"
    docker-compose -f docker-compose.prod.yml exec -T nginx ls -lh /app/frontend/index.html
else
    echo "   ❌ index.html NOT found!"
fi
echo ""

echo "5. Checking nginx configuration..."
echo "   Root directory: $(docker-compose -f docker-compose.prod.yml exec -T nginx grep -r 'root' /etc/nginx/conf.d/ | head -1)"
echo ""

echo "6. Testing frontend access from nginx..."
if docker-compose -f docker-compose.prod.yml exec -T nginx cat /app/frontend/index.html >/dev/null 2>&1; then
    echo "   ✅ Nginx can read index.html"
else
    echo "   ❌ Nginx CANNOT read index.html"
fi
echo ""

echo "7. Checking volume mounts..."
docker volume inspect openmakersuite_frontend_build 2>/dev/null | grep -A5 Mountpoint || echo "   ❌ Volume not found"
echo ""

echo "📋 Recommendations:"
echo "   If frontend files are missing, rebuild:"
echo "   docker-compose -f docker-compose.prod.yml build --no-cache frontend"
echo "   docker-compose -f docker-compose.prod.yml up -d frontend"
