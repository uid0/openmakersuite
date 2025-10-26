.PHONY: help build up down logs shell test test-backend test-frontend coverage migrate superuser clean

DOCKER_COMPOSE ?= $(shell docker compose version >/dev/null 2>&1 && echo "docker compose")
ifeq ($(strip $(DOCKER_COMPOSE)),)
DOCKER_COMPOSE := $(shell command -v docker-compose >/dev/null 2>&1 && echo "docker-compose")
endif
ifeq ($(strip $(DOCKER_COMPOSE)),)
$(error Docker Compose is not installed. Install Docker Compose plugin or docker-compose v1)
endif

help:  ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Available targets:'
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

build:  ## Build Docker containers
	$(DOCKER_COMPOSE) build

up:  ## Start all services
	$(DOCKER_COMPOSE) up -d

down:  ## Stop all services
	$(DOCKER_COMPOSE) down

logs:  ## Show logs from all services
	$(DOCKER_COMPOSE) logs -f

shell-backend:  ## Open shell in backend container
	$(DOCKER_COMPOSE) exec backend /bin/bash

shell-frontend:  ## Open shell in frontend container
	$(DOCKER_COMPOSE) exec frontend /bin/sh

# Testing targets
test:  ## Run all tests (backend and frontend)
	@echo "Running backend tests..."
	@make test-backend
	@echo "\nRunning frontend tests..."
	@make test-frontend

test-backend:  ## Run backend tests with coverage
	$(DOCKER_COMPOSE) exec backend pytest --cov --cov-report=term-missing

test-backend-unit:  ## Run only backend unit tests
	$(DOCKER_COMPOSE) exec backend pytest -m unit

test-backend-integration:  ## Run only backend integration tests
	$(DOCKER_COMPOSE) exec backend pytest -m integration

test-frontend:  ## Run frontend tests with coverage
	$(DOCKER_COMPOSE) exec frontend npm run test:coverage

test-watch:  ## Run frontend tests in watch mode
	$(DOCKER_COMPOSE) exec frontend npm test

coverage:  ## Generate coverage reports
	@echo "Generating backend coverage report..."
	$(DOCKER_COMPOSE) exec backend pytest --cov --cov-report=html
	@echo "Backend coverage report: backend/htmlcov/index.html"
	@echo "\nGenerating frontend coverage report..."
	$(DOCKER_COMPOSE) exec frontend npm run test:coverage
	@echo "Frontend coverage report: frontend/coverage/lcov-report/index.html"

# Database targets
migrate:  ## Run database migrations
	$(DOCKER_COMPOSE) exec backend python manage.py migrate

makemigrations:  ## Create new database migrations
	$(DOCKER_COMPOSE) exec backend python manage.py makemigrations

superuser:  ## Create Django superuser
	$(DOCKER_COMPOSE) exec backend python manage.py createsuperuser

# Utility targets
clean:  ## Clean up containers, volumes, and cache
	$(DOCKER_COMPOSE) down -v
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf backend/htmlcov
	rm -rf backend/.coverage
	rm -rf frontend/coverage
	rm -rf frontend/node_modules/.cache

lint-backend:  ## Lint backend code with flake8
	$(DOCKER_COMPOSE) exec backend flake8 . || echo "flake8 not installed"

format-backend:  ## Format backend code with black
	$(DOCKER_COMPOSE) exec backend black . || echo "black not installed"

format-check-backend:  ## Check backend code formatting without changing
	$(DOCKER_COMPOSE) exec backend black --check --diff . || echo "black not installed"

isort-backend:  ## Sort backend imports
	$(DOCKER_COMPOSE) exec backend isort . || echo "isort not installed"

isort-check-backend:  ## Check backend import sorting without changing
	$(DOCKER_COMPOSE) exec backend isort --check-only --diff . || echo "isort not installed"

quality-backend:  ## Run all backend code quality checks
	@echo "Running flake8..."
	@make lint-backend
	@echo "\nChecking black formatting..."
	@make format-check-backend
	@echo "\nChecking import sorting..."
	@make isort-check-backend

security-backend:  ## Run backend security checks
	@echo "Running bandit security checks..."
	$(DOCKER_COMPOSE) exec backend bandit -r . -x ./tests,./migrations -s B101,B106 || echo "bandit not installed"
	@echo "\nChecking for vulnerable dependencies..."
	$(DOCKER_COMPOSE) exec backend safety check || echo "safety not installed"

lint-frontend:  ## Lint frontend code
	$(DOCKER_COMPOSE) exec frontend npm run lint || echo "No lint script configured"

install:  ## Install dependencies
	$(DOCKER_COMPOSE) run backend pip install -r requirements.txt
	$(DOCKER_COMPOSE) run frontend npm install

setup:  ## Initial project setup
	@echo "Setting up project..."
	cp .env.example .env
	$(DOCKER_COMPOSE) build
	$(DOCKER_COMPOSE) up -d
	@echo "Waiting for database..."
	sleep 10
	$(DOCKER_COMPOSE) exec backend python manage.py migrate
	@echo "\nSetup complete! Run 'make superuser' to create an admin account."

