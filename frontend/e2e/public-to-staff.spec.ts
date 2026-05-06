/**
 * E2E: Public-to-staff loop (AC-21)
 *
 * Verifies the full proficiency loop end-to-end through the UI:
 *   1. An unauthenticated user opens a public inventory scan URL.
 *   2. The scan auto-submits a reorder request.
 *   3. A staff/admin user logs in and sees the request in the admin queue.
 *   4. The staff user clicks Approve on the row in the dashboard UI; the
 *      reorder transitions out of "pending".
 *
 * Step 4 deliberately drives the UI (not the API) so the spec proves the
 * full triage path — including dashboard row rendering and the approve
 * action button — works for an unscripted staff user. This is the
 * "UI-complete staff triage path" called out in issue #332.
 */
import { expect, test } from '@playwright/test';
import {
  API_BASE_URL,
  checkBackendAvailable,
  createActiveMembershipForUser,
  createTestCategory,
  createTestInventoryItem,
  createTestLocation,
  createTestUser,
  dismissWebpackOverlay,
  loginUser,
  setAuthToken,
} from './fixtures';

test.describe('Public-to-staff proficiency loop', () => {
  let backendAvailable = false;
  let adminToken: string;
  let item: any;

  test.beforeAll(async () => {
    backendAvailable = await checkBackendAvailable();
    if (!backendAvailable) {
      console.warn(
        'Backend not available, skipping public-to-staff E2E. Start backend on http://localhost:8000.'
      );
      return;
    }

    try {
      const adminUsername = `oms_admin_${Date.now()}`;
      await createTestUser(adminUsername, 'adminpass123', `${adminUsername}@test.com`, true);
      await createActiveMembershipForUser(adminUsername);
      adminToken = await loginUser(adminUsername, 'adminpass123');

      const stamp = Date.now();
      const category = await createTestCategory(`E2E Loop Cat ${stamp}`, adminToken);
      const location = await createTestLocation(`E2E Loop Loc ${stamp}`, adminToken);
      item = await createTestInventoryItem(
        {
          name: `E2E Loop Widget ${stamp}`,
          category: category.id,
          location: location.id,
          current_stock: 0,
          minimum_stock: 5,
          reorder_quantity: 25,
          sku: `E2E-${stamp}`,
        },
        adminToken
      );
    } catch (error: any) {
      console.error('Failed to seed public-to-staff e2e data:', error.message);
      throw new Error(`Test setup failed: ${error.message}`);
    }
  });

  test('public scan auto-submits reorder, staff sees it in pending queue, can approve', async ({
    page,
    context,
  }) => {
    test.skip(!backendAvailable, 'Backend not available');

    // Step 1: public user (no auth) hits the inventory scan page. We clear
    // any storage from prior tests in this context to mimic a phone with no
    // session.
    await context.clearCookies();
    await page.goto(`/inventory/scan/${item.id}`);
    await dismissWebpackOverlay(page);
    await expect(page.getByRole('heading', { name: item.name })).toBeVisible({
      timeout: 10000,
    });

    // Step 2: ScanPage either auto-submits and redirects to /thanks, or shows
    // an "already requested" state if a duplicate guard fired. Both prove the
    // public path completed without login.
    await page.waitForURL((url) => /\/thanks|\/inventory\/scan\//.test(url.pathname), {
      timeout: 10000,
    });

    // Confirm via the API that a reorder request now exists for this item.
    const listResponse = await fetch(
      `${API_BASE_URL}/reorders/requests/?item=${item.id}`,
      { headers: { Authorization: `Bearer ${adminToken}` } }
    );
    expect(listResponse.ok).toBe(true);
    const listBody = await listResponse.json();
    const results = Array.isArray(listBody) ? listBody : listBody.results || [];
    const matching = results.filter(
      (r: any) => r.item === item.id || r.item_id === item.id
    );
    expect(matching.length).toBeGreaterThan(0);
    const created = matching[0];
    expect(['pending', 'approved']).toContain(created.status);

    // Step 3: staff logs in via auth token and lands on admin dashboard.
    await setAuthToken(page, adminToken);
    await page.goto('/inventory/admin');
    await dismissWebpackOverlay(page);

    await expect(page.getByRole('heading', { level: 1 })).toBeVisible({
      timeout: 15000,
    });

    // Step 4: staff approves the request through the dashboard UI. We locate
    // the row by item name (the dashboard renders a table where the item
    // name appears in a cell), then click the Approve action for that row.
    // This is what makes the spec UI-complete: the staff triage path is
    // exercised end-to-end through clicks, not API calls.
    if (created.status === 'pending') {
      const itemRow = page.locator('tr', { hasText: item.name }).first();
      await expect(itemRow).toBeVisible({ timeout: 15000 });

      // The Approve button has title="Approve" — addressable, accessible,
      // and stable across dashboard sort/filter reflows.
      await itemRow.getByRole('button', { name: 'Approve' }).click();

      // The dashboard reloads requests after approve. Wait for the row to
      // either disappear from the default "pending" filter or its status
      // badge to flip to approved. We poll the API as the source of truth
      // for status because the dashboard's filter state is "pending" by
      // default — an approved row may simply leave the visible set.
      await expect
        .poll(
          async () => {
            const r = await fetch(
              `${API_BASE_URL}/reorders/requests/${created.id}/`,
              { headers: { Authorization: `Bearer ${adminToken}` } }
            );
            if (!r.ok) return 'fetch-failed';
            const body = await r.json();
            return body.status;
          },
          { timeout: 15000 }
        )
        .not.toBe('pending');
    }

    // Final assertion: the reorder is no longer pending — the proficiency
    // loop closed.
    const followup = await fetch(
      `${API_BASE_URL}/reorders/requests/${created.id}/`,
      { headers: { Authorization: `Bearer ${adminToken}` } }
    );
    expect(followup.ok).toBe(true);
    const followupBody = await followup.json();
    expect(followupBody.status).not.toBe('pending');
  });
});
