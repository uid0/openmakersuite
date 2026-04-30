/**
 * API service for communicating with the Django backend
 */
import * as Sentry from '@sentry/react';
import axios from 'axios';
import { ActiveMaintenanceRow, Asset, AssetPart, AssetProblem, AssetProblemPhoto, AssetProblemsData, Breaker, Category, ChangePasswordRequest, Checklist, ChecklistCompletion, CheckMaterialStockResponse, CreateReorderRequest, DashboardWidget, DeliveriesData, Disposition, DonationItem, Fixture, FixtureRefillRequest, InventoryItem, ItemSupplier, KioskPayload, LightSwitch, Location, LocationProblem, LowStockData, MaintenanceItem, MaintenanceLog, MaintenanceMaterial, MaintenanceTask, NetworkDrop, NetworkDropType, NotificationPreferences, Outlet, PendingReordersData, QRScansData, RecentSearch, ReorderRequest, Screen, ScreenContentBlock, ScreenStatusEntry, SearchResult, SIG, SIGMember, SiteSettings, Supplier, SupplierDetail, SystemMessage, TaxReceipt, UsageLog, UserProfile, Webhook, WebhookTestResult, WorkOrder, WorkOrderPhoto, WorkOrderTaskCompletion, WorkOrderUploadResult } from '../types';

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

const normalizeResults = <T>(data: { results?: T[] } | T[]): { results: T[] } => {
  if (Array.isArray(data)) {
    return { results: data };
  }
  if (data && Array.isArray(data.results)) {
    return { results: data.results };
  }
  return { results: [] };
};

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
  // Send the Django session cookie alongside JWT so a single backend login
  // authenticates the SPA, the DRF browsable API, and the admin at once.
  withCredentials: true,
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
    api.get<{ results?: Location[] } | Location[]>('/inventory/locations/')
      .then((response) => ({
        ...response,
        data: normalizeResults<Location>(response.data),
      })),

  listCategories: () =>
    api.get<{ results: Category[] }>('/inventory/categories/'),

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

  // Stock reconciliation (oms-90k)
  listLocationReconcileGrid: (locationId: string | number) =>
    api.get<{
      location_id: string;
      location_name: string;
      items: ReconciliationGridItem[];
    }>(`/inventory/locations/${locationId}/reconcile/`),

  submitReconciliationBatch: (rows: ReconciliationRow[]) =>
    api.post<ReconciliationBatchResponse>('/inventory/reconciliations/batch/', {
      rows,
    }),

  listReconciliations: (params?: { item?: string; reason?: string }) =>
    api.get<StockReconciliation[]>('/inventory/reconciliations/', { params }),

  // CSV offline batch (oms-sig)
  exportReconcileTemplate: (locationId: string | number) =>
    api.get<Blob>(
      `/inventory/locations/${locationId}/reconcile/export/`,
      { responseType: 'blob' },
    ),

  uploadReconcileCsv: (file: File, partial = false) => {
    const form = new FormData();
    form.append('file', file);
    return api.post<ReconciliationUploadResponse>(
      `/inventory/reconciliations/upload/${partial ? '?partial=true' : ''}`,
      form,
      { headers: { 'Content-Type': 'multipart/form-data' } },
    );
  },
};

export interface ReconciliationGridItem {
  item_id: string;
  name: string;
  sku: string;
  projected: number;
  minimum_stock: number;
  reorder_quantity: number;
  owning_group_name: string;
}

export interface ReconciliationRow {
  item_id: string;
  actual_count: number;
  reason:
    | 'lost'
    | 'damaged'
    | 'miscounted'
    | 'used_without_scan'
    | 'found'
    | 'other';
  notes?: string;
  skip_reorder?: boolean;
}

export interface StockReconciliation {
  id: number;
  item: string;
  item_name: string;
  item_sku: string;
  projected_count: number;
  actual_count: number;
  delta: number;
  reason: string;
  notes: string;
  reconciled_by: number;
  reconciled_by_name: string;
  reconciled_at: string;
  triggered_reorder: number | null;
}

export interface ReconciliationBatchResponse {
  reconciled: number;
  reorders_created: number;
  reconciliations: StockReconciliation[];
}

export interface ReconciliationUploadError {
  row: number;
  error: string;
}

export interface ReconciliationUploadResponse {
  created: number;
  skipped: number;
  errors: ReconciliationUploadError[];
}

// Assets API
export const assetsAPI = {
  listAssets: (params?: {
    status?: string;
    category?: number;
    location?: number;
    search?: string;
    owning_group?: number;
    inventory_item?: string;
    manufacturer?: number;
    is_active?: boolean;
    date_received_after?: string;
    date_received_before?: string;
    age_min_days?: number;
    age_max_days?: number;
    ordering?: string;
    page?: number;
    page_size?: number;
  }) =>
    api.get<{ count: number; next: string | null; previous: string | null; results: Asset[] }>('/inventory/assets/', { params }),

  getMySIGAssets: () =>
    api.get<Asset[]>('/inventory/assets/my_sig_assets/'),

  getAsset: (id: string) =>
    api.get<Asset>(`/inventory/assets/${id}/`),

  createAsset: (data: FormData | Partial<Asset>) => {
    if (data instanceof FormData) {
      return api.post<Asset>('/inventory/assets/', data, {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      });
    }
    return api.post<Asset>('/inventory/assets/', data);
  },

  updateAsset: (id: string, data: FormData | Partial<Asset>) => {
    if (data instanceof FormData) {
      return api.patch<Asset>(`/inventory/assets/${id}/`, data, {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      });
    }
    return api.patch<Asset>(`/inventory/assets/${id}/`, data);
  },

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
    api.post<AssetProblem>(`/inventory/assets/${id}/report_problem/`, { description }),

  resolveProblem: (id: string, problemId: string, resolutionNotes?: string, status?: string) =>
    api.post(`/inventory/assets/${id}/resolve_problem/`, {
      problem_id: problemId,
      resolution_notes: resolutionNotes,
      status: status || 'resolved',
    }),

  downloadLabel: (id: string) =>
    api.get(`/inventory/assets/${id}/download_label/`, {
      responseType: 'blob',
    }),

  getTagUrl: (id: string, size: 'standard' | 'large' = 'standard', download = false) => {
    const params = new URLSearchParams({ size });
    if (download) params.set('download', '1');
    return `${API_BASE_URL}/inventory/assets/${id}/tag/?${params.toString()}`;
  },

  downloadLabelsBatch: (assetIds: string[]) =>
    api.post(`/inventory/assets/download_labels_batch/`, { asset_ids: assetIds }, {
      responseType: 'blob',
    }),

  getNotCheckedIn: (params?: { status?: string; inventory_item?: string }) =>
    api.get<Asset[]>('/inventory/assets/not_checked_in/', { params }),

  getMaintenanceItems: (assetId: string) =>
    api.get<MaintenanceItem[]>(`/inventory/assets/${assetId}/maintenance_items/`),
};

