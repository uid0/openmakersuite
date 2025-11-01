#!/bin/bash
# Quick script to restart the backend

echo "🔄 Restarting Backend Server"
echo "============================"
echo

# Find the process running on port 8000
PID=$(lsof -ti:8000 2>/dev/null | head -1)

if [ -z "$PID" ]; then
    echo "ℹ️  No backend server running on port 8000"
    echo "   Starting fresh..."
else
    echo "🛑 Stopping backend server (PID: $PID)..."
    kill $PID
    sleep 2
    
    # Check if it's still running
    if lsof -ti:8000 > /dev/null 2>&1; then
        echo "⚠️  Process still running, force killing..."
        kill -9 $PID
        sleep 1
    fi
    echo "✅ Backend stopped"
fi

echo
echo "🚀 Starting backend server..."
echo "   (Press Ctrl+C to stop)"
echo

cd backend
python3 manage.py runserver

