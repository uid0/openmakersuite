/**
 * API service for communicating with the Django backend
 */
import * as Sentry from '@sentry/react';
import axios from 'axios';
import { CreateReorderRequest, InventoryItem, ItemSupplier, ReorderRequest } from '../types';

const API_BASE_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';

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
    api.get<InventoryItem>(`/api/inventory/items/${id}/`),

  getItemSuppliers: (itemId: string) => 
    api.get<{ results: ItemSupplier[] }>(`/api/inventory/item-suppliers/?item_id=${itemId}`),

  listItems: (params?: { category?: number; search?: string }) =>
    api.get<{ results: InventoryItem[] }>('/api/inventory/items/', { params }),

  getLowStockItems: () =>
    api.get<InventoryItem[]>('/api/inventory/items/low_stock/'),

  getReorderedItems: () =>
    api.get<InventoryItem[]>('/api/inventory/items/reordered/'),

  downloadCard: (id: string) =>
    api.get(`/api/inventory/items/${id}/download_card/`, {
      responseType: 'blob',
    }),

  generateQR: (id: string) =>
    api.post(`/api/inventory/items/${id}/generate_qr/`),

  logUsage: (id: string, quantity: number, notes?: string) =>
    api.post(`/api/inventory/items/${id}/log_usage/`, {
      quantity,
      notes,
    }),
};

// Reorder API
export const reorderAPI = {
  createRequest: (data: CreateReorderRequest) =>
    api.post<ReorderRequest>('/api/reorders/requests/', data),

  listRequests: (params?: { status?: string }) =>
    api.get<{ results: ReorderRequest[] }>('/api/reorders/requests/', { params }),

  getPendingRequests: () =>
    api.get<ReorderRequest[]>('/api/reorders/requests/pending/'),

  getBySupplier: () =>
    api.get('/api/reorders/requests/by_supplier/'),

  approveRequest: (id: number, adminNotes?: string) =>
    api.post(`/api/reorders/requests/${id}/approve/`, { admin_notes: adminNotes }),

  markOrdered: (id: number, data: {
    order_number?: string;
    estimated_delivery?: string;
    actual_cost?: number;
  }) =>
    api.post(`/api/reorders/requests/${id}/mark_ordered/`, data),

  markReceived: (id: number, actualDelivery?: string) =>
    api.post(`/api/reorders/requests/${id}/mark_received/`, {
      actual_delivery: actualDelivery,
    }),

  cancelRequest: (id: number, adminNotes?: string) =>
    api.post(`/api/reorders/requests/${id}/cancel/`, { admin_notes: adminNotes }),

  generateCartLinks: () =>
    api.get('/api/reorders/requests/generate_cart_links/'),
};

// Auth API
export const authAPI = {
  login: (username: string, password: string) =>
    api.post('/api/auth/login/', { username, password }),

  register: (userData: { username: string; email: string; password: string; password2: string }) =>
    api.post('/api/auth/register/', userData),

  refresh: (refreshToken: string) =>
    api.post('/api/auth/refresh/', { refresh: refreshToken }),
};

export default api;
