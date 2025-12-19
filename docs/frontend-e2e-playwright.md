# Playwright Testing Guide

## Recording Interactions for Review

Playwright provides several ways to record and review test interactions:

### 1. **Trace Viewer** (Recommended for Debugging)

Traces record the entire test execution including:
- DOM snapshots at each step
- Network requests/responses
- Console logs
- Screenshots at each action
- Timeline of all events

**View traces:**
```bash
# After running tests, open the trace viewer
npm run test:e2e:show-report

# Or view a specific trace file
npx playwright show-trace trace.zip
```

**Configure trace recording:**
- `trace: 'on'` - Record every test (useful for debugging, larger files)
- `trace: 'on-first-retry'` - Record only when test fails and retries (default, good balance)
- `trace: 'retain-on-failure'` - Keep traces only for failed tests (good for CI)
- `trace: 'off'` - No traces (fastest)

**Run with full trace recording:**
```bash
npm run test:e2e:trace
```

### 2. **Video Recording**

Videos show the full browser interaction during tests.

**Configure video recording:**
- `video: 'on'` - Record video for every test
- `video: 'retain-on-failure'` - Keep videos only for failed tests (recommended)
- `video: 'on-first-retry'` - Record video on retry
- `video: 'off'` - No video recording

**Run with video recording:**
```bash
npm run test:e2e:video
```

Videos are saved in `test-results/` directory and included in the HTML report.

### 3. **Screenshots**

Already configured to capture screenshots on failure. Screenshots are saved in `test-results/` and included in reports.

### 4. **Playwright Codegen** (Record New Tests)

Record interactions to generate test code:

```bash
npm run test:e2e:record
```

This opens a browser and Playwright Inspector. As you interact with the page, it generates test code that you can copy into your test files.

### 5. **HTML Report**

After running tests, view the interactive HTML report:

```bash
npm run test:e2e:show-report
```

The report includes:
- Test results with pass/fail status
- Screenshots for failed tests
- Videos (if enabled)
- Traces (if enabled)
- Timeline of test execution

## Ensuring Tests Run Correctly

### Prerequisites Checklist

Before running Playwright tests, ensure:

1. **Backend is running**
   ```bash
   # In backend directory
   python manage.py runserver
   # Should be accessible at http://localhost:8000
   ```

2. **Frontend is running** (or will be started automatically)
   ```bash
   # In frontend directory
   npm start
   # Should be accessible at http://localhost:3000
   ```

   Note: Playwright config has `webServer` configured, so it will automatically start the frontend if not running (unless `CI=true`).

3. **Database is set up**
   ```bash
   # In backend directory
   python manage.py migrate
   ```

4. **Browsers are installed**
   ```bash
   npx playwright install
   # Or for specific browser:
   npx playwright install chromium
   ```

### Running Tests

**Basic test run:**
```bash
npm run test:e2e
```

**With UI mode (recommended for debugging):**
```bash
npm run test:e2e:ui
```
- Interactive UI shows test execution in real-time
- Can pause, step through, and inspect at any point
- Shows network requests, console logs, and DOM state

**Headed mode (see browser):**
```bash
npm run test:e2e:headed
```
- Runs tests with visible browser window
- Useful for seeing what's happening

**Debug mode:**
```bash
npm run test:e2e:debug
```
- Opens Playwright Inspector
- Step through tests line by line
- Inspect page state at each step

### Configuration Verification

Check your `playwright.config.ts`:

1. **Base URL** - Should match your frontend URL
   ```typescript
   baseURL: process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:3000'
   ```

2. **API URL** - Check `e2e/fixtures.ts` for backend URL
   ```typescript
   API_BASE_URL = process.env.PLAYWRIGHT_API_URL || 'http://localhost:8000/api'
   ```

3. **Web Server** - Auto-starts frontend if not running
   ```typescript
   webServer: {
     command: 'npm start',
     url: 'http://localhost:3000',
     reuseExistingServer: !process.env.CI,
   }
   ```

### Common Issues and Solutions

#### 1. "Connection refused" errors

**Problem:** Backend or frontend not running

