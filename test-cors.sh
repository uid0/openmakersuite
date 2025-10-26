#!/bin/bash
# Test CORS configuration

echo "🌐 Testing CORS Configuration"
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

# Test CORS preflight (OPTIONS request)
echo "🔍 Testing CORS Preflight (OPTIONS)..."
echo "----------------------------------------"
RESPONSE=$(curl -s -v \
    -H "Origin: http://localhost:3000" \
    -H "Access-Control-Request-Method: POST" \
    -H "Access-Control-Request-Headers: content-type,authorization" \
    -X OPTIONS \
    http://localhost:8000/api/reorders/requests/pending/ 2>&1)

echo "$RESPONSE" | grep -i "access-control-allow-origin" > /dev/null
if [ $? -eq 0 ]; then
    echo "✅ Access-Control-Allow-Origin header present"
    echo "   $(echo "$RESPONSE" | grep -i "access-control-allow-origin")"
else
    echo "❌ Access-Control-Allow-Origin header missing!"
fi

echo "$RESPONSE" | grep -i "access-control-allow-methods" > /dev/null
if [ $? -eq 0 ]; then
    echo "✅ Access-Control-Allow-Methods header present"
    echo "   $(echo "$RESPONSE" | grep -i "access-control-allow-methods")"
else
    echo "❌ Access-Control-Allow-Methods header missing!"
fi

echo "$RESPONSE" | grep -i "access-control-allow-headers" > /dev/null
if [ $? -eq 0 ]; then
    echo "✅ Access-Control-Allow-Headers header present"
    echo "   $(echo "$RESPONSE" | grep -i "access-control-allow-headers")"
else
    echo "❌ Access-Control-Allow-Headers header missing!"
fi

echo "$RESPONSE" | grep -i "access-control-allow-credentials" > /dev/null
if [ $? -eq 0 ]; then
    echo "✅ Access-Control-Allow-Credentials header present"
    echo "   $(echo "$RESPONSE" | grep -i "access-control-allow-credentials")"
else
    echo "⚠️  Access-Control-Allow-Credentials header missing (might be okay)"
fi

echo
echo "🔍 Testing Actual Request (GET with Origin)..."
echo "-----------------------------------------------"
RESPONSE=$(curl -s -v \
    -H "Origin: http://localhost:3000" \
    -X GET \
    http://localhost:8000/api/reorders/requests/pending/ 2>&1)

echo "$RESPONSE" | grep -i "access-control-allow-origin" > /dev/null
if [ $? -eq 0 ]; then
    echo "✅ CORS headers present on actual request"
    echo "   $(echo "$RESPONSE" | grep -i "access-control-allow-origin")"
else
    echo "❌ CORS headers missing on actual request!"
fi

# Check status code
STATUS=$(echo "$RESPONSE" | grep "HTTP" | tail -1 | awk '{print $3}')
if [ "$STATUS" = "200" ]; then
    echo "✅ Request successful (HTTP 200)"
elif [ "$STATUS" = "401" ]; then
    echo "⚠️  Request returned HTTP 401 (CORS is working, but authentication failed)"
    echo "   This is expected if DEVELOPMENT_MODE=0"
else
    echo "⚠️  Request returned HTTP $STATUS"
fi

echo
echo "📊 Configuration Check"
echo "----------------------"
if grep -q "DEVELOPMENT_MODE=1" backend/.env 2>/dev/null; then
    echo "✅ DEVELOPMENT_MODE=1 (CORS_ALLOW_ALL_ORIGINS = True)"
    echo "   All origins are allowed"
else
    echo "🔒 DEVELOPMENT_MODE=0 or not set"
    echo "   Only localhost:3000 and 127.0.0.1:3000 are allowed"
fi

echo
echo "🎯 How to Test in Browser (Most Accurate):"
echo "-------------------------------------------"
echo "1. Open http://localhost:3000 in your browser"
echo "2. Open Developer Tools (F12)"
echo "3. Go to Console tab"
echo "4. Run this command:"
echo
echo "   fetch('http://localhost:8000/api/reorders/requests/pending/')"
echo "     .then(r => r.json())"
echo "     .then(console.log)"
echo "     .catch(console.error)"
echo
echo "If you see CORS error → Check CORS configuration"
echo "If you see 401 error → CORS works, but need authentication"
echo "If you see data → Everything works! 🎉"
echo
echo "📚 For more details, see: CORS_GUIDE.md"

