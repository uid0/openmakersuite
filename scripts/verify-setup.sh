#!/bin/bash

# DevContainer verification script
echo "🔍 Verifying DevContainer Setup"
echo "================================"

# Check Python
echo -n "🐍 Python: "
python --version || echo "❌ Not found"

# Check Node.js
echo -n "⚛️  Node.js: "
node --version || echo "❌ Not found"

# Check NPM
echo -n "📦 NPM: "
npm --version || echo "❌ Not found"

# Check Django
echo -n "🦄 Django: "
python -c "import django; print(django.VERSION)" 2>/dev/null || echo "❌ Not available"

# Check database connection
echo -n "🗄️  Database: "
if [ -f "/workspace/backend/db.sqlite3" ]; then
    echo "✅ SQLite Ready"
else
    echo "❌ Not ready"
fi

# Check Redis connection
echo -n "📦 Redis: "
if redis-cli ping 2>/dev/null; then
    echo "✅ Connected"
else
    echo "❌ Not ready"
fi

# Check project structure
echo -n "📁 Project structure: "
if [ -d "backend" ] && [ -d "frontend" ] && [ -f "docker-compose.yml" ]; then
    echo "✅ Complete"
else
    echo "❌ Missing directories"
fi

# Check helper script
echo -n "🛠️  Helper script: "
if [ -x "dev-commands.sh" ]; then
    echo "✅ Available"
else
    echo "❌ Not found"
fi

echo ""
echo "📋 Available commands:"
if [ -x "dev-commands.sh" ]; then
    ./dev-commands.sh status 2>/dev/null || echo "  (Run './dev-commands.sh' without arguments to see help)"
else
    echo "  cd backend && python manage.py runserver 0.0.0.0:8000"
    echo "  cd frontend && npm start"
fi

echo ""
echo "✅ Setup verification complete!"
