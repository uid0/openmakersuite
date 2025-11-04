#!/bin/bash
set -e

echo "🚀 Deploying Dallas Makerspace Inventory Management System..."

# Check if .env file exists
if [ ! -f .env ]; then
    echo "❌ Error: .env file not found!"
    echo "Please create a .env file from .env.prod.example"
    exit 1
fi

# Load environment variables
export $(cat .env | grep -v '^#' | xargs)

# Stop existing containers
echo "⏹️  Stopping existing containers..."
docker-compose -f docker-compose.prod.yml down

# Build and start services
echo "🏗️  Building and starting services..."
docker-compose -f docker-compose.prod.yml build --no-cache

echo "🚀 Starting services..."
docker-compose -f docker-compose.prod.yml up -d

# Wait for database to be ready
echo "⏳ Waiting for database..."
sleep 10

# Run migrations
echo "📦 Running database migrations..."
docker-compose -f docker-compose.prod.yml exec -T backend python manage.py migrate --noinput

# Collect static files
echo "📁 Collecting static files..."
docker-compose -f docker-compose.prod.yml exec -T backend python manage.py collectstatic --noinput

# Create superuser (if needed)
echo "👤 Create superuser? (y/n)"
read -r create_superuser
if [ "$create_superuser" = "y" ]; then
    docker-compose -f docker-compose.prod.yml exec backend python manage.py createsuperuser
fi

echo "✅ Deployment complete!"
echo ""
echo "📍 Your application is now running at:"
echo "   http://${DOMAIN:-dallas.openmakersuite.net}"
echo ""
echo "🔧 Useful commands:"
echo "   View logs:    docker-compose -f docker-compose.prod.yml logs -f"
echo "   Stop:         docker-compose -f docker-compose.prod.yml down"
echo "   Restart:      docker-compose -f docker-compose.prod.yml restart"
echo "   Shell:        docker-compose -f docker-compose.prod.yml exec backend python manage.py shell"
