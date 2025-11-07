# E2E Tests with Playwright

This directory contains end-to-end (E2E) tests for the asset management system using Playwright.

## Prerequisites

1. **Backend must be running** on `http://localhost:8000`
2. **Frontend must be running** on `http://localhost:3000`
3. **Database must be set up** with migrations applied

## Running Tests

### Run all E2E tests
```bash
npm run test:e2e
```

### Run tests in UI mode (interactive)
```bash
npm run test:e2e:ui
```

### Run tests in headed mode (see browser)
```bash
npm run test:e2e:headed
```

### Run tests in debug mode
```bash
npm run test:e2e:debug
```

### Run specific test file
```bash
npx playwright test e2e/asset-scan.spec.ts
```

## Test Structure

### Test Files

- **`asset-scan.spec.ts`**: Tests for asset QR code scanning functionality
  - Unauthenticated user scanning
  - Authenticated user scanning
  - Asset information display
  - QR code display
  - Last scanned timestamp updates
  - Problem reporting
  - Enable/disable actions

- **`admin-dashboard-assets.spec.ts`**: Tests for admin dashboard assets section
  - Assets not checked in display
  - Last scanned information
  - Asset table display

### Fixtures (`fixtures.ts`)

Helper functions for:
- Creating test users
- Logging in users
- Setting authentication tokens
- Creating test assets
- Generating QR codes
- Common assertions

## Configuration

The Playwright configuration is in `playwright.config.ts`. Key settings:

- **Base URL**: `http://localhost:3000` (configurable via `PLAYWRIGHT_BASE_URL`)
- **API URL**: `http://localhost:8000/api` (configurable via `PLAYWRIGHT_API_URL`)
- **Browsers**: Chromium, Firefox, WebKit
- **Retries**: 2 retries in CI, 0 in local development
- **Screenshots**: Taken on failure
- **Traces**: Captured on first retry

## Test Data

Tests create their own test data:
- Test users (regular and admin)
- Test assets with various configurations
- QR codes for assets

All test data is created via API calls and cleaned up automatically.

## CI/CD Integration

For CI environments, set:
- `CI=true` - Enables retries and stricter settings
- `PLAYWRIGHT_BASE_URL` - Frontend URL
- `PLAYWRIGHT_API_URL` - Backend API URL

## Troubleshooting

### Tests fail with "Connection refused"
- Ensure both frontend and backend are running
- Check that ports 3000 and 8000 are available

### Tests fail with authentication errors
- Ensure the backend has the auth endpoints configured
- Check that test users can be created

### Tests fail with database errors
- Ensure migrations are applied
- Check database connection settings

### Browser not found
- Run `npx playwright install` to install browsers
- For CI, use `npx playwright install --with-deps chromium`

## Writing New Tests

1. Create a new test file in `e2e/` directory
2. Import fixtures and helpers from `fixtures.ts`
3. Use `test.beforeAll` to set up test data
4. Use `test.afterAll` to clean up if needed
5. Follow the existing test patterns

Example:
```typescript
import { test, expect } from '@playwright/test';
import { createTestAsset, setAuthToken } from './fixtures';

test.describe('My Feature', () => {
  test('should do something', async ({ page }) => {
    // Test implementation
  });
});
```

