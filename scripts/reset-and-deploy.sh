#!/bin/bash
set -e

echo "🔄 Resetting and Deploying Dallas Makerspace Inventory Management System..."

# Check if .env file exists
if [ ! -f .env ]; then
    echo "❌ Error: .env file not found!"
    echo "Please create a .env file from .env.prod.example"
    exit 1
fi

# Load environment variables
export $(cat .env | grep -v '^#' | xargs)

echo "⏹️  Stopping all containers..."
docker compose -f docker compose.prod.yml down

echo "🗑️  Removing old volumes (this will delete existing data)..."
docker volume rm openmakersuite_postgres_data 2>/dev/null || true
docker volume rm openmakersuite_static_volume 2>/dev/null || true
docker volume rm openmakersuite_media_volume 2>/dev/null || true
docker volume rm openmakersuite_frontend_build 2>/dev/null || true

echo "🏗️  Building fresh images..."
docker compose -f docker compose.prod.yml build --no-cache

echo "🚀 Starting services..."
docker compose -f docker compose.prod.yml up -d

# Wait for database to be ready
echo "⏳ Waiting for database to initialize..."
sleep 15

# Check database connection
echo "🔍 Verifying database connection..."
docker compose -f docker compose.prod.yml exec -T backend python -c "
from django.db import connection
try:
    connection.ensure_connection()
    print('✅ Database connection successful!')
except Exception as e:
    print(f'❌ Database connection failed: {e}')
    exit(1)
"

# Run migrations
echo "📦 Running database migrations..."
docker compose -f docker compose.prod.yml exec -T backend python manage.py migrate --noinput

# Collect static files
echo "📁 Collecting static files..."
docker compose -f docker compose.prod.yml exec -T backend python manage.py collectstatic --noinput

# Create superuser (if needed)
echo ""
echo "👤 Would you like to create a superuser? (y/n)"
read -r create_superuser
if [ "$create_superuser" = "y" ]; then
    docker compose -f docker compose.prod.yml exec backend python manage.py createsuperuser
fi

echo ""
echo "✅ Fresh deployment complete!"
echo ""
echo "📍 Your application is now running at:"
echo "   http://${DOMAIN:-dallas.openmakersuite.net}"
echo ""
echo "🔧 Useful commands:"
echo "   View logs:    docker compose -f docker compose.prod.yml logs -f"
echo "   Stop:         docker compose -f docker compose.prod.yml down"
echo "   Restart:      docker compose -f docker compose.prod.yml restart"
