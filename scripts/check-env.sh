#!/bin/bash

echo "🔍 Environment Configuration Check"
echo "=================================="
echo ""

if [ ! -f .env ]; then
    echo "❌ .env file not found!"
    echo ""
    echo "Create it from the example:"
    echo "  cp .env.prod.example .env"
    echo "  nano .env  # Edit with your values"
    exit 1
fi

echo "📋 Current .env configuration:"
echo ""

echo "Database Settings:"
echo "  POSTGRES_DB:       $(grep '^POSTGRES_DB=' .env | cut -d '=' -f2 || echo 'NOT SET')"
echo "  POSTGRES_USER:     $(grep '^POSTGRES_USER=' .env | cut -d '=' -f2 || echo 'NOT SET')"
echo "  POSTGRES_PASSWORD: $(grep '^POSTGRES_PASSWORD=' .env | cut -d '=' -f2 | sed 's/./*/g' || echo 'NOT SET')"
echo ""

echo "Django Settings:"
echo "  DEBUG:             $(grep '^DEBUG=' .env | cut -d '=' -f2 || echo 'NOT SET')"
echo "  SECRET_KEY:        $(grep '^SECRET_KEY=' .env | cut -d '=' -f2 | sed 's/./*/g' || echo 'NOT SET')"
echo "  ALLOWED_HOSTS:     $(grep '^ALLOWED_HOSTS=' .env | cut -d '=' -f2 || echo 'NOT SET')"
echo ""

echo "Domain:"
echo "  DOMAIN:            $(grep '^DOMAIN=' .env | cut -d '=' -f2 || echo 'NOT SET')"
echo ""

echo "=================================="
echo ""

# Check for common issues
ISSUES=0

if ! grep -q "^POSTGRES_DB=makerspace_inventory" .env; then
    echo "⚠️  WARNING: POSTGRES_DB should be 'makerspace_inventory'"
    ISSUES=$((ISSUES + 1))
fi

if grep -q "^SECRET_KEY=your-very-secret" .env; then
    echo "⚠️  WARNING: SECRET_KEY is still set to example value - change it!"
    ISSUES=$((ISSUES + 1))
fi

if grep -q "^POSTGRES_PASSWORD=your-strong" .env; then
    echo "⚠️  WARNING: POSTGRES_PASSWORD is still set to example value - change it!"
    ISSUES=$((ISSUES + 1))
fi

if ! grep -q "^DEBUG=0" .env; then
    echo "⚠️  WARNING: DEBUG should be 0 in production"
    ISSUES=$((ISSUES + 1))
fi

if [ $ISSUES -eq 0 ]; then
    echo "✅ All checks passed!"
else
    echo ""
    echo "❌ Found $ISSUES issue(s) - please fix your .env file"
fi

echo ""
