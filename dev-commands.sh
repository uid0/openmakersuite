#!/bin/bash
# Development helper commands
# Supports both /workspace and /workspaces/openmakersuite paths

# Detect workspace path
if [ -d "/workspaces/openmakersuite" ]; then
    WORKSPACE="/workspaces/openmakersuite"
elif [ -d "/workspace" ]; then
    WORKSPACE="/workspace"
else
    # Try current directory
    WORKSPACE="$(pwd)"
fi

BACKEND_DIR="${WORKSPACE}/backend"
FRONTEND_DIR="${WORKSPACE}/frontend"

case "$1" in
    "test-backend")
        echo "🧪 Running backend tests..."
        cd "${BACKEND_DIR}" && python -m pytest --cov=inventory --cov=reorder_queue --cov=config --cov=membership --cov=forgekey --cov=notifications --cov-report=term -v
        ;;
    "test-frontend")
        echo "🧪 Running frontend tests..."
        cd "${FRONTEND_DIR}" && npm test -- --coverage --ci --watchAll=false
        ;;
    "test-all")
        echo "🧪 Running all tests (backend + frontend)..."
        cd "${BACKEND_DIR}" && python -m pytest --cov=inventory --cov=reorder_queue --cov=config --cov=membership --cov=forgekey --cov=notifications --cov-report=term -v
        TEST_BACKEND_EXIT=$?
        cd "${FRONTEND_DIR}" && npm test -- --coverage --ci --watchAll=false
        TEST_FRONTEND_EXIT=$?
        if [ $TEST_BACKEND_EXIT -ne 0 ] || [ $TEST_FRONTEND_EXIT -ne 0 ]; then
            exit 1
        fi
        ;;
    "all-tests")
        echo "🚀 Running all CI tests (backend, frontend, security, code quality)..."
        echo ""
        
        # Code quality checks (matching CI workflow)
        echo "📋 Running code quality checks..."
        echo "  → Checking code formatting with black..."
        cd "${BACKEND_DIR}" && black --check . || { echo "❌ Black check failed"; exit 1; }
        echo "  → Checking import sorting with isort..."
        cd "${BACKEND_DIR}" && isort --check-only . || { echo "❌ isort check failed"; exit 1; }
        echo "  → Linting with flake8..."
        cd "${BACKEND_DIR}" && flake8 . || { echo "❌ flake8 check failed"; exit 1; }
        # Note: Frontend linting is not run in CI, so we skip it here to match CI behavior
        # Run 'npm run lint' separately if you want to check frontend code quality
        echo "✅ Code quality checks passed"
        echo ""
        
        # Security tests
        echo "🔒 Running security tests..."
        echo "  → Running bandit security check..."
        cd "${BACKEND_DIR}" && bandit -r . -x ./tests,./migrations -f json -o bandit-report.json || true
        cd "${BACKEND_DIR}" && bandit -r . -x ./tests,./migrations || { echo "❌ Bandit check failed"; exit 1; }
        echo "  → Checking for vulnerable dependencies..."
        cd "${BACKEND_DIR}" && (pip-audit --desc 2>/dev/null || safety check --short-report 2>/dev/null || echo "⚠️  Dependency check tools not available") || true
        echo "  → Running gitleaks scan..."
        if command -v gitleaks >/dev/null 2>&1; then
            if [ -f "${WORKSPACE}/.gitleaks.toml" ]; then
                gitleaks detect --config "${WORKSPACE}/.gitleaks.toml" --verbose --no-git || { echo "❌ Gitleaks scan failed"; exit 1; }
            else
                gitleaks detect --verbose --no-git || { echo "❌ Gitleaks scan failed"; exit 1; }
            fi
        else
            echo "⚠️  gitleaks not installed, skipping..."
        fi
        echo "✅ Security tests passed"
        echo ""
        
        # Backend tests
        echo "🧪 Running backend tests with coverage..."
        cd "${BACKEND_DIR}" && python -m pytest --cov=inventory --cov=reorder_queue --cov=config --cov=membership --cov=forgekey --cov=notifications --cov-report=term -v || { echo "❌ Backend tests failed"; exit 1; }
        echo "✅ Backend tests passed"
        echo ""
        
        # Frontend tests
        echo "🧪 Running frontend tests with coverage..."
        cd "${FRONTEND_DIR}" && npm test -- --coverage --ci --watchAll=false || { echo "❌ Frontend tests failed"; exit 1; }
        echo "✅ Frontend tests passed"
        echo ""
        
        echo "✅ All tests passed!"
        ;;
    "security-tests")
        echo "🔒 Running security tests..."
        echo "  → Running bandit security check..."
        cd "${BACKEND_DIR}" && bandit -r . -x ./tests,./migrations -f json -o bandit-report.json || true
        cd "${BACKEND_DIR}" && bandit -r . -x ./tests,./migrations
        echo "  → Checking for vulnerable dependencies..."
        cd "${BACKEND_DIR}" && (pip-audit --desc 2>/dev/null || safety check --short-report 2>/dev/null || echo "⚠️  Dependency check tools not available")
        echo "  → Running gitleaks scan..."
        if command -v gitleaks >/dev/null 2>&1; then
            if [ -f "${WORKSPACE}/.gitleaks.toml" ]; then
                gitleaks detect --config "${WORKSPACE}/.gitleaks.toml" --verbose --no-git
            else
                gitleaks detect --verbose --no-git
            fi
        else
            echo "⚠️  gitleaks not installed, skipping..."
        fi
        ;;
    "lint-backend")
        echo "📋 Running backend linting..."
        cd "${BACKEND_DIR}" && flake8 . && black --check . && isort --check-only .
        ;;
    "lint-frontend")
        echo "📋 Running frontend linting..."
        cd "${FRONTEND_DIR}" && npm run lint
        ;;
    "quality-checks")
        echo "📋 Running all code quality checks..."
        echo "  → Checking code formatting with black..."
        cd "${BACKEND_DIR}" && black --check .
        echo "  → Checking import sorting with isort..."
        cd "${BACKEND_DIR}" && isort --check-only .
        echo "  → Linting with flake8..."
        cd "${BACKEND_DIR}" && flake8 .
        echo "  → Linting frontend (optional, not in CI)..."
        cd "${FRONTEND_DIR}" && npm run lint || echo "⚠️  Frontend lint has issues (non-fatal)"
        ;;
    "run-backend")
        cd "${BACKEND_DIR}" && python manage.py runserver 0.0.0.0:8000
        ;;
    "run-frontend")
        cd "${FRONTEND_DIR}" && npm start
        ;;
    "migrate")
        cd "${BACKEND_DIR}" && python manage.py migrate
        ;;
    "shell")
        cd "${BACKEND_DIR}" && python manage.py shell
        ;;
    "status")
        echo "📊 Development Environment Status"
        echo "Python: $(python --version)"
        echo "Node: $(node --version)"
        echo "NPM: $(npm --version)"
        echo "Workspace: ${WORKSPACE}"
        echo "Database: $(ls -la ${BACKEND_DIR}/db.sqlite3 2>/dev/null && echo '✅ SQLite Ready' || echo '❌ Not ready')"
        echo "Redis: $(redis-cli ping 2>/dev/null && echo '✅ Connected' || echo '❌ Not ready')"
        ;;
    *)
        echo "📋 Available commands:"
        echo "  ./dev-commands.sh test-backend      # Run backend tests with coverage"
        echo "  ./dev-commands.sh test-frontend     # Run frontend tests with coverage"
        echo "  ./dev-commands.sh test-all          # Run backend + frontend tests"
        echo "  ./dev-commands.sh all-tests         # Run all CI tests (tests + security + quality)"
        echo "  ./dev-commands.sh security-tests    # Run security tests (bandit, pip-audit, gitleaks)"
        echo "  ./dev-commands.sh lint-backend      # Lint backend code (flake8, black, isort)"
        echo "  ./dev-commands.sh lint-frontend     # Lint frontend code"
        echo "  ./dev-commands.sh quality-checks    # Run all code quality checks"
        echo "  ./dev-commands.sh run-backend       # Start Django server"
        echo "  ./dev-commands.sh run-frontend      # Start React server"
        echo "  ./dev-commands.sh migrate           # Run database migrations"
        echo "  ./dev-commands.sh shell             # Open Django shell"
        echo "  ./dev-commands.sh status            # Show environment status"
        exit 1
        ;;
esac
