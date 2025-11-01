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
