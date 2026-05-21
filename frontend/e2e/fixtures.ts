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
 * Check if the backend API is available
 */
export async function checkBackendAvailable(): Promise<boolean> {
  try {
    // Try the dashboard health endpoint first
    const healthUrl = `${API_BASE_URL.replace('/api', '')}/api/dashboard/health/`;
    const response = await fetch(healthUrl, {
      method: 'GET',
      signal: AbortSignal.timeout(2000), // 2 second timeout
    });
    if (response.ok) {
      return true;
    }
  } catch {
    // Health endpoint might not be available, try auth endpoint as fallback
  }
  
  // Try auth endpoint as fallback (should return 400 for bad request, not connection error)
  try {
    const response = await fetch(`${API_BASE_URL}/auth/login/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
      signal: AbortSignal.timeout(2000),
    });
    // Any response (even 400) means backend is up - connection refused would throw
    return response.status !== 0;
  } catch (error: any) {
    // Connection refused or timeout means backend is down
    if (error.message?.includes('ECONNREFUSED') || error.name === 'AbortError') {
      return false;
    }
    // Other errors might mean backend is up but endpoint doesn't exist
    return true;
  }
}

/**
 * Create an active membership for a user via test helper endpoint
 * This endpoint is only available in DEBUG mode
 */
export async function createActiveMembershipForUser(
  username: string,
  options: { isStaff?: boolean } = {}
): Promise<void> {
  try {
    const response = await fetch(`${API_BASE_URL}/auth/test-membership/`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ username, is_staff: options.isStaff === true }),
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`Failed to create membership: ${errorText}`);
    }
  } catch (error: any) {
    console.warn(`Could not create membership for ${username}: ${error.message}`);
    throw error;
  }
}

/**
 * Create a test user via API and ensure they can login
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
      return { username, password, token };
    } else {
      const errorText = await loginResponse.text();
      // If login fails due to membership, try to update user via admin API
      // For now, we'll throw a more helpful error
      throw new Error(`Failed to create or login user: ${response.statusText}. Login error: ${errorText}. Note: Test users need active memberships or staff status.`);
    }
  } else {
    const data = await response.json();
    token = data.access;
    if (!token) {
      throw new Error('Registration succeeded but no access token returned');
    }
  }

  // Note: Newly registered users won't have memberships, so they can't login
  // The registration token works, but subsequent logins will fail
  // For E2E tests, we need to create memberships or make users staff/board members
  // This should be done in the test setup using an admin token
  
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

/**
 * Create a category via the API. Categories are required by inventory items.
 * Returns the created category id.
 */
export async function createTestCategory(
  name: string,
  token: string
): Promise<{ id: number; name: string }> {
  const response = await fetch(`${API_BASE_URL}/inventory/categories/`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ name }),
  });
  if (!response.ok) {
    throw new Error(
      `Failed to create category: ${response.statusText}. Body: ${await response.text()}`
    );
  }
  return response.json();
}

/**
 * Create a location via the API. Locations are required by inventory items.
 */
export async function createTestLocation(
  name: string,
  token: string
): Promise<{ id: number; name: string }> {
  const response = await fetch(`${API_BASE_URL}/inventory/locations/`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ name }),
  });
  if (!response.ok) {
    throw new Error(
      `Failed to create location: ${response.statusText}. Body: ${await response.text()}`
    );
  }
  return response.json();
}

/**
 * Create an inventory item via the API. Used by the public-to-staff e2e loop
 * to seed an item that an unauthenticated user can scan.
 */
export async function createTestInventoryItem(
  payload: {
    name: string;
    category: number | string;
    location: number | string;
    current_stock?: number;
    minimum_stock?: number;
    reorder_quantity?: number;
    description?: string;
    sku?: string;
  },
  token: string
): Promise<any> {
  const body = {
    current_stock: 0,
    minimum_stock: 1,
    reorder_quantity: 10,
    description: 'Public-to-staff e2e seeded item',
    ...payload,
  };
  const response = await fetch(`${API_BASE_URL}/inventory/items/`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(
      `Failed to create inventory item: ${response.statusText}. Body: ${await response.text()}`
    );
  }
  return response.json();
}

/**
 * Approve a reorder request via the staff API. Used to demonstrate staff
 * triage of a public submission in the public-to-staff e2e loop.
 */
export async function approveReorderRequest(
  requestId: number | string,
  token: string,
  notes = 'Approved via e2e test'
): Promise<any> {
  const response = await fetch(
    `${API_BASE_URL}/reorders/requests/${requestId}/approve/`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ admin_notes: notes }),
    }
  );
  if (!response.ok) {
    throw new Error(
      `Failed to approve reorder: ${response.statusText}. Body: ${await response.text()}`
    );
  }
  return response.json();
}
