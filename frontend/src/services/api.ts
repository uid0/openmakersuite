/**
 * API service for communicating with the Django backend
 */
import * as Sentry from '@sentry/react';
import axios from 'axios';
import { Asset, AssetPart, AssetProblem, Category, Checklist, ChecklistCompletion, CreateReorderRequest, Disposition, DonationItem, Fixture, FixtureRefillRequest, InventoryItem, ItemSupplier, ReorderRequest, SIG, SIGMember, SiteSettings, Supplier, SupplierDetail, TaxReceipt } from '../types';

/**
 * Resolves the API base URL based on environment.
 * - If REACT_APP_API_URL is set, uses that (for production/docker)
 * - If on localhost and not set, uses direct Django backend URL
 * - Otherwise, uses relative URL for nginx forwarding
 */
export const resolveApiBaseUrl = () => {
  const rawBase = process.env.REACT_APP_API_URL;

  // If REACT_APP_API_URL is explicitly set, use it (handles production and docker dev)
  if (rawBase && rawBase.trim().length > 0) {
    const trimmedBase = rawBase.replace(/\/+$/, '');

    if (trimmedBase.endsWith('/api') || trimmedBase.includes('/api/')) {
      return trimmedBase;
    }

    return `${trimmedBase}/api`;
  }

  // If not set, detect environment
  const isLocalhost =
    typeof window !== 'undefined' &&
    (window.location.hostname === 'localhost' ||
     window.location.hostname === '127.0.0.1' ||
     window.location.hostname === '[::1]');

  // When developing locally (no nginx), use direct Django backend
  if (isLocalhost) {
    return 'http://localhost:8000/api';
  }

  // When deployed (with nginx), use relative URL for nginx forwarding
  return '/api';
};

const API_BASE_URL = resolveApiBaseUrl();

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Add auth token to requests if available
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Track if we're currently refreshing to avoid multiple refresh attempts
let isRefreshing = false;
let failedQueue: Array<{
  resolve: (value?: any) => void;
  reject: (reason?: any) => void;
}> = [];

const processQueue = (error: any, token: string | null = null) => {
  failedQueue.forEach((prom) => {
    if (error) {
      prom.reject(error);
    } else {
      prom.resolve(token);
    }
  });
  failedQueue = [];
};

