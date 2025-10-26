#!/bin/bash

# DevContainer post-creation setup script
# This script runs after the devcontainer is created to set up the development environment

set -e

echo "🚀 Setting up OpenMakerSuite development environment..."

# Change to workspace directory
cd /workspace

# Install backend Python dependencies
echo "📦 Installing Python dependencies..."
cd backend
pip3 install -r requirements.txt
pip3 install -r requirements-dev.txt
cd ..

# Install frontend Node dependencies
echo "📦 Installing Node.js dependencies..."
cd frontend
npm install
cd ..

# Set up git config (if not already configured)
if [ -z "$(git config --global user.name)" ]; then
    echo "⚙️  Setting up Git configuration..."
    echo "Please run the following commands to configure Git:"
    echo "  git config --global user.name \"Your Name\""
    echo "  git config --global user.email \"your.email@example.com\""
fi

# Install pre-commit hooks
echo "🪝 Setting up pre-commit hooks..."
if command -v pre-commit >/dev/null 2>&1; then
    pre-commit install
else
    echo "⚠️  pre-commit not available, skipping hook installation"
fi

# Wait for services to be ready
echo "⏳ Waiting for database and Redis to be ready..."
timeout 60s bash -c 'until pg_isready -h db -p 5432 -U makerspace; do sleep 2; done' || echo "⚠️  Database not ready, you may need to wait longer"
timeout 30s bash -c 'until redis-cli -h redis ping; do sleep 2; done' || echo "⚠️  Redis not ready, you may need to wait longer"

# Run database migrations
echo "🗄️  Running database migrations..."
cd backend
python manage.py migrate || echo "⚠️  Migration failed, database may not be ready yet"
cd ..

# Create superuser script (optional)
cat > create_superuser.sh << 'EOF'
#!/bin/bash
# Script to create Django superuser
cd /workspace/backend
echo "Creating Django superuser..."
python manage.py createsuperuser
EOF
chmod +x create_superuser.sh

echo "✅ DevContainer setup complete!"
echo ""
echo "🎯 Quick start commands:"
echo "  Backend tests:   cd backend && pytest"
echo "  Frontend tests:  cd frontend && npm test"
echo "  Run backend:     cd backend && python manage.py runserver 0.0.0.0:8000"
echo "  Run frontend:    cd frontend && npm start"
echo "  Create superuser: ./create_superuser.sh"
echo ""
echo "🌐 Services will be available at:"
echo "  Frontend: http://localhost:3000"
echo "  Backend:  http://localhost:8000"
echo ""
echo "📋 To run the exact same tests as CI:"
echo "  Backend: cd backend && flake8 . && black --check . && isort --check . && pytest --cov"
echo "  Frontend: cd frontend && npm run lint && npm run test:ci"