reset-db:  ## Reset database (WARNING: deletes all data)
	$(DOCKER_COMPOSE) down -v
	$(DOCKER_COMPOSE) up -d db
	sleep 5
	$(DOCKER_COMPOSE) exec backend python manage.py migrate
	@echo "Database reset complete"

backup-db:  ## Backup database
	$(DOCKER_COMPOSE) exec db pg_dump -U makerspace makerspace_inventory > backup_$$(date +%Y%m%d_%H%M%S).sql
	@echo "Database backed up"

# Development helpers
dev:  ## Start development environment
	@make up
	@echo "\nServices started!"
	@echo "Frontend: http://localhost:3000"
	@echo "Backend: http://localhost:8000"
	@echo "Admin: http://localhost:8000/admin"
	@echo "API Docs: http://localhost:8000/api/docs/"

stop:  ## Stop development environment
	@make down

restart:  ## Restart all services
	@make down
	@make up

ps:  ## Show running containers
	$(DOCKER_COMPOSE) ps

stats:  ## Show container resource usage
	docker stats $$($(DOCKER_COMPOSE) ps -q)

# Pre-commit hooks
install-hooks:  ## Install pre-commit hooks
	@echo "Installing pre-commit hooks..."
	$(DOCKER_COMPOSE) exec backend pip install pre-commit
	$(DOCKER_COMPOSE) exec backend pre-commit install
	@echo "✅ Pre-commit hooks installed!"
	@echo "Hooks will run automatically on git commit"

install-dev:  ## Install development dependencies
	@echo "Installing development dependencies..."
	$(DOCKER_COMPOSE) exec backend pip install -r requirements-dev.txt
	@echo "✅ Development dependencies installed!"

run-hooks:  ## Run pre-commit hooks on all files
	@echo "Running pre-commit hooks on all files..."
	$(DOCKER_COMPOSE) exec backend pre-commit run --all-files

update-hooks:  ## Update pre-commit hooks to latest versions
	$(DOCKER_COMPOSE) exec backend pre-commit autoupdate

# CI/CD targets
ci-test:  ## Run CI tests locally (mimics GitHub Actions)
	@echo "Running CI tests locally..."
	@echo "\n=== Code Quality Checks ==="
	@make quality-backend
	@echo "\n=== Security Checks ==="
	@make security-backend
	@echo "\n=== Backend Tests ==="
	@make test-backend
	@echo "\n=== Frontend Tests ==="
	@make test-frontend
	@echo "\n✅ All CI checks passed!"

pre-commit:  ## Run pre-commit checks (format, lint, test)
	@echo "Running pre-commit checks..."
	@make format-backend
	@make isort-backend
	@make lint-backend
	@make test-backend
	@echo "\n✅ Pre-commit checks complete!"

# GitHub Actions testing with act
act-test:  ## Run GitHub Actions workflow locally using act
	@echo "Running GitHub Actions CI workflow locally with act..."
	@command -v act >/dev/null 2>&1 || { echo "❌ Error: act is not installed. Install with: brew install act (macOS) or see https://github.com/nektos/act"; exit 1; }
	act --verbose

act-backend:  ## Run only backend tests job locally with act
	@echo "Running backend tests job locally with act..."
	@command -v act >/dev/null 2>&1 || { echo "❌ Error: act is not installed. Install with: brew install act (macOS) or see https://github.com/nektos/act"; exit 1; }
	act -j backend-tests --verbose

act-frontend:  ## Run only frontend tests job locally with act
	@echo "Running frontend tests job locally with act..."
	@command -v act >/dev/null 2>&1 || { echo "❌ Error: act is not installed. Install with: brew install act (macOS) or see https://github.com/nektos/act"; exit 1; }
	act -j frontend-tests --verbose

act-security:  ## Run only security/code quality job locally with act
	@echo "Running code quality & security job locally with act..."
	@command -v act >/dev/null 2>&1 || { echo "❌ Error: act is not installed. Install with: brew install act (macOS) or see https://github.com/nektos/act"; exit 1; }
	act -j code-quality --verbose

act-docker:  ## Run only docker build job locally with act
	@echo "Running docker build job locally with act..."
	@command -v act >/dev/null 2>&1 || { echo "❌ Error: act is not installed. Install with: brew install act (macOS) or see https://github.com/nektos/act"; exit 1; }
	act -j docker-build --verbose

act-install:  ## Install act utility (macOS only)
	@echo "Installing act via Homebrew..."
	@command -v brew >/dev/null 2>&1 || { echo "❌ Error: Homebrew is not installed. Please install from https://brew.sh/"; exit 1; }
	brew install act
	@echo "✅ act installed successfully!"
	@echo "💡 You can now run 'make act-test' to test GitHub Actions locally"
