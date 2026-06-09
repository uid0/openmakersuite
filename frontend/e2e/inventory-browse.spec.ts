/**
 * E2E: Inventory browse + search (AC-13, AC-37)
 *
 * Covers the staff inventory browse journey from the journey inventory:
 *   - Land on /inventory/items as an authenticated staff user.
 *   - The seeded items render in the list.
 *   - The search input filters the list client-side (the dashboard shows
 *     "N of M items" — N must shrink when a unique substring is typed).
 *   - Clicking a row navigates to the item's detail page.
 *
 * This is the "lowest-cost layer" coverage called out in
 * docs/PROFICIENCY_METRICS.md AC-37 → "Inventory browse + search".
 */
import { expect, test } from '@playwright/test';
import {
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

test.describe('Inventory browse + search', () => {
  let backendAvailable = false;
  let adminToken: string;
  let widget: any;
  let gizmo: any;

  test.beforeAll(async () => {
    backendAvailable = await checkBackendAvailable();
    if (!backendAvailable) {
      console.warn(
        'Backend not available, skipping inventory-browse E2E. Start backend on http://localhost:8000.'
      );
      return;
    }

    const adminUsername = `oms_browse_admin_${Date.now()}`;
    await createTestUser(adminUsername, 'adminpass123', `${adminUsername}@test.com`, true);
    // is_staff is required so the seed step below can hit
    // LocationViewSet.create (IsAdminUser); without it, every spec in
    // this file fails at setup with HTTP 403.
    await createActiveMembershipForUser(adminUsername, { isStaff: true });
    adminToken = await loginUser(adminUsername, 'adminpass123');

    const stamp = Date.now();
    const category = await createTestCategory(`E2E Browse Cat ${stamp}`, adminToken);
    const location = await createTestLocation(`E2E Browse Loc ${stamp}`, adminToken);
    widget = await createTestInventoryItem(
      {
        name: `BrowseWidget-${stamp}`,
        category: category.id,
        location: location.id,
        current_stock: 12,
        minimum_stock: 5,
        reorder_quantity: 25,
        sku: `BWG-${stamp}`,
      },
      adminToken
    );
    gizmo = await createTestInventoryItem(
      {
        name: `BrowseGizmo-${stamp}`,
        category: category.id,
        location: location.id,
        current_stock: 7,
        minimum_stock: 2,
        reorder_quantity: 10,
        sku: `BGZ-${stamp}`,
      },
      adminToken
    );
  });

  test('staff can browse, search, and open an inventory item', async ({ page }) => {
    test.skip(!backendAvailable, 'Backend not available');

    await setAuthToken(page, adminToken);
    await page.goto('/inventory/items');
    await dismissWebpackOverlay(page);

    // The page now uses WorkspacePage's split eyebrow/title shape
    // ("Inventory" eyebrow + "Items" title) instead of a single
    // "Inventory Items" string. Use the page testid to anchor that
    // we landed on the right page without depending on copy.
    await expect(page.getByTestId('inventory-list-page')).toBeVisible({
      timeout: 15000,
    });

    // Both seeded items should be visible in the table once data loads.
    await expect(page.getByText(widget.name, { exact: true })).toBeVisible({
      timeout: 15000,
    });
    await expect(page.getByText(gizmo.name, { exact: true })).toBeVisible();

    // Type a substring that uniquely matches one of the seeded items.
    // The dashboard renders "N of M items" — N must drop when filtered.
    const search = page.getByPlaceholder('Search by name, SKU, or description...');
    await search.fill(widget.name);

    // After filtering, only the matching item should be visible. The
    // non-matching item must disappear from the rendered table body.
    await expect(page.getByText(widget.name, { exact: true })).toBeVisible();
    await expect(page.getByText(gizmo.name, { exact: true })).toHaveCount(0);

    // Clearing the search should bring the second item back. This proves
    // the filter is reactive, not a one-shot reload.
    await search.fill('');
    await expect(page.getByText(gizmo.name, { exact: true })).toBeVisible();

    // Clicking the row navigates to item detail. The list page applies a
    // pointer cursor and onClick to each row.
    await page.getByText(widget.name, { exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/inventory/items/${widget.id}(?:/|$)`));
  });
});
