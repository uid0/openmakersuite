#!/bin/bash
set -e

echo "🚀 OpenMakerSuite - Quick Development Setup"
echo "=========================================="
echo

# Check we're in the right directory
if [ ! -d "backend" ] || [ ! -d "frontend" ]; then
    echo "❌ Error: Please run this script from the project root directory"
    exit 1
fi

# Create backend .env if it doesn't exist
if [ ! -f backend/.env ]; then
    echo "📝 Creating backend/.env file..."
    cat > backend/.env << 'EOF'
# Django Configuration
DEBUG=1
SECRET_KEY=development-only-insecure-key-replace-in-production
DATABASE_URL=sqlite:///db.sqlite3
ALLOWED_HOSTS=localhost,127.0.0.1,0.0.0.0

# Development Mode (allows unauthenticated API access for easier development)
# ⚠️ NEVER enable this in production!
DEVELOPMENT_MODE=1

# Redis & Celery
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/0

# Frontend
FRONTEND_URL=http://localhost:3000

# Sentry (optional - leave empty to disable)
SENTRY_DSN=
SENTRY_ENVIRONMENT=development

# CI Flag
CI=false
EOF
    echo "✅ Created backend/.env"
else
    echo "✅ backend/.env already exists"
fi

# Install Python dependencies
echo
echo "📦 Installing Python dependencies..."
cd backend
if command -v pip3 > /dev/null 2>&1; then
    pip3 install -q -r requirements.txt -r requirements-dev.txt
elif command -v pip > /dev/null 2>&1; then
    pip install -q -r requirements.txt -r requirements-dev.txt
else
    echo "❌ Error: pip not found. Please install Python 3.11"
    exit 1
fi
cd ..

# Install Node dependencies
echo "📦 Installing Node dependencies..."
cd frontend
npm install --silent
cd ..

# Run migrations
echo
echo "🗄️  Running database migrations..."
cd backend
if command -v python3 > /dev/null 2>&1; then
    python3 manage.py migrate --no-input
elif command -v python > /dev/null 2>&1; then
    python manage.py migrate --no-input
else
    echo "❌ Error: python not found. Please install Python 3.11"
    exit 1
fi
cd ..

# Create development user
echo
echo "👤 Creating development superuser..."
cd backend
if command -v python3 > /dev/null 2>&1; then
    python3 create_dev_user.py
else
    python create_dev_user.py
fi
cd ..

# Check Redis
echo
echo "🔍 Checking Redis..."
if redis-cli ping > /dev/null 2>&1; then
    echo "✅ Redis is running"
else
    echo "⚠️  Redis is not running. Starting Redis..."
    if command -v redis-server > /dev/null 2>&1; then
        redis-server --daemonize yes 2>/dev/null || \
        sudo service redis-server start 2>/dev/null || \
        echo "   ℹ️  Could not start Redis automatically. Please start it manually."
    else
        echo "   ℹ️  Redis not installed. Install with: brew install redis (macOS) or apt-get install redis-server (Linux)"
    fi
fi

echo
echo "✅ Setup complete!"
echo
echo "🚀 To start development:"
echo
echo "   Terminal 1 (Backend):"
echo "   $ cd backend && python manage.py runserver"
echo
echo "   Terminal 2 (Frontend):"
echo "   $ cd frontend && npm start"
echo
echo "   Terminal 3 (Celery Worker - optional):"
echo "   $ cd backend && celery -A config worker -l info"
echo
echo "🔐 Development Authentication:"
echo "   Username: admin"
echo "   Password: admin"
echo "   Django Admin: http://localhost:8000/admin"
echo
echo "   With DEVELOPMENT_MODE=1, the API allows unauthenticated access."
echo "   To require authentication, set DEVELOPMENT_MODE=0 in backend/.env"
echo
echo "📚 For more details, see:"
echo "   - README.md"
echo "   - DEV_AUTH_GUIDE.md"
echo

