#!/bin/bash
# Force restart the backend - kills the current process and starts fresh

echo "🔄 FORCE RESTARTING BACKEND"
echo "=============================="
echo

# Kill the process on port 8000
PID=$(lsof -ti:8000 2>/dev/null | head -1)

if [ -z "$PID" ]; then
    echo "ℹ️  No process running on port 8000"
else
    echo "🛑 Killing process $PID..."
    kill -9 $PID
    sleep 2
    echo "✅ Process killed"
fi

echo
echo "🔍 Verifying .env configuration..."
echo "DEVELOPMENT_MODE=$(grep DEVELOPMENT_MODE backend/.env | cut -d= -f2)"
echo

echo "🚀 Starting backend with Python 3..."
cd backend

# Try to find the correct Python with Django installed
if python3 -c "import django" 2>/dev/null; then
    echo "✅ Using python3"
    python3 manage.py runserver
elif python -c "import django" 2>/dev/null; then
    echo "✅ Using python"
    python manage.py runserver
else
    echo "❌ Django not found in python or python3"
    echo "   You may need to:"
    echo "   1. Activate a virtual environment"
    echo "   2. Or install dependencies: pip install -r requirements.txt"
    exit 1
fi