// Add error logging and token refresh to responses
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;

    // Handle 401 errors (token expired) with automatic refresh
    // Skip refresh logic for:
    // - The refresh endpoint itself (to avoid infinite loops)
    // - Public endpoints that don't require authentication
    const isRefreshEndpoint = originalRequest?.url?.includes('/auth/refresh/');
    const isPublicEndpoint = originalRequest?.url?.includes('/reorders/purchase-orders/') ||
                            originalRequest?.url?.includes('/reorders/analytics/transparency/') ||
                            originalRequest?.url?.includes('/reorders/analytics/logistics_dashboard/');
    
    // Only attempt refresh if we have a valid error response with 401 status
    // and we haven't already retried this request
    if (
      error.response?.status === 401 && 
      !originalRequest._retry && 
      !isRefreshEndpoint &&
      !isPublicEndpoint &&
      originalRequest
    ) {
      if (isRefreshing) {
        // If already refreshing, queue this request
        return new Promise((resolve, reject) => {
          failedQueue.push({ resolve, reject });
        })
          .then((token) => {
            if (token && originalRequest.headers) {
              originalRequest.headers.Authorization = `Bearer ${token}`;
            }
            return api(originalRequest);
          })
          .catch((err) => {
            return Promise.reject(err);
          });
      }

      originalRequest._retry = true;
      isRefreshing = true;

      const refreshToken = localStorage.getItem('refresh_token');
      if (!refreshToken) {
        // No refresh token, clear auth and reject
        localStorage.removeItem('token');
        localStorage.removeItem('refresh_token');
        localStorage.removeItem('username');
        localStorage.removeItem('is_staff');
        isRefreshing = false;
        processQueue(error, null);
        return Promise.reject(error);
      }

      try {
        // Use a direct axios call to avoid triggering the interceptor again
        const refreshResponse = await axios.post(`${API_BASE_URL}/auth/refresh/`, { refresh: refreshToken }, {
          headers: {
            'Content-Type': 'application/json',
          },
        });
        const { access } = refreshResponse.data;
        localStorage.setItem('token', access);
        
        // Update the original request headers
        if (!originalRequest.headers) {
          originalRequest.headers = {};
        }
        originalRequest.headers.Authorization = `Bearer ${access}`;
        
        isRefreshing = false;
        processQueue(null, access);
        
        // Retry the original request with the new token
        return api(originalRequest);
      } catch (refreshError: any) {
        // Refresh failed, clear auth and reject
        localStorage.removeItem('token');
        localStorage.removeItem('refresh_token');
        localStorage.removeItem('username');
        localStorage.removeItem('is_staff');
        isRefreshing = false;
        processQueue(refreshError, null);
        // Return the original error if refresh failed, as it's more informative
        return Promise.reject(error);
      }
    }

    // Log API errors to Sentry
    if (error.response) {
      // The request was made and the server responded with a status code
      // that falls out of the range of 2xx
      Sentry.captureException(error, {
        contexts: {
          api: {
            url: error.config?.url,
            method: error.config?.method,
            status: error.response.status,
            data: error.response.data,
          },
        },
      });
    } else if (error.request) {
      // The request was made but no response was received
      Sentry.captureException(error, {
        contexts: {
          api: {
            url: error.config?.url,
            method: error.config?.method,
            error: 'No response received',
          },
        },
      });
    } else {
      // Something happened in setting up the request that triggered an Error
      Sentry.captureException(error);
    }
    return Promise.reject(error);
  }
);

