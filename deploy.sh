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

# Stop existing containers
echo "⏹️  Stopping existing containers..."
docker compose -f docker-compose.prod.yml down

# Build and start services
echo "🏗️  Building and starting services..."
echo "📝 Using GIT_HASH=$GIT_HASH for build..."
# Export GIT_HASH so docker-compose can use it in the yml file
export GIT_HASH
docker compose -f docker-compose.prod.yml build --no-cache frontend

echo "🚀 Starting services..."
docker compose -f docker-compose.prod.yml up -d

# Wait for database to be ready
echo "⏳ Waiting for database..."
sleep 10

# Run migrations
echo "📦 Running database migrations..."
docker compose -f docker-compose.prod.yml exec -T backend python manage.py migrate --noinput

# Collect static files
echo "📁 Collecting static files..."
docker compose -f docker-compose.prod.yml exec -T backend python manage.py collectstatic --noinput

# Wait for frontend to finish building and ensure volume is populated
echo "⏳ Waiting for frontend build to complete..."
sleep 5

# Restart nginx to pick up any frontend changes
echo "🔄 Restarting nginx to pick up frontend changes..."
docker compose -f docker-compose.prod.yml restart nginx

# Verify frontend build
echo "🔍 Verifying frontend build..."
if docker compose -f docker-compose.prod.yml exec -T nginx ls -la /app/frontend/index.html >/dev/null 2>&1; then
    echo "✅ Frontend files found!"
else
    echo "❌ Frontend files NOT found in nginx container!"
    echo "   Checking frontend container..."
    docker compose -f docker-compose.prod.yml exec -T frontend ls -la /app/frontend/ || echo "   Frontend volume is empty!"
fi

# Verify static files
echo "🔍 Verifying Django static files..."
if docker compose -f docker-compose.prod.yml exec -T nginx ls -la /app/staticfiles/ >/dev/null 2>&1; then
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
echo "   View logs:    docker compose -f docker-compose.prod.yml logs -f"
echo "   Stop:         docker compose -f docker-compose.prod.yml down"
echo "   Restart:      docker compose -f docker-compose.prod.yml restart"
echo "   Shell:        docker compose -f docker-compose.prod.yml exec backend python manage.py shell"
