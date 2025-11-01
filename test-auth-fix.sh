#!/bin/bash
# Test script to verify the authentication fix is working

echo "🧪 Testing Authentication Fix"
echo "=============================="
echo

# Check if backend is running
if ! curl -s http://localhost:8000/api/ > /dev/null 2>&1; then
    echo "❌ Backend not running. Please start it first:"
    echo "   cd backend && python manage.py runserver"
    exit 1
fi

echo "✅ Backend is running"
echo

# Test unauthenticated read (should work)
echo "📖 Testing unauthenticated READ request..."
STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/inventory/items/)
if [ "$STATUS" = "200" ]; then
    echo "✅ Unauthenticated READ works (HTTP $STATUS)"
else
    echo "⚠️  Unauthenticated READ returned HTTP $STATUS (expected 200)"
fi
echo

# Test unauthenticated write (should work with DEVELOPMENT_MODE=1)
echo "📝 Testing unauthenticated POST request..."
RESPONSE=$(curl -s -w "\nHTTP_STATUS:%{http_code}" \
    -X POST http://localhost:8000/api/inventory/categories/ \
    -H "Content-Type: application/json" \
    -d '{"name": "Test Category '$(date +%s)'", "description": "Test"}')

STATUS=$(echo "$RESPONSE" | grep "HTTP_STATUS" | cut -d: -f2)

if [ "$STATUS" = "201" ] || [ "$STATUS" = "200" ]; then
    echo "✅ Unauthenticated WRITE works (HTTP $STATUS)"
    echo "   DEVELOPMENT_MODE is enabled correctly!"
elif [ "$STATUS" = "401" ] || [ "$STATUS" = "403" ]; then
    echo "❌ Unauthenticated WRITE failed (HTTP $STATUS)"
    echo "   Check that DEVELOPMENT_MODE=1 in backend/.env"
    echo "   Current backend/.env:"
    grep "DEVELOPMENT_MODE" backend/.env 2>/dev/null || echo "   DEVELOPMENT_MODE not found in .env"
else
    echo "⚠️  Unexpected response (HTTP $STATUS)"
    echo "   Response: $(echo "$RESPONSE" | head -n1)"
fi
echo

echo "🔐 Authentication Status:"
if grep -q "DEVELOPMENT_MODE=1" backend/.env 2>/dev/null; then
    echo "   ✅ DEVELOPMENT_MODE=1 (unauthenticated access allowed)"
    echo "   📝 To require authentication, change to DEVELOPMENT_MODE=0"
else
    echo "   🔒 DEVELOPMENT_MODE not set or disabled"
    echo "   📝 To allow unauthenticated access, add DEVELOPMENT_MODE=1 to backend/.env"
fi
echo

echo "📚 See DEV_AUTH_GUIDE.md for more authentication options"

