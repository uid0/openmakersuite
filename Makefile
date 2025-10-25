.PHONY: help build up down logs shell test test-backend test-frontend coverage migrate superuser clean

help:  ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Available targets:'
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

build:  ## Build Docker containers
	docker-compose build

up:  ## Start all services
	docker-compose up -d

down:  ## Stop all services
	docker-compose down

logs:  ## Show logs from all services
	docker-compose logs -f

shell-backend:  ## Open shell in backend container
	docker-compose exec backend /bin/bash

shell-frontend:  ## Open shell in frontend container
	docker-compose exec frontend /bin/sh

# Testing targets
test:  ## Run all tests (backend and frontend)
	@echo "Running backend tests..."
	@make test-backend
	@echo "\nRunning frontend tests..."
	@make test-frontend

test-backend:  ## Run backend tests with coverage
	docker-compose exec backend pytest --cov --cov-report=term-missing

test-backend-unit:  ## Run only backend unit tests
	docker-compose exec backend pytest -m unit

test-backend-integration:  ## Run only backend integration tests
	docker-compose exec backend pytest -m integration

test-frontend:  ## Run frontend tests with coverage
	docker-compose exec frontend npm run test:coverage

test-watch:  ## Run frontend tests in watch mode
	docker-compose exec frontend npm test

coverage:  ## Generate coverage reports
	@echo "Generating backend coverage report..."
	docker-compose exec backend pytest --cov --cov-report=html
	@echo "Backend coverage report: backend/htmlcov/index.html"
	@echo "\nGenerating frontend coverage report..."
	docker-compose exec frontend npm run test:coverage
	@echo "Frontend coverage report: frontend/coverage/lcov-report/index.html"

# Database targets
migrate:  ## Run database migrations
	docker-compose exec backend python manage.py migrate

makemigrations:  ## Create new database migrations
	docker-compose exec backend python manage.py makemigrations

superuser:  ## Create Django superuser
	docker-compose exec backend python manage.py createsuperuser

# Utility targets
clean:  ## Clean up containers, volumes, and cache
	docker-compose down -v
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf backend/htmlcov
	rm -rf backend/.coverage
	rm -rf frontend/coverage
	rm -rf frontend/node_modules/.cache

lint-backend:  ## Lint backend code with flake8
	docker-compose exec backend flake8 . || echo "flake8 not installed"

format-backend:  ## Format backend code with black
	docker-compose exec backend black . || echo "black not installed"

format-check-backend:  ## Check backend code formatting without changing
	docker-compose exec backend black --check --diff . || echo "black not installed"

isort-backend:  ## Sort backend imports
	docker-compose exec backend isort . || echo "isort not installed"

isort-check-backend:  ## Check backend import sorting without changing
	docker-compose exec backend isort --check-only --diff . || echo "isort not installed"

quality-backend:  ## Run all backend code quality checks
	@echo "Running flake8..."
	@make lint-backend
	@echo "\nChecking black formatting..."
	@make format-check-backend
	@echo "\nChecking import sorting..."
	@make isort-check-backend

security-backend:  ## Run backend security checks
	@echo "Running bandit security checks..."
	docker-compose exec backend bandit -r . -x ./tests,./migrations || echo "bandit not installed"
	@echo "\nChecking for vulnerable dependencies..."
	docker-compose exec backend safety check || echo "safety not installed"

lint-frontend:  ## Lint frontend code
	docker-compose exec frontend npm run lint || echo "No lint script configured"

install:  ## Install dependencies
	docker-compose run backend pip install -r requirements.txt
	docker-compose run frontend npm install

setup:  ## Initial project setup
	@echo "Setting up project..."
	cp .env.example .env
	docker-compose build
	docker-compose up -d
	@echo "Waiting for database..."
	sleep 10
	docker-compose exec backend python manage.py migrate
	@echo "\nSetup complete! Run 'make superuser' to create an admin account."

reset-db:  ## Reset database (WARNING: deletes all data)
	docker-compose down -v
	docker-compose up -d db
	sleep 5
	docker-compose exec backend python manage.py migrate
	@echo "Database reset complete"

backup-db:  ## Backup database
	docker-compose exec db pg_dump -U makerspace makerspace_inventory > backup_$$(date +%Y%m%d_%H%M%S).sql
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
	docker-compose ps

stats:  ## Show container resource usage
	docker stats $$(docker-compose ps -q)

# Pre-commit hooks
install-hooks:  ## Install pre-commit hooks
	@echo "Installing pre-commit hooks..."
	docker-compose exec backend pip install pre-commit
	docker-compose exec backend pre-commit install
	@echo "✅ Pre-commit hooks installed!"
	@echo "Hooks will run automatically on git commit"

install-dev:  ## Install development dependencies
	@echo "Installing development dependencies..."
	docker-compose exec backend pip install -r requirements-dev.txt
	@echo "✅ Development dependencies installed!"

run-hooks:  ## Run pre-commit hooks on all files
	@echo "Running pre-commit hooks on all files..."
	docker-compose exec backend pre-commit run --all-files

update-hooks:  ## Update pre-commit hooks to latest versions
	docker-compose exec backend pre-commit autoupdate

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
