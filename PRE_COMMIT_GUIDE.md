# Pre-Commit Hooks Guide

## Overview

This project uses pre-commit hooks to ensure code quality before commits. Pre-commit automatically runs code formatters, linters, and security checks on your code.

## Quick Start

### For Docker Users (Recommended)

The easiest way to validate your code is using the Makefile commands:

```bash
# Run all pre-commit checks manually
make pre-commit

# This runs:
# - black (code formatting)
# - isort (import sorting)
# - flake8 (linting)
# - pytest (tests)
```

### For Local Development (Outside Docker)

If you're working outside Docker and have initialized a git repository:

1. **Install pre-commit**:
   ```bash
   cd backend
   pip install pre-commit
   ```

2. **Install git hooks**:
   ```bash
   pre-commit install
   ```

3. **Run on all files** (optional):
   ```bash
   pre-commit run --all-files
   ```

Now pre-commit will run automatically on `git commit`.

## What Hooks Run

The pre-commit configuration includes:

### 1. General File Cleanups
- **trailing-whitespace**: Removes trailing whitespace
- **end-of-file-fixer**: Ensures files end with newline
- **check-yaml**: Validates YAML syntax
- **check-json**: Validates JSON syntax
- **check-toml**: Validates TOML syntax
- **check-added-large-files**: Prevents large files (>1MB)
- **check-merge-conflict**: Detects merge conflict markers
- **detect-private-key**: Prevents committing private keys
- **mixed-line-ending**: Enforces consistent line endings

### 2. Python Code Quality

**Black** - Code Formatter
- Automatically formats Python code
- Line length: 100 characters
- Skips migrations

**isort** - Import Sorter
- Sorts and organizes imports
- Profile: black (compatible with black)
- Django-aware import grouping

**flake8** - Linter
- Checks PEP 8 style compliance
- Max line length: 127
- Max complexity: 15
- Ignores: E203, W503, E501, F401, F841
- Skips migrations

### 3. Security

**bandit** - Security Scanner
- Scans for common security issues
- Checks for SQL injection risks
- Validates secure random usage
- Detects hardcoded passwords
- Skips tests and migrations

### 4. Django-Specific

**django-upgrade** - Django Best Practices
- Automatically upgrades Django patterns
- Target version: Django 4.2
- Modernizes deprecated code

## Usage Workflows

### Before Every Commit (Recommended)

```bash
# Quick check (formatting + linting)
make format-backend
make isort-backend
make lint-backend

# Or use the combined command
make pre-commit  # Includes tests
```

### Auto-fix Code Issues

```bash
# Auto-format code with black
make format-backend

# Auto-sort imports
make isort-backend

# Then check for remaining issues
make lint-backend
```

### Manual Pre-Commit Run

If you have git initialized in your project root:

```bash
cd /path/to/dms-inventory-management-system
pre-commit run --all-files
```

### Updating Hooks

```bash
# Update to latest hook versions
make update-hooks
```

## Configuration Files

### `.pre-commit-config.yaml`
Located in `backend/.pre-commit-config.yaml`, this file defines all hooks.

### `.flake8`
Located in `backend/.flake8`, configures flake8 linting rules.

### `pyproject.toml`
Located in `backend/pyproject.toml`, configures black and isort.

## Common Issues and Solutions

### Issue: "pre-commit: command not found"

**Solution**: Install pre-commit:
```bash
docker-compose exec backend pip install pre-commit
# OR
cd backend && pip install -r requirements-dev.txt
```

### Issue: "git failed. Is it installed?"

**Cause**: Pre-commit requires git to be initialized.

**Solution**: Either initialize git or use manual checks:
```bash
# Initialize git (if not done)
git init

# OR use manual checks instead
make pre-commit
```

### Issue: Black/isort changed my code

**This is expected behavior!** Black and isort automatically format your code.

**What to do**:
1. Review the changes
2. If they look good, stage them: `git add .`
3. Commit again

### Issue: Flake8 failures