// Location Problem API (oms-sd1)
export const locationProblemsAPI = {
  list: (params?: { location?: string | number; status?: string; severity?: string }) =>
    api.get<{ results: LocationProblem[] }>('/inventory/location-problems/', { params }),

  get: (id: string) =>
    api.get<LocationProblem>(`/inventory/location-problems/${id}/`),

  reportForLocation: (locationId: string | number, payload: {
    description: string;
    severity?: 'low' | 'medium' | 'high' | 'urgent';
    photo?: File;
  }) => {
    const form = new FormData();
    form.append('description', payload.description);
    if (payload.severity) form.append('severity', payload.severity);
    if (payload.photo) form.append('photo', payload.photo);
    return api.post<LocationProblem>(
      `/inventory/locations/${locationId}/report_problem/`,
      form,
      { headers: { 'Content-Type': 'multipart/form-data' } },
    );
  },

  listForLocation: (locationId: string | number) =>
    api.get<LocationProblem[]>(`/inventory/locations/${locationId}/problems/`),

  promoteStandard: (id: string, maintenanceItemId: string) =>
    api.post<LocationProblem>(
      `/inventory/location-problems/${id}/promote-standard/`,
      { maintenance_item: maintenanceItemId },
    ),

  promoteThirdParty: (id: string, payload: {
    vendor: string;
    title: string;
    work_type?: string;
  }) =>
    api.post<LocationProblem>(
      `/inventory/location-problems/${id}/promote-third-party/`,
      payload,
    ),

  resolve: (id: string, payload: {
    status?: 'resolved' | 'closed';
    resolution_notes?: string;
  }) =>
    api.post<LocationProblem>(
      `/inventory/location-problems/${id}/resolve/`,
      payload,
    ),
};

// Active maintenance work-order list (unioned: WO + AssetProblem + LocationProblem)
export const activeMaintenanceAPI = {
  list: () =>
    api.get<{ results: ActiveMaintenanceRow[]; count: number }>(
      '/inventory/maintenance/active/',
    ),
};

// Asset Problem API
export const assetProblemsAPI = {
  get: (id: string) =>
    api.get<AssetProblem>(`/inventory/asset-problems/${id}/`),

  uploadPhoto: (problemId: string, image: File, caption?: string) => {
    const formData = new FormData();
    formData.append('image', image);
    if (caption) {
      formData.append('caption', caption);
    }
    return api.post<AssetProblemPhoto>(
      `/inventory/asset-problems/${problemId}/upload-photo/`,
      formData,
      { headers: { 'Content-Type': 'multipart/form-data' } },
    );
  },
};

// Maintenance API
export const maintenanceAPI = {
  listItems: (params?: { asset?: string; is_active?: boolean }) =>
    api.get<{ results: MaintenanceItem[] }>('/inventory/maintenance-items/', { params }),

  getItem: (id: string) =>
    api.get<MaintenanceItem>(`/inventory/maintenance-items/${id}/`),

  createItem: (data: Partial<MaintenanceItem>) =>
    api.post<MaintenanceItem>('/inventory/maintenance-items/', data),

  updateItem: (id: string, data: Partial<MaintenanceItem>) =>
    api.patch<MaintenanceItem>(`/inventory/maintenance-items/${id}/`, data),

  deleteItem: (id: string) =>
    api.delete(`/inventory/maintenance-items/${id}/`),

  completeItem: (id: string, data: { time_spent_minutes?: number; cost_incurred?: string; notes?: string }) =>
    api.post<MaintenanceLog>(`/inventory/maintenance-items/${id}/complete/`, data),

  cloneItem: (id: string, targetAssetId: string) =>
    api.post<MaintenanceItem>(`/inventory/maintenance-items/${id}/clone/`, {
      target_asset_id: targetAssetId,
    }),

  listMaterials: (maintenanceItemId: string) =>
    api.get<{ results: MaintenanceMaterial[] }>('/inventory/maintenance-materials/', { params: { maintenance_item: maintenanceItemId } }),

  createMaterial: (data: Partial<MaintenanceMaterial>) =>
    api.post<MaintenanceMaterial>('/inventory/maintenance-materials/', data),

  deleteMaterial: (id: string) =>
    api.delete(`/inventory/maintenance-materials/${id}/`),

  listLogs: (params?: { maintenance_item?: string; asset?: string }) =>
    api.get<{ results: MaintenanceLog[] }>('/inventory/maintenance-logs/', { params }),

  getDashboard: () =>
    api.get<MaintenanceDashboardData>('/inventory/maintenance/dashboard/'),
};

