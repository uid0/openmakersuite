#!/bin/bash
# Fix Redis setup in DevContainer

echo "🔧 Fixing Redis Setup in DevContainer"
echo "======================================"
echo

# Check if Redis is already running
if redis-cli ping > /dev/null 2>&1; then
    echo "✅ Redis is already running"
    echo "   Status: $(redis-cli ping)"
    exit 0
fi

echo "❌ Redis is not running. Fixing..."

# Method 1: Try systemctl (if available)
if command -v systemctl > /dev/null 2>&1; then
    echo "🔄 Trying systemctl..."
    sudo systemctl start redis-server 2>/dev/null || sudo systemctl start redis 2>/dev/null || echo "systemctl failed"
fi

# Method 2: Try service command
if command -v service > /dev/null 2>&1; then
    echo "🔄 Trying service command..."
    sudo service redis-server start 2>/dev/null || sudo service redis start 2>/dev/null || echo "service failed"
fi

# Method 3: Start Redis directly as daemon
if ! redis-cli ping > /dev/null 2>&1; then
    echo "🔄 Starting Redis daemon directly..."
    sudo redis-server --daemonize yes --port 6379 || echo "Direct daemon start failed"
    sleep 2
fi

# Method 4: Start Redis in background (if all else fails)
if ! redis-cli ping > /dev/null 2>&1; then
    echo "🔄 Starting Redis in background..."
    nohup redis-server --port 6379 > /tmp/redis.log 2>&1 &
    sleep 2
fi

# Test Redis
echo
echo "🧪 Testing Redis connection..."
if redis-cli ping > /dev/null 2>&1; then
    echo "✅ Redis is now running!"
    echo "   Response: $(redis-cli ping)"
    echo "   Info: $(redis-cli info server | grep -E "(redis_version|uptime|connected_clients)" | head -3)"
else
    echo "❌ Redis still not responding"
    echo
    echo "🔍 Manual troubleshooting:"
    echo "1. Check if Redis is installed: redis-server --version"
    echo "2. Install if missing: sudo apt-get update && sudo apt-get install redis-server"
    echo "3. Start manually: redis-server --daemonize yes"
    echo "4. Check logs: tail /var/log/redis/redis-server.log"
fi

echo
echo "📋 Redis Status Check"
echo "---------------------"
echo "Redis CLI ping: $(redis-cli ping 2>/dev/null || echo 'Failed')"
echo "Port 6379 open: $(netstat -tln 2>/dev/null | grep :6379 || echo 'Not listening')"

echo
echo "🚀 Django should now work with Redis!"
echo "   Restart your backend to test: ./dev-commands.sh run-backend"

