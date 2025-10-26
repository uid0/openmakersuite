#!/bin/bash
# Comprehensive setup verification script

echo "🔍 OpenMakerSuite Development Environment Check"
echo "==============================================="
echo

# Check if we're in the right directory
if [ ! -d "backend" ] || [ ! -d "frontend" ]; then
    echo "❌ Error: Please run this script from the project root directory"
    exit 1
fi

echo "✅ Running from project root"
echo

# Check Python
echo "🐍 Python Check"
echo "---------------"
if command -v python3 > /dev/null 2>&1; then
    echo "✅ Python 3: $(python3 --version)"
elif command -v python > /dev/null 2>&1; then
    echo "✅ Python: $(python --version)"
else
    echo "❌ Python not found"
fi

# Check Node.js
echo
echo "🟢 Node.js Check"
echo "----------------"
if command -v node > /dev/null 2>&1; then
    echo "✅ Node.js: $(node --version)"
    if command -v npm > /dev/null 2>&1; then
        echo "✅ NPM: $(npm --version)"
    else
        echo "❌ NPM not found"
    fi
else
    echo "❌ Node.js not found"
fi

# Check Redis
echo
echo "🔴 Redis Check"
echo "--------------"
if redis-cli ping > /dev/null 2>&1; then
    echo "✅ Redis running: $(redis-cli ping)"
    echo "   Info: $(redis-cli info server | grep redis_version | cut -d: -f2)"
else
    echo "❌ Redis not responding"
    echo "   Run: ./.devcontainer/fix-redis.sh"
fi

# Check Django
echo
echo "🌐 Django Backend Check"
echo "-----------------------"
if curl -s http://localhost:8000/api/ > /dev/null 2>&1; then
    echo "✅ Backend accessible"

    # Test authentication bypass
    PENDING_TEST=$(curl -s http://localhost:8000/api/reorders/requests/pending/)
    if echo "$PENDING_TEST" | jq -e 'type == "array"' > /dev/null 2>&1; then
        echo "✅ DEVELOPMENT_MODE active (no auth required)"
        echo "   Pending requests: $(echo "$PENDING_TEST" | jq 'length')"
    else
        echo "❌ Authentication still required"
        echo "   Response: $(echo "$PENDING_TEST" | head -1)"
    fi

    # Check CORS headers
    CORS_TEST=$(curl -s -I -H "Origin: http://localhost:3000" http://localhost:8000/api/inventory/items/ 2>/dev/null | grep "Access-Control-Allow-Origin" || echo "No CORS headers")
    if echo "$CORS_TEST" | grep -q "Access-Control-Allow-Origin"; then
        echo "✅ CORS headers present"
    else
        echo "⚠️  CORS headers missing"
    fi

else
    echo "❌ Backend not running"
    echo "   Start with: ./dev-commands.sh run-backend"
fi

# Check frontend
echo
echo "⚛️  React Frontend Check"
echo "-----------------------"
if curl -s http://localhost:3000 > /dev/null 2>&1; then
    echo "✅ Frontend accessible"
    echo "   Build check: $(curl -s http://localhost:3000 | grep -i "react" | head -1 || echo "No React content")"
else
    echo "❌ Frontend not running"
    echo "   Start with: ./dev-commands.sh run-frontend"
fi

# Check database
echo
echo "🗄️  Database Check"
echo "-----------------"
if [ -f "backend/db.sqlite3" ]; then
    echo "✅ SQLite database exists"
    echo "   Size: $(ls -lh backend/db.sqlite3 | awk '{print $5}')"

    # Check if migrations ran
    if cd backend && python manage.py showmigrations | grep -q "\[X\]" 2>/dev/null; then
        echo "✅ Database migrations applied"
    else
        echo "⚠️  Database migrations may not be applied"
    fi
    cd ..
else
    echo "❌ SQLite database not found"
fi

# Check dependencies
echo
echo "📦 Dependencies Check"
echo "--------------------"
if [ -d "backend" ] && [ -f "backend/requirements.txt" ]; then
    echo "✅ Backend requirements.txt found"
    if cd backend && python -c "import django, rest_framework, redis" 2>/dev/null; then
        echo "✅ Key Python packages installed"
    else
        echo "⚠️  Key Python packages may be missing"
    fi
    cd ..
fi

if [ -d "frontend" ] && [ -f "frontend/package.json" ]; then
    echo "✅ Frontend package.json found"
    if cd frontend && npm list react react-dom axios 2>/dev/null | grep -q "react@"; then
        echo "✅ Key Node packages installed"
    else
        echo "⚠️  Key Node packages may be missing"
    fi
    cd ..
fi

# Check environment configuration
echo
echo "⚙️  Configuration Check"
echo "----------------------"
if [ -f "backend/.env" ]; then
    echo "✅ Backend .env file exists"
    if grep -q "DEVELOPMENT_MODE=1" backend/.env; then
        echo "✅ DEVELOPMENT_MODE=1 (auth bypass enabled)"
    else
        echo "⚠️  DEVELOPMENT_MODE not set to 1"
    fi

    if grep -q "REDIS_URL" backend/.env; then
        echo "✅ Redis configuration found"
    else
        echo "❌ Redis configuration missing"
    fi
else
    echo "❌ Backend .env file missing"
fi

echo
echo "📊 Overall Status"
echo "================="
echo

# Count issues
ISSUES=0

if ! redis-cli ping > /dev/null 2>&1; then
    echo "❌ Redis not running"
    ((ISSUES++))
fi

if ! curl -s http://localhost:8000/api/ > /dev/null 2>&1; then
    echo "❌ Backend not accessible"
    ((ISSUES++))
fi

if ! curl -s http://localhost:3000 > /dev/null 2>&1; then
    echo "❌ Frontend not accessible"
    ((ISSUES++))
fi

if ! echo "$PENDING_TEST" | jq -e 'type == "array"' > /dev/null 2>&1; then
    echo "❌ Authentication still required"
    ((ISSUES++))
fi

if [ $ISSUES -eq 0 ]; then
    echo "🎉 Everything is working perfectly!"
    echo
    echo "🚀 Ready for development:"
    echo "   - Create items via API"
    echo "   - Generate index cards"
    echo "   - Test QR code scanning"
    echo "   - Use admin dashboard"
else
    echo "⚠️  Found $ISSUES issue(s). See above for details."
fi

echo
echo "🛠️  Quick Fix Commands"
echo "======================"
if ! redis-cli ping > /dev/null 2>&1; then
    echo "Fix Redis: ./.devcontainer/fix-redis.sh"
fi
if ! curl -s http://localhost:8000/api/ > /dev/null 2>&1; then
    echo "Start Backend: ./dev-commands.sh run-backend"
fi
if ! curl -s http://localhost:3000 > /dev/null 2>&1; then
    echo "Start Frontend: ./dev-commands.sh run-frontend"
fi

echo
echo "📚 Documentation:"
echo "   REDIS_FIX_GUIDE.md - Redis troubleshooting"
echo "   INDEX_CARD_TESTING.md - Testing index cards"
echo "   DEVCONTAINER_FIX.md - DevContainer setup"
