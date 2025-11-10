/**
 * API service for communicating with the Django backend
 */
import * as Sentry from '@sentry/react';
import axios from 'axios';
import { Asset, CreateReorderRequest, Fixture, FixtureRefillRequest, InventoryItem, ItemSupplier, ReorderRequest, SiteSettings } from '../types';

const resolveApiBaseUrl = () => {
  const rawBase = process.env.REACT_APP_API_URL;
  const defaultBase = 'http://localhost:8000/api';

  if (!rawBase || rawBase.trim().length === 0) {
    return defaultBase;
  }

  const trimmedBase = rawBase.replace(/\/+$/, '');

  if (trimmedBase.endsWith('/api') || trimmedBase.includes('/api/')) {
    return trimmedBase;
  }

  return `${trimmedBase}/api`;
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

// Add error logging to responses
api.interceptors.response.use(
  (response) => response,
  (error) => {
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

  listItems: (params?: { category?: number; search?: string }) =>
    api.get<{ results: InventoryItem[] }>('/inventory/items/', { params }),

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

  logUsage: (id: string, quantity: number, notes?: string) =>
    api.post(`/inventory/items/${id}/log_usage/`, {
      quantity,
      notes,
    }),
};

// Assets API
export const assetsAPI = {
  listAssets: (params?: { status?: string; category?: number; search?: string }) =>
    api.get<{ results: Asset[] }>('/inventory/assets/', { params }),

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

// Reorder API
export const reorderAPI = {
  createRequest: (data: CreateReorderRequest) =>
    api.post<ReorderRequest>('/reorders/requests/', data),

  listRequests: (params?: { status?: string }) =>
    api.get<{ results: ReorderRequest[] }>('/reorders/requests/', { params }),

  getPendingRequests: () =>
    api.get<ReorderRequest[]>('/reorders/requests/pending/'),

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

export default api;
