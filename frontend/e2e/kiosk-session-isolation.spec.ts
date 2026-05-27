/**
 * E2E: Kiosk / shared-device session isolation (R-10 / AC-16).
 *
 * AC-16 is the "shared-device safety" leg of R-10. A shop phone or wall
 * tablet is used by multiple makers in sequence — the scanner page,
 * the public reorder confirmation, and the donor-receipt lookup are
 * all AllowAny by design. The proficiency contract here is:
 *
 *   - /scan loads with no auth token and never silently picks up a
 *     stale `token` left in localStorage by a prior signed-in user.
 *   - No password / sensitive-secret inputs render on /scan, so a
 *     browser autofill cannot leak credentials onto a shared screen.
 *   - The anonymous scan flow operates without any Authorization
 *     header on outbound requests — confirmed at the network layer.
 *
 * If any of these regress, the kiosk path silently becomes a
 * privilege-bearing path and AC-16 is violated.
 */
import { devices, expect, test } from '@playwright/test';
import { checkBackendAvailable, dismissWebpackOverlay } from './fixtures';

test.use({ ...devices['iPhone 12'] });

test.describe('Kiosk session isolation', () => {
  let backendAvailable = false;

  test.beforeAll(async () => {
    backendAvailable = await checkBackendAvailable();
    if (!backendAvailable) {
      console.warn(
        'Backend not available, skipping kiosk-session E2E. Start backend on http://localhost:8000.',
      );
    }
  });

  test('/scan does not pick up a stale auth token from prior user', async ({
    page,
    context,
  }) => {
    test.skip(!backendAvailable, 'Backend not available');

    // Simulate a shared device where the previous user signed in and
    // never signed out. The kiosk flow must operate as truly
    // unauthenticated regardless of what's left in localStorage.
    await context.clearCookies();
    await page.addInitScript(() => {
      try {
        localStorage.setItem('token', 'stale-prior-user-token');
      } catch {
        // ignore
      }
    });

    // Capture outbound requests so we can confirm the scanner dispatch
    // call does not send an Authorization header. If it does, the
    // kiosk path is accidentally privilege-bearing.
    const dispatchRequests: Array<Record<string, string>> = [];
    page.on('request', (req) => {
      if (req.url().includes('/api/scanner/dispatch')) {
        dispatchRequests.push(req.headers());
      }
    });

    await page.goto('/scan');
    await dismissWebpackOverlay(page);

    await expect(page.getByTestId('universal-scanner-page')).toBeVisible({ timeout: 10000 });

    const input = page.getByTestId('universal-scanner-input');
    await input.fill(`kiosk-isolation-${Date.now()}`);
    await page.getByRole('button', { name: 'Submit' }).click();

    // Wait for the dispatch network call to land — the history card
    // appears once the response resolves.
    await expect(page.getByTestId('universal-scanner-history')).toBeVisible({
      timeout: 10000,
    });

    expect(dispatchRequests.length).toBeGreaterThan(0);
    for (const headers of dispatchRequests) {
      // The stale token must NOT be promoted into an Authorization
      // header on the kiosk path. axios passes lowercase header keys.
      expect(headers['authorization']).toBeUndefined();
    }
  });

  test('/scan renders no password inputs that browser autofill could target', async ({
    page,
    context,
  }) => {
    test.skip(!backendAvailable, 'Backend not available');

    await context.clearCookies();
    await page.goto('/scan');
    await dismissWebpackOverlay(page);

    await expect(page.getByTestId('universal-scanner-page')).toBeVisible({ timeout: 10000 });

    // No password fields anywhere on the kiosk page — that's the
    // simplest defence against autofill leaking a prior user's
    // credentials onto a shared screen.
    const passwordInputs = await page.locator('input[type="password"]').count();
    expect(passwordInputs).toBe(0);

    // The scanner input itself should opt out of autocomplete so a
    // mobile keyboard doesn't suggest the previous user's text.
    const scannerInput = page.getByTestId('universal-scanner-input');
    await expect(scannerInput).toHaveAttribute('autocomplete', 'off');
  });
});
