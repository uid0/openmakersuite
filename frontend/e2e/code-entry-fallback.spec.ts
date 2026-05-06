/**
 * E2E: Mobile code-entry fallback (AC-14)
 *
 * Covers the camera-free public scan path on a phone-sized viewport:
 *   - /inventory/scan renders the access-code form.
 *   - The submit button is gated until a 6-character code is entered.
 *   - Invalid characters (lower-case, ambiguous glyphs like 0/O/1/I/L) are
 *     filtered out by the input — typing them yields no progress.
 *   - Submitting a well-formed but unknown code surfaces an actionable
 *     error message rather than silently failing.
 *
 * This is the proficiency contract for AC-14: a public user on a phone
 * with no camera (or denied permission) can still complete the scan flow
 * via manual entry.
 */
import { devices, expect, test } from '@playwright/test';
import { checkBackendAvailable, dismissWebpackOverlay } from './fixtures';

test.use({ ...devices['iPhone 12'] });

test.describe('Mobile code-entry fallback', () => {
  let backendAvailable = false;

  test.beforeAll(async () => {
    backendAvailable = await checkBackendAvailable();
    if (!backendAvailable) {
      console.warn(
        'Backend not available, skipping code-entry fallback E2E. Start backend on http://localhost:8000.'
      );
    }
  });

  test('phone user can use code-entry fallback when scanning is unavailable', async ({ page }) => {
    test.skip(!backendAvailable, 'Backend not available');

    await page.goto('/inventory/scan');
    await dismissWebpackOverlay(page);

    // The page heading anchors the fallback UI. If this regresses, the
    // entire camera-free path is gone and AC-14 is violated.
    await expect(page.getByRole('heading', { name: 'Enter Access Code' })).toBeVisible({
      timeout: 10000,
    });

    const codeInput = page.locator('#code');
    const submit = page.getByRole('button', { name: /Go to Item|Looking up/ });

    // Submit must stay disabled while the code is incomplete. This is the
    // duplicate-tap / accidental-submit guard for the public path.
    await expect(submit).toBeDisabled();

    // Invalid characters (lower-case, '1', 'I', '0', 'O', 'L') are stripped
    // at the input layer. Typing only-invalid input must not advance the
    // form — the input value should remain empty after these keystrokes.
    await codeInput.fill('');
    await codeInput.type('1iol0');
    await expect(codeInput).toHaveValue('');
    await expect(submit).toBeDisabled();

    // A well-formed 6-character code that does not match any seeded item
    // should produce a server "not found" error rather than navigating.
    // We use an unambiguous value drawn from the allowed alphabet
    // (excludes I, O, 0, 1, L) so the input accepts it as-is.
    await codeInput.fill('ZZZZZZ');
    await expect(submit).toBeEnabled();
    await submit.click();

    // The page must surface an actionable failure message — not a blank
    // screen, not a raw exception. AC-14 requires a recovery path.
    await expect(
      page.getByText(/not found|invalid|please check/i)
    ).toBeVisible({ timeout: 10000 });

    // The user remains on the code-entry page so they can retry. If the
    // app navigated away on error, the recovery path would be broken.
    await expect(page).toHaveURL(/\/inventory\/scan(?:\?|$)/);
  });
});