// Inventory API
export const inventoryAPI = {
  getItem: (id: string) =>
    api.get<InventoryItem>(`/inventory/items/${id}/`),

  getItemSuppliers: (itemId: string) =>
    api.get<{ results: ItemSupplier[] }>(`/inventory/item-suppliers/?item_id=${itemId}`),
  markItemSupplierDiscontinued: (itemSupplierId: string) =>
    api.post(`/inventory/item-suppliers/${itemSupplierId}/mark_discontinued/`),

  listItems: (params?: { category?: number; location?: number; search?: string; low_stock?: boolean; owning_group?: number }) =>
    api.get<{ results: InventoryItem[] }>('/inventory/items/', { params }),

  getMySIGInventory: () =>
    api.get<InventoryItem[]>('/inventory/items/my_sig_inventory/'),

  getLowStockItems: () =>
    api.get<InventoryItem[]>('/inventory/items/low_stock/'),

  getReorderedItems: () =>
    api.get<InventoryItem[]>('/inventory/items/reordered/'),

  downloadCard: (id: string) =>
    api.get(`/inventory/items/${id}/download_card/`, {
      responseType: 'blob',
    }),

  generateQR: (id: string) =>
    api.post(`/inventory/items/${id}/generate_qr/`),
  lookupByCode: (code: string) =>
    api.get(`/inventory/lookup-code/`, { params: { code } }),

  logUsage: (id: string, quantity: number, notes?: string) =>
    api.post(`/inventory/items/${id}/log_usage/`, {
      quantity,
      notes,
    }),

  getLocation: (id: string) =>
    api.get(`/inventory/locations/${id}/`),

  getLocationChecklists: (id: string) =>
    api.get<Checklist[]>(`/inventory/locations/${id}/checklists/`),

  getItemChecklists: (id: string) =>
    api.get<Checklist[]>(`/inventory/items/${id}/checklists/`),

  listLocations: () =>
    api.get<Location[]>('/inventory/locations/'),

  listCategories: () =>
    api.get<Category[]>('/inventory/categories/'),

  listSuppliers: (params?: { supplier_type?: string; search?: string }) =>
    api.get<{ results: Supplier[] }>('/inventory/suppliers/', { params }),

  getSupplier: (id: string) =>
    api.get<SupplierDetail>(`/inventory/suppliers/${id}/`),

  createSupplier: (data: Partial<Supplier>) =>
    api.post<Supplier>('/inventory/suppliers/', data),

  updateSupplier: (id: string, data: Partial<Supplier>) =>
    api.patch<Supplier>(`/inventory/suppliers/${id}/`, data),

  deleteSupplier: (id: string) =>
    api.delete(`/inventory/suppliers/${id}/`),

  getSupplierAnalytics: (id: string) =>
    api.get(`/inventory/suppliers/${id}/analytics/`),

  createItem: (data: FormData | Partial<InventoryItem>) => {
    if (data instanceof FormData) {
      return api.post<InventoryItem>('/inventory/items/', data, {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      });
    }
    return api.post<InventoryItem>('/inventory/items/', data);
  },

  updateItem: (id: string, data: FormData | Partial<InventoryItem>) => {
    if (data instanceof FormData) {
      return api.patch<InventoryItem>(`/inventory/items/${id}/`, data, {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      });
    }
    return api.patch<InventoryItem>(`/inventory/items/${id}/`, data);
  },

  updateStock: (id: string, quantity: number) =>
    api.patch<InventoryItem>(`/inventory/items/${id}/`, { current_stock: quantity }),

  getUsageLogs: (itemId: string) =>
    api.get<{ results: UsageLog[] }>(`/inventory/usage-logs/?item_id=${itemId}`),

  createCategory: (data: { name: string; description?: string; parent?: number; color?: string }) =>
    api.post<Category>('/inventory/categories/', data),

  updateCategory: (id: string, data: Partial<Category>) =>
    api.patch<Category>(`/inventory/categories/${id}/`, data),

  deleteCategory: (id: string) =>
    api.delete(`/inventory/categories/${id}/`),

  createLocation: (data: Partial<Location>) =>
    api.post<Location>('/inventory/locations/', data),

  updateLocation: (id: string, data: Partial<Location>) =>
    api.patch<Location>(`/inventory/locations/${id}/`, data),

  deleteLocation: (id: string) =>
    api.delete(`/inventory/locations/${id}/`),

  getLocationFixtures: (locationId: string) =>
    api.get<Fixture[]>(`/inventory/locations/${locationId}/fixtures/`),

  generateLocationQR: (id: string) =>
    api.post(`/inventory/locations/${id}/generate_qr/`),
};

// Assets API
export const assetsAPI = {
  listAssets: (params?: { status?: string; category?: number; location?: number; search?: string; owning_group?: number }) =>
    api.get<{ results: Asset[] }>('/inventory/assets/', { params }),

  getMySIGAssets: () =>
    api.get<Asset[]>('/inventory/assets/my_sig_assets/'),

  getAsset: (id: string) =>
    api.get<Asset>(`/inventory/assets/${id}/`),

  createAsset: (data: Partial<Asset>) =>
    api.post<Asset>('/inventory/assets/', data),

  updateAsset: (id: string, data: Partial<Asset>) =>
    api.patch<Asset>(`/inventory/assets/${id}/`, data),

  deleteAsset: (id: string) =>
    api.delete(`/inventory/assets/${id}/`),

  generateQR: (id: string) =>
    api.post(`/inventory/assets/${id}/generate_qr/`),

  scanAsset: (id: string) =>
    api.post<Asset>(`/inventory/assets/${id}/scan/`),

  getAssetChecklists: (id: string) =>
    api.get<Checklist[]>(`/inventory/assets/${id}/checklists/`),

  getAssetProblems: (id: string) =>
    api.get<AssetProblem[]>(`/inventory/assets/${id}/get_problems/`),

  enableAsset: (id: string) =>
    api.post<Asset>(`/inventory/assets/${id}/enable/`),

  disableAsset: (id: string) =>
    api.post<Asset>(`/inventory/assets/${id}/disable/`),

  lockAsset: (id: string) =>
    api.post<Asset>(`/inventory/assets/${id}/lock/`),

  unlockAsset: (id: string) =>
    api.post<Asset>(`/inventory/assets/${id}/unlock/`),

  reportProblem: (id: string, description: string) =>
    api.post(`/inventory/assets/${id}/report_problem/`, { description }),

  downloadLabel: (id: string) =>
    api.get(`/inventory/assets/${id}/download_label/`, {
      responseType: 'blob',
    }),

  downloadLabelsBatch: (assetIds: string[]) =>
    api.post(`/inventory/assets/download_labels_batch/`, { asset_ids: assetIds }, {
      responseType: 'blob',
    }),

  getNotCheckedIn: () =>
    api.get<Asset[]>('/inventory/assets/not_checked_in/'),
};

