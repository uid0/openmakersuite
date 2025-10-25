# CI/CD Documentation

## Overview

This project uses GitHub Actions for Continuous Integration and Continuous Deployment (CI/CD). The workflow automatically runs tests, linting, and security checks on every push and pull request.

## Workflow Jobs

The CI pipeline consists of four main jobs:

### 1. Backend Tests
**Purpose**: Test the Django backend application

**Steps**:
- Sets up Python 3.11 environment
- Starts PostgreSQL and Redis services
- Installs dependencies
- Runs code linting (flake8)
- Checks code formatting (black)
- Checks import sorting (isort)
- Runs database migrations
- Executes pytest with coverage (80% minimum)
- Uploads coverage reports to Codecov

**Environment Variables**:
- `DATABASE_URL`: PostgreSQL connection string
- `REDIS_URL`: Redis connection string
- `SECRET_KEY`: Django secret key for testing
- `DEBUG`: Enabled for CI
- `ALLOWED_HOSTS`: Localhost for testing

### 2. Frontend Tests
**Purpose**: Test the React frontend application

**Steps**:
- Sets up Node.js 18 environment
- Installs npm dependencies
- Runs linting (if configured)
- Executes Jest tests with coverage (70% minimum)
- Uploads coverage reports to Codecov

**Environment Variables**:
- `CI`: true (enables CI mode for create-react-app)

### 3. Docker Build Test
**Purpose**: Verify Docker Compose setup works correctly

**Steps**:
- Builds all Docker images
- Starts all services (db, redis, backend, celery, frontend)
- Waits for services to be healthy
- Runs database migrations
- Executes backend tests in Docker environment
- Shows logs on failure for debugging
- Cleans up containers and volumes

**Why This Matters**:
Ensures the production-like Docker environment works correctly and all services can communicate.

### 4. Code Quality & Security
**Purpose**: Security scanning and code quality checks

**Steps**:
- Runs `bandit` for Python security issues
- Runs `safety` to check for vulnerable dependencies
- Runs `gitleaks` to detect accidentally committed secrets

## Code Quality Tools

### Backend

#### flake8
Python linting tool that checks for:
- PEP 8 style violations
- Syntax errors
- Undefined names
- Code complexity

**Configuration**: `backend/.flake8`
```bash
# Run manually
cd backend
flake8 .
```

#### black
Opinionated code formatter for consistent Python style.

**Configuration**: `backend/pyproject.toml`
```bash
# Check formatting
cd backend
black --check .

# Auto-format code
black .
```

#### isort
Sorts Python imports alphabetically and by type.

**Configuration**: `backend/pyproject.toml`
```bash
# Check import sorting
cd backend
isort --check-only .

# Auto-sort imports
isort .
```

#### bandit
Security-focused linting tool that finds common security issues.

```bash
cd backend
bandit -r . -x ./tests,./migrations
```

#### safety
Checks installed dependencies for known security vulnerabilities.

```bash
cd backend
safety check
```

### Frontend

#### ESLint
JavaScript linting (configured by create-react-app).

```bash
cd frontend
npm run lint
```

## Running Tests Locally

### Backend Tests

```bash
# With Docker (recommended)
docker-compose exec backend pytest --cov

# Without Docker
cd backend
pip install -r requirements-dev.txt
pytest --cov

# Run specific test file
pytest inventory/tests/test_models.py -v

# Run with parallel execution
pytest -n auto
```

### Frontend Tests

```bash
# With Docker
docker-compose exec frontend npm test

# Without Docker
cd frontend
npm install
npm test

# With coverage
npm run test:coverage
```

### Docker Build Test

```bash
# Full integration test
docker-compose down -v
docker-compose build
docker-compose up -d
docker-compose exec backend python manage.py migrate
docker-compose exec backend pytest
```

## Pre-commit Checks

To ensure code quality before committing, run these commands:

```bash
# Backend
cd backend
black .
isort .
flake8 .
pytest --cov

# Frontend
cd frontend
npm run lint
npm test
```

## Setting Up Pre-commit Hooks (Optional)

Install pre-commit hooks to automatically check code before commits:

```bash
# Install pre-commit
pip install pre-commit

# Install hooks
pre-commit install

# Run manually
pre-commit run --all-files
```

Create `.pre-commit-config.yaml` in project root:

```yaml
repos:
  - repo: https://github.com/psf/black
    rev: 24.1.1
    hooks:
      - id: black
        files: ^backend/

  - repo: https://github.com/pycqa/isort
    rev: 5.13.2
    hooks:
      - id: isort
        files: ^backend/

  - repo: https://github.com/pycqa/flake8
    rev: 7.0.0
    hooks:
      - id: flake8
        files: ^backend/
        exclude: migrations/
```

## Coverage Reports