export interface MaintenanceDashboardScheduledRow {
  asset_id: string;
  asset_name: string;
  maintenance_item_id: string;
  title: string;
  interval_days: number | null;
  next_due: string | null;
  days_until: number | null;
  last_completed_at: string | null;
  is_overdue: boolean;
}

export interface MaintenanceDashboardUnscheduledRow {
  workorder_id: string;
  short_id: string;
  asset_id: string;
  asset_name: string;
  problem: string;
  opened_at: string;
  status: string;
}

export interface MaintenanceDashboardByAssetRow {
  asset_id: string;
  asset_name: string;
  total_cost: string;
  days_in_maintenance_90d: number;
}

export interface MaintenanceDashboardData {
  scheduled_pm: MaintenanceDashboardScheduledRow[];
  unscheduled: MaintenanceDashboardUnscheduledRow[];
  costs: {
    per_period: {
      today: string;
      this_week: string;
      this_month: string;
      this_year: string;
      all_time: string;
    };
    by_asset: MaintenanceDashboardByAssetRow[];
  };
}

// Maintenance Task API (sub-task steps within a MaintenanceItem)
export const maintenanceTaskAPI = {
  listTasks: (maintenanceItemId: string) =>
    api.get<{ results: MaintenanceTask[] }>('/inventory/maintenance-tasks/', {
      params: { maintenance_item: maintenanceItemId },
    }),

  createTask: (data: Partial<MaintenanceTask>) =>
    api.post<MaintenanceTask>('/inventory/maintenance-tasks/', data),

  updateTask: (id: string, data: Partial<MaintenanceTask>) =>
    api.patch<MaintenanceTask>(`/inventory/maintenance-tasks/${id}/`, data),

  deleteTask: (id: string) => api.delete(`/inventory/maintenance-tasks/${id}/`),
};