// Asset Parts API
export const assetPartsAPI = {
  listAssetParts: (params?: { asset?: string }) =>
    api.get<{ results: AssetPart[] }>('/inventory/asset-parts/', { params }),

  markReplaced: (id: string) =>
    api.post<AssetPart>(`/inventory/asset-parts/${id}/mark_replaced/`),
};

// Reorder API
export const reorderAPI = {
  createRequest: (data: CreateReorderRequest) =>
    api.post<ReorderRequest>('/reorders/requests/', data),

  listRequests: (params?: { status?: string }) =>
    api.get<{ results: ReorderRequest[] }>('/reorders/requests/', { params }),

  getPendingRequests: () =>
    api.get<ReorderRequest[]>('/reorders/requests/pending/'),

  getSIGPendingRequests: () =>
    api.get<ReorderRequest[]>('/reorders/requests/sig_pending/'),

  getBySupplier: () =>
    api.get('/reorders/requests/by_supplier/'),

  approveRequest: (id: number, adminNotes?: string) =>
    api.post(`/reorders/requests/${id}/approve/`, { admin_notes: adminNotes }),

  markOrdered: (id: number, data: {
    order_number?: string;
    estimated_delivery?: string;
    actual_cost?: number;
  }) =>
    api.post(`/reorders/requests/${id}/mark_ordered/`, data),

  markReceived: (id: number, actualDelivery?: string) =>
    api.post(`/reorders/requests/${id}/mark_received/`, {
      actual_delivery: actualDelivery,
    }),

  cancelRequest: (id: number, adminNotes?: string) =>
    api.post(`/reorders/requests/${id}/cancel/`, { admin_notes: adminNotes }),

  generateCartLinks: () =>
    api.get('/reorders/requests/generate_cart_links/'),

  updateTracking: (id: number, data: {
    tracking_number?: string;
    carrier?: string;
    expected_delivery_date?: string;
    delivery_tracking_url?: string;
  }) =>
    api.patch(`/reorders/requests/${id}/`, data),
};

export const analyticsAPI = {
  getTransparencyLedger: <T = unknown>() =>
    api.get<T>('/reorders/analytics/transparency/'),
  getLogisticsDashboard: <T = unknown>() =>
    api.get<T>('/reorders/analytics/logistics_dashboard/'),
};

