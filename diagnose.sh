#!/bin/bash

echo "🔍 Diagnosing deployment issues..."
echo ""

# Check if .env exists
echo "1. Checking .env file..."
if [ -f .env ]; then
    echo "✅ .env file exists"
    echo "   POSTGRES_PASSWORD is set: $(grep -q POSTGRES_PASSWORD .env && echo 'Yes' || echo 'No')"
    echo "   DOMAIN: $(grep DOMAIN .env | cut -d'=' -f2)"
    echo "   SECRET_KEY is set: $(grep -q SECRET_KEY .env && echo 'Yes' || echo 'No')"
else
    echo "❌ .env file NOT found"
fi
echo ""

# Check running containers
echo "2. Checking running containers..."
docker compose -f docker compose.prod.yml ps
echo ""

# Check database container
echo "3. Checking database..."
if docker compose -f docker compose.prod.yml exec -T db pg_isready -U makerspace 2>/dev/null; then
    echo "✅ Database is accepting connections"

    # Try to connect with the password from .env
    export $(cat .env | grep -v '^#' | grep POSTGRES_PASSWORD | xargs)
    if [ -n "$POSTGRES_PASSWORD" ]; then
        echo "   Testing with POSTGRES_PASSWORD from .env..."
        if PGPASSWORD=$POSTGRES_PASSWORD docker compose -f docker compose.prod.yml exec -T db psql -U makerspace -d makerspace_inventory -c "SELECT 1" >/dev/null 2>&1; then
            echo "   ✅ Password from .env works!"
        else
            echo "   ❌ Password from .env does NOT work"
            echo "   This means the database was created with a different password"
        fi
    fi
else
    echo "❌ Database is not running or not ready"
fi
echo ""

# Check backend logs
echo "4. Recent backend logs:"
docker compose -f docker compose.prod.yml logs --tail=20 backend
echo ""

# Check volumes
echo "5. Docker volumes:"
docker volume ls | grep openmakersuite
echo ""

# Recommendations
echo "📋 Recommendations:"
echo ""
echo "If you see 'password authentication failed':"
echo "   Option 1: Use ./reset-and-deploy.sh to start fresh (DELETES DATA)"
echo "   Option 2: Update .env with the correct password"
echo "   Option 3: Manually reset database password"
echo ""
echo "To reset database password manually:"
echo "   1. docker compose -f docker compose.prod.yml down"
echo "   2. docker volume rm openmakersuite_postgres_data"
echo "   3. docker compose -f docker compose.prod.yml up -d"