// Work Order API
export const workOrderAPI = {
  listWorkOrders: (params?: { asset?: string; maintenance_item?: string; status?: string }) =>
    api.get<{ results: WorkOrder[] }>('/inventory/work-orders/', { params }),

  getWorkOrder: (id: string) => api.get<WorkOrder>(`/inventory/work-orders/${id}/`),

  createWorkOrder: (data: Partial<WorkOrder>) =>
    api.post<WorkOrder>('/inventory/work-orders/', data),

  updateWorkOrder: (id: string, data: Partial<WorkOrder>) =>
    api.patch<WorkOrder>(`/inventory/work-orders/${id}/`, data),

  getPdfUrl: (id: string) => `${API_BASE_URL}/inventory/work-orders/${id}/pdf/`,

  generateWorkOrder: (maintenanceItemId: string, data?: { due_date?: string; notes?: string }) =>
    api.post<WorkOrder>(`/inventory/maintenance-items/${maintenanceItemId}/generate_work_order/`, data || {}),

  checkMaterialStock: (maintenanceItemId: string) =>
    api.get<CheckMaterialStockResponse>(
      `/inventory/maintenance-items/${maintenanceItemId}/check_material_stock/`
    ),

  generateBulkWorkOrders: () =>
    api.post<{ created: number; work_order_ids: string[] }>(
      '/inventory/maintenance-items/generate_work_orders_bulk/',
      {}
    ),

  completeTask: (workOrderId: string, taskCompletionId: string, data: { is_completed: boolean; notes?: string }) =>
    api.patch<WorkOrderTaskCompletion>(
      `/inventory/work-orders/${workOrderId}/tasks/${taskCompletionId}/complete/`,
      data
    ),

  toggleMaterial: (workOrderId: string, materialUsageId: string, wasUsed: boolean) =>
    api.patch(
      `/inventory/work-orders/${workOrderId}/materials/${materialUsageId}/toggle/`,
      { was_used: wasUsed }
    ),

  addPhoto: (workOrderId: string, formData: FormData) =>
    api.post<WorkOrderPhoto>(`/inventory/work-orders/${workOrderId}/add_photo/`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }),

  uploadPdf: (file: File) => {
    const formData = new FormData();
    formData.append('pdf', file);
    return api.post<WorkOrderUploadResult>('/inventory/work-orders/upload-pdf/', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
  },

  getDueThisWeek: () =>
    api.get<MaintenanceItem[]>('/inventory/maintenance-items/due_this_week/'),

  getDueThisMonth: () =>
    api.get<MaintenanceItem[]>('/inventory/maintenance-items/due_this_month/'),
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
export interface ReorderDataItem {
  item_supplier_id: number;
  item_id: string;
  item_name: string;
  item_sku: string;
  current_stock: number;
  minimum_stock: number;
  reorder_quantity: number;
  suggested_quantity: number;
  unit_cost: string;
  package_cost: string | null;
  quantity_per_package: number;
  lead_time_days: number;
  supplier_sku: string;
  supplier_url: string;
  is_primary: boolean;
  line_total: string;
  has_active_reorder_request?: boolean;
  reorder_request_id?: number | null;
}

export interface ReorderDataAsset {
  id: string;
  name: string;
  asset_tag: string;
  serial_number: string;
  product_url: string;
}

export interface ReorderDataSupplier {
  id: number;
  name: string;
  supplier_type: string;
  website: string;
  items: ReorderDataItem[];
  assets: ReorderDataAsset[];
  total_items: number;
  estimated_total: string;
  avg_lead_time: number;
}

export interface ReorderDataResponse {
  suppliers: ReorderDataSupplier[];
  total_suppliers: number;
  total_low_stock_items: number;
  items_with_requests?: number;
}

export interface CreatePurchaseOrderItem {
  item_supplier_id?: number;
  asset_id?: string;
  description?: string;
  quantity: number;
  order_in_packages?: number;
  unit_cost?: number;
  expected_shipment_date?: string;
  notes?: string;
}

export interface CreatePurchaseOrderData {
  supplier: number;
  expected_delivery_date?: string;
  notes?: string;
  items: CreatePurchaseOrderItem[];
}

export const purchaseOrderAPI = {
  listOrders: (params?: { status?: string }) =>
    api.get<{ results: any[] }>('/reorders/purchase-orders/', { params }),
  getOrder: (id: string) =>
    api.get<any>(`/reorders/purchase-orders/${id}/`),
  getReorderData: () =>
    api.get<ReorderDataResponse>('/reorders/purchase-orders/reorder_data/'),
  createOrder: (data: CreatePurchaseOrderData) =>
    api.post<any>('/reorders/purchase-orders/', data),
  updateLineItem: (orderId: string, itemId: string, data: { expected_shipment_date?: string; notes?: string; line_cost?: number; unit_cost_actual?: number }) =>
    api.patch(`/reorders/purchase-orders/${orderId}/items/${itemId}/`, data),
  voidLineItem: (orderId: string, itemId: string, reason?: string) =>
    api.post(`/reorders/purchase-orders/${orderId}/items/${itemId}/void/`, { reason }),
  voidOrder: (orderId: string, reason: string) =>
    api.post<any>(`/reorders/purchase-orders/${orderId}/void/`, { reason }),
  markDelivered: (
    orderId: string,
    data: { delivery_date: string; tracking_number?: string; carrier?: string; receipt_notes?: string },
  ) => api.post<any>(`/reorders/purchase-orders/${orderId}/mark-delivered/`, data),
  updateOrder: (
    orderId: string,
    data: {
      supplier_order_number?: string;
      sales_order_number?: string;
      expected_delivery_date?: string | null;
      notes?: string;
    },
  ) => api.patch<any>(`/reorders/purchase-orders/${orderId}/`, data),
  uploadAttachment: (orderId: string, file: File, description?: string) => {
    const formData = new FormData();
    formData.append('file', file);
    if (description) {
      formData.append('description', description);
    }
    return api.post<any>(
      `/reorders/purchase-orders/${orderId}/upload-attachment/`,
      formData,
      { headers: { 'Content-Type': 'multipart/form-data' } },
    );
  },
  deleteAttachment: (orderId: string, attachmentId: number | string) =>
    api.delete(`/reorders/purchase-orders/${orderId}/attachments/${attachmentId}/`),
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

  createSIG: (data: { name: string; group_email?: string }) =>
    api.post<{ id: number; name: string; group_email?: string }>('/membership/sigs/', data),

  updateSIG: (id: number, data: { name: string; group_email?: string }) =>
    api.put<{ id: number; name: string; group_email?: string }>(
      `/membership/sigs/${id}/`,
      data,
    ),

  deleteSIG: (id: number) =>
    api.delete(`/membership/sigs/${id}/`),

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

  logout: () =>
    api.post('/auth/logout/'),
};

// User Profile API
export const userAPI = {
  getProfile: () =>
    api.get<UserProfile>('/membership/profile/me/'),

  updateProfile: (data: Partial<UserProfile>) =>
    api.put<UserProfile>('/membership/profile/update_me/', data),

  changePassword: (data: ChangePasswordRequest) =>
    api.post<{ message: string }>('/membership/change-password/', data),

  uploadSignature: (file: File, password: string) => {
    const formData = new FormData();
    formData.append('signature', file);
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

// Customization API
export const customizationAPI = {
  getSiteSettings: () =>
    api.get<SiteSettings>('/customization/settings/'),
  updateSiteSettings: (data: FormData) =>
    api.put<SiteSettings>('/customization/settings/', data, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    }),
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

  getTrafficReport: (params: {
    location?: string;
    start?: string;
    end?: string;
    bucket?: 'hour' | 'day' | 'week' | 'month';
  }) =>
    api.get<{
      bucket: string;
      start: string;
      end: string;
      results: { bucket_start: string; count: number }[];
    }>('/location-checkins/reports/traffic/', { params }),

  getTopLocations: (params?: { start?: string; end?: string; limit?: number }) =>
    api.get<{
      start: string;
      end: string;
      results: { location_id: number; location_name: string; count: number }[];
    }>('/location-checkins/reports/top/', { params }),
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
    notes?: string,
    photo?: { file: File; caption?: string }
  ) => {
    if (photo) {
      const formData = new FormData();
      formData.append('step_id', stepId);
      if (scannedItem.asset_id) formData.append('asset_id', scannedItem.asset_id);
      if (scannedItem.location_id !== undefined)
        formData.append('location_id', String(scannedItem.location_id));
      if (scannedItem.item_id) formData.append('item_id', scannedItem.item_id);
      formData.append('notes', notes || '');
      formData.append('photo', photo.file);
      if (photo.caption) formData.append('photo_caption', photo.caption);
      return api.post<ChecklistCompletion>(
        `/checklists/completions/${completionId}/scan/`,
        formData,
        { headers: { 'Content-Type': 'multipart/form-data' } }
      );
    }
    return api.post<ChecklistCompletion>(`/checklists/completions/${completionId}/scan/`, {
      step_id: stepId,
      ...scannedItem,
      notes: notes || '',
    });
  },

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

// Search API
export const searchAPI = {
  globalSearch: (query: string, limit?: number) =>
    api.get<{ results: SearchResult[] }>('/search/', {
      params: { q: query, limit },
    }),

  getRecentSearches: (limit?: number) =>
    api.get<{ results: RecentSearch[] }>('/search/recent/', {
      params: { limit },
    }),

  saveRecentSearch: (data: {
    query: string;
    result_type: 'inventory' | 'asset' | 'purchase_order' | 'supplier' | 'location';
    result_id: string;
    result_title: string;
  }) =>
    api.post<RecentSearch>('/search/recent/save/', data),
};

// Notifications API
export interface BackendNotification {
  id: string;
  type: 'success' | 'error' | 'warning' | 'info';
  title: string;
  message: string;
  read: boolean;
  created_at: string;
  action_url?: string;
  metadata?: Record<string, unknown>;
}

export const notificationsAPI = {
  list: (params?: { read?: boolean }) =>
    api.get<{ results: BackendNotification[] }>('/notifications/', { params }),

  markAsRead: (id: string) =>
    api.post(`/notifications/${id}/mark-read/`),

  markAllAsRead: () =>
    api.post('/notifications/mark-all-read/'),

  delete: (id: string) =>
    api.delete(`/notifications/${id}/`),

  getPreferences: () =>
    api.get<NotificationPreferences>('/notifications/preferences/'),

  updatePreferences: (data: Partial<NotificationPreferences>) =>
    api.put<NotificationPreferences>('/notifications/preferences/', data),
};

// Date range params type for reports
type DateRangeParams = { start_date?: string; end_date?: string };

// Reports API
export const reportsAPI = {
  // Inventory Reports
  getInventoryStockByCategory: () =>
    api.get('/inventory/reports/inventory/stock_by_category/'),

  getInventoryReorderFrequency: (params?: DateRangeParams) =>
    api.get('/inventory/reports/inventory/reorder_frequency/', { params }),

  getInventoryValueByLocation: () =>
    api.get('/inventory/reports/inventory/value_by_location/'),

  exportInventoryReport: (type: 'stock_by_category' | 'reorder_frequency' | 'value_by_location', params?: DateRangeParams) =>
    api.get('/inventory/reports/inventory/export/', {
      params: { type, ...params },
      responseType: 'blob',
    }),

  // Purchasing Reports
  getPurchasingSpendBySupplier: () =>
    api.get('/reorders/reports/purchasing/spend_by_supplier/'),

  getPurchasingSpendByCategory: () =>
    api.get('/reorders/reports/purchasing/spend_by_category/'),

  getPurchasingLeadTimeAnalysis: (params?: DateRangeParams) =>
    api.get('/reorders/reports/purchasing/lead_time_analysis/', { params }),

  getPurchasingPriceTrends: (params?: DateRangeParams) =>
    api.get('/reorders/reports/purchasing/price_trends/', { params }),

  exportPurchasingReport: (type: 'spend_by_supplier' | 'spend_by_category' | 'lead_time_analysis' | 'price_trends', params?: DateRangeParams) =>
    api.get('/reorders/reports/purchasing/export/', {
      params: { type, ...params },
      responseType: 'blob',
    }),

  // Asset Reports
  getAssetAssetsByStatus: () =>
    api.get('/inventory/reports/assets/assets_by_status/'),

  getAssetMaintenanceDue: () =>
    api.get('/inventory/reports/assets/maintenance_due/'),

  getAssetUtilization: (params?: DateRangeParams) =>
    api.get('/inventory/reports/assets/utilization/', { params }),

  getAssetTco: () =>
    api.get('/inventory/reports/assets/tco/'),

  exportAssetReport: (type: 'assets_by_status' | 'maintenance_due' | 'utilization' | 'tco', params?: DateRangeParams) =>
    api.get('/inventory/reports/assets/export/', {
      params: { type, ...params },
      responseType: 'blob',
    }),
};

// Dashboard API
export const dashboardAPI = {
  getWidgets: () =>
    api.get<DashboardWidget[]>('/dashboard/widgets/'),

  saveWidgets: (widgets: Partial<DashboardWidget>[]) =>
    api.post<{ widgets: DashboardWidget[] }>('/dashboard/widgets/save/', { widgets }),

  getLowStockData: () =>
    api.get<LowStockData>('/dashboard/widget-data/low-stock/'),

  getPendingReordersData: () =>
    api.get<PendingReordersData>('/dashboard/widget-data/pending-reorders/'),

  getAssetProblemsData: () =>
    api.get<AssetProblemsData>('/dashboard/widget-data/asset-problems/'),

  getQRScansData: () =>
    api.get<QRScansData>('/dashboard/widget-data/qr-scans/'),

  getDeliveriesData: () =>
    api.get<DeliveriesData>('/dashboard/widget-data/deliveries/'),
};

// Webhooks API
export const webhooksAPI = {
  listWebhooks: () =>
    api.get<{ results: Webhook[] }>('/reorders/webhooks/'),

  getWebhook: (id: number) =>
    api.get<Webhook>(`/reorders/webhooks/${id}/`),

  createWebhook: (data: Partial<Webhook>) =>
    api.post<Webhook>('/reorders/webhooks/', data),

  updateWebhook: (id: number, data: Partial<Webhook>) =>
    api.patch<Webhook>(`/reorders/webhooks/${id}/`, data),

  deleteWebhook: (id: number) =>
    api.delete(`/reorders/webhooks/${id}/`),

  testWebhook: (id: number) =>
    api.post<WebhookTestResult>(`/reorders/webhooks/${id}/test/`),

  getTestStatus: (taskId: string) =>
    api.get<WebhookTestResult>(`/reorders/webhooks/test-status/`, { params: { task_id: taskId } }),
};

// Interactive Screens / Kiosk Display API
export const screensAPI = {
  listScreens: () =>
    api.get<Screen[] | { results: Screen[] }>('/screens/screens/'),

  getScreen: (slug: string) =>
    api.get<Screen>(`/screens/screens/${slug}/`),

  createScreen: (data: Partial<Screen>) =>
    api.post<Screen>('/screens/screens/', data),

  updateScreen: (slug: string, data: Partial<Screen>) =>
    api.patch<Screen>(`/screens/screens/${slug}/`, data),

  deleteScreen: (slug: string) =>
    api.delete(`/screens/screens/${slug}/`),

  getStatus: () =>
    api.get<{ count: number; screens: ScreenStatusEntry[] }>('/screens/screens/status/'),

  rotateToken: (slug: string) =>
    api.post<{ access_token: string }>(`/screens/screens/${slug}/rotate-token/`),

  listBlocks: (screenId?: string) =>
    api.get<ScreenContentBlock[] | { results: ScreenContentBlock[] }>(
      '/screens/blocks/',
      screenId ? { params: { screen: screenId } } : undefined,
    ),

  createBlock: (data: Partial<ScreenContentBlock>) =>
    api.post<ScreenContentBlock>('/screens/blocks/', data),

  updateBlock: (id: string, data: Partial<ScreenContentBlock>) =>
    api.patch<ScreenContentBlock>(`/screens/blocks/${id}/`, data),

  deleteBlock: (id: string) =>
    api.delete(`/screens/blocks/${id}/`),

  listSystemMessages: () =>
    api.get<SystemMessage[] | { results: SystemMessage[] }>('/screens/messages/'),

  createSystemMessage: (data: Partial<SystemMessage>) =>
    api.post<SystemMessage>('/screens/messages/', data),

  updateSystemMessage: (id: string, data: Partial<SystemMessage>) =>
    api.patch<SystemMessage>(`/screens/messages/${id}/`, data),

  deleteSystemMessage: (id: string) =>
    api.delete(`/screens/messages/${id}/`),
};

export const kioskAPI = {
  fetchPayload: (slug: string, token: string) =>
    axios.get<KioskPayload>(
      `${resolveApiBaseUrl()}/screens/kiosk/${slug}/`,
      { headers: { 'X-Screen-Token': token, 'Content-Type': 'application/json' } },
    ),

  heartbeat: (slug: string, token: string, contentVersion?: string) =>
    axios.post(
      `${resolveApiBaseUrl()}/screens/kiosk/${slug}/heartbeat/`,
      { content_version: contentVersion || '' },
      { headers: { 'X-Screen-Token': token, 'Content-Type': 'application/json' } },
    ),
};

export interface MakerBox {
  id: number;
  bin_id: string;
  assigned_username: string;
  first_name: string;
  last_name: string;
  email: string;
  display_name: string;
  assigned_at: string | null;
  expires_at: string | null;
  last_verified_at: string | null;
  status: 'valid' | 'grace' | 'expired' | 'unassigned' | 'unknown';
  paid_at: string | null;
  notes: string;
  created_at: string;
  updated_at: string;
}

export interface MakerBoxScanResult {
  status: 'valid' | 'grace' | 'expired' | 'unknown';
  bin_id: string;
  username: string;
  first_name: string;
  last_name: string;
  email: string;
  expires_at: string | null;
  days_remaining: number | null;
}

export interface ForgeKeyDevice {
  id: string;
  mac_address: string;
  device_type: number | null;
  device_type_name: string | null;
  name: string;
  description: string;
  firmware_version: string;
  last_seen: string | null;
  is_online: boolean;
  is_active: boolean;
  location: number | null;
  enrollment_photo: string | null;
  last_photo: string | null;
  boot_count: number | null;
  free_heap: number | null;
  ip: string | null;
  created_at: string;
  updated_at: string;
}

export const forgekeyAPI = {
  listDevices: () =>
    api.get<{ results?: ForgeKeyDevice[] } | ForgeKeyDevice[]>('/forgekey/devices/'),
  updateDevice: (id: string, data: Partial<ForgeKeyDevice>) =>
    api.patch<ForgeKeyDevice>(`/forgekey/devices/${id}/`, data),
};

export const makerBoxesAPI = {
  list: () => api.get<{ results: MakerBox[] } | MakerBox[]>('/maker-boxes/'),
  scan: (binId: string, username: string) =>
    api.post<MakerBoxScanResult>('/maker-boxes/scan/', { bin_id: binId, username }),
  labelUrl: (id: number) => `${API_BASE_URL}/maker-boxes/${id}/label/`,
  manualLabel: (data: { username: string; first_name?: string; last_name?: string }) =>
    api.post('/maker-boxes/manual-label/', data, { responseType: 'blob' }),
  emailPickup: (id: number, email?: string) =>
    api.post<{ sent: boolean; to: string }>(`/maker-boxes/${id}/email-pickup/`, email ? { email } : {}),
};

export interface AssetWarrantyDto {
  id: string;
  asset: string;
  install_date: string;
  duration_days: number | null;
  end_date: string | null;
  provider: string;
  policy_number: string;
  notes: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export type ComplianceBucket = 'ok' | 'expiring_soon' | 'expired' | 'missing';

export interface VendorComplianceDto {
  vendor_id: string;
  vendor_name: string;
  is_active: boolean;
  tdlr_status: ComplianceBucket;
  tdlr_expires_at: string | null;
  coi_status: ComplianceBucket;
  coi_expires_at: string | null;
}

export interface AssetWoStatusDto {
  asset_id: string;
  warranty: AssetWarrantyDto | null;
  warranty_recovery_recommended: boolean;
  vendor_compliance?: VendorComplianceDto;
}

export type ThirdPartyWorkOrderStatus =
  | 'requested'
  | 'sourcing'
  | 'scheduled'
  | 'in_progress'
  | 'validated'
  | 'financial_review'
  | 'closed'
  | 'cancelled';

export type VarianceStatus = '' | 'auto_approved' | 'blocked';

export interface ThirdPartyQuoteDto {
  id: string;
  work_order: string;
  vendor: string;
  vendor_name: string;
  amount: string;
  notes: string;
  submitted_by: number | null;
  created_at: string;
}

export interface ThirdPartyAssetLinkDto {
  id: string;
  work_order: string;
  asset: string;
  asset_name?: string;
  asset_tag?: string;
  share_pct: string;
  allocated_cost: string | null;
  notes: string;
  created_at: string;
}

export interface ThirdPartyAttachmentDto {
  id: string;
  work_order: string;
  file: string;
  kind: 'invoice' | 'fsr' | 'photo' | 'quote' | 'paper_form' | 'other';
  kind_display: string;
  caption: string;
  uploaded_by: number | null;
  uploaded_by_username: string | null;
  uploaded_at: string;
}

export interface ThirdPartyWorkflowGates {
  has_nte: boolean;
  has_active_emergency_authorization: boolean;
  has_required_quotes: boolean;
  quote_count: number;
  has_photo_evidence: boolean;
  has_invoice_and_fsr: boolean;
  variance_status: VarianceStatus;
}

export interface ThirdPartyWorkOrderDto {
  id: string;
  short_id: string;
  title: string;
  asset: string | null;
  asset_name: string | null;
  vendor: string;
  vendor_name: string;
  work_type: string;
  work_type_display: string;
  is_emergency: boolean;
  status: ThirdPartyWorkOrderStatus;
  status_display: string;
  nte_amount: string | null;
  par_cost_buffer: string;
  actual_invoice_total: string | null;
  dispatch_fee: string | null;
  downtime_start: string | null;
  downtime_end: string | null;
  total_downtime: string | null;
  keyfob_id: string;
  warranty_recovery: boolean;
  variance_status: VarianceStatus;
  asset_links: ThirdPartyAssetLinkDto[];
  attachments: ThirdPartyAttachmentDto[];
  quotes: ThirdPartyQuoteDto[];
  workflow: ThirdPartyWorkflowGates;
  notes: string;
  internal_notes: string;
  created_at: string;
  updated_at: string;
  closed_at: string | null;
}

// Electrical Circuits & Network Drops (oms-tt5 / oms-a5f)
export interface BreakerListParams {
  location?: number | string;
  panel?: string;
  is_active?: boolean;
}
export interface OutletListParams {
  location?: number | string;
  breaker?: number | string;
  is_active?: boolean;
}
export interface LightSwitchListParams {
  location?: number | string;
  controls_location?: number | string;
  is_active?: boolean;
}
export interface NetworkDropListParams {
  location?: number | string;
  drop_type?: NetworkDropType;
  is_active?: boolean;
}

const electricalBase = '/electrical-circuits';

const buildOutletFormData = (data: Omit<Partial<Outlet>, 'photo'> & { photo?: File | null }) => {
  const form = new FormData();
  Object.entries(data).forEach(([key, value]) => {
    if (value === undefined) return;
    if (key === 'photo') {
      if (value instanceof File) form.append('photo', value);
      // Ignore string URLs / null on update (keeps existing image)
      return;
    }
    if (value === null) {
      form.append(key, '');
      return;
    }
    form.append(key, String(value));
  });
  return form;
};

const buildNetworkDropFormData = (data: Omit<Partial<NetworkDrop>, 'photo'> & { photo?: File | null }) => {
  const form = new FormData();
  Object.entries(data).forEach(([key, value]) => {
    if (value === undefined) return;
    if (key === 'photo') {
      if (value instanceof File) form.append('photo', value);
      return;
    }
    if (value === null) {
      form.append(key, '');
      return;
    }
    form.append(key, String(value));
  });
  return form;
};

export const electricalCircuitsAPI = {
  // Breakers
  listBreakers: (params?: BreakerListParams) =>
    api.get<{ results: Breaker[] } | Breaker[]>(`${electricalBase}/breakers/`, { params })
      .then((response) => ({ ...response, data: normalizeResults<Breaker>(response.data) })),
  getBreaker: (id: number | string) =>
    api.get<Breaker>(`${electricalBase}/breakers/${id}/`),
  createBreaker: (data: Partial<Breaker>) =>
    api.post<Breaker>(`${electricalBase}/breakers/`, data),
  updateBreaker: (id: number | string, data: Partial<Breaker>) =>
    api.patch<Breaker>(`${electricalBase}/breakers/${id}/`, data),
  deleteBreaker: (id: number | string) =>
    api.delete(`${electricalBase}/breakers/${id}/`),

  // Outlets
  listOutlets: (params?: OutletListParams) =>
    api.get<{ results: Outlet[] } | Outlet[]>(`${electricalBase}/outlets/`, { params })
      .then((response) => ({ ...response, data: normalizeResults<Outlet>(response.data) })),
  getOutlet: (id: number | string) =>
    api.get<Outlet>(`${electricalBase}/outlets/${id}/`),
  createOutlet: (data: Omit<Partial<Outlet>, 'photo'> & { photo?: File | null }) => {
    if (data.photo instanceof File) {
      return api.post<Outlet>(`${electricalBase}/outlets/`, buildOutletFormData(data), {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
    }
    return api.post<Outlet>(`${electricalBase}/outlets/`, data);
  },
  updateOutlet: (id: number | string, data: Omit<Partial<Outlet>, 'photo'> & { photo?: File | null }) => {
    if (data.photo instanceof File) {
      return api.patch<Outlet>(`${electricalBase}/outlets/${id}/`, buildOutletFormData(data), {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
    }
    return api.patch<Outlet>(`${electricalBase}/outlets/${id}/`, data);
  },
  deleteOutlet: (id: number | string) =>
    api.delete(`${electricalBase}/outlets/${id}/`),

  // Light switches
  listLightSwitches: (params?: LightSwitchListParams) =>
    api.get<{ results: LightSwitch[] } | LightSwitch[]>(`${electricalBase}/light-switches/`, { params })
      .then((response) => ({ ...response, data: normalizeResults<LightSwitch>(response.data) })),
  getLightSwitch: (id: number | string) =>
    api.get<LightSwitch>(`${electricalBase}/light-switches/${id}/`),
  createLightSwitch: (data: Partial<LightSwitch>) =>
    api.post<LightSwitch>(`${electricalBase}/light-switches/`, data),
  updateLightSwitch: (id: number | string, data: Partial<LightSwitch>) =>
    api.patch<LightSwitch>(`${electricalBase}/light-switches/${id}/`, data),
  deleteLightSwitch: (id: number | string) =>
    api.delete(`${electricalBase}/light-switches/${id}/`),

  // Network drops
  listNetworkDrops: (params?: NetworkDropListParams) =>
    api.get<{ results: NetworkDrop[] } | NetworkDrop[]>(`${electricalBase}/network-drops/`, { params })
      .then((response) => ({ ...response, data: normalizeResults<NetworkDrop>(response.data) })),
  getNetworkDrop: (id: number | string) =>
    api.get<NetworkDrop>(`${electricalBase}/network-drops/${id}/`),
  createNetworkDrop: (data: Omit<Partial<NetworkDrop>, 'photo'> & { photo?: File | null }) => {
    if (data.photo instanceof File) {
      return api.post<NetworkDrop>(`${electricalBase}/network-drops/`, buildNetworkDropFormData(data), {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
    }
    return api.post<NetworkDrop>(`${electricalBase}/network-drops/`, data);
  },
  updateNetworkDrop: (id: number | string, data: Omit<Partial<NetworkDrop>, 'photo'> & { photo?: File | null }) => {
    if (data.photo instanceof File) {
      return api.patch<NetworkDrop>(`${electricalBase}/network-drops/${id}/`, buildNetworkDropFormData(data), {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
    }
    return api.patch<NetworkDrop>(`${electricalBase}/network-drops/${id}/`, data);
  },
  deleteNetworkDrop: (id: number | string) =>
    api.delete(`${electricalBase}/network-drops/${id}/`),
};

export const OUTLET_TYPE_OPTIONS = [
  { value: 'standard', label: 'Standard 120V' },
  { value: '240v', label: '240V' },
  { value: 'nema_5_15', label: 'NEMA 5-15 (120V 15A)' },
  { value: 'nema_5_20', label: 'NEMA 5-20 (120V 20A)' },
  { value: 'nema_6_15', label: 'NEMA 6-15 (240V 15A)' },
  { value: 'nema_6_20', label: 'NEMA 6-20 (240V 20A)' },
  { value: 'nema_l6_30', label: 'NEMA L6-30 (240V 30A locking)' },
  { value: 'nema_14_30', label: 'NEMA 14-30 (240V 30A)' },
  { value: 'nema_14_50', label: 'NEMA 14-50 (240V 50A)' },
  { value: 'usb', label: 'USB charging' },
  { value: 'other', label: 'Other' },
];

export const NETWORK_DROP_TYPE_OPTIONS = [
  { value: 'data', label: 'Data jack' },
  { value: 'voice', label: 'Voice / phone' },
  { value: 'patch_panel', label: 'Patch panel termination' },
  { value: 'ap', label: 'Wireless access point' },
  { value: 'camera', label: 'Camera' },
  { value: 'iot', label: 'IoT sensor' },
  { value: 'other', label: 'Other' },
];

export const thirdPartyMaintenanceAPI = {
  getAssetWoStatus: (assetId: string, vendorId?: string) =>
    api.get<AssetWoStatusDto>(
      `/maintenance-orders/assets/${assetId}/wo-status/`,
      { params: vendorId ? { vendor: vendorId } : {} }
    ),
  retrieve: (id: string) =>
    api.get<ThirdPartyWorkOrderDto>(`/maintenance-orders/work-orders/${id}/`),
  setNte: (id: string, amount: string) =>
    api.post<ThirdPartyWorkOrderDto>(
      `/maintenance-orders/work-orders/${id}/set-nte/`,
      { nte_amount: amount }
    ),
  authorizeEmergency: (id: string, reason: string) =>
    api.post(`/maintenance-orders/work-orders/${id}/authorize-emergency/`, {
      reason,
    }),
  advanceToSourcing: (id: string) =>
    api.post<ThirdPartyWorkOrderDto>(
      `/maintenance-orders/work-orders/${id}/advance-to-sourcing/`
    ),
  waiveQuoteRequirement: (id: string, reason: string) =>
    api.post<ThirdPartyWorkOrderDto>(
      `/maintenance-orders/work-orders/${id}/waive-quote-requirement/`,
      { reason }
    ),
  advanceToScheduled: (id: string) =>
    api.post<ThirdPartyWorkOrderDto>(
      `/maintenance-orders/work-orders/${id}/advance-to-scheduled/`
    ),
  vendorArrived: (id: string, payload: { keyfob_id?: string; shadow_user?: string }) =>
    api.post<ThirdPartyWorkOrderDto>(
      `/maintenance-orders/work-orders/${id}/vendor-arrived/`,
      payload
    ),
  signOff: (id: string) =>
    api.post<ThirdPartyWorkOrderDto>(
      `/maintenance-orders/work-orders/${id}/sign-off/`
    ),
  advanceToFinancialReview: (
    id: string,
    payload: { actual_invoice_total: string; dispatch_fee?: string }
  ) =>
    api.post<ThirdPartyWorkOrderDto>(
      `/maintenance-orders/work-orders/${id}/advance-to-financial-review/`,
      payload
    ),
  close: (id: string) =>
    api.post<ThirdPartyWorkOrderDto>(
      `/maintenance-orders/work-orders/${id}/close/`
    ),
  addQuote: (payload: {
    work_order: string;
    vendor: string;
    amount: string;
    notes?: string;
  }) => api.post<ThirdPartyQuoteDto>('/maintenance-orders/quotes/', payload),
};

export default api;
