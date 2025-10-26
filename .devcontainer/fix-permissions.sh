#!/bin/bash
# Fix permissions in the DevContainer environment

echo "🔧 Fixing Authentication Permissions in DevContainer"
echo "====================================================="
echo

# Ensure DEVELOPMENT_MODE is set
echo "📝 Updating .env file with DEVELOPMENT_MODE..."
if ! grep -q "DEVELOPMENT_MODE" /workspace/backend/.env; then
    echo "DEVELOPMENT_MODE=1" >> /workspace/backend/.env
    echo "✅ Added DEVELOPMENT_MODE=1 to .env"
else
    sed -i 's/DEVELOPMENT_MODE=.*/DEVELOPMENT_MODE=1/' /workspace/backend/.env
    echo "✅ Updated DEVELOPMENT_MODE=1 in .env"
fi

echo
echo "📋 Current .env DEVELOPMENT_MODE setting:"
grep "DEVELOPMENT_MODE" /workspace/backend/.env

echo
echo "🔍 Checking if backend is running..."
if lsof -i:8000 > /dev/null 2>&1; then
    echo "⚠️  Backend is running. You need to restart it!"
    echo
    echo "To restart:"
    echo "  1. Find the terminal where you ran './dev-commands.sh run-backend'"
    echo "  2. Press Ctrl+C to stop it"
    echo "  3. Run it again: ./dev-commands.sh run-backend"
    echo
    echo "Or kill and restart:"
    PID=$(lsof -ti:8000 | head -1)
    echo "  kill $PID"
    echo "  cd /workspace/backend && python manage.py runserver 0.0.0.0:8000"
else
    echo "ℹ️  Backend not running. Start it with:"
    echo "  ./dev-commands.sh run-backend"
fi

echo
echo "✅ Permissions fix applied!"
echo
echo "🧪 After restarting backend, test with:"
echo "  curl http://localhost:8000/api/reorders/requests/pending/"
echo
echo "Expected: HTTP 200 with data (not 401)"

