/**
 * E2E tests for Asset QR Code Scanning
 * 
 * Tests cover:
 * - Unauthenticated user scanning assets
 * - Authenticated user scanning assets
 * - Asset information display
 * - QR code display
 * - Last scanned timestamp update
 */
import { test, expect } from '@playwright/test';
import {
  API_BASE_URL,
  createTestAsset,
  createTestUser,
  dismissWebpackOverlay,
  generateAssetQR,
  loginUser,
  setAuthToken,
  verifyAssetInfo,
  waitForAssetScanPage,
} from './fixtures';

test.describe('Asset QR Code Scanning', () => {
  let testAsset: any;
  let testUser: { username: string; password: string; token?: string };
  let adminToken: string;

  test.beforeAll(async () => {
    // Create test user
    testUser = await createTestUser('testuser', 'testpass123', 'testuser@test.com');
    if (!testUser.token) {
      testUser.token = await loginUser(testUser.username, testUser.password);
    }

    // Create admin user for asset creation
    const adminUser = await createTestUser('admin', 'adminpass123', 'admin@test.com', true);
    if (!adminUser.token) {
      adminToken = await loginUser(adminUser.username, adminUser.password);
    } else {
      adminToken = adminUser.token;
    }

    if (!adminToken) {
      throw new Error('Failed to obtain admin token for asset creation');
    }

    // Create a test asset (requires authentication)
    testAsset = await createTestAsset(
      {
        name: 'Test 3D Printer',
        description: 'A test 3D printer for E2E testing',
        serial_number: 'TEST-12345',
        status: 'active',
        circuit: 'Circuit A',
        needs_compressed_air: false,
        needs_ventilation: true,
        is_chargeable: true,
      },
      adminToken
    );

    // Generate QR code for the asset
    await generateAssetQR(testAsset.id, adminToken);
  });

  test('unauthenticated user can scan asset and see basic info', async ({ page }) => {
    // Navigate to asset scan page
    await page.goto(`/scan/asset/${testAsset.id}`);

    // Wait for page to load
    await waitForAssetScanPage(page);

    // Verify asset name is displayed
    await verifyAssetInfo(page, testAsset.name);

    // Verify basic information is shown (use heading role to avoid strict mode violations)
    await expect(page.getByRole('heading', { name: testAsset.name })).toBeVisible();
    if (testAsset.description) {
      await expect(page.getByText(testAsset.description, { exact: false })).toBeVisible();
    }

    // Verify QR code is displayed (if available)
    const qrCode = page.locator('img[alt="QR Code"]');
    const qrCodeCount = await qrCode.count();
    if (qrCodeCount > 0) {
      await expect(qrCode.first()).toBeVisible();
    }

    // Verify last scanned timestamp is shown (use more specific selector)
    await expect(page.locator('p.qr-info')).toBeVisible();

    // Verify unauthenticated users don't see admin actions
    await expect(page.getByText('Enable Asset')).not.toBeVisible();
    await expect(page.getByText('Disable Asset')).not.toBeVisible();
    await expect(page.getByText('Report a Problem')).not.toBeVisible();
  });

  test('authenticated user can scan asset and see full details', async ({ page }) => {
    // Set authentication token
    await setAuthToken(page, testUser.token!);

    // Navigate to asset scan page
    await page.goto(`/scan/asset/${testAsset.id}`);

    // Wait for page to load
    await waitForAssetScanPage(page);

    // Verify asset name is displayed
    await verifyAssetInfo(page, testAsset.name);

    // Verify full information is shown (use heading role to avoid strict mode violations)
    await expect(page.getByRole('heading', { name: testAsset.name })).toBeVisible();
    if (testAsset.description) {
      await expect(page.getByText(testAsset.description, { exact: false })).toBeVisible();
    }

    // Verify authenticated user sees admin actions
    await expect(page.getByText('Actions')).toBeVisible();
    await expect(page.getByText('Report a Problem')).toBeVisible();

    // Verify asset status is displayed (use more specific selector)
    await expect(page.locator('.info-item .label:has-text("Status")')).toBeVisible();

    // Verify operational requirements if set (use more specific selectors)
    if (testAsset.needs_ventilation) {
      await expect(page.locator('.info-item .value:has-text("Ventilation")')).toBeVisible();
    }
    if (testAsset.is_chargeable) {
      await expect(page.locator('.info-item .value:has-text("Chargeable")')).toBeVisible();
    }
  });

  test('scanning asset updates last_scanned_at timestamp', async ({ page }) => {
    // Get initial last_scanned_at
    const initialResponse = await fetch(`${API_BASE_URL}/inventory/assets/${testAsset.id}/`);
    const initialAsset = await initialResponse.json();
    const initialScannedAt = initialAsset.last_scanned_at;

    // Navigate to asset scan page (this triggers the scan endpoint)
    await page.goto(`/scan/asset/${testAsset.id}`);

    // Wait a bit for the scan to complete
    await page.waitForTimeout(1000);

    // Verify last_scanned_at was updated
    const updatedResponse = await fetch(`${API_BASE_URL}/inventory/assets/${testAsset.id}/`);
    const updatedAsset = await updatedResponse.json();

    if (initialScannedAt) {
      expect(new Date(updatedAsset.last_scanned_at).getTime()).toBeGreaterThan(
        new Date(initialScannedAt).getTime()
      );
    } else {
      expect(updatedAsset.last_scanned_at).not.toBeNull();
    }

    // Verify the timestamp is displayed on the page
    await expect(page.getByText(/Last scanned/i)).toBeVisible();
  });

  test('authenticated user can report a problem', async ({ page }) => {
    // Set authentication token
    await setAuthToken(page, testUser.token!);

    // Navigate to asset scan page
    await page.goto(`/scan/asset/${testAsset.id}`);

    // Wait for page to load (this also dismisses webpack overlay)
    await waitForAssetScanPage(page);

    // Find and fill the problem report form
    const problemTextarea = page.locator('textarea[placeholder*="problem"]');
    await expect(problemTextarea).toBeVisible();

    const problemDescription = 'E2E Test: This asset is not working properly';
    await problemTextarea.fill(problemDescription);

    // Submit the form - use force click to bypass overlay if needed
    const submitButton = page.getByRole('button', { name: /Report Problem/i });
    await expect(submitButton).toBeVisible();
    await submitButton.click({ force: true });

    // Wait for success message
    await expect(page.getByText(/Problem reported successfully/i)).toBeVisible({
      timeout: 5000,
    });
  });

  test('authenticated user can enable/disable asset', async ({ page }) => {
    // Set authentication token
    await setAuthToken(page, testUser.token!);

    // Navigate to asset scan page
    await page.goto(`/scan/asset/${testAsset.id}`);

    // Wait for page to load (this also dismisses webpack overlay)
    await waitForAssetScanPage(page);

    // Check if asset is currently active
    const disableButton = page.getByRole('button', { name: /Disable Asset/i });
    const enableButton = page.getByRole('button', { name: /Enable Asset/i });

    if (await disableButton.isVisible()) {
      // Asset is active, test disabling
      await disableButton.click({ force: true });

      // Wait for success message
      await expect(page.getByText(/Asset disabled successfully/i)).toBeVisible({
        timeout: 5000,
      });

      // Verify enable button is now visible
      await expect(enableButton).toBeVisible({ timeout: 3000 });
    } else if (await enableButton.isVisible()) {
      // Asset is inactive, test enabling
      await enableButton.click({ force: true });

      // Wait for success message
      await expect(page.getByText(/Asset enabled successfully/i)).toBeVisible({
        timeout: 5000,
      });

      // Verify disable button is now visible
      await expect(disableButton).toBeVisible({ timeout: 3000 });
    }
  });

  test('displays asset operational requirements', async ({ page }) => {
    // Navigate to asset scan page
    await page.goto(`/scan/asset/${testAsset.id}`);

    // Wait for page to load
    await waitForAssetScanPage(page);

    // Verify circuit information if set
    if (testAsset.circuit) {
      // Use more specific selector to avoid strict mode violations
      await expect(page.locator('.info-item .label:has-text("Circuit")')).toBeVisible();
      await expect(page.locator('.info-item .value').filter({ hasText: testAsset.circuit })).toBeVisible();
    }

    // Verify requirements section (use more specific selector)
    if (testAsset.needs_ventilation || testAsset.needs_compressed_air || testAsset.is_chargeable) {
      await expect(page.locator('.info-item .label:has-text("Requirements")')).toBeVisible();
    }
  });

  test('displays wiki page link if available', async ({ page }) => {
    // Update asset with wiki page URL
    const wikiUrl = 'https://wiki.example.com/test-asset';
    await fetch(`${API_BASE_URL}/inventory/assets/${testAsset.id}/`, {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${adminToken}`,
      },
      body: JSON.stringify({ wiki_page_url: wikiUrl }),
    });

    // Navigate to asset scan page
    await page.goto(`/scan/asset/${testAsset.id}`);

    // Wait for page to load
    await waitForAssetScanPage(page);

    // Verify wiki link is displayed
    const wikiLink = page.getByRole('link', { name: /View Wiki Page/i });
    await expect(wikiLink).toBeVisible();
    await expect(wikiLink).toHaveAttribute('href', wikiUrl);
  });
});

