import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright E2E test configuration
 *
 * Tests run against the development server (frontend + backend)
 * Make sure both services are running before executing tests
 */
export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: 'html',
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:3000',
    // Trace recording options:
    // 'on' - record trace for every test (good for debugging, larger files)
    // 'on-first-retry' - record trace only when test fails and retries (default, good balance)
    // 'retain-on-failure' - record trace only for failed tests (good for CI)
    // 'off' - no traces (fastest, no debugging info)
    trace: process.env.PLAYWRIGHT_TRACE || 'on-first-retry',
    // Video recording options:
    // 'on' - record video for every test
    // 'retain-on-failure' - keep videos only for failed tests (recommended)
    // 'on-first-retry' - record video on retry
    // 'off' - no video recording
    video: process.env.PLAYWRIGHT_VIDEO || 'retain-on-failure',
    screenshot: 'only-on-failure',
    // Add a global setup to dismiss webpack overlay
    actionTimeout: 10000,
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'firefox',
      use: { ...devices['Desktop Firefox'] },
    },
    {
      name: 'webkit',
      use: { ...devices['Desktop Safari'] },
    },
  ],

  // When PLAYWRIGHT_BASE_URL is set explicitly (CI e2e job, or a dev who
  // pre-built and is serving via serve-spa.py), use that server directly
  // instead of trying to spawn `npm start`. Only fall back to launching
  // the dev server for the unconfigured local case.
  webServer: process.env.PLAYWRIGHT_BASE_URL
    ? undefined
    : {
        command: 'npm start',
        url: 'http://localhost:3000',
        reuseExistingServer: !process.env.CI,
        timeout: 120 * 1000,
      },
});