// Purchase Order API
export const purchaseOrderAPI = {
  listOrders: (params?: { status?: string }) =>
    api.get<{ results: any[] }>('/reorders/purchase-orders/', { params }),
  getOrder: (id: string) =>
    api.get<any>(`/reorders/purchase-orders/${id}/`),
  createOrder: (data: {
    supplier: number;
    expected_delivery_date?: string;
    notes?: string;
    items: Array<{
      item_supplier_id?: number;
      asset_id?: string;
      quantity: number;
      unit_cost?: number;
      expected_shipment_date?: string;
      notes?: string;
    }>;
  }) => api.post('/reorders/purchase-orders/', data),
  updateLineItem: (orderId: string, itemId: string, data: { expected_shipment_date?: string; notes?: string; line_cost?: number; unit_cost_actual?: number }) =>
    api.patch(`/reorders/purchase-orders/${orderId}/items/${itemId}/`, data),
  voidLineItem: (orderId: string, itemId: string, reason?: string) =>
    api.post(`/reorders/purchase-orders/${orderId}/items/${itemId}/void/`, { reason }),
  scanBarcode: (data: {
    purchase_order_id: number;
    scanned_upc: string;
    quantity_received: number;
    is_damaged?: boolean;
    is_expired?: boolean;
    condition_notes?: string;
  }) => api.post('/reorders/receipts/scan_barcode/', data),
  createDelivery: (data: {
    purchase_order: number;
    delivery_date?: string;
    tracking_number?: string;
    carrier?: string;
    receipt_notes?: string;
  }) => api.post('/reorders/receipts/', data),
  getDeliveries: (orderId: string) =>
    api.get(`/reorders/receipts/?purchase_order=${orderId}`),
  getDelivery: (deliveryId: string) =>
    api.get(`/reorders/receipts/${deliveryId}/`),
};

// Fixtures API
export const fixturesAPI = {
  getFixture: (id: string) =>
    api.get<Fixture>(`/inventory/fixtures/${id}/`),

  scanFixture: (id: string, notes?: string) =>
    api.post<FixtureRefillRequest>(`/inventory/fixtures/${id}/scan/`, notes ? { notes } : {}),

  resolveFixtureRequest: (fixtureId: string, notes?: string) =>
    api.post(`/inventory/fixtures/${fixtureId}/resolve_all/`, { notes }),

  resolveRequest: (requestId: string, notes?: string) =>
    api.post<FixtureRefillRequest>(`/inventory/fixture-refill-requests/${requestId}/resolve/`, { notes }),

  listRequests: (params?: { fixture?: string; status?: string; location?: string }) =>
    api.get<{ results: FixtureRefillRequest[] }>('/inventory/fixture-refill-requests/', { params }),
};

// SIG (Special Interest Group) API
export const sigAPI = {
  listMySIGs: () =>
    api.get<{ results: SIG[] }>('/membership/sigs/'),

  getSIG: (id: number) =>
    api.get<SIG>(`/membership/sigs/${id}/`),

  getSIGDetails: (id: number) =>
    api.get<SIG>(`/membership/sigs/${id}/details/`),

  getSIGMembers: (sigId: number) =>
    api.get<SIGMember[]>(`/membership/sigs/${sigId}/members/`),

  addSIGMember: (sigId: number, userId: number) =>
    api.post(`/membership/sigs/${sigId}/members/`, { user_id: userId }),

  removeSIGMember: (sigId: number, userId: number) =>
    api.delete(`/membership/sigs/${sigId}/members/${userId}/`),
};

// Auth API
export const authAPI = {
  login: (username: string, password: string) =>
    api.post('/auth/login/', { username, password }),

  register: (userData: { username: string; email: string; password: string; password2: string }) =>
    api.post('/auth/register/', userData),

  refresh: (refreshToken: string) =>
    api.post('/auth/refresh/', { refresh: refreshToken }),
};

// Customization API
export const customizationAPI = {
  getSiteSettings: () =>
    api.get<SiteSettings>('/customization/settings/'),
};

// Location Check-in API
export const locationCheckinAPI = {
  checkin: (locationId: string, data: {
    checkin_type?: 'volunteer' | 'contractor' | 'anonymous';
    notes?: string;
  }) =>
    api.post(`/location-checkins/checkins/checkin/`, {
      location_id: locationId,
      ...data,
    }),

  submitFeedback: (locationId: string, data: {
    feedback_type: 'positive' | 'neutral' | 'negative';
    message: string;
  }) =>
    api.post(`/location-checkins/feedback/submit/`, {
      location_id: locationId,
      ...data,
    }),

  reportSecurity: (locationId: string, data: {
    report_type: 'cleaning' | 'safety';
    is_urgent?: boolean;
    description?: string;
  }) =>
    api.post(`/location-checkins/security-reports/report/`, {
      location_id: locationId,
      ...data,
    }),

  getTasks: (params?: { location?: string; status?: string }) =>
    api.get('/location-checkins/tasks/', { params }),

  completeTask: (taskId: string, notes?: string) =>
    api.post(`/location-checkins/tasks/${taskId}/complete/`, { notes }),
};