**Solution:**
```bash
# Check if backend is running
curl http://localhost:8000/api/auth/login/

# Check if frontend is running
curl http://localhost:3000

# Start services if needed
# Backend: python manage.py runserver
# Frontend: npm start (or let Playwright start it)
```

#### 2. "Browser not found" errors

**Problem:** Playwright browsers not installed

**Solution:**
```bash
npx playwright install
# Or for CI:
npx playwright install --with-deps chromium
```

#### 3. Tests timeout

**Problem:** Tests taking too long or waiting for elements

**Solution:**
- Increase timeout in config:
  ```typescript
  use: {
    actionTimeout: 30000, // 30 seconds
  }
  ```
- Or in specific test:
  ```typescript
  test.setTimeout(60000); // 60 seconds
  ```

#### 4. Authentication failures

**Problem:** Test users not created or tokens invalid

**Solution:**
- Check backend auth endpoints are working
- Verify test user creation in `fixtures.ts
- Check token storage in browser:
  ```typescript
  // In test, after login:
  const token = await page.evaluate(() => localStorage.getItem('token'));
  console.log('Token:', token);
  ```

#### 5. Database errors

**Problem:** Migrations not applied or database not accessible

**Solution:**
```bash
# Apply migrations
python manage.py migrate

# Check database connection
python manage.py dbshell
```

#### 6. Tests fail in CI but pass locally

**Problem:** Environment differences

**Solution:**
- Set CI environment variables:
  ```bash
  CI=true
  PLAYWRIGHT_BASE_URL=http://your-frontend-url
  PLAYWRIGHT_API_URL=http://your-backend-url/api
  ```
- Check CI logs for specific errors
- Use `test:e2e:headed` locally to see what's happening

### Best Practices

1. **Use UI mode for development**
   ```bash
   npm run test:e2e:ui
   ```
   - See tests run in real-time
   - Easy to debug failures
   - Can pause and inspect

2. **Enable traces for debugging**
   ```bash
   PLAYWRIGHT_TRACE=on npm run test:e2e
   ```
   - Full execution history
   - Network requests
   - DOM snapshots

3. **Use video for failed tests**
   - Already configured with `video: 'retain-on-failure'`
   - Videos show exactly what happened

4. **Check HTML report after runs**
   ```bash
   npm run test:e2e:show-report
   ```
   - Comprehensive view of all test results
   - Screenshots, videos, and traces included

5. **Run specific tests during development**
   ```bash
   npx playwright test e2e/asset-scan.spec.ts
   npx playwright test e2e/asset-scan.spec.ts -g "authenticated user"
   ```

6. **Use fixtures for common setup**
   - Reuse test data creation
   - Consistent authentication
   - Shared helper functions

### Environment Variables

Configure via environment variables:

```bash
# Frontend URL
PLAYWRIGHT_BASE_URL=http://localhost:3000

# Backend API URL
PLAYWRIGHT_API_URL=http://localhost:8000/api

# Trace recording
PLAYWRIGHT_TRACE=on  # or 'on-first-retry', 'retain-on-failure', 'off'

# Video recording
PLAYWRIGHT_VIDEO=on  # or 'retain-on-failure', 'on-first-retry', 'off'

# CI mode
CI=true
```

### Test Output Locations

- **Screenshots**: `test-results/` directory
- **Videos**: `test-results/` directory (if enabled)
- **Traces**: `test-results/` directory (if enabled)
- **HTML Report**: `playwright-report/index.html`

### Quick Reference

```bash
# Run all tests
npm run test:e2e

# Interactive UI mode (best for debugging)
npm run test:e2e:ui

# See browser while running
npm run test:e2e:headed

# Step through with debugger
npm run test:e2e:debug

# Record new test interactions
npm run test:e2e:record

# View test report
npm run test:e2e:show-report

# Run with full trace recording
npm run test:e2e:trace

# Run with video recording
npm run test:e2e:video

# Run specific test file
npx playwright test e2e/asset-scan.spec.ts

# Run tests matching pattern
npx playwright test -g "authenticated"
```
