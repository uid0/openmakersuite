/**
 * E2E: Mobile scan page renders (legacy AC-14 coverage retooled)
 *
 * Originally covered a camera-free 6-character access-code fallback. That
 * fallback was intentionally removed once the org standardized on QR
 * codes (see backend/inventory/models.py: the access_code field is gone
 * from new items, and frontend/src/pages/CodeEntryPage.tsx renders only
 * the QR scanner trigger now). The test is retained on a phone-sized
 * viewport so the journey gate still verifies the public-scan landing
 * page is reachable + the scanner button is the entry surface.
 *
 * If access-code-by-keystroke entry is ever restored as a real
 * accessibility path, expand this spec to cover the keyboard flow again.
 */
import { devices, expect, test } from '@playwright/test';
import { checkBackendAvailable, dismissWebpackOverlay } from './fixtures';

// Pixel 5 is a chromium-based device emulation, so this still proves the
// phone-sized landing path but stays on the one browser engine CI
// installs. iPhone 12 forces webkit and breaks the chromium-only CI job
// with `webkit Executable doesn't exist`.
test.use({ ...devices['Pixel 5'] });

test.describe('Mobile scan landing page', () => {
  let backendAvailable = false;

  test.beforeAll(async () => {
    backendAvailable = await checkBackendAvailable();
    if (!backendAvailable) {
      console.warn(
        'Backend not available, skipping mobile scan E2E. Start backend on http://localhost:8000.'
      );
    }
  });

  test('phone user lands on the scan page and sees the scanner trigger', async ({ page }) => {
    test.skip(!backendAvailable, 'Backend not available');

    await page.goto('/inventory/scan');
    await dismissWebpackOverlay(page);

    // Heading + intro copy anchor the page. If this regresses, members
    // arriving from a printed QR can't recover.
    await expect(page.getByRole('heading', { name: 'Scan QR Code' })).toBeVisible({
      timeout: 10000,
    });
    await expect(
      page.getByText(/Scan an item, asset, or location/i)
    ).toBeVisible();

    // The scanner button must be enabled on initial render — a disabled
    // button here means the loading state is stuck.
    const scanButton = page.getByRole('button', { name: /Scan QR Code/i });
    await expect(scanButton).toBeEnabled();
  });
});
