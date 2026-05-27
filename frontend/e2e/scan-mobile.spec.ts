/**
 * E2E: Universal scanner on a phone-sized viewport (R-10 / AC-14, AC-15).
 *
 * `/scan` is the kiosk-mode anonymous scanner page. Most field uses are
 * a shipping clerk on a phone tapping the auto-focused input, scanning
 * a code with a Bluetooth scanner (which types + presses Enter), and
 * watching the recent-scans card update. This spec exercises that
 * contract on an iPhone-sized viewport:
 *
 *   - The page lands without auth and renders the scanner input.
 *   - The input is auto-focused so a BT scanner can type into it
 *     without a tap (AC-14 — phone user with no mouse hits Submit
 *     without extra interaction).
 *   - An unknown payload produces a visible error state in the
 *     recent-scans history (AC-15 — clear final-state surface).
 *   - The input clears + refocuses after submit so the next scan in
 *     a sequence drops straight in.
 */
import { devices, expect, test } from '@playwright/test';
import { checkBackendAvailable, dismissWebpackOverlay } from './fixtures';

test.use({ ...devices['iPhone 12'] });

test.describe('Universal scanner mobile (/scan)', () => {
  let backendAvailable = false;

  test.beforeAll(async () => {
    backendAvailable = await checkBackendAvailable();
    if (!backendAvailable) {
      console.warn(
        'Backend not available, skipping /scan mobile E2E. Start backend on http://localhost:8000.',
      );
    }
  });

  test('phone user can submit a scan and see the result in history', async ({ page }) => {
    test.skip(!backendAvailable, 'Backend not available');

    await page.goto('/scan');
    await dismissWebpackOverlay(page);

    await expect(page.getByTestId('universal-scanner-page')).toBeVisible({ timeout: 10000 });

    const input = page.getByTestId('universal-scanner-input');
    await expect(input).toBeVisible();

    // AC-14 proficiency contract: input is auto-focused so a Bluetooth
    // scanner can type into it without a prior tap. If this regresses,
    // the BT-scanner workflow breaks on phones.
    await expect(input).toBeFocused();

    // Submit stays disabled while the input is empty — the duplicate-tap
    // / accidental-submit guard for the public path.
    const submit = page.getByRole('button', { name: 'Submit' });
    await expect(submit).toBeDisabled();

    // An unknown payload — not a UUID, UPC, or asset code — resolves to
    // action=unknown on the backend, which surfaces an error card in
    // history rather than a silent failure (AC-15).
    const unknownPayload = `e2e-unknown-${Date.now()}`;
    await input.fill(unknownPayload);
    await expect(submit).toBeEnabled();
    await submit.click();

    // History card appears with the failed scan. The recent-scans
    // surface is the AC-15 final-state for this page — the user must
    // be able to tell the scan was rejected without a navigation.
    await expect(page.getByTestId('universal-scanner-history')).toBeVisible({
      timeout: 10000,
    });
    await expect(page.getByText(/Unknown|not match|could not/i).first()).toBeVisible();

    // After submit the input is cleared + refocused so a clerk scanning
    // a stack of codes doesn't have to re-tap between scans.
    await expect(input).toHaveValue('');
    await expect(input).toBeFocused();
  });

  test('camera tile is reachable on a phone without overflow', async ({ page }) => {
    test.skip(!backendAvailable, 'Backend not available');

    await page.goto('/scan');
    await dismissWebpackOverlay(page);

    const cameraBtn = page.getByRole('button', { name: /Open camera/i });
    await expect(cameraBtn).toBeVisible();

    // On a phone viewport, the camera button must remain in the visible
    // viewport (not pushed off-screen by the hero / input). If this
    // assertion fails the layout is broken on iPhone-class devices.
    const box = await cameraBtn.boundingBox();
    expect(box).not.toBeNull();
    const viewport = page.viewportSize();
    expect(viewport).not.toBeNull();
    if (box && viewport) {
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(viewport.width);
    }
  });
});
