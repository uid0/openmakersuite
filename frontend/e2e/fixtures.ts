/**
 * Playwright test fixtures and helpers for E2E testing
 */
import { Page, expect } from '@playwright/test';

// Type declaration for Node.js process in Playwright tests
declare const process: {
  env: {
    PLAYWRIGHT_API_URL?: string;
    PLAYWRIGHT_BASE_URL?: string;
  };
};

/**
 * API base URL for backend requests
 */
export const API_BASE_URL = process.env.PLAYWRIGHT_API_URL || 'http://localhost:8000/api';

/**
 * Create a test user via API
 */
export async function createTestUser(
  username: string,
  password: string,
  email?: string,
  isStaff = false
): Promise<{ username: string; password: string; token?: string }> {
  const response = await fetch(`${API_BASE_URL}/auth/register/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      username,
      password,
      email: email || `${username}@test.com`,
    }),
  });

  let token: string | undefined;

  if (!response.ok) {
    // User might already exist, try to login
    const loginResponse = await fetch(`${API_BASE_URL}/auth/login/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });

    if (loginResponse.ok) {
      const data = await loginResponse.json();
      token = data.access;
      if (!token) {
        throw new Error('Login succeeded but no access token returned');
      }
    } else {
      const errorText = await loginResponse.text();
      throw new Error(`Failed to create or login user: ${response.statusText}. Login error: ${errorText}`);
    }
  } else {
    const data = await response.json();
    token = data.access;
    if (!token) {
      throw new Error('Registration succeeded but no access token returned');
    }
  }

  // Note: isStaff parameter is not currently supported by the registration endpoint
  // For tests that need staff users, they should be created via Django admin or
  // a separate endpoint that supports staff creation

  return { username, password, token };
}

/**
 * Login user via API and return token
 */
export async function loginUser(username: string, password: string): Promise<string> {
  const response = await fetch(`${API_BASE_URL}/auth/login/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });

  if (!response.ok) {
    throw new Error(`Login failed: ${response.statusText}`);
  }

  const data = await response.json();
  return data.access;
}

/**
 * Set authentication token in localStorage via page context
 */
export async function setAuthToken(page: Page, token: string): Promise<void> {
  await page.addInitScript((token) => {
    localStorage.setItem('token', token);
  }, token);
}

/**
 * Create a test asset via API
 */
export async function createTestAsset(
  assetData: {
    name: string;
    description?: string;
    serial_number?: string;
    location_id?: number;
    category_id?: number;
    status?: string;
    circuit?: string;
    needs_compressed_air?: boolean;
    needs_ventilation?: boolean;
    is_chargeable?: boolean;
  },
  token: string
): Promise<any> {
  if (!token) {
    throw new Error('Token is required to create assets');
  }

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${token}`,
  };

  const response = await fetch(`${API_BASE_URL}/inventory/assets/`, {
    method: 'POST',
    headers,
    body: JSON.stringify(assetData),
  });

  if (!response.ok) {
    const error = await response.text();
    throw new Error(`Failed to create asset: ${error}. Status: ${response.status}`);
  }

  return response.json();
}

/**
 * Generate QR code for an asset
 */
export async function generateAssetQR(assetId: string, token: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/inventory/assets/${assetId}/generate_qr/`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
  });

  if (!response.ok) {
    throw new Error(`Failed to generate QR code: ${response.statusText}`);
  }
}

/**
 * Dismiss webpack-dev-server overlay if present
 * This overlay can intercept clicks during development
 */
export async function dismissWebpackOverlay(page: Page): Promise<void> {
  try {
    // Check if overlay exists and hide it
    const overlayExists = await page.locator('#webpack-dev-server-client-overlay').count();
    if (overlayExists > 0) {
      await page.evaluate(() => {
        // Try to hide the webpack overlay iframe
        const overlay = document.getElementById('webpack-dev-server-client-overlay');
        if (overlay) {
          (overlay as HTMLElement).style.display = 'none';
          (overlay as HTMLElement).style.visibility = 'hidden';
          (overlay as HTMLElement).style.pointerEvents = 'none';
          (overlay as HTMLElement).style.zIndex = '-1';
        }
        // Also try to remove any error overlay
        const errorOverlay = document.querySelector('[data-overlay]');
        if (errorOverlay) {
          (errorOverlay as HTMLElement).remove();
        }
      });
      // Wait for overlay to be hidden
      await page.waitForTimeout(200);
    }
  } catch {
    // Overlay not present or already dismissed, continue
  }
}

/**
 * Wait for asset scan page to load and verify basic elements
 */
export async function waitForAssetScanPage(page: Page): Promise<void> {
  await page.waitForSelector('h1', { timeout: 10000 });
  await expect(page.locator('h1')).toBeVisible();
  // Dismiss webpack overlay after page loads
  await dismissWebpackOverlay(page);
}

/**
 * Verify asset information is displayed
 */
export async function verifyAssetInfo(page: Page, assetName: string): Promise<void> {
  // Use getByRole for heading to avoid strict mode violations
  await expect(page.getByRole('heading', { name: assetName })).toBeVisible();
}

/**
 * Check if user is logged in (by checking for auth token in localStorage)
 */
export async function isLoggedIn(page: Page): Promise<boolean> {
  return await page.evaluate(() => {
    return !!localStorage.getItem('token');
  });
}
