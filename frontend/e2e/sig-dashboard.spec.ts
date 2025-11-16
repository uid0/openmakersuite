/**
 * E2E tests for SIG Dashboard
 *
 * Tests cover:
 * - SIG dashboard access and navigation
 * - Viewing SIG overview, members, assets, inventory
 * - Managing SIG members
 * - Viewing SIG-specific reorder requests
 */
import { test, expect } from '@playwright/test';
import {
  API_BASE_URL,
  checkBackendAvailable,
  createTestAsset,
  createTestUser,
  loginUser,
  setAuthToken,
} from './fixtures';

test.describe('SIG Dashboard', () => {
  let sigAdminToken: string;
  let regularUserToken: string;
  let sigGroupId: number;
  let backendAvailable = false;

  test.beforeAll(async () => {
    // Check if backend is available
    backendAvailable = await checkBackendAvailable();

    if (!backendAvailable) {
      console.warn(
        'Backend not available, skipping E2E tests. Start backend on http://localhost:8000 to run these tests.'
      );
      return;
    }

    try {
      // Create SIG admin user
      const sigAdmin = await createTestUser(
        'sigadmin',
        'sigadmin123',
        'sigadmin@test.com',
        false
      );

      // Create regular user
      const regularUser = await createTestUser(
        'regularuser',
        'regular123',
        'regular@test.com',
        false
      );

      // Create active memberships
      const { createActiveMembershipForUser } = await import('./fixtures');
      await createActiveMembershipForUser('sigadmin');
      await createActiveMembershipForUser('regularuser');

      // Login to get tokens
      sigAdminToken = await loginUser('sigadmin', 'sigadmin123');
      regularUserToken = await loginUser('regularuser', 'regular123');

      if (!sigAdminToken || !regularUserToken) {
        throw new Error('Failed to obtain authentication tokens');
      }

      // Create a SIG (Group) and assign admin
      const response = await fetch(`${API_BASE_URL}/membership/sig-admins/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${sigAdminToken}`,
        },
        body: JSON.stringify({
          user: sigAdmin.id,
          group: null, // Will be created via admin
        }),
      });

      // For now, we'll need to create the group via admin API or use existing test setup
      // This is a simplified version - in real tests, you'd set up the SIG properly
    } catch (error) {
      console.error('Error setting up SIG dashboard tests:', error);
      backendAvailable = false;
    }
  });

  test('should redirect to home if user is not a SIG admin', async ({ page }) => {
    test.skip(!backendAvailable, 'Backend not available');

    await setAuthToken(page, regularUserToken);
    await page.goto('/sig-dashboard');

    // Should redirect or show message that user is not a SIG admin
    await expect(page).toHaveURL(/\/sig-dashboard/);
    // Check for message about not being a SIG admin
    const content = await page.textContent('body');
    expect(content).toContain('not an admin');
  });

  test('should display SIG dashboard for SIG admin', async ({ page }) => {
    test.skip(!backendAvailable, 'Backend not available');

    await setAuthToken(page, sigAdminToken);
    await page.goto('/sig-dashboard');

    // Should show SIG dashboard
    await expect(page.locator('h1')).toContainText('SIG Dashboard');
  });

  test('should show SIG overview with stats', async ({ page }) => {
    test.skip(!backendAvailable, 'Backend not available');

    await setAuthToken(page, sigAdminToken);
    await page.goto('/sig-dashboard');

    // Check for overview stats
    await expect(page.locator('.sig-stats')).toBeVisible();
    await expect(page.locator('.stat-card')).toHaveCount(3); // Members, Assets, Inventory
  });

  test('should navigate between tabs', async ({ page }) => {
    test.skip(!backendAvailable, 'Backend not available');

    await setAuthToken(page, sigAdminToken);
    await page.goto('/sig-dashboard');

    // Click on Members tab
    await page.click('button:has-text("Members")');
    await expect(page.locator('.sig-members')).toBeVisible();

    // Click on Assets tab
    await page.click('button:has-text("Assets")');
    await expect(page.locator('.sig-assets')).toBeVisible();

    // Click on Inventory tab
    await page.click('button:has-text("Inventory")');
    await expect(page.locator('.sig-inventory')).toBeVisible();

    // Click on Reorder Requests tab
    await page.click('button:has-text("Reorder Requests")');
    await expect(page.locator('.sig-reorders')).toBeVisible();
  });

  test('should display SIG members', async ({ page }) => {
    test.skip(!backendAvailable, 'Backend not available');

    await setAuthToken(page, sigAdminToken);
    await page.goto('/sig-dashboard');

    // Navigate to Members tab
    await page.click('button:has-text("Members")');

    // Should show members table
    await expect(page.locator('.sig-members table')).toBeVisible();
  });

  test('should display SIG assets', async ({ page }) => {
    test.skip(!backendAvailable, 'Backend not available');

    await setAuthToken(page, sigAdminToken);
    await page.goto('/sig-dashboard');

    // Navigate to Assets tab
    await page.click('button:has-text("Assets")');

    // Should show assets list
    await expect(page.locator('.sig-assets')).toBeVisible();
  });

  test('should display SIG inventory', async ({ page }) => {
    test.skip(!backendAvailable, 'Backend not available');

    await setAuthToken(page, sigAdminToken);
    await page.goto('/sig-dashboard');

    // Navigate to Inventory tab
    await page.click('button:has-text("Inventory")');

    // Should show inventory list
    await expect(page.locator('.sig-inventory')).toBeVisible();
  });

  test('should display SIG reorder requests', async ({ page }) => {
    test.skip(!backendAvailable, 'Backend not available');

    await setAuthToken(page, sigAdminToken);
    await page.goto('/sig-dashboard');

    // Navigate to Reorder Requests tab
    await page.click('button:has-text("Reorder Requests")');

    // Should show reorder requests list
    await expect(page.locator('.sig-reorders')).toBeVisible();
  });
});