### Backend Coverage
- **Target**: 80% minimum
- **Current**: ~95%
- **Report Location**: `backend/htmlcov/index.html`

```bash
# Generate HTML coverage report
cd backend
pytest --cov --cov-report=html
# Open backend/htmlcov/index.html in browser
```

### Frontend Coverage
- **Target**: 70% minimum
- **Report Location**: `frontend/coverage/lcov-report/index.html`

```bash
# Generate coverage report
cd frontend
npm run test:coverage
# Open frontend/coverage/lcov-report/index.html in browser
```

## CI/CD Best Practices

### 1. Always Run Tests Before Pushing
```bash
# Quick test
make test  # or docker-compose exec backend pytest

# Full test with coverage
make coverage
```

### 2. Keep Dependencies Updated
```bash
# Backend
cd backend
pip list --outdated

# Frontend
cd frontend
npm outdated
```

### 3. Fix Failing Tests Immediately
Don't let broken tests accumulate. Fix them before merging to main.

### 4. Monitor Coverage Trends
- Coverage should not decrease
- New code should have tests
- Aim for >80% backend, >70% frontend

### 5. Review Security Warnings
Bandit and Safety warnings should be reviewed and addressed.

## Troubleshooting CI Failures

### Backend Tests Failing

1. **Check logs in GitHub Actions**:
   - Click on the failed job
   - Expand the failed step
   - Look for error messages

2. **Reproduce locally**:
   ```bash
   docker-compose exec backend pytest -v
   ```

3. **Common issues**:
   - Database migration not applied
   - Missing environment variable
   - Test data conflicts

### Frontend Tests Failing

1. **Check for snapshot updates**:
   ```bash
   cd frontend
   npm test -- -u
   ```

2. **Common issues**:
   - Outdated snapshots
   - Missing mock data
   - Async timing issues

### Docker Build Failing

1. **Check service health**:
   ```bash
   docker-compose ps
   docker-compose logs backend
   ```

2. **Common issues**:
   - Port conflicts
   - Missing .env file
   - Image build cache issues

3. **Clear and rebuild**:
   ```bash
   docker-compose down -v
   docker system prune -f
   docker-compose build --no-cache
   docker-compose up
   ```

## Environment Variables for CI

The following environment variables can be set in GitHub repository settings:

### Required
- None (all handled in workflow)

### Optional
- `CODECOV_TOKEN`: For private repositories (coverage uploads)
- `SENTRY_AUTH_TOKEN`: For Sentry release tracking
- `SLACK_WEBHOOK_URL`: For Slack notifications on failures

## Codecov Integration

Coverage reports are automatically uploaded to Codecov after successful test runs.

**View Coverage**:
- https://codecov.io/gh/[your-username]/dms-inventory-management-system

**Badge for README**:
```markdown
[![codecov](https://codecov.io/gh/[username]/dms-inventory-management-system/branch/main/graph/badge.svg)](https://codecov.io/gh/[username]/dms-inventory-management-system)
```

## Status Badges

Add these to your README.md:

```markdown
![CI](https://github.com/[username]/dms-inventory-management-system/workflows/CI/badge.svg)
[![codecov](https://codecov.io/gh/[username]/dms-inventory-management-system/branch/main/graph/badge.svg)](https://codecov.io/gh/[username]/dms-inventory-management-system)
```

## Continuous Deployment (Future)

The current setup focuses on Continuous Integration. For Continuous Deployment:

1. **Add deployment job** to `.github/workflows/ci.yml`
2. **Deploy to staging** on merge to `develop` branch
3. **Deploy to production** on merge to `main` branch
4. **Use Docker Hub** or GitHub Container Registry for images

Example deployment job:
```yaml
deploy-production:
  name: Deploy to Production
  runs-on: ubuntu-latest
  needs: [backend-tests, frontend-tests, docker-build]
  if: github.ref == 'refs/heads/main'
  steps:
    - name: Deploy
      run: |
        # Your deployment commands here
        echo "Deploy to production"
```

## Performance Optimization

### Speed Up Tests
1. **Use pytest-xdist** for parallel execution:
   ```bash
   pytest -n auto
   ```

2. **Cache dependencies** in CI (already configured)

3. **Skip slow tests** during development:
   ```bash
   pytest -m "not slow"
   ```

### Speed Up Docker Builds
1. **Use layer caching** (already configured with buildx)
2. **Multi-stage builds** for smaller images
3. **Pre-built base images** for common dependencies

## Maintenance

### Weekly Tasks
- [ ] Review and update dependencies
- [ ] Check security advisories
- [ ] Review coverage trends

### Monthly Tasks
- [ ] Update Python/Node.js versions
- [ ] Review and update CI workflow
- [ ] Clean up old test data

### On New Features
- [ ] Add tests (TDD recommended)
- [ ] Update documentation
- [ ] Check coverage impact
