#!/bin/bash

# DevContainer post-create setup script
set -e

echo "🚀 Setting up OpenMakerSuite development environment..."

# Ensure we're in the right directory
cd /workspace

# Install Python development dependencies
echo "📦 Installing Python development dependencies..."
cd backend
pip install -r requirements.txt
pip install -r requirements-dev.txt
cd ..

# Install Node.js dependencies for frontend
echo "📦 Installing Node.js dependencies..."
cd frontend
npm install
cd ..

# Set up environment file for development
echo "⚙️  Setting up environment configuration..."
if [ ! -f backend/.env ]; then
    cat > backend/.env << EOF
DEBUG=1
SECRET_KEY=dev-secret-key-not-for-production
DATABASE_URL=sqlite:///db.sqlite3
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/0
ALLOWED_HOSTS=localhost,127.0.0.1,0.0.0.0
SENTRY_DSN=
SENTRY_ENVIRONMENT=development
CI=true
DEVELOPMENT_MODE=1
EOF
else
    # Update existing .env to ensure DEVELOPMENT_MODE is set
    if ! grep -q "DEVELOPMENT_MODE" backend/.env; then
        echo "DEVELOPMENT_MODE=1" >> backend/.env
    fi
fi

# Install and start Redis server
echo "🔄 Installing and starting Redis server..."

# Install Redis if not present
if ! command -v redis-server > /dev/null 2>&1; then
    echo "📦 Installing Redis..."
    sudo apt-get update && sudo apt-get install -y redis-server
fi

# Start Redis using the most reliable method
echo "🚀 Starting Redis..."

# Method 1: Try service command
if command -v service > /dev/null 2>&1; then
    sudo service redis-server start 2>/dev/null || echo "Service command failed"
fi

# Method 2: Try systemctl
if command -v systemctl > /dev/null 2>&1; then
    sudo systemctl start redis-server 2>/dev/null || sudo systemctl start redis 2>/dev/null || echo "systemctl failed"
fi

# Method 3: Start directly (most reliable)
if ! redis-cli ping > /dev/null 2>&1; then
    echo "🔄 Starting Redis daemon directly..."
    sudo redis-server --daemonize yes --port 6379 --bind 127.0.0.1 || echo "Direct start failed"
    sleep 2
fi

# Method 4: Background process (fallback)
if ! redis-cli ping > /dev/null 2>&1; then
    echo "🔄 Starting Redis in background..."
    nohup redis-server --port 6379 --bind 127.0.0.1 > /tmp/redis.log 2>&1 &
    sleep 3
fi

# Wait for Redis to be ready
echo "⏳ Waiting for Redis to be ready..."
timeout 30s bash -c 'until redis-cli ping 2>/dev/null; do sleep 2; done' || echo "⚠️  Redis may not be ready yet"

# Verify Redis is working
if redis-cli ping > /dev/null 2>&1; then
    echo "✅ Redis is running: $(redis-cli ping)"
else
    echo "⚠️  Redis setup completed, but may need manual start"
    echo "   Run: sudo redis-server --daemonize yes"
fi

# Run database migrations
echo "🗄️  Running database migrations..."
cd backend
python manage.py migrate
cd ..

# Create helper scripts
cat > /workspace/dev-commands.sh << 'SCRIPT'
#!/bin/bash
# Development helper commands

case "$1" in
    "test-backend")
        cd /workspace/backend && python -m pytest
        ;;
    "test-frontend")
        cd /workspace/frontend && npm test
        ;;
    "test-all")
        cd /workspace/backend && python -m pytest && cd /workspace/frontend && npm test
        ;;
    "lint-backend")
        cd /workspace/backend && flake8 . && black --check . && isort --check .
        ;;
    "lint-frontend")
        cd /workspace/frontend && npm run lint
        ;;
    "run-backend")
        cd /workspace/backend && python manage.py runserver 0.0.0.0:8000
        ;;
    "run-frontend")
        cd /workspace/frontend && npm start
        ;;
    "migrate")
        cd /workspace/backend && python manage.py migrate
        ;;
    "shell")
        cd /workspace/backend && python manage.py shell
        ;;
    "status")
        echo "📊 Development Environment Status"
        echo "Python: $(python --version)"
        echo "Node: $(node --version)"
        echo "NPM: $(npm --version)"
        echo "Database: $(ls -la backend/db.sqlite3 2>/dev/null && echo '✅ SQLite Ready' || echo '❌ Not ready')"
        echo "Redis: $(redis-cli ping && echo '✅ Connected' || echo '❌ Not ready')"
        ;;
    *)
        echo "📋 Available commands:"
        echo "  ./dev-commands.sh test-backend     # Run backend tests"
        echo "  ./dev-commands.sh test-frontend    # Run frontend tests"
        echo "  ./dev-commands.sh test-all         # Run all tests"
        echo "  ./dev-commands.sh lint-backend     # Lint backend code"
        echo "  ./dev-commands.sh lint-frontend    # Lint frontend code"
        echo "  ./dev-commands.sh run-backend      # Start Django server"
        echo "  ./dev-commands.sh run-frontend     # Start React server"
        echo "  ./dev-commands.sh migrate          # Run database migrations"
        echo "  ./dev-commands.sh shell            # Open Django shell"
        echo "  ./dev-commands.sh status           # Show environment status"
        exit 1
        ;;
esac
SCRIPT

chmod +x /workspace/dev-commands.sh

# Create verification script
cat > /workspace/verify-setup.sh << 'SCRIPT'
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
SCRIPT

chmod +x /workspace/verify-setup.sh

echo "✅ Development environment setup complete!"
echo ""
echo "🎯 Quick start commands:"
echo "  ./dev-commands.sh run-backend     # Start Django development server"
echo "  ./dev-commands.sh run-frontend    # Start React development server"
echo "  ./dev-commands.sh test-all        # Run all tests"
echo "  ./dev-commands.sh lint-backend    # Lint backend code"
echo "  ./dev-commands.sh migrate         # Run database migrations"
echo ""
echo "🌐 Services available at:"
echo "  Frontend: http://localhost:3000"
echo "  Backend:  http://localhost:8000"
echo "  Admin:    http://localhost:8000/admin"
echo ""
echo "💡 Note: Database (SQLite) and Redis run automatically in the background"
