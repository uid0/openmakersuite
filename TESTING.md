## Testing and Code Coverage

This project includes comprehensive test coverage for both backend (Django) and frontend (React) components.

## Backend Testing (Django + pytest)

### Test Infrastructure

- **Framework**: pytest with pytest-django
- **Coverage Tool**: coverage.py with pytest-cov
- **Factories**: factory_boy for test data generation
- **Mocking**: pytest-mock
- **Time Mocking**: freezegun

### Running Backend Tests

```bash
# Run all tests
docker-compose exec backend pytest

# Run with coverage
docker-compose exec backend pytest --cov

# Run specific test file
docker-compose exec backend pytest inventory/tests/test_models.py

# Run specific test class
docker-compose exec backend pytest inventory/tests/test_models.py::TestInventoryItemModel

# Run specific test
docker-compose exec backend pytest inventory/tests/test_models.py::TestInventoryItemModel::test_item_creation

# Run tests with markers
docker-compose exec backend pytest -m unit
docker-compose exec backend pytest -m integration
```

### Coverage Goals

- **Minimum Coverage**: 80%
- **Coverage Reports**: HTML, XML, and terminal output
- **HTML Report Location**: `backend/htmlcov/index.html`

### Test Organization

```
backend/
├── conftest.py                      # Global fixtures
├── pytest.ini                       # Pytest configuration
├── .coveragerc                      # Coverage configuration
├── inventory/tests/
│   ├── __init__.py
│   ├── factories.py                 # Test data factories
│   ├── test_models.py              # Model unit tests
│   ├── test_api.py                 # API integration tests
│   ├── test_utils.py               # Utility function tests
│   └── test_tasks.py               # Celery task tests
└── reorder_queue/tests/
    ├── __init__.py
    ├── factories.py
    ├── test_models.py
    └── test_api.py
```

### Test Categories

**Unit Tests** (`@pytest.mark.unit`):
- Model business logic
- Property calculations
- Utility functions
- Celery tasks (mocked)

**Integration Tests** (`@pytest.mark.integration`):
- API endpoints
- Database interactions
- Authentication/authorization
- Full request/response cycles

### Example Test

```python
import pytest
from inventory.tests.factories import InventoryItemFactory

@pytest.mark.unit
class TestInventoryItemModel:
    def test_needs_reorder_true(self):
        """Test needs_reorder returns True when stock is low."""
        item = InventoryItemFactory(
            current_stock=5,
            minimum_stock=10
        )
        assert item.needs_reorder is True
```

### Common Fixtures

- `api_client`: Unauthenticated API client
- `authenticated_client`: Authenticated API client + user
- `admin_user`: Superuser for admin tests
- `sample_image`: Test image for upload tests
- `mock_celery_task`: Mock Celery tasks

## Frontend Testing (React + Jest)

### Test Infrastructure

- **Framework**: Jest with React Testing Library
- **Coverage Tool**: Jest built-in coverage
- **Mocking**: Jest mocks + axios-mock-adapter

### Running Frontend Tests

```bash
# Run all tests in watch mode
docker-compose exec frontend npm test

# Run tests once with coverage
docker-compose exec frontend npm run test:coverage

# Run in CI mode
docker-compose exec frontend npm run test:ci

# Run specific test file
docker-compose exec frontend npm test -- ScanPage.test.tsx
```

### Coverage Goals

- **Minimum Coverage**: 70% for all metrics
  - Branches: 70%
  - Functions: 70%
  - Lines: 70%
  - Statements: 70%

### Test Organization

```
frontend/src/
├── setupTests.ts                    # Test setup and mocks
├── __tests__/
│   ├── App.test.tsx
│   ├── services/
│   │   └── api.test.ts             # API service tests
│   ├── pages/
│   │   └── ScanPage.test.tsx       # Page component tests
│   └── components/
│       └── [component tests]
```

### Example Test

```tsx
import { render, screen, waitFor } from '@testing-library/react';
import ScanPage from '../../pages/ScanPage';
import * as api from '../../services/api';

jest.mock('../../services/api');

test('displays item details after loading', async () => {
  (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({
    data: mockItem,
  });

  render(<ScanPage />);

  await waitFor(() => {
    expect(screen.getByText('Test Widget')).toBeInTheDocument();
  });
});
```

## Continuous Integration

### GitHub Actions (Example)

```yaml
name: Tests

on: [push, pull_request]

jobs:
  backend-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Run backend tests
        run: |
          docker-compose up -d db redis
          docker-compose run backend pytest --cov --cov-fail-under=80

  frontend-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Run frontend tests
        run: |
          cd frontend
          npm ci
          npm run test:ci
```

## Coverage Reports

### Backend Coverage Report

```bash
# Generate HTML coverage report
docker-compose exec backend pytest --cov --cov-report=html

# Open in browser
open backend/htmlcov/index.html
```