// Checklists API
export const checklistsAPI = {
  getAvailableChecklists: (params?: { asset_id?: string; location_id?: string; item_id?: string }) =>
    api.get<Checklist[]>('/checklists/checklists/available/', { params }),

  getChecklist: (id: string) =>
    api.get<Checklist>(`/checklists/checklists/${id}/detail/`),

  startChecklist: (checklistId: string, userName?: string) =>
    api.post<ChecklistCompletion>(`/checklists/checklists/${checklistId}/start/`, {
      user_name: userName || '',
    }),

  getCompletion: (completionId: string) =>
    api.get<ChecklistCompletion>(`/checklists/completions/${completionId}/`),

  scanStep: (
    completionId: string,
    stepId: string,
    scannedItem: { asset_id?: string; location_id?: number; item_id?: string },
    notes?: string
  ) =>
    api.post<ChecklistCompletion>(`/checklists/completions/${completionId}/scan/`, {
      step_id: stepId,
      ...scannedItem,
      notes: notes || '',
    }),

  completeChecklist: (completionId: string) =>
    api.post<ChecklistCompletion>(`/checklists/completions/${completionId}/complete/`),
};

// Index Cards API
export const indexCardsAPI = {
  generateTestSheet: (itemIds: string[]) =>
    api.post('/index-cards/test-sheet/', { item_ids: itemIds }, {
      responseType: 'blob',
    }),
};

// Donations API
export const donationsAPI = {
  getDonationItem: (id: string) =>
    api.get<DonationItem>(`/donations/donation-items/${id}/`),

  updateDonationItem: (id: string, data: Partial<DonationItem>) =>
    api.patch<DonationItem>(`/donations/donation-items/${id}/`, data),

  createDisposition: (data: {
    donation_item: string;
    disposition_type: string;
    quantity: number;
    disposition_date?: string;
    sale_method?: string;
    sale_price?: string;
    kept_destination?: string;
    kept_for_sig?: number;
    notes?: string;
    recipient_name?: string;
  }) =>
    api.post<Disposition>('/donations/dispositions/', data),

  getDispositions: (params?: { donation_item?: string }) =>
    api.get<{ results: Disposition[] }>('/donations/dispositions/', { params }),

  // Tax Receipt API
  lookupTaxReceipt: (serialNumber: string) =>
    api.get<TaxReceipt>(`/donations/tax-receipts/lookup/?serial_number=${serialNumber}`),

  getTaxReceipt: (id: string) =>
    api.get<TaxReceipt>(`/donations/tax-receipts/${id}/`),

  downloadTaxReceipt: (id: string, isCopy: boolean = false) =>
    api.get(`/donations/tax-receipts/${id}/download_pdf/?copy=${isCopy}`, {
      responseType: 'blob',
    }),

  generateTaxReceipt: (donationId: string, data?: { signature_user_id?: string; password?: string }) =>
    api.post<TaxReceipt>(`/donations/donations/${donationId}/generate_tax_receipt/`, data),

  downloadDonationTaxReceipt: (donationId: string, isCopy: boolean = false) =>
    api.get(`/donations/donations/${donationId}/download_tax_receipt/?copy=${isCopy}`, {
      responseType: 'blob',
    }),

  uploadSignature: (signatureFile: File, password: string) => {
    const formData = new FormData();
    formData.append('signature', signatureFile);
    formData.append('password', password);
    return api.post<{ message: string; signature_url: string | null }>(
      '/donations/upload-signature/',
      formData,
      {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      }
    );
  },
};

export default api;