**Solution**: Fix the reported issues or update `.flake8` to ignore specific errors (if justified).

```bash
# Common fixes:
# - Remove unused imports (F401)
# - Remove unused variables (F841)
# - Simplify complex functions (C901)
```

### Issue: Bandit security warnings

**Solution**: Review and fix security issues. Common ones:
- Don't use `eval()` or `exec()`
- Don't hardcode passwords
- Use `secrets` module for random values
- Validate user input

## Skipping Hooks (Not Recommended)

If you absolutely must skip hooks (not recommended):

```bash
git commit --no-verify -m "Your message"
```

**Warning**: This bypasses all quality checks. Use sparingly!

## Integration with CI/CD

Pre-commit hooks are also run in GitHub Actions CI/CD pipeline:

- Every push runs: black (check), isort (check), flake8
- Pull requests must pass all checks
- Coverage must remain above 80%

## Best Practices

### 1. Run Before Committing
Always run `make pre-commit` before committing:
```bash
make pre-commit && git commit -m "Your message"
```

### 2. Fix Issues Immediately
Don't accumulate technical debt. Fix issues as they arise.

### 3. Keep Hooks Updated
```bash
make update-hooks  # Monthly
```

### 4. Review Auto-fixes
Black and isort will auto-fix code. Always review changes:
```bash
git diff  # Before staging
```

### 5. Use Type Hints
Pre-commit works better with properly typed code:
```python
def my_function(name: str) -> str:
    return f"Hello {name}"
```

## Makefile Commands Reference

```bash
# Pre-commit related
make install-dev      # Install all dev dependencies
make install-hooks    # Install pre-commit git hooks
make run-hooks        # Run pre-commit on all files
make update-hooks     # Update hook versions

# Code quality
make format-backend         # Auto-format with black
make format-check-backend   # Check formatting (no changes)
make isort-backend          # Auto-sort imports
make isort-check-backend    # Check import sorting
make lint-backend           # Run flake8
make quality-backend        # Run all checks

# Security
make security-backend  # Run bandit + safety

# Combined workflows
make pre-commit  # Format, sort, lint, test
make ci-test     # Full CI suite locally
```

## Editor Integration

### VS Code

Install these extensions for automatic formatting:

```json
{
  "python.formatting.provider": "black",
  "python.linting.flake8Enabled": true,
  "editor.formatOnSave": true,
  "python.sortImports.provider": "isort",
  "[python]": {
    "editor.codeActionsOnSave": {
      "source.organizeImports": true
    }
  }
}
```

### PyCharm

1. Settings → Tools → Black → Enable
2. Settings → Tools → isort → Enable
3. Settings → Tools → External Tools → Add flake8

## Examples

### Successful Pre-Commit

```bash
$ make pre-commit
Running pre-commit checks...
Formatting code with black...
All done! ✨ 🍰 ✨
12 files left unchanged.

Sorting imports...
Skipped 2 files

Linting with flake8...
0 errors found

Running tests...
70 passed in 8.97s

✅ Pre-commit checks complete!
```

### Failed Pre-Commit

```bash
$ make pre-commit
Running pre-commit checks...
Formatting code with black...
reformatted inventory/models.py
1 file reformatted, 11 files left unchanged.

Sorting imports...
Fixing inventory/views.py

Linting with flake8...
inventory/views.py:42:1: F401 'os' imported but unused
1 error found

❌ Please fix linting errors and try again
```

**Fix**: Remove unused import, then run again.

## Getting Help

If you encounter issues:

1. Check this guide
2. Review error messages carefully
3. Check [pre-commit documentation](https://pre-commit.com/)
4. Ask in team chat/GitHub issues

## Summary

Pre-commit hooks enforce code quality automatically:

✅ Consistent code style (black)
✅ Organized imports (isort)
✅ PEP 8 compliance (flake8)
✅ Security checks (bandit)
✅ Django best practices (django-upgrade)

**Remember**: `make pre-commit` before every commit!
