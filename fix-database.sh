#!/bin/bash
set -e

echo "🔧 Fixing Database Name Issue..."

# Check if .env file exists
if [ ! -f .env ]; then
    echo "❌ Error: .env file not found!"
    exit 1
fi

# Load environment variables
export $(cat .env | grep -v '^#' | xargs)

# Check current database name in .env
CURRENT_DB=$(grep "^POSTGRES_DB=" .env | cut -d'=' -f2 | tr -d ' ')

echo "Current POSTGRES_DB in .env: ${CURRENT_DB:-not set}"
echo ""

if [ "$CURRENT_DB" = "makerspace" ]; then
    echo "❌ Found incorrect database name 'makerspace'"
    echo "   Django expects 'makerspace_inventory'"
    echo ""
    echo "Fixing .env file..."

    # Backup .env
    cp .env .env.backup

    # Replace database name
    sed -i 's/POSTGRES_DB=makerspace$/POSTGRES_DB=makerspace_inventory/' .env

    echo "✅ Updated .env file (backup saved as .env.backup)"
    echo ""
fi

echo "⏹️  Stopping containers..."
docker-compose -f docker-compose.prod.yml down

echo "🗑️  Removing database volume..."
docker volume rm openmakersuite_postgres_data 2>/dev/null || true

echo "🚀 Starting fresh with correct database name..."
docker-compose -f docker-compose.prod.yml up -d db

echo "⏳ Waiting for database to initialize..."
sleep 10

# Verify database was created correctly
echo "🔍 Verifying database..."
if docker-compose -f docker-compose.prod.yml exec -T db psql -U ${POSTGRES_USER:-makerspace} -lqt | cut -d \| -f 1 | grep -qw makerspace_inventory; then
    echo "✅ Database 'makerspace_inventory' created successfully!"
else
    echo "❌ Database 'makerspace_inventory' NOT found!"
    docker-compose -f docker-compose.prod.yml exec -T db psql -U ${POSTGRES_USER:-makerspace} -lqt
    exit 1
fi

echo ""
echo "🚀 Starting all services..."
docker-compose -f docker-compose.prod.yml up -d

echo "⏳ Waiting for services to start..."
sleep 15

echo "📦 Running migrations..."
docker-compose -f docker-compose.prod.yml exec -T backend python manage.py migrate

echo "📁 Collecting static files..."
docker-compose -f docker-compose.prod.yml exec -T backend python manage.py collectstatic --noinput

echo ""
echo "✅ Database fix complete!"
echo ""
echo "Test connection:"
echo "  docker-compose -f docker-compose.prod.yml exec backend python manage.py dbshell"
