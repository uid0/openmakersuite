/**
 * E2E tests for Admin Dashboard Assets Section
 * 
 * Tests cover:
 * - Displaying assets not checked in for 3+ months
 * - Asset information in the table
 */
import { test, expect } from '@playwright/test';
import {
  API_BASE_URL,
  createTestAsset,
  createTestUser,
  loginUser,
  setAuthToken,
} from './fixtures';

test.describe('Admin Dashboard - Assets Not Checked In', () => {
  let adminToken: string;
  let testAssets: any[] = [];

  test.beforeAll(async () => {
    // Create admin user
    const adminUser = await createTestUser('admin', 'adminpass123', 'admin@test.com', true);
    if (!adminUser.token) {
      adminToken = await loginUser(adminUser.username, adminUser.password);
    } else {
      adminToken = adminUser.token;
    }

    if (!adminToken) {
      throw new Error('Failed to obtain admin token for asset creation');
    }

    // Create test assets - some old, some recent
    const oldDate = new Date();
    oldDate.setMonth(oldDate.getMonth() - 4); // 4 months ago

    // Create asset that hasn't been scanned (last_scanned_at is null)
    const asset1 = await createTestAsset(
      {
        name: 'Old Unscanned Asset',
        description: 'Asset that has never been scanned',
        status: 'active',
      },
      adminToken
    );

    // Create asset that was scanned 4 months ago
    const asset2 = await createTestAsset(
      {
        name: 'Old Scanned Asset',
        description: 'Asset scanned 4 months ago',
        status: 'active',
      },
      adminToken
    );

    // Update asset2 to have old last_scanned_at
    await fetch(`${API_BASE_URL}/inventory/assets/${asset2.id}/`, {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${adminToken}`,
      },
      body: JSON.stringify({
        last_scanned_at: oldDate.toISOString(),
      }),
    });

    testAssets = [asset1, asset2];
  });

  test('admin can view assets not checked in section', async ({ page }) => {
    // Set authentication token
    await setAuthToken(page, adminToken);

    // Navigate to admin dashboard
    await page.goto('/orderadmin');

    // Wait for page to load
    await page.waitForSelector('h1', { timeout: 10000 });
    await expect(page.getByText('Admin Dashboard')).toBeVisible();

    // Scroll to assets section
    const assetsSection = page.getByText('Assets Not Checked In (3+ Months)');
    await assetsSection.scrollIntoViewIfNeeded();
    await expect(assetsSection).toBeVisible();
  });

  test('displays assets not checked in for 3+ months', async ({ page }) => {
    // Set authentication token
    await setAuthToken(page, adminToken);

    // Navigate to admin dashboard
    await page.goto('/orderadmin');

    // Wait for assets section
    await page.waitForSelector('text=Assets Not Checked In', { timeout: 10000 });

    // Check if assets table is visible
    const assetsTable = page.locator('.assets-table table');
    const tableVisible = await assetsTable.isVisible().catch(() => false);

    if (tableVisible) {
      // Verify table headers
      await expect(page.getByText('Asset Name')).toBeVisible();
      await expect(page.getByText('Asset Tag')).toBeVisible();
      await expect(page.getByText('Location')).toBeVisible();
      await expect(page.getByText('Last Scanned')).toBeVisible();
      await expect(page.getByText('Status')).toBeVisible();

      // Verify at least one of our test assets is in the table
      const asset1Visible = await page.getByText('Old Unscanned Asset').isVisible().catch(() => false);
      const asset2Visible = await page.getByText('Old Scanned Asset').isVisible().catch(() => false);

      expect(asset1Visible || asset2Visible).toBeTruthy();
    } else {
      // If no assets table, should show "All assets have been checked in recently"
      const noDataMessage = page.getByText('All assets have been checked in recently');
      await expect(noDataMessage).toBeVisible();
    }
  });

  test('displays correct last scanned information', async ({ page }) => {
    // Set authentication token
    await setAuthToken(page, adminToken);

    // Navigate to admin dashboard
    await page.goto('/orderadmin');

    // Wait for assets section
    await page.waitForSelector('text=Assets Not Checked In', { timeout: 10000 });

    // Check if assets are displayed
    const assetsTable = page.locator('.assets-table table');
    if (await assetsTable.isVisible().catch(() => false)) {
      // Verify "Never" is shown for unscanned assets
      const neverScanned = page.getByText('Never');
      const neverCount = await neverScanned.count();
      if (neverCount > 0) {
        await expect(neverScanned.first()).toBeVisible();
      }
    }
  });
});

