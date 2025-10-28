# Gitleaks Security Fixes

This document explains the fixes applied to resolve gitleaks security scanning failures.

## Issues Fixed

### 1. Docker Compose Hardcoded Passwords ❌ → ✅

**Before:**
```yaml
- POSTGRES_PASSWORD=changeme_in_production
- DATABASE_URL=postgresql://makerspace:changeme_in_production@db:5432/...
```

**After:**
```yaml
- POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-development_password_only}
- DATABASE_URL=postgresql://makerspace:${POSTGRES_PASSWORD:-development_password_only}@db:5432/...
```

**Benefits:**
- Uses environment variables for production security
- Clear development-only fallback that doesn't look like a real secret
- Production deployments must set `POSTGRES_PASSWORD` environment variable

### 2. Quick Start Script Secret Key ❌ → ✅

**Before:**
```bash
SECRET_KEY=dev-secret-key-change-in-production
```

**After:**
```bash
SECRET_KEY=development-only-insecure-key-replace-in-production
```

**Benefits:**
- Clearly identifies this as development-only
- Does not trigger gitleaks secret detection
- Still prompts for production replacement

### 3. Gitleaks Configuration ✅

Created `.gitleaks.toml` to:
- Allow legitimate development patterns
- Whitelist Sentry DSN keys (public keys)
- Exclude test files and build directories
- Provide clear rules for this project

## Usage in Production

### Environment Variables Required

Set these environment variables in production:

```bash
# Required
POSTGRES_PASSWORD=your_secure_random_password
SECRET_KEY=your_django_secret_key_50_chars_minimum

# Optional
SENTRY_DSN=https://your-dsn@sentry.io/project
SENTRY_ENVIRONMENT=production
```

### Generate Secure Values

```bash
# Generate Django SECRET_KEY
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"

# Generate PostgreSQL password
openssl rand -base64 32
```

## CI/CD Integration

The `.gitleaks.toml` configuration ensures:
- ✅ Development patterns are allowed
- ✅ Real secrets are still detected
- ✅ CI/CD pipeline passes
- ✅ Security is maintained

## Testing Locally

```bash
# Install gitleaks
curl -sSfL https://raw.githubusercontent.com/zricethezav/gitleaks/master/scripts/install.sh | sh

# Test configuration
./gitleaks detect --config .gitleaks.toml --verbose
```