### Frontend Coverage Report

```bash
# Generate coverage report
docker-compose exec frontend npm run test:coverage

# Report is displayed in terminal
# HTML report at frontend/coverage/lcov-report/index.html
```

## Writing New Tests

### Backend Test Checklist

- [ ] Use factories for test data
- [ ] Mark tests with `@pytest.mark.unit` or `@pytest.mark.integration`
- [ ] Use descriptive test names (e.g., `test_needs_reorder_when_stock_below_minimum`)
- [ ] Test both success and failure cases
- [ ] Mock external dependencies (Celery, Sentry, external APIs)
- [ ] Clean up test data (pytest-django handles this automatically)

### Frontend Test Checklist

- [ ] Mock API calls with axios-mock-adapter or jest.mock
- [ ] Test loading, success, and error states
- [ ] Use `waitFor` for asynchronous updates
- [ ] Test user interactions with `fireEvent` or `userEvent`
- [ ] Clean up with `jest.clearAllMocks()` in `beforeEach`
- [ ] Avoid testing implementation details

## Test Data Factories

### Backend Factories

```python
from inventory.tests.factories import (
    SupplierFactory,
    CategoryFactory,
    InventoryItemFactory,
    UsageLogFactory
)

# Create single instance
item = InventoryItemFactory(name='Widget')

# Create batch
items = InventoryItemFactory.create_batch(10)

# Override attributes
item = InventoryItemFactory(current_stock=5, minimum_stock=10)
```

### Frontend Test Data

```typescript
const mockItem = {
  id: 'test-id',
  name: 'Test Item',
  // ... other required fields
};
```

## Debugging Tests

### Backend

```bash
# Run with verbose output
docker-compose exec backend pytest -v

# Run with print statements
docker-compose exec backend pytest -s

# Drop into debugger on failure
docker-compose exec backend pytest --pdb

# Run last failed tests
docker-compose exec backend pytest --lf
```

### Frontend

```bash
# Debug in watch mode
docker-compose exec frontend npm test -- --watch

# Run with verbose output
docker-compose exec frontend npm test -- --verbose

# Update snapshots
docker-compose exec frontend npm test -- -u
```

## Test Performance

### Backend

- Unit tests should run in < 1 second each
- Integration tests should run in < 5 seconds each
- Use `@pytest.mark.slow` for tests taking > 5 seconds

### Frontend

- Component tests should run in < 1 second each
- Use `jest.setTimeout()` for slow tests

## Coverage Exceptions

### Backend (.coveragerc)

Excluded from coverage:
- Migration files
- Test files
- `__repr__` and `__str__` methods
- Abstract methods
- Debug-only code

### Frontend (package.json)

Excluded from coverage:
- Type definition files (*.d.ts)
- index.tsx (app entry point)
- reportWebVitals.ts

## Best Practices

### Do's

✅ Write tests before fixing bugs
✅ Test edge cases and error conditions
✅ Use factories/fixtures for consistent test data
✅ Mock external dependencies
✅ Keep tests fast and focused
✅ Use descriptive test names
✅ Aim for high coverage in critical paths

### Don'ts

❌ Test implementation details
❌ Write tests that depend on execution order
❌ Make network calls in tests
❌ Commit commented-out tests
❌ Skip tests without a good reason
❌ Test third-party library code
❌ Have flaky tests

## Troubleshooting

### Backend Tests Failing

1. Check database migrations are up to date
2. Verify test fixtures are properly set up
3. Clear any cached data: `docker-compose exec backend pytest --cache-clear`
4. Rebuild containers: `docker-compose build backend`

### Frontend Tests Failing

1. Clear Jest cache: `docker-compose exec frontend npm test -- --clearCache`
2. Update snapshots if UI changed: `npm test -- -u`
3. Check for async issues (missing `waitFor`)
4. Verify mocks are properly configured

### Coverage Not Updating

**Backend:**
```bash
# Remove coverage data
docker-compose exec backend coverage erase
docker-compose exec backend pytest --cov
```

**Frontend:**
```bash
# Remove coverage directory
rm -rf frontend/coverage
docker-compose exec frontend npm run test:coverage
```

## Resources

- [pytest documentation](https://docs.pytest.org/)
- [pytest-django documentation](https://pytest-django.readthedocs.io/)
- [React Testing Library](https://testing-library.com/react)
- [Jest documentation](https://jestjs.io/)
- [factory_boy documentation](https://factoryboy.readthedocs.io/)

## Coverage Status

Run these commands to check current coverage:

```bash
# Backend coverage
docker-compose exec backend pytest --cov --cov-report=term-missing

# Frontend coverage
docker-compose exec frontend npm run test:coverage
```

Target: **80%+ backend coverage, 70%+ frontend coverage**
