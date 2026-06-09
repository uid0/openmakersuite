/**
 * E2E: Public invite redemption on a phone-sized viewport (R-10 / AC-15).
 *
 * `/invite/:code` is the anonymous landing page incoming board members
 * follow when staff sends them an onboarding link. The full proficiency
 * contract on a phone is:
 *
 *   - The preview endpoint loads the invite metadata + renders the
 *     redeem form within the visible viewport on iPhone-class devices.
 *   - Client-side validation surfaces actionable feedback (username
 *     too short, password too short, mismatched confirm) without the
 *     keyboard hiding the affected field.
 *   - A correct submission lands the user on the "Account created"
 *     final-state card (AC-15 — clear final-state surface for public
 *     actions).
 *
 * The redeem endpoint is single-use, so we mint a fresh invite via the
 * DEBUG-only test helper for each run.
 */
import { devices, expect, test } from '@playwright/test';
import {
  checkBackendAvailable,
  createTestInviteCode,
  dismissWebpackOverlay,
} from './fixtures';

test.use({ ...devices['Pixel 5'] }); // gh-456: chromium-emulated phone (webkit not installed in the blocking-gate CI job)

test.describe('Public invite redemption mobile (/invite/:code)', () => {
  let backendAvailable = false;

  test.beforeAll(async () => {
    backendAvailable = await checkBackendAvailable();
    if (!backendAvailable) {
      console.warn(
        'Backend not available, skipping invite-redeem mobile E2E. Start backend on http://localhost:8000.',
      );
    }
  });

  test('phone invitee sees error landing for an invalid code', async ({ page }) => {
    test.skip(!backendAvailable, 'Backend not available');

    await page.goto('/invite/not-a-real-code');
    await dismissWebpackOverlay(page);

    // Invalid / expired codes route to the "Invite link not valid" card
    // so the invitee gets a clear next step rather than a blank screen.
    await expect(page.getByRole('heading', { name: /Invite link not valid/i })).toBeVisible({
      timeout: 10000,
    });
    await expect(page.getByRole('button', { name: /Back to homepage/i })).toBeVisible();
  });

  test('phone invitee can complete redemption end-to-end', async ({ page }) => {
    test.skip(!backendAvailable, 'Backend not available');

    const invite = await createTestInviteCode(`E2E mobile redeem ${Date.now()}`);
    await page.goto(invite.redeem_url);
    await dismissWebpackOverlay(page);

    await expect(page.getByTestId('invite-redeem-page')).toBeVisible({ timeout: 10000 });
    // The intended_label is what the invitee uses to confirm they got
    // the right link before typing anything.
    await expect(page.getByRole('heading', { name: invite.intended_label })).toBeVisible();

    const stamp = Date.now();
    const username = `e2e_invitee_${stamp}`;
    const email = `${username}@example.com`;

    await page.getByLabel('First name').fill('E2E');
    await page.getByLabel('Last name').fill('Invitee');
    await page.getByLabel('Username').fill(username);
    await page.getByLabel('Email').fill(email);

    const submit = page.getByRole('button', { name: 'Create account' });

    // A 5-char password should fail the client-side 12-char rule. The
    // failure must surface as field-level text — not a blank submit or
    // an unhandled toast — so the invitee can self-correct.
    // Mantine renders required-field asterisks inside the label's
    // accessible name ("Password *"), so {exact: true} against
    // "Password" misses. Anchored regex matches the "Password"
    // prefix without overlapping "Confirm password".
    await page.getByLabel(/^Password\b/).fill('short');
    await page.getByLabel('Confirm password').fill('short');
    await submit.click();
    await expect(
      page.getByText(/Password must be at least 12 characters/i),
    ).toBeVisible();

    // Now use a valid password but mismatched confirm — second
    // client-side guard.
    await page.getByLabel(/^Password\b/).fill('mobile-redeem-pw-123');
    await page.getByLabel('Confirm password').fill('mobile-redeem-pw-XXX');
    await submit.click();
    await expect(page.getByText(/Passwords do not match/i)).toBeVisible();

    // Matching valid password — should succeed and land on the final
    // "Account created" surface (AC-15).
    await page.getByLabel('Confirm password').fill('mobile-redeem-pw-123');
    await submit.click();

    await expect(page.getByRole('heading', { name: /Account created/i })).toBeVisible({
      timeout: 15000,
    });
    // The success card must show the username back so the invitee
    // knows what to sign in with.
    await expect(page.getByText(username)).toBeVisible();
    await expect(page.getByRole('link', { name: 'Sign in' })).toBeVisible();
  });
});
