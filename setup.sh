#!/bin/bash

# Makerspace Inventory Management System - Setup Script
# This script automates the initial setup process

set -e

echo "=========================================="
echo "Makerspace Inventory Setup"
echo "=========================================="
echo ""

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "Error: Docker is not installed. Please install Docker first."
    exit 1
fi

# Check if Docker Compose is installed
if ! command -v docker-compose &> /dev/null; then
    echo "Error: Docker Compose is not installed. Please install Docker Compose first."
    exit 1
fi

# Create .env file if it doesn't exist
if [ ! -f .env ]; then
    echo "Creating .env file from .env.example..."
    cp .env.example .env

    # Generate a random secret key
    SECRET_KEY=$(openssl rand -base64 50 | tr -d '\n')
    sed -i.bak "s/change-this-to-a-random-secret-key-in-production/$SECRET_KEY/" .env
    rm .env.bak 2>/dev/null || true

    echo "✓ .env file created"
    echo "⚠ Please review .env and update POSTGRES_PASSWORD and other settings as needed"
    echo ""
else
    echo "✓ .env file already exists"
    echo ""
fi

# Build and start containers
echo "Building Docker containers..."
docker-compose build

echo ""
echo "Starting containers..."
docker-compose up -d

echo ""
echo "Waiting for database to be ready..."
sleep 10

# Run migrations
echo "Running database migrations..."
docker-compose exec -T backend python manage.py migrate

echo ""
echo "Creating cache table..."
docker-compose exec -T backend python manage.py createcachetable || true

echo ""
echo "Collecting static files..."
docker-compose exec -T backend python manage.py collectstatic --noinput

echo ""
echo "=========================================="
echo "Setup Complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "1. Create a superuser account:"
echo "   docker-compose exec backend python manage.py createsuperuser"
echo ""
echo "2. Access the application:"
echo "   - Frontend: http://localhost:3000"
echo "   - Django Admin: http://localhost:8000/admin"
echo "   - API Docs: http://localhost:8000/api/docs/"
echo ""
echo "3. To stop the application:"
echo "   docker-compose down"
echo ""
echo "4. To view logs:"
echo "   docker-compose logs -f"
echo ""
