/**
 * API service for communicating with the Django backend
 */
import axios from 'axios';
import { ActiveMaintenanceRow, Asset, AssetCostRecoveryReport, AssetDocument, AssetMeter, AssetMeterReading, AssetPart, AssetProblem, AssetProblemPhoto, AssetProblemsData, AssignSlotRequest, Breaker, Category, ChangePasswordRequest, Checklist, ChecklistCompletion, CheckMaterialStockResponse, CreateReorderRequest, DashboardWidget, DeliveriesData, Disposition, DonationItem, Fixture, FixtureRefillRequest, GenerateRackRequest, GenerateRackResult, InventoryItem, InventoryItemMetrics, ItemCountMode, ItemOnHandDisplay, ItemPurchaseHistory, ItemSupplier, Kit, KitSummary, KitSupplierTerms, KioskPayload, LightSwitch, Location, LocationProblem, LogUsageRequest, LogUsageResponse, LowStockData, MaintenanceItem, MaintenanceLog, MaintenanceMaterial, MaintenanceTask, MaintenanceTool, NetworkDrop, NetworkDropType, NotificationPreferences, Outlet, PendingReordersData, ProjectStorageStatus, ProjectStorageStint, QRScansData, RecentSearch, ReorderRequest, ResilienceStatus, Screen, ScreenContentBlock, ScreenStatusEntry, SearchResult, SIG, SIGMember, SiteSettings, StockHistory, StorageAssignment, StorageAssignmentType, StorageOverview, StorageSlot, StorageSlotCardPreview, Supplier, SupplierAgreement, SupplierDetail, SystemMessage, TaxReceipt, UsageLog, UserProfile, Webhook, WebhookTestResult, WorkOrder, WorkOrderAdHocMaterialInput, WorkOrderAdHocToolInput, WorkOrderLotoCompletion, WorkOrderMaterialUsage, WorkOrderPhoto, WorkOrderTaskCompletion, WorkOrderToolRow, WorkOrderUploadResult } from '../types';

/**
 * Resolves the API base URL based on environment.
 * - If REACT_APP_API_URL is set, uses that (for production/docker)
 * - If on localhost and not set, uses direct Django backend URL
 * - Otherwise, uses relative URL for nginx forwarding
 */
export const resolveApiBaseUrl = () => {
  const rawBase = import.meta.env.VITE_API_URL;

  // If VITE_API_URL is explicitly set, use it (handles production and docker dev)
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

// Notify the SessionExpiredBanner (mounted in the layout) so the user gets
// an actionable recovery prompt with their attempted route preserved.
// Throwing on a missing window keeps this safe under SSR/jsdom.
const notifySessionExpired = () => {
  if (typeof window === 'undefined' || typeof window.dispatchEvent !== 'function') {
    return;
  }
  try {
    const detail = { pathname: window.location?.pathname };
    window.dispatchEvent(new CustomEvent('oms:session-expired', { detail }));
  } catch {
    // Older browsers without CustomEvent support — fall back to a plain Event.
    try {
      window.dispatchEvent(new Event('oms:session-expired'));
    } catch {
      // Nothing else we can do; the API call still rejects below.
    }
  }
};

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
    // - Endpoints that run NO authentication at all, so a stale token cannot
    //   produce the 401 a refresh would fix.
    //
    // `/reorders/purchase-orders/` and `/reorders/analytics/transparency/` were
    // on this list under the justification "public endpoints that don't require
    // authentication", and op-anonymous-read-posture made that untrue of both.
    // Purchase orders are IsAuthenticated on every action now. Transparency is
    // still AllowAny, but it lost `authentication_classes=[]` — which it had to,
    // because that setting made request.user AnonymousUser for a SIGNED-IN
    // caller and no vendor gate could tell the two apart. It therefore runs
    // CSRFExemptJWTAuthentication, an expired Bearer raises InvalidToken, and
    // DRF answers 401 before AllowAny is ever consulted. Skipping the refresh
    // left a member who had left the tab open staring at "Unable to load
    // transparency data" on a page that is public. `logistics_dashboard` keeps
    // `authentication_classes=[]` and so genuinely cannot 401 on a stale token.
    const isRefreshEndpoint = originalRequest?.url?.includes('/auth/refresh/');
    const isPublicEndpoint = originalRequest?.url?.includes('/reorders/analytics/logistics_dashboard/');

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
        notifySessionExpired();
        return Promise.reject(error);
      }

      try {
        // Use a direct axios call to avoid triggering the interceptor again
        const refreshResponse = await axios.post(`${API_BASE_URL}/auth/refresh/`, { refresh: refreshToken }, {
          headers: {
            'Content-Type': 'application/json',
          },
          // Bypassing the `api` instance also bypasses its `withCredentials`,
          // and this is the one request that must not lose it: the backend
          // slides the Django session cookie /media/ runs on ONLY when the
          // refresh presents it (auth_views._renew_session_from_token). A
          // cross-origin XHR without credentials sends no cookie, so on
          // localhost and in any split deployment the slide silently became a
          // no-op and every gated download 403'd from day 15.
          withCredentials: true,
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
        notifySessionExpired();
        // Return the original error if refresh failed, as it's more informative
        return Promise.reject(error);
      }
    }

    return Promise.reject(error);
  }
);

// Inventory API
export const inventoryAPI = {
  getItem: (id: string) =>
    api.get<InventoryItem>(`/inventory/items/${id}/`),

  getItemMetrics: (id: string) =>
    api.get<InventoryItemMetrics>(`/inventory/items/${id}/metrics/`),

  // Stock-history endpoint (op-izy5 backend) — weekly snapshots + cycle counts +
  // reorder-event dates + reorder/desired thresholds. Note the underscore.
  getStockHistory: (id: string) =>
    api.get<StockHistory>(`/inventory/items/${id}/stock_history/`),

  // Purchase/receipt provenance (op-96uo backend) — per-order unit costs plus
  // one row per delivery (tracking numbers). Auth-required; note the
  // underscore, as with stock_history.
  getPurchaseHistory: (id: string) =>
    api.get<ItemPurchaseHistory>(`/inventory/items/${id}/purchase_history/`),

  getItemSuppliers: (itemId: string) =>
    api.get<{ results: ItemSupplier[] }>(`/inventory/item-suppliers/?item_id=${itemId}`),
  markItemSupplierDiscontinued: (itemSupplierId: string) =>
    api.post(`/inventory/item-suppliers/${itemSupplierId}/mark_discontinued/`),

  // Item-supplier links are written one row at a time against the same
  // `item-suppliers` ModelViewSet the list above reads: there is no bulk route,
  // and none is needed — saving a row with `is_primary` set demotes the item's
  // other primaries server-side, so "make this one primary" is a single request.
  createItemSupplier: (data: ItemSupplierWritePayload) =>
    api.post<ItemSupplier>('/inventory/item-suppliers/', data),

  // PATCH, never PUT: the inventory item form offers only part of ItemSupplier
  // (no UPCs, package dimensions, notes or the active/discontinued flags), and
  // a full replace would blank everything it does not show.
  updateItemSupplier: (itemSupplierId: number, data: Partial<ItemSupplierWritePayload>) =>
    api.patch<ItemSupplier>(`/inventory/item-suppliers/${itemSupplierId}/`, data),

  deleteItemSupplier: (itemSupplierId: number) =>
    api.delete(`/inventory/item-suppliers/${itemSupplierId}/`),

  listItems: (params?: {
    category?: number;
    location?: number;
    search?: string;
    low_stock?: boolean;
    owning_group?: number;
    is_active?: boolean;
    // Retired items with stock are always listed; retired-and-empty items are
    // hidden by default. Pass include_retired=true to surface them (op-jv7r).
    include_retired?: boolean;
    // Kits (op-8n0) are InventoryItem rows with is_kit=true and are EXCLUDED
    // from this endpoint by default, because receiving one credits its
    // components rather than the kit. Pass include_kits to get both, or
    // is_kit to filter to one side. Mirrors include_retired above.
    include_kits?: boolean;
    is_kit?: boolean;
    ordering?: string;
    page?: number;
    page_size?: number;
  }) =>
    api.get<{ count: number; next: string | null; previous: string | null; results: InventoryItem[] }>(
      '/inventory/items/',
      { params }
    ),

  /** Kits that supply this component — reorder context, public read (AC-15). */
  getItemKits: (id: string) =>
    api.get<KitSummary[]>(`/inventory/items/${id}/kits/`),

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

  // Retire / un-retire an item (op-jv7r). Retiring phases the item out: it is
  // never flagged for reorder and auto-hides from the list once stock hits 0.
  // Both actions return the refreshed item payload (with is_retired/retired_at).
  retireItem: (id: string) =>
    api.post<InventoryItem>(`/inventory/items/${id}/retire/`),

  unretireItem: (id: string) =>
    api.post<InventoryItem>(`/inventory/items/${id}/unretire/`),

  logUsage: (id: string, body: LogUsageRequest) =>
    api.post<LogUsageResponse>(`/inventory/items/${id}/log_usage/`, body),

  // Cycle count (op-c7y4): count physical qty for a single item, reconcile
  // system on-hand through the shared reconciliation helper, and stamp
  // last_counted_at. Returns the fresh stock snapshot + audit row.
  cycleCount: (itemId: string, payload: CycleCountPayload) =>
    api.post<CycleCountResponse>(`/inventory/items/${itemId}/cycle-count/`, payload),

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

  createItem: (data: FormData | Partial<InventoryItem> | InventoryItemPackagingPayload) => {
    if (data instanceof FormData) {
      return api.post<InventoryItem>('/inventory/items/', data, {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      });
    }
    return api.post<InventoryItem>('/inventory/items/', data);
  },

  updateItem: (
    id: string,
    data: FormData | Partial<InventoryItem> | InventoryItemPackagingPayload
  ) => {
    if (data instanceof FormData) {
      return api.patch<InventoryItem>(`/inventory/items/${id}/`, data, {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      });
    }
    return api.patch<InventoryItem>(`/inventory/items/${id}/`, data);
  },

  // Open a sealed pack / finish the open one, for `open_closed` items (op-ev14).
  // Opening IS consumption under that mode — stock drops by the pack's base
  // units, the open tally rises, and a usage log is written; finishing only
  // clears the open tally. Anything else (a non-open_closed item, no sealed pack
  // left, no open pack) is a 400.
  packContainer: (itemId: string, transition: PackTransition, notes?: string) =>
    api.post<PackContainerResponse>(`/inventory/items/${itemId}/pack-container/`, {
      transition,
      ...(notes ? { notes } : {}),
    }),

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
    | 'vision_supply_check'
    | 'other';
  notes?: string;
  skip_reorder?: boolean;
}

// Cycle count (op-c7y4). Reuses the reconciliation reason codes; the user-
// facing form omits the system-only `vision_supply_check` reason.
export interface CycleCountPayload {
  counted_qty: number;
  reason: ReconciliationRow['reason'];
  skip_reorder?: boolean;
  notes?: string;
  // op-ev14, opt-in: read `counted_qty` as a count of whole `count_level` packs
  // instead of base units. Omitting it keeps the base-unit reading every
  // each-mode caller depends on; sending it for an item that is not counted in
  // packs is a 400. `open_count` sets an `open_closed` item's open-container
  // tally in the same reconciliation.
  at_level?: boolean;
  open_count?: number;
}

export interface CycleCountResponse {
  id: string;
  current_stock: number;
  last_counted_at: string | null;
  days_since_last_count: number | null;
  // op-ev14 echoes the unit the count was read in plus the refreshed on-hand.
  counted_unit?: string;
  on_hand_display?: ItemOnHandDisplay;
  reconciliation: {
    id: number;
    projected_count: number;
    actual_count: number;
    delta: number;
    reason: string;
    reconciled_at: string;
    reconciled_by: number;
  };
}

// ---- Packaging matrix write payloads (op-hzji/op-ev14 backend, op-lkxl web) ----

/**
 * One rung of the nested-writable `packaging_levels` chain. The pk is
 * deliberately absent: the serializer upserts rungs on `(item, sort_order)`, so
 * position carries identity and a kept rung keeps its pk (and any `count_level`
 * FK pointing at it).
 */
export interface PackagingLevelInput {
  name: string;
  sort_order: number;
  base_units: number;
}

/**
 * The packaging half of an item write. Sent as JSON rather than folded into the
 * form's multipart body because `packaging_levels` is a nested list, and
 * `count_level` is a pk that can only be resolved once the chain exists.
 */
export interface InventoryItemPackagingPayload {
  base_unit?: string;
  count_mode?: ItemCountMode;
  count_level?: number | null;
  packaging_levels?: PackagingLevelInput[];
  /** Set on-hand as a count of whole `count_level` packs; not with current_stock. */
  current_stock_at_level?: number;
}

/**
 * The half of `ItemSupplier` the inventory item form's relationship editor
 * offers. `item` is sent on create only — an existing row never changes item.
 */
export interface ItemSupplierWritePayload {
  item?: string;
  supplier: number;
  supplier_sku: string;
  supplier_url: string;
  unit_cost: string | null;
  package_cost: string | null;
  quantity_per_package: number;
  average_lead_time: number;
  is_primary: boolean;
}

export type PackTransition = 'open' | 'finish';

export interface PackContainerResponse {
  transition: PackTransition;
  id: string;
  current_stock: number;
  open_container_count: number;
  on_hand_display: ItemOnHandDisplay;
  usage_log: UsageLog | null;
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

// Result of a bulk cost-recovery flag set. `matched` is the size of the
// category; `updated` counts only the assets whose flag actually flipped.
export interface CostRecoverableBulkResult {
  category_id: number;
  category_slug: string;
  category_name: string;
  is_cost_recoverable: boolean;
  matched: number;
  updated: number;
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
    // Restrict to assets that use this inventory item as a consumable/part
    // (AssetPart through-model) — scopes the serialized-component install
    // picker to compatible assets only (op-sk0s).
    consumable_for_item?: string;
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

  // Bulk-set the landlord cost-recovery flag on a whole asset category in one
  // request (staff only) — the alternative is one PATCH per asset. `category`
  // takes the Category PK or its slug; omitting `isCostRecoverable` flags them.
  setCostRecoverableByCategory: (
    category: number | string,
    isCostRecoverable: boolean = true,
  ) =>
    api.post<CostRecoverableBulkResult>(
      '/inventory/assets/set_cost_recoverable_by_category/',
      { category, is_cost_recoverable: isCostRecoverable },
    ),

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

  // `partIds` are AssetPart ids the reporter flagged as needing replace/fix.
  // Only sent when non-empty so description-only reports keep the same payload.
  reportProblem: (id: string, description: string, partIds?: string[]) =>
    api.post<AssetProblem>(`/inventory/assets/${id}/report_problem/`, {
      description,
      ...(partIds && partIds.length > 0 ? { part_ids: partIds } : {}),
    }),

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

  getMaintenanceHistory: (assetId: string, params?: {
    since?: string;
    until?: string;
    source?: 'all' | 'historical' | 'workorder';
  }) =>
    api.get<MaintenanceHistoryResponse>(
      `/inventory/assets/${assetId}/maintenance-history/`,
      { params },
    ),
};

// Asset reservations + out-of-service surfaces — staff + SIG admin
// gated on write. The e-paper renderer reads these to pick which face
// to show on a bound panel (see backend/forgekey/services/epaper_render.py).
export interface AssetReservation {
  id: string;
  asset: string;
  asset_name: string;
  title: string;
  reserved_by: number;
  reserved_by_username: string;
  starts_at: string;
  ends_at: string;
  notes: string;
  cancelled_at: string | null;
  is_active: boolean;
  is_current: boolean;
  created_at: string;
  updated_at: string;
}

export interface AssetOutOfService {
  id: string;
  asset: string;
  asset_name: string;
  placed_out_at: string;
  placed_by: number;
  placed_by_username: string;
  expected_return_at: string | null;
  reason: string;
  restored_at: string | null;
  restored_by: number | null;
  restored_by_username: string | null;
  is_open: boolean;
  created_at: string;
  updated_at: string;
}

export const assetReservationsAPI = {
  list: (params?: { asset?: string; active?: boolean; current?: boolean }) =>
    api.get<{
      count: number;
      next: string | null;
      previous: string | null;
      results: AssetReservation[];
    }>('/inventory/asset-reservations/', { params }),

  create: (data: {
    asset: string;
    title: string;
    starts_at: string;
    ends_at: string;
    notes?: string;
  }) => api.post<AssetReservation>('/inventory/asset-reservations/', data),

  // DELETE soft-cancels (sets cancelled_at server-side); returns 200
  // with the updated row so callers can flip their cached state
  // without a follow-up fetch.
  cancel: (id: string) =>
    api.delete<AssetReservation>(`/inventory/asset-reservations/${id}/`),
};

export const assetOutOfServiceAPI = {
  list: (params?: { asset?: string; open?: boolean }) =>
    api.get<{
      count: number;
      next: string | null;
      previous: string | null;
      results: AssetOutOfService[];
    }>('/inventory/asset-out-of-service/', { params }),

  open: (data: {
    asset: string;
    reason: string;
    expected_return_at?: string | null;
  }) => api.post<AssetOutOfService>('/inventory/asset-out-of-service/', data),

  // POST .../restore/ closes the OOS row.
  restore: (id: string) =>
    api.post<AssetOutOfService>(`/inventory/asset-out-of-service/${id}/restore/`),
};

// ── Serialized components (#818/#819) ──────────────────────────────────────
// Individual serial-numbered units of a serialized InventoryItem, tracked
// through a lifecycle branched on the item's tracking mode. See
// SerializedComponentViewSet + ComponentUsageEventViewSet + serialized_forecast.

export type SerializedTrackingMode = 'consumable' | 'reusable';

export type SerializedComponentStatus =
  | 'received'
  | 'in_stock'
  | 'installed'
  | 'removed'
  | 'consumed'
  | 'retired'
  | 'disposed';

export type SerializedComponentAction =
  | 'receive'
  | 'install'
  | 'remove'
  | 'consume'
  | 'retire'
  | 'dispose';

export interface ComponentUsageEvent {
  id: string;
  component: string;
  component_serial: string;
  component_item_name: string;
  asset: string | null;
  asset_name: string | null;
  action: SerializedComponentAction;
  action_display: string;
  at: string;
  actor: number | null;
  actor_username: string | null;
  notes: string;
  created_at: string;
}

export interface SerializedComponent {
  id: string;
  item: string;
  item_name: string;
  item_sku: string;
  serial_number: string;
  lot: string;
  // Optional shelf-life date (record/display only — no forecast/alert effect).
  // ISO calendar date "YYYY-MM-DD" as returned by the DRF DateField, or null.
  expiration_date: string | null;
  status: SerializedComponentStatus;
  status_display: string;
  tracking_mode: SerializedTrackingMode;
  available_actions: SerializedComponentAction[];
  installed_in_asset: string | null;
  installed_in_asset_name: string | null;
  received_at: string | null;
  installed_at: string | null;
  disposed_at: string | null;
  provenance_delivery_item: number | null;
  provenance_purchase_order_item: number | null;
  disposal_reason: string;
  created_at: string;
  updated_at: string;
}

// A lifecycle action returns the updated unit plus the event it logged.
export interface SerializedComponentActionResult extends SerializedComponent {
  event: ComponentUsageEvent;
}

// scan_receive returns the (created-or-existing) unit plus a `created` flag:
// true when this scan minted+received a new unit (HTTP 201), false on an
// idempotent re-scan of the same (item, serial_number) pair (HTTP 200).
export interface SerializedComponentScanResult extends SerializedComponent {
  created: boolean;
}

interface Paginated<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

export const serializedComponentsAPI = {
  list: (params?: {
    item?: string;
    status?: SerializedComponentStatus;
    installed_in_asset?: string;
  }) =>
    api.get<Paginated<SerializedComponent>>('/inventory/serialized-components/', {
      params,
    }),

  get: (id: string) =>
    api.get<SerializedComponent>(`/inventory/serialized-components/${id}/`),

  create: (data: {
    item: string;
    serial_number: string;
    lot?: string;
    expiration_date?: string | null;
    provenance_delivery_item?: number | null;
    provenance_purchase_order_item?: number | null;
  }) => api.post<SerializedComponent>('/inventory/serialized-components/', data),

  // Scan-driven batch receive: idempotent get-or-create-and-receive by
  // (item, serial_number). A first scan mints the unit and applies `receive`
  // (201 `created:true`); a re-scan resolves to the existing unit without a
  // unique-constraint 400 (200 `created:false`). Drives the web + ScanTTY
  // batch-scan surfaces.
  scanReceive: (data: {
    item: string;
    serial_number: string;
    lot?: string;
    expiration_date?: string | null;
  }) =>
    api.post<SerializedComponentScanResult>(
      '/inventory/serialized-components/scan_receive/',
      data,
    ),

  // Lifecycle actions — each returns the updated unit plus the logged event.
  receive: (id: string, data?: { notes?: string }) =>
    api.post<SerializedComponentActionResult>(
      `/inventory/serialized-components/${id}/receive/`,
      data ?? {},
    ),
  install: (id: string, data: { asset: string; notes?: string }) =>
    api.post<SerializedComponentActionResult>(
      `/inventory/serialized-components/${id}/install/`,
      data,
    ),
  remove: (id: string, data?: { notes?: string }) =>
    api.post<SerializedComponentActionResult>(
      `/inventory/serialized-components/${id}/remove/`,
      data ?? {},
    ),
  consume: (id: string, data?: { notes?: string }) =>
    api.post<SerializedComponentActionResult>(
      `/inventory/serialized-components/${id}/consume/`,
      data ?? {},
    ),
  retire: (id: string, data?: { notes?: string }) =>
    api.post<SerializedComponentActionResult>(
      `/inventory/serialized-components/${id}/retire/`,
      data ?? {},
    ),
  dispose: (id: string, data: { disposal_reason: string; notes?: string }) =>
    api.post<SerializedComponentActionResult>(
      `/inventory/serialized-components/${id}/dispose/`,
      data,
    ),

  // Usage/audit log — filter by component (a unit's history) or asset (every
  // serial a given machine has used).
  listUsageEvents: (params: { component?: string; asset?: string }) =>
    api.get<Paginated<ComponentUsageEvent>>('/inventory/component-usage-events/', {
      params,
    }),
};

// Which supplier the `lead_time_days` on a forecast row was read from — see
// `SerializedForecastRow.lead_time_basis`. Mirrors the backend constants in
// `inventory/services/component_forecast.py`.
export type SerializedForecastLeadBasis =
  | 'orderable_supplier'
  | 'unorderable_supplier'
  | 'no_supplier';

// One row per active serialized item from the consumption forecast /
// low-stock report (InventoryReportViewSet.serialized_forecast).
export interface SerializedForecastRow {
  item_id: string;
  item_name: string;
  sku: string;
  category_name: string | null;
  serial_tracking_mode: SerializedTrackingMode;
  // Serialized stock split. `available` (= on_hand − installed) is what drives
  // the reorder math; `on_hand` counts every physically-present unit including
  // those currently installed in an asset. `available_stock` is a
  // backward-compatible alias of `on_hand`.
  available: number;
  on_hand: number;
  installed: number;
  available_stock: number;
  current_stock: number;
  window_days: number;
  units_depleted_in_window: number;
  avg_daily_use: number;
  days_until_stockout: number | null;
  projected_stockout_date: string | null;
  lead_time_days: number | null;
  // Whether `reorder_point` includes a lead-time component at all (op-c1ke).
  // `false` — only for an item carrying no supplier link — means the number
  // below is the safety stock ALONE, a lower bound rather than the classic
  // `avg_daily_use × lead_time + safety_stock` reorder point. Label it as
  // incomplete rather than quoting it as a horizon.
  lead_time_known: boolean;
  // WHOSE wait `lead_time_days` describes (op-3vqk). Three states, and they
  // must not be collapsed into two:
  //   'orderable_supplier'   — the supplier we would actually buy from. The
  //                            reorder point is a horizon we can order against.
  //   'unorderable_supplier' — every supplier link is inactive or discontinued.
  //                            The number is REAL (that vendor did take this
  //                            long) but nobody can buy from them. The row is
  //                            NOT incomplete — it keeps its full lead
  //                            component and its flag — so say "unbuyable
  //                            supplier", never "unknown".
  //   'no_supplier'          — no link at all, so nothing is on record. This is
  //                            the only basis on which `lead_time_known` is
  //                            false.
  lead_time_basis: SerializedForecastLeadBasis;
  safety_stock: number;
  reorder_point: number;
  needs_reorder: boolean;
}

// How a demand forecast was produced. `restock_interval` means the item had
// enough purchase history to average a buying cadence from; when it did not,
// the row is `insufficient_history` and carries no cadence at all. The other
// three are the retired v1 usage-rate methods — historical rows only, kept so
// pre-v2 rows still resolve a display label.
export type DemandForecastMethod =
  | 'restock_interval'
  | 'insufficient_history'
  | 'prophet'
  | 'holtwinters'
  | 'fallback';

// One stored forecast row (the latest run) for a non-serialized item, from
// InventoryReportViewSet.demand_forecast / .reorder_alerts (op-1 storage,
// op-2 nightly engine, v2 restock-interval model). Every value is a snapshot
// taken at generation time — later purchases never rewrite a row. Rows only
// exist once the nightly task has run; both endpoints return [] until then.
export interface DemandForecastRow {
  // The forecast row's own id; `item` is the inventory item's UUID.
  id: number;
  item: string;
  item_name: string;
  sku: string;
  category_name: string | null;
  generated_at: string;

  // --- restock-interval signal (v2) — how often this item actually gets
  // bought, and therefore when it is due to be bought again. All of these are
  // null/0 on an `insufficient_history` row (under two purchase events there
  // is no gap to average), so never render them as a number without checking.
  avg_interval_days: number | null;
  interval_samples: number;
  last_restock_date: string | null;
  predicted_next_reorder_date: string | null;
  // Days from generation to the predicted date; negative when overdue.
  days_until_due: number | null;

  // --- retired v1 usage-rate projection. The backend still emits these keys,
  // written 0/null on every current row, so they stay typed but are not
  // rendered anywhere. Do not build new UI on them.
  horizon_days: number;
  predicted_daily_demand: number;
  horizon_demand: number;
  horizon_demand_upper: number;
  days_until_stockout: number | null;
  projected_stockout_date: string | null;
  predictive_reorder_point: number;
  safety_stock: number;

  // --- decision + provenance (both generations). `available_at_generation` is
  // informational under the interval model — it does not drive needs_reorder.
  available_at_generation: number;
  needs_reorder: boolean;
  lead_time_days: number | null;
  method: DemandForecastMethod;
  model_version: string;
}

// Asset Maintenance History (oms-0xxlp2)
export interface MaintenanceHistoryRow {
  id: string;
  source: 'historical' | 'workorder';
  title: string;
  description: string;
  completed_on: string;
  performed_by: {
    vendor: { id: string; name: string } | null;
    internal_user: { id: number; username: string } | null;
  };
  cost: string | null;
  invoice_number: string;
  notes: string;
  attachment_url: string | null;
  detail_url: string | null;
}

export interface MaintenanceHistoryResponse {
  count: number;
  total_cost: string;
  results: MaintenanceHistoryRow[];
}

export interface MaintenanceRecord {
  id: string;
  asset: string;
  asset_name: string;
  title: string;
  description: string;
  completed_on: string;
  vendor: string | null;
  vendor_name: string | null;
  performed_by_internal: number | null;
  performed_by_internal_username: string | null;
  cost: string | null;
  invoice_number: string;
  attachment: string | null;
  attachment_url: string | null;
  notes: string;
  recorded_by: number | null;
  recorded_by_username: string | null;
  recorded_at: string;
  updated_at: string;
}

export interface MaintenanceRecordCreatePayload {
  asset: string;
  title: string;
  description: string;
  completed_on: string;
  vendor?: string | null;
  performed_by_internal?: number | null;
  cost?: string | null;
  invoice_number?: string;
  notes?: string;
  attachment?: File | null;
}

export const maintenanceRecordsAPI = {
  list: (params?: { asset?: string; vendor?: string; since?: string; until?: string }) =>
    api.get<{ count: number; results: MaintenanceRecord[] } | MaintenanceRecord[]>(
      '/inventory/maintenance-records/',
      { params },
    ),

  get: (id: string) =>
    api.get<MaintenanceRecord>(`/inventory/maintenance-records/${id}/`),

  create: (payload: MaintenanceRecordCreatePayload) => {
    if (payload.attachment) {
      const form = new FormData();
      form.append('asset', payload.asset);
      form.append('title', payload.title);
      form.append('description', payload.description);
      form.append('completed_on', payload.completed_on);
      if (payload.vendor) form.append('vendor', payload.vendor);
      if (payload.performed_by_internal != null) {
        form.append('performed_by_internal', String(payload.performed_by_internal));
      }
      if (payload.cost != null && payload.cost !== '') form.append('cost', payload.cost);
      if (payload.invoice_number) form.append('invoice_number', payload.invoice_number);
      if (payload.notes) form.append('notes', payload.notes);
      form.append('attachment', payload.attachment);
      return api.post<MaintenanceRecord>('/inventory/maintenance-records/', form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
    }
    return api.post<MaintenanceRecord>('/inventory/maintenance-records/', payload);
  },

  update: (id: string, payload: Partial<MaintenanceRecordCreatePayload>) => {
    // FileField needs multipart/form-data; everything else can stay JSON.
    if (payload.attachment instanceof File) {
      const form = new FormData();
      if (payload.asset !== undefined) form.append('asset', payload.asset);
      if (payload.title !== undefined) form.append('title', payload.title);
      if (payload.description !== undefined) form.append('description', payload.description);
      if (payload.completed_on !== undefined) form.append('completed_on', payload.completed_on);
      if (payload.vendor !== undefined && payload.vendor !== null) {
        form.append('vendor', payload.vendor);
      }
      if (payload.performed_by_internal !== undefined && payload.performed_by_internal !== null) {
        form.append('performed_by_internal', String(payload.performed_by_internal));
      }
      if (payload.cost !== undefined && payload.cost !== null && payload.cost !== '') {
        form.append('cost', payload.cost);
      }
      if (payload.invoice_number !== undefined) form.append('invoice_number', payload.invoice_number);
      if (payload.notes !== undefined) form.append('notes', payload.notes);
      form.append('attachment', payload.attachment);
      return api.patch<MaintenanceRecord>(`/inventory/maintenance-records/${id}/`, form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
    }
    return api.patch<MaintenanceRecord>(`/inventory/maintenance-records/${id}/`, payload);
  },

  delete: (id: string) =>
    api.delete(`/inventory/maintenance-records/${id}/`),
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

  // Promote a report to real work. Unlike the LocationProblem sibling there is
  // no MaintenanceItem picker — a corrective work order anchors straight to the
  // problem's asset. All three actions return the full updated AssetProblem so
  // the caller can patch its row without a follow-up GET.
  promoteStandard: (id: string) =>
    api.post<AssetProblem>(`/inventory/asset-problems/${id}/promote-standard/`, {}),

  promoteThirdParty: (id: string, payload: {
    vendor: string;
    title: string;
    work_type?: string;
  }) =>
    api.post<AssetProblem>(
      `/inventory/asset-problems/${id}/promote-third-party/`,
      payload,
    ),

  resolve: (id: string, payload: {
    status?: 'resolved' | 'closed';
    resolution_notes?: string;
  }) =>
    api.post<AssetProblem>(`/inventory/asset-problems/${id}/resolve/`, payload),
};

// Asset Document Library API (per-asset manuals / CAD / wiring / cut-ready files)
export const assetDocumentsAPI = {
  list: (params?: { asset?: string; category?: string; is_current?: boolean }) =>
    api.get<{ count: number; next: string | null; previous: string | null; results: AssetDocument[] }>(
      '/inventory/asset-documents/',
      { params },
    ),

  upload: (payload: {
    asset: string;
    file: File;
    title: string;
    category: string;
    description?: string;
    supersedes?: string;
  }) => {
    const formData = new FormData();
    formData.append('asset', payload.asset);
    formData.append('file', payload.file);
    formData.append('title', payload.title);
    formData.append('category', payload.category);
    if (payload.description) {
      formData.append('description', payload.description);
    }
    if (payload.supersedes) {
      formData.append('supersedes', payload.supersedes);
    }
    return api.post<AssetDocument>('/inventory/asset-documents/', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
  },

  supersede: (
    id: string,
    payload: { file: File; title?: string; category?: string; description?: string },
  ) => {
    const formData = new FormData();
    formData.append('file', payload.file);
    if (payload.title) {
      formData.append('title', payload.title);
    }
    if (payload.category) {
      formData.append('category', payload.category);
    }
    if (payload.description) {
      formData.append('description', payload.description);
    }
    return api.post<AssetDocument>(
      `/inventory/asset-documents/${id}/supersede/`,
      formData,
      { headers: { 'Content-Type': 'multipart/form-data' } },
    );
  },

  update: (id: string, data: Partial<Pick<AssetDocument, 'title' | 'category' | 'description'>>) =>
    api.patch<AssetDocument>(`/inventory/asset-documents/${id}/`, data),

  delete: (id: string) => api.delete(`/inventory/asset-documents/${id}/`),
};

// Asset meters (EAM bead-1) — usage counters + manual reading entry. Meters are
// also embedded read-only on the asset detail payload (`asset.meters`); these
// endpoints are for the manual-first record-reading / adjust flow (staff-gated)
// and the read-only reading ledger.
export const assetMetersAPI = {
  list: (params?: { asset?: string; is_active?: boolean }) =>
    api.get<{ count: number; next: string | null; previous: string | null; results: AssetMeter[] }>(
      '/inventory/asset-meters/',
      { params },
    ),

  create: (payload: {
    asset: string;
    name: string;
    meter_type: string;
    unit?: string;
    source?: string;
  }) => api.post<AssetMeter>('/inventory/asset-meters/', payload),

  recordReading: (
    id: string,
    payload: { value: number; is_absolute: boolean; is_estimated?: boolean; observed_at?: string },
  ) =>
    api.post<{ meter: AssetMeter; reading: AssetMeterReading }>(
      `/inventory/asset-meters/${id}/record-reading/`,
      payload,
    ),

  adjust: (id: string, payload: { target: number; reason: string }) =>
    api.post<{ meter: AssetMeter; reading: AssetMeterReading }>(
      `/inventory/asset-meters/${id}/adjust/`,
      payload,
    ),

  delete: (id: string) => api.delete(`/inventory/asset-meters/${id}/`),

  listReadings: (params: { meter?: string; asset?: string }) =>
    api.get<{ count: number; next: string | null; previous: string | null; results: AssetMeterReading[] }>(
      '/inventory/asset-meter-readings/',
      { params },
    ),
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

// Tools a PM task needs (gathered and returned, unlike materials, which are
// consumed). Mirrors the material endpoints above; the work order carries a
// read-only copy of this list for the up-front "Tools Required" panel.
export const maintenanceToolAPI = {
  list: (maintenanceItemId: string) =>
    api.get<{ results: MaintenanceTool[] }>('/inventory/maintenance-tools/', { params: { maintenance_item: maintenanceItemId } }),

  create: (data: Partial<MaintenanceTool>) =>
    api.post<MaintenanceTool>('/inventory/maintenance-tools/', data),

  update: (id: string, data: Partial<MaintenanceTool>) =>
    api.patch<MaintenanceTool>(`/inventory/maintenance-tools/${id}/`, data),

  delete: (id: string) =>
    api.delete(`/inventory/maintenance-tools/${id}/`),
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

/**
 * A step carries an optional `reference_image` File, which has to go up as
 * multipart. Everything else is happy as JSON, so only the payloads that
 * actually carry a File get converted — that keeps the wire format (and the
 * request logs) unchanged for plain title/order edits.
 */
const taskRequest = (data: Partial<MaintenanceTask>) => {
  if (!(data.reference_image instanceof File)) {
    return { body: data as unknown as Record<string, unknown>, config: undefined };
  }
  const formData = new FormData();
  Object.entries(data).forEach(([key, value]) => {
    if (value === undefined || value === null) return;
    formData.append(key, value instanceof File ? value : String(value));
  });
  return {
    body: formData as unknown as Record<string, unknown>,
    config: { headers: { 'Content-Type': 'multipart/form-data' } },
  };
};

// Maintenance Task API (sub-task steps within a MaintenanceItem)
export const maintenanceTaskAPI = {
  listTasks: (maintenanceItemId: string) =>
    api.get<{ results: MaintenanceTask[] }>('/inventory/maintenance-tasks/', {
      params: { maintenance_item: maintenanceItemId },
    }),

  createTask: (data: Partial<MaintenanceTask>) => {
    const { body, config } = taskRequest(data);
    return api.post<MaintenanceTask>('/inventory/maintenance-tasks/', body, config);
  },

  updateTask: (id: string, data: Partial<MaintenanceTask>) => {
    const { body, config } = taskRequest(data);
    return api.patch<MaintenanceTask>(`/inventory/maintenance-tasks/${id}/`, body, config);
  },

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

  // The work order PDF is the OMR "scan-to-complete" form: a printable form
  // with corner fiducials that can be filled by hand and scanned back in.
  // Backend unified /pdf/ onto this variant (op-8mjw); /omr-pdf/ is a
  // deprecated alias, so the web uses the single /pdf/ endpoint.
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

  completeLoto: (workOrderId: string, lotoCompletionId: string, data: { is_completed: boolean; notes?: string }) =>
    api.patch<WorkOrderLotoCompletion>(
      `/inventory/work-orders/${workOrderId}/loto/${lotoCompletionId}/complete/`,
      data
    ),

  // op-m3so: the work-order stopwatch. Server-authoritative — the page only
  // ticks a display over `elapsed_seconds`/`is_timing`, so a reload (or a
  // second device) shows the same total. Both calls are idempotent.
  timer: (workOrderId: string, action: 'start' | 'pause') =>
    api.post<WorkOrder>(`/inventory/work-orders/${workOrderId}/timer/`, { action }),

  // Per-step clock. Starting one step pauses whichever other step was running,
  // so reload the work order afterwards to pick that up.
  taskTimer: (workOrderId: string, taskCompletionId: string, action: 'start' | 'pause') =>
    api.post<WorkOrderTaskCompletion>(
      `/inventory/work-orders/${workOrderId}/tasks/${taskCompletionId}/timer/`,
      { action }
    ),

  // `quantityUsed` and `unitCost` are only honoured before the stock decrement
  // is applied — the backend freezes both once stock has moved, so the recorded
  // spend can never drift from the decrement it backs (op-768w). Omit a value
  // to leave it alone; pass `null` for unitCost to clear a recorded price.
  toggleMaterial: (
    workOrderId: string,
    materialUsageId: string,
    wasUsed: boolean,
    quantityUsed?: number | string,
    unitCost?: number | string | null
  ) => {
    const body: Record<string, unknown> = { was_used: wasUsed };
    if (quantityUsed !== undefined) body.quantity_used = quantityUsed;
    if (unitCost !== undefined) body.unit_cost = unitCost;
    return api.patch<WorkOrderMaterialUsage>(
      `/inventory/work-orders/${workOrderId}/materials/${materialUsageId}/toggle/`,
      body
    );
  },

  // op-768w: add an ad-hoc material line — the only way a *corrective* work
  // order (no PM template to copy rows from) records a material at all, and the
  // way any work order records something bought mid-job. Pass FormData when a
  // receipt image rides along, mirroring `addPhoto`.
  addMaterial: (workOrderId: string, data: WorkOrderAdHocMaterialInput | FormData) =>
    api.post<WorkOrderMaterialUsage>(
      `/inventory/work-orders/${workOrderId}/materials/`,
      data,
      data instanceof FormData
        ? { headers: { 'Content-Type': 'multipart/form-data' } }
        : undefined
    ),

  // Only ad-hoc lines with no stock applied can go: the backend 400s on a
  // template row and on a line still holding a decrement (un-toggle it first).
  removeMaterial: (workOrderId: string, materialUsageId: string) =>
    api.delete(`/inventory/work-orders/${workOrderId}/materials/${materialUsageId}/`),

  // op-0v4: add an ad-hoc tool — the only way a *corrective* work order (no PM
  // template to copy rows from) lists a tool at all. Never moves stock: a tool
  // is gathered, used and returned.
  addTool: (workOrderId: string, data: WorkOrderAdHocToolInput) =>
    api.post<WorkOrderToolRow>(`/inventory/work-orders/${workOrderId}/tools/`, data),

  // Restage a tool for THIS job. Allowed on every row, template-derived
  // included — it writes only to the work order, never back to the PM template.
  // Blank clears the hint and lets the inventory item's location stand in.
  updateToolLocation: (workOrderId: string, toolId: string, locationHint: string) =>
    api.patch<WorkOrderToolRow>(`/inventory/work-orders/${workOrderId}/tools/${toolId}/`, {
      location_hint: locationHint,
    }),

  // Only ad-hoc rows can go: the backend 400s on a template-derived row, which
  // is the frozen copy of what the job was supposed to need.
  removeTool: (workOrderId: string, toolId: string) =>
    api.delete(`/inventory/work-orders/${workOrderId}/tools/${toolId}/`),

  addPhoto: (workOrderId: string, formData: FormData) =>
    api.post<WorkOrderPhoto>(`/inventory/work-orders/${workOrderId}/add_photo/`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }),

  // op-rjsv: the internal WO's general attachments list (op-7pjj backend). The
  // route is top-level and filtered by `?work_order=`, not nested on the work
  // order, so `work_order` rides in the query on list and in the multipart body
  // on upload. Paginated like every inventory list (PAGE_SIZE 50) — a work
  // order never carries anywhere near a page of files.
  listAttachments: (workOrderId: string) =>
    api.get<{ count: number; next: string | null; previous: string | null; results: WorkOrderAttachment[] }>(
      '/inventory/work-order-attachments/',
      { params: { work_order: workOrderId } },
    ),

  uploadAttachment: (workOrderId: string, file: File, description?: string) => {
    const formData = new FormData();
    formData.append('work_order', workOrderId);
    formData.append('file', file);
    if (description) {
      formData.append('description', description);
    }
    return api.post<WorkOrderAttachment>('/inventory/work-order-attachments/', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
  },

  // `workOrderId` is unused in the URL (the detail route is keyed only by the
  // attachment id) but kept for call-site symmetry with list/upload.
  deleteAttachment: (workOrderId: string, attachmentId: string) =>
    api.delete(`/inventory/work-order-attachments/${attachmentId}/`),

  uploadPdf: (file: File) => {
    const formData = new FormData();
    formData.append('pdf', file);
    return api.post<WorkOrderUploadResult>('/inventory/work-orders/upload-pdf/', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
  },

  validateChecklist: (
    id: string,
    data: {
      electrical_acknowledged: boolean;
      loto_acknowledged: boolean;
      required_fields_acknowledged: boolean;
      notes?: string;
    }
  ) => api.post(`/inventory/work-orders/${id}/validate/`, data),

  // Optional body (OMR bead-2): `target_ids` applies/rejects only those marks
  // (per-row), and `confirm_complete` is the human gate that closes the WO.
  applyPendingChanges: (
    workOrderId: string,
    submissionId: string,
    body?: { target_ids?: string[]; confirm_complete?: boolean },
  ) =>
    api.post(
      `/inventory/work-orders/${workOrderId}/submissions/${submissionId}/apply-pending/`,
      body ?? {},
    ),

  discardPendingChanges: (
    workOrderId: string,
    submissionId: string,
    body?: { target_ids?: string[] },
  ) =>
    api.post(
      `/inventory/work-orders/${workOrderId}/submissions/${submissionId}/discard-pending/`,
      body ?? {},
    ),

  // Authed PNG of one warped OMR mark region (returns a Blob for object-URL use).
  getMarkCrop: (workOrderId: string, submissionId: string, targetId: string) =>
    api.get(
      `/inventory/work-orders/${workOrderId}/submissions/${submissionId}/mark-crop/${targetId}/`,
      { responseType: 'blob' },
    ),

  // op-o6rs: authed PNG of the FULL scanned page so the reviewer can verify the
  // detected marks against the actual paper form (returns a Blob for object-URL).
  getScanImage: (workOrderId: string, submissionId: string) =>
    api.get(
      `/inventory/work-orders/${workOrderId}/submissions/${submissionId}/scan-image/`,
      { responseType: 'blob' },
    ),

  getDueThisWeek: () =>
    api.get<MaintenanceItem[]>('/inventory/maintenance-items/due_this_week/'),

  getDueThisMonth: () =>
    api.get<MaintenanceItem[]>('/inventory/maintenance-items/due_this_month/'),
};

// Asset Parts API
export const assetPartsAPI = {
  listAssetParts: (params?: { asset?: string }) =>
    api.get<{ results: AssetPart[] }>('/inventory/asset-parts/', { params }),

  createAssetPart: (data: {
    asset: string;
    part: string;
    quantity_needed: number;
    is_required: boolean;
    maintenance_interval_days: number | null;
    notes: string;
  }) =>
    api.post<AssetPart>('/inventory/asset-parts/', data),

  updateAssetPart: (
    id: string,
    data: Partial<{
      quantity_needed: number;
      is_required: boolean;
      maintenance_interval_days: number | null;
      notes: string;
    }>,
  ) =>
    api.patch<AssetPart>(`/inventory/asset-parts/${id}/`, data),

  deleteAssetPart: (id: string) =>
    api.delete(`/inventory/asset-parts/${id}/`),

  markReplaced: (id: string, replacementSerialNumber?: string) =>
    api.post<AssetPart>(
      `/inventory/asset-parts/${id}/mark_replaced/`,
      replacementSerialNumber
        ? { replacement_serial_number: replacementSerialNumber }
        : undefined,
    ),
};

/**
 * One supplier's pending reorder requests, as `by_supplier` groups them.
 *
 * `total_estimated_cost` is the sum of the requests the server COULD price;
 * `unpriced_item_count` is how many it could not, and
 * `estimated_total_is_partial` is the claim a screen is allowed to make about
 * the number (op-9m2v). The two counts are optional in the type so an older
 * cached payload cannot crash the dashboard.
 */
export interface ReorderRequestsBySupplierGroup {
  supplier: string;
  supplier_type: string;
  requests: ReorderRequest[];
  item_count: number;
  total_estimated_cost: number;
  unpriced_item_count?: number;
  estimated_total_is_partial?: boolean;
}

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
    api.get<ReorderRequestsBySupplierGroup[]>('/reorders/requests/by_supplier/'),

  approveRequest: (id: number, adminNotes?: string) =>
    api.post(`/reorders/requests/${id}/approve/`, { admin_notes: adminNotes }),

  markOrdered: (id: number, data: {
    order_number?: string;
    estimated_delivery?: string;
    actual_cost?: number;
  } = {}) =>
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
  /**
   * What the vendor charges per base unit, or `null` when nobody recorded it
   * (op-9m2v). This used to be the string "0.00" for BOTH an unpriced link and
   * a vendor that gives the item away, so the form prefilled a fabricated zero
   * into the cost box and the operator confirmed it onto a purchase order.
   * A "0.00" here now means the vendor charges nothing.
   */
  unit_cost: string | null;
  /**
   * Why there is no price, and what to do about it. Optional in the type even
   * though the backend always sends them — the same reason `kits?` is: an
   * older cached payload must not crash the purchase-order form.
   */
  unit_cost_state?: 'known' | 'not_recorded' | 'no_supplier_link' | 'no_orderable_link';
  unit_cost_detail?: string | null;
  package_cost: string | null;
  quantity_per_package: number;
  lead_time_days: number;
  supplier_sku: string;
  supplier_url: string;
  is_primary: boolean;
  /** `null` when `unit_cost` is: an unknown price makes an unknown line total. */
  line_total: string | null;
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

/**
 * A kit this supplier sells that would restock one of their low components
 * (op-8n0). Informational context on the reorder screen and a purchasable line
 * on the PO form — never an "action row": kits are excluded from `items` and
 * contribute nothing to `total_items` or `estimated_total`.
 */
export interface ReorderDataKitComponent {
  id: string;
  name: string;
  sku: string;
  quantity_per_kit: number;
  /** Whether this component is one of the low items that surfaced the kit. */
  is_low: boolean;
}

export interface ReorderDataKit {
  id: string;
  name: string;
  sku: string;
  supplier_sku: string;
  /** `null` when no price is recorded for the kit — see `ReorderDataItem`. */
  unit_cost: string | null;
  item_supplier_id: number;
  components: ReorderDataKitComponent[];
  low_component_count: number;
}

export interface ReorderDataSupplier {
  id: number;
  name: string;
  supplier_type: string;
  website: string;
  items: ReorderDataItem[];
  assets: ReorderDataAsset[];
  /**
   * Optional in the type even though the backend always sends it, so callers
   * are forced to write `supplier.kits ?? []` and older cached payloads cannot
   * crash the purchase-order form.
   */
  kits?: ReorderDataKit[];
  total_items: number;
  /**
   * The sum of the lines this pad COULD price. `unpriced_item_count` is how
   * many it could not, and `estimated_total_is_partial` is the claim a screen
   * is allowed to make about the number (op-9m2v). Optional in the type so an
   * older cached payload cannot crash the form.
   */
  estimated_total: string;
  unpriced_item_count?: number;
  estimated_total_is_partial?: boolean;
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
  // Per-line association (op-bu80 / op-shb9): the job this line was ordered to
  // complete, and the committee (SIG) it was ordered on behalf of.
  work_order_id?: string;
  owning_group_id?: number;
}

/**
 * Purchase-order header terms (op-bwo9). `priority` always carries a value —
 * the model defaults it to `normal` — while both terms fields are blank until
 * they are agreed with the supplier.
 */
export type PurchaseOrderPriority = 'low' | 'normal' | 'high' | 'urgent';

export type PurchaseOrderPaymentTerms =
  | 'due_on_receipt'
  | 'net_15'
  | 'net_30'
  | 'net_60'
  | 'cod'
  | 'prepaid';

export type PurchaseOrderFreightTerms =
  | 'fob_origin'
  | 'fob_destination'
  | 'prepaid'
  | 'collect'
  | 'third_party';

/**
 * The single payment an order implies, derived server-side from its
 * `payment_terms`, `order_date` and `expected_delivery_date` (op-bwo9).
 * Read-only: recomputed on every read, never stored, so voiding a line moves
 * `amount` with it.
 *
 * `due_date` is a bare 'YYYY-MM-DD', null where the terms have nothing to
 * anchor to (none agreed, or delivery-anchored terms with no expected
 * delivery). `amount` is the order's live estimated total as a 2dp string,
 * matching `estimated_total` next to it.
 */
export interface PurchaseOrderPaymentSchedule {
  due_date: string | null;
  amount: string;
  basis: string;
}

/**
 * The four header fields a purchase order carries beyond its supplier and
 * lines (op-bwo9). Shared by create and update: `order_date` is a *datetime*
 * on the wire even though it is edited as a day — see `ymdToUtcDateTime`.
 */
export interface PurchaseOrderHeaderTerms {
  order_date?: string;
  priority?: PurchaseOrderPriority;
  // '' clears the field back to "not agreed yet".
  payment_terms?: PurchaseOrderPaymentTerms | '';
  freight_terms?: PurchaseOrderFreightTerms | '';
}

export interface CreatePurchaseOrderData extends PurchaseOrderHeaderTerms {
  supplier: number;
  // Optional purchase/pricing agreement this order is placed under (op-yoos).
  // Must belong to `supplier` — the backend rejects a mismatch.
  supplier_agreement?: number;
  // Order-level association (op-shb9): the work order this order was placed
  // for, and the committee (SIG) it was placed on behalf of. Both optional and
  // attribution-only — neither moves stock nor posts to the ledger.
  work_order?: string;
  owning_group?: number;
  expected_delivery_date?: string;
  notes?: string;
  items: CreatePurchaseOrderItem[];
}

/**
 * Fields the PO detail page can PATCH onto the order header. Omitting a key
 * leaves it alone; `null` clears an association or the expected delivery date.
 */
export interface PurchaseOrderUpdate extends PurchaseOrderHeaderTerms {
  supplier_order_number?: string;
  sales_order_number?: string;
  expected_delivery_date?: string | null;
  notes?: string;
  // Order-level associations (op-shb9). `null` clears one.
  work_order?: string | null;
  owning_group?: number | null;
}

/**
 * Fields the PO detail page can PATCH onto a single line (op-shb9 adds the two
 * associations). `null` clears an association; omitting a key leaves it alone.
 */
export interface LineItemUpdate {
  expected_shipment_date?: string;
  notes?: string;
  line_cost?: number;
  unit_cost_actual?: number;
  work_order?: string | null;
  owning_group?: number | null;
}

/**
 * Per-supplier ordering adapter (op-svpq). Selects what the order-pad export
 * emits for a supplier: a generic part#,qty pad (`none`/`generic_csv`), an
 * Amazon add-to-cart URL (`amazon`), or an HD Supply Part#,Qty CSV (`hdsupply`).
 */
export type OrderingAdapter = 'none' | 'generic_csv' | 'amazon' | 'hdsupply';

/**
 * Order-pad export for a purchase order. The supplier's `adapter` selects the
 * shape (op-svpq):
 *
 * - `none`/`generic_csv`/`hdsupply` → a CSV (`csv`, with header) + a
 *   tab-separated copy-paste block (`text`) + a `filename`.
 * - `amazon` → one or more Amazon add-to-cart URLs (`cart_urls`); no CSV/file.
 *
 * `missing_sku` lists lines whose supplier part number is blank; `invalid_sku`
 * lists lines whose part number fails the adapter's format (a non-ASIN for
 * Amazon, a non-numeric part for HD Supply) — both surfaced so the operator can
 * fix them before ordering, never silently dropped.
 */
export interface OrderPadExport {
  adapter: OrderingAdapter;
  supplier: string;
  line_count: number;
  missing_sku: string[];
  invalid_sku: string[];
  // CSV adapters (none/generic_csv/hdsupply) only.
  csv?: string;
  text?: string;
  filename?: string;
  // Amazon adapter only.
  cart_urls?: string[];
}

/**
 * Per-supplier purchase/pricing agreements (op-yoos). The PO create form asks
 * for one supplier's *active* agreements so retired paperwork stays out of the
 * picker.
 */
export const supplierAgreementAPI = {
  listBySupplier: (supplierId: number) =>
    api.get<{ results: SupplierAgreement[] }>(
      `/inventory/supplier-agreements/?supplier=${supplierId}&is_active=true`
    ),
};

/**
 * Kit SKUs (op-8n0): purchasable bundles that decompose into component stock.
 *
 * `/inventory/kits/` is a facade over the `is_kit=true` slice of InventoryItem,
 * so a Kit is shaped exactly like an InventoryItem plus its `components`.
 * Reads are public; writes need auth.
 *
 * There is deliberately no kit-components endpoint — the bill of materials is
 * nested-writable on the kit, and every mutation returns the FULL refreshed kit
 * so callers can patch visible state from the response rather than refetching
 * (docs/REACTIVE_MUTATIONS.md).
 */
export const kitAPI = {
  listKits: (params?: {
    search?: string;
    is_active?: boolean;
    /** Supplier id — kits purchasable from this supplier. */
    supplier?: number;
    /** Inventory item id — kits that contain this component. */
    component?: string;
    ordering?: string;
    page?: number;
    page_size?: number;
  }) =>
    api.get<{ count: number; next: string | null; previous: string | null; results: Kit[] }>(
      '/inventory/kits/',
      { params }
    ),

  getKit: (id: string) => api.get<Kit>(`/inventory/kits/${id}/`),

  createKit: (
    payload: Omit<Partial<Kit>, 'components'> & {
      components: Array<{ component: string; quantity: number; notes?: string }>;
      supplier_terms?: KitSupplierTerms;
    }
  ) => api.post<Kit>('/inventory/kits/', payload),

  updateKit: (
    id: string,
    payload: Omit<Partial<Kit>, 'components'> & {
      components?: Array<{ component: string; quantity: number; notes?: string }>;
      supplier_terms?: KitSupplierTerms;
    }
  ) => api.patch<Kit>(`/inventory/kits/${id}/`, payload),

  deleteKit: (id: string) => api.delete(`/inventory/kits/${id}/`),
};

/**
 * One item a typed/scanned identifier resolved to, in the context of one
 * purchase order's supplier (oms-po-add-item).
 *
 * `match_kind` is the tier that matched — `unit_barcode`, `package_barcode`,
 * `vendor_sku`, `item_sku`, `item_name`, a weaker `partial_*` variant, or
 * `other_supplier_listing` when the identifier came off ANOTHER vendor's
 * listing for an item this order's supplier also carries (the candidate is
 * always this supplier's own row; `match_label` names the vendor the code came
 * from). `match_label` is otherwise the tier spelled out for the operator.
 *
 * `already_on_order` is non-null when this purchase order already carries a
 * line for the item, in which case adding it again GROWS that line rather than
 * creating a second one — so `repeat_increment` / `quantity_ordered_after`, not
 * `suggested_quantity`, are the numbers to show on a confirm screen.
 */
export interface PurchaseOrderLineCandidate {
  item_supplier: number;
  match_kind: string;
  match_label: string;
  matched_value: string;
  is_exact: boolean;
  item: { id: string; name: string; sku: string; is_kit: boolean };
  supplier_sku: string;
  package_upc: string;
  unit_upc: string;
  quantity_per_package: number;
  /** What a FRESH line would land on — not what a repeat add produces. */
  suggested_quantity: number;
  /**
   * `null` when nothing is on file — neither a price on the supplier
   * relationship nor a past purchase from them (op-9m2v). It used to be the
   * string "0.00", which a prompt could show and an operator accept as a
   * quote. Adding a candidate whose suggestion is `null` without sending a
   * `unit_cost` is REFUSED by the server, so render the null as a blank the
   * operator must fill in. A "0.00" means the vendor charges nothing.
   */
  suggested_unit_cost: string | null;
  already_on_order: {
    line_item: string;
    quantity_ordered: number;
    is_voided: boolean;
    /** Quantity a repeat add with no explicit quantity adds; null if voided. */
    repeat_increment: number | null;
    /** Where that leaves the line; null if voided (the add is refused). */
    quantity_ordered_after: number | null;
  } | null;
}

/** An item the identifier named that this order still cannot carry, and why. */
export interface PurchaseOrderLineUnavailable {
  item: { id: string; name: string; sku: string };
  reason: string;
  message: string;
}

/** Response of the supplier-scoped identifier lookup. */
export interface PurchaseOrderItemLookup {
  query: string;
  supplier: { id: number; name: string };
  purchase_order: {
    id: string;
    po_number: string | null;
    status: string;
    can_add_items: boolean;
  };
  best_match_kind: string | null;
  /** True when a client may add straight from this lookup without asking. */
  resolves: boolean;
  candidates: PurchaseOrderLineCandidate[];
  /**
   * `candidates` is capped server-side, so these report the match counts
   * BEFORE the cap: `total_candidates` across every tier, `best_match_total`
   * within `best_match_kind`, and `truncated` when the list really was
   * shortened. A capped list must be shown as capped, never as the whole set.
   */
  total_candidates: number;
  best_match_total: number;
  truncated: boolean;
  unavailable: PurchaseOrderLineUnavailable[];
  /** The same pre-cap accounting for `unavailable`, which is capped too. */
  total_unavailable: number;
  unavailable_truncated: boolean;
}

/**
 * Add-a-line request. Name what is being bought EXACTLY ONE way — these are the
 * three line shapes a purchase order carries, with the inventory shape offered
 * two ways:
 *
 * - `identifier` — inventory, by what the operator typed or the scanner emitted;
 * - `item_supplier` — inventory, by the catalogue row they picked out of an
 *   ambiguous response;
 * - `asset` — a tracked hard asset, by id;
 * - `description` — a freeform line for something the catalogue does not know.
 *
 * On the inventory shapes quantity and cost are optional: the server derives
 * them from the supplier relationship and purchase history. `asset` and
 * `description` lines have neither, so `unit_cost` is REQUIRED on both — the
 * same rule that applies when an order is created with such a line.
 */
export interface AddPurchaseOrderLinePayload {
  identifier?: string;
  item_supplier?: number;
  asset?: string;
  description?: string;
  quantity?: number;
  unit_cost?: string | number;
  notes?: string;
  work_order?: string | null;
  owning_group?: number | null;
}

/**
 * Add-a-line response. `created` is false when an existing line was grown
 * instead. `purchase_order` is the FULL refreshed order, so callers patch the
 * page from it rather than re-running the initial loader
 * (docs/REACTIVE_MUTATIONS.md).
 */
export interface AddPurchaseOrderLineResponse {
  created: boolean;
  line_item: any;
  match: PurchaseOrderLineCandidate | null;
  purchase_order: any;
}

/** The 400/409 body the add endpoint returns when a line cannot be added. */
export interface AddPurchaseOrderLineError {
  error: string;
  code: string;
  candidates?: PurchaseOrderLineCandidate[];
}

/** One serial-numbered unit captured during a receipt. */
export interface ReceiveSerial {
  serial_number: string;
  /**
   * Which inventory identity this serial belongs to.
   *
   * Optional on an ordinary line (there is only one it could be) and REQUIRED
   * on a kit line, where the receipt credits several components and guessing
   * between them would invent the operator's intent. Naming the KIT itself is
   * refused: a kit is never stocked, so a serial against it names a unit that
   * can never be drawn down.
   */
  item?: string;
  /** Optional batch/lot number, recorded verbatim. */
  lot?: string;
  /** Optional expiry, ISO date. Recorded and displayed only. */
  expiration_date?: string;
}

/** An inventory identity a receipt may record serials against. */
export interface ReceivingSerialTarget {
  item: string;
  item_name: string;
  item_sku: string;
  serial_tracking_mode?: string;
  /** Units of this identity the FULL ordered quantity implies. */
  quantity: number;
  /** Units of this identity already accessioned against the line. */
  recorded: number;
}

/** An identifier a scanner could read off a line's goods. */
export interface ReceivingScanCode {
  code: string;
  kind: 'item_sku' | 'package_upc' | 'unit_upc' | 'supplier_sku';
}

export interface ReceivingWorksheetLine {
  purchase_order_item: number;
  label: string;
  item: string | null;
  item_type: string | null;
  quantity_ordered: number;
  quantity_received: number;
  quantity_pending: number;
  /** Signed: negative short, positive over. Never floored. */
  quantity_variance: number;
  receipt_state:
    | 'not_received'
    | 'partially_received'
    | 'received'
    | 'over_received'
    | 'closed_short'
    | 'voided';
  receipt_state_label: string;
  is_settled: boolean;
  is_voided: boolean;
  is_closed_short: boolean;
  closed_short_reason: string;
  /** A close-short taken back. Reported alongside the one it corrects. */
  was_reopened: boolean;
  reopened_reason: string;
  is_kit_line: boolean;
  scan_codes: ReceivingScanCode[];
  serial_targets: ReceivingSerialTarget[];
  serials_recorded: number;
  /** Per-identity breakdown of units credited with no serial yet. */
  serial_gap: {
    item: string;
    item_name: string;
    expected: number;
    recorded: number;
    outstanding: number;
  }[];
  /** Units on this line in stock with no serial recorded. */
  serials_outstanding: number;
}

/**
 * The receive screen's whole payload, derived server-side on every read.
 *
 * `can_receive` and `unavailable_reason` are a deliberate pair: "you may not
 * receive against this, and here is why" is a different fact from "there is
 * nothing outstanding", and an operator acts differently on each.
 */
export interface ReceivingWorksheet {
  /** Integer: a purchase order's primary key, unlike an item's UUID. */
  purchase_order: number;
  po_number: string | null;
  supplier: string;
  status: string;
  status_label: string;
  can_receive: boolean;
  unavailable_reason: string | null;
  is_settled: boolean;
  is_fully_received: boolean;
  has_receipt_variance: boolean;
  outstanding_line_count: number;
  variance_line_count: number;
  /** Order-level roll-up of units in stock with no serial recorded. */
  serials_outstanding: number;
  lines: ReceivingWorksheetLine[];
}

export const purchaseOrderAPI = {
  /**
   * Resolve a typed or scanned identifier against this order's supplier
   * without adding anything (oms-po-add-item). Read-only companion to
   * `addLineItem`, for clients that want to show what matched before
   * committing.
   */
  lookupLineItem: (orderId: string, query: string) =>
    api.get<PurchaseOrderItemLookup>(
      `/reorders/purchase-orders/${orderId}/item-lookup/`,
      { params: { q: query } },
    ),
  /**
   * Add a line to a DRAFT purchase order, in any of its three shapes —
   * inventory (by `identifier` or `item_supplier`), `asset`, or freeform
   * `description`. Rejects with 400 when the order is not a draft, its
   * supplier does not supply the item (or did not make the asset), or a
   * priced-by-hand shape arrived without `unit_cost`; and with 409 plus a
   * `candidates` list when an identifier matches more than one item.
   */
  addLineItem: (orderId: string, data: AddPurchaseOrderLinePayload) =>
    api.post<AddPurchaseOrderLineResponse>(
      `/reorders/purchase-orders/${orderId}/items/`,
      data,
    ),

  listOrders: (params?: { status?: string }) =>
    api.get<{ results: any[] }>('/reorders/purchase-orders/', { params }),
  getOrder: (id: string) =>
    api.get<any>(`/reorders/purchase-orders/${id}/`),
  getReorderData: () =>
    api.get<ReorderDataResponse>('/reorders/purchase-orders/reorder_data/'),
  createOrder: (data: CreatePurchaseOrderData) =>
    api.post<any>('/reorders/purchase-orders/', data),
  updateLineItem: (orderId: string, itemId: string, data: LineItemUpdate) =>
    api.patch(`/reorders/purchase-orders/${orderId}/items/${itemId}/`, data),
  voidLineItem: (orderId: string, itemId: string, reason?: string) =>
    api.post(`/reorders/purchase-orders/${orderId}/items/${itemId}/void/`, { reason }),
  /**
   * DESTROY a line on a pre-send purchase order. Not an undo of anything and
   * not reversible: the line is gone, with no ghost left behind, which is why
   * it takes no reason. Rejects with 400 `not_draft` once the supplier holds a
   * copy of the order — void the line instead, as that refusal says.
   *
   * Offer it only where the order's own `can_delete_items` says so; never
   * re-derive that from `status` here (see `can_receive`).
   *
   * Returns the full refreshed order for an in-place patch, plus a `deleted`
   * block describing what was destroyed.
   */
  deleteLineItem: (orderId: string, itemId: string) =>
    api.delete<{ deleted: Record<string, unknown>; purchase_order: any }>(
      `/reorders/purchase-orders/${orderId}/items/${itemId}/`,
    ),
  voidOrder: (orderId: string, reason: string) =>
    api.post<any>(`/reorders/purchase-orders/${orderId}/void/`, { reason }),
  sendToSupplier: (id: string) =>
    api.post<any>(`/reorders/purchase-orders/${id}/send_to_supplier/`),
  confirmOrder: (id: string, body?: { expected_delivery_date?: string }) =>
    api.post<any>(`/reorders/purchase-orders/${id}/confirm_order/`, body),
  exportOrder: (id: string) =>
    api.get<OrderPadExport>(`/reorders/purchase-orders/${id}/export-order/`),
  markDelivered: (
    orderId: string,
    data: { delivery_date: string; tracking_number?: string; carrier?: string; receipt_notes?: string },
  ) => api.post<any>(`/reorders/purchase-orders/${orderId}/mark-delivered/`, data),
  receiveItems: (
    orderId: string,
    data: {
      items: {
        purchase_order_item: number;
        quantity_received: number;
        at_level?: boolean;
        // Serialized units arriving with this quantity. `item` names which
        // inventory identity each belongs to and is REQUIRED on a kit line,
        // where the receipt credits several components — see the API's
        // `serial_targets`. Recorded inside the receipt's own transaction, so
        // a serial that cannot be saved rolls the receipt back rather than
        // leaving stock credited with the serials lost.
        serials?: ReceiveSerial[];
        // "Whatever is still outstanding after this is not coming." Settles
        // the line without ever claiming the missing units arrived.
        close_short?: boolean;
        close_short_reason?: string;
      }[];
      delivery_date?: string;
      // The carrier's tracking barcode for this parcel, stored as scanned.
      tracking_number?: string;
      carrier?: string;
      receipt_notes?: string;
    },
  ) => api.post<any>(`/reorders/purchase-orders/${orderId}/receive/`, data),
  // The receive screen's whole payload, derived server-side: which lines are
  // outstanding, what a scanner will read off each box, and which identities
  // may carry serials. Both clients (web + ScanTTY) drive the same endpoint.
  receivingWorksheet: (orderId: string) =>
    api.get<ReceivingWorksheet>(`/reorders/purchase-orders/${orderId}/receiving/`),
  closeShort: (
    orderId: string,
    data: { items: { purchase_order_item: number; reason?: string }[] },
  ) => api.post<any>(`/reorders/purchase-orders/${orderId}/close-short/`, data),
  // Take a close-short back. A CORRECTION, not an undo: the close-short stays
  // on the line and this is stamped beside it with its own actor, time and
  // reason. The line becomes outstanding again, so the receive panel offers it.
  reopenShort: (
    orderId: string,
    data: { items: { purchase_order_item: number; reason?: string }[] },
  ) => api.post<any>(`/reorders/purchase-orders/${orderId}/reopen-short/`, data),
  // Finish the order off: closes every still-outstanding line short. The order
  // reaches `received` only if something was actually received against it, so
  // read the returned order's `status` rather than assuming. Distinct from
  // `markDelivered`, which asserts the opposite — that everything DID arrive.
  markReceived: (orderId: string, data?: { reason?: string }) =>
    api.post<any>(`/reorders/purchase-orders/${orderId}/mark-received/`, data ?? {}),
  updateOrder: (orderId: string, data: PurchaseOrderUpdate) =>
    api.patch<any>(`/reorders/purchase-orders/${orderId}/`, data),
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

// A member as returned by the staff-only user directory (op-tup). Backs the
// access-control grant user-picker and the badge-enrollment admin: enough to
// identify a member and show their current badge.
export interface DirectoryUser {
  id: number;
  username: string;
  first_name: string;
  last_name: string;
  full_name: string;
  email: string;
  badge_number: string | null;
  is_active: boolean;
}

// User Profile API
export const userAPI = {
  getProfile: () =>
    api.get<UserProfile>('/membership/profile/me/'),

  // Staff-only directory for access-control pickers. ``search`` matches
  // username / name / email; ``hasBadge`` narrows to members with (or without)
  // a badge for the enrollment admin.
  listUsers: (opts: { search?: string; hasBadge?: boolean } = {}) =>
    api.get<{ results?: DirectoryUser[] } | DirectoryUser[]>('/membership/users/', {
      params: {
        ...(opts.search ? { search: opts.search } : {}),
        ...(opts.hasBadge != null ? { has_badge: String(opts.hasBadge) } : {}),
      },
    }),

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

export type CostRecoveryPeriod = 'past_week' | 'past_month' | 'past_year';

// Asset ownership scope for the cost-recovery report (Asset.OwnershipType).
export type CostRecoveryOwnershipType = 'user' | 'group' | 'space';

// Selection + window for the asset cost-recovery report. Supply at least one
// selection param (asset_ids / category_ids / all_assets / ownership_type /
// owning_group), and exactly one of period OR start_date+end_date. Note that
// all_assets or either ownership filter widens the base set to every asset,
// so they are not narrowed by asset_ids/category_ids.
export interface CostRecoveryParams {
  asset_ids?: string[];
  category_ids?: number[];
  all_assets?: boolean;
  ownership_type?: CostRecoveryOwnershipType;
  owning_group?: number; // auth.Group PK — the owning SIG / committee
  period?: CostRecoveryPeriod;
  start_date?: string; // YYYY-MM-DD
  end_date?: string; // YYYY-MM-DD
}

// Build the flat query object for the cost_recovery endpoint. The backend
// accepts asset_ids / category_ids as comma-joined lists (it splits on ","),
// which avoids axios's bracketed array serialization (asset_ids[]=) that the
// endpoint would not read. Empty selections/windows are omitted so the caller
// controls exactly which params are sent.
const buildCostRecoveryQuery = (
  params: CostRecoveryParams,
  format?: 'csv' | 'pdf',
): Record<string, string> => {
  const query: Record<string, string> = {};
  if (params.asset_ids && params.asset_ids.length > 0) {
    query.asset_ids = params.asset_ids.join(',');
  }
  if (params.category_ids && params.category_ids.length > 0) {
    query.category_ids = params.category_ids.join(',');
  }
  if (params.all_assets) {
    query.all_assets = 'true';
  }
  if (params.ownership_type) {
    query.ownership_type = params.ownership_type;
  }
  if (params.owning_group != null) {
    query.owning_group = String(params.owning_group);
  }
  if (params.period) {
    query.period = params.period;
  } else if (params.start_date && params.end_date) {
    query.start_date = params.start_date;
    query.end_date = params.end_date;
  }
  if (format) {
    query.format = format;
  }
  return query;
};

// Reports API
export const reportsAPI = {
  // Inventory Reports
  getInventoryStockByCategory: () =>
    api.get('/inventory/reports/inventory/stock_by_category/'),

  getInventoryReorderFrequency: (params?: DateRangeParams) =>
    api.get('/inventory/reports/inventory/reorder_frequency/', { params }),

  getInventoryValueByLocation: () =>
    api.get('/inventory/reports/inventory/value_by_location/'),

  // Serialized-component consumption forecast + low-stock report (#819).
  getSerializedForecast: (params?: { window_days?: number; low_stock_only?: boolean }) =>
    api.get<SerializedForecastRow[]>(
      '/inventory/reports/inventory/serialized_forecast/',
      { params },
    ),

  // Restock-cadence forecast for non-serialized items — the latest stored row
  // per item, already sorted most-urgent first (flagged rows, then soonest
  // due, nulls last), so render the response order as-is. `low_stock_only`
  // narrows it to rows flagged `needs_reorder`. Returns [] until the nightly
  // forecasting task has run.
  getDemandForecast: (params?: { low_stock_only?: boolean }) =>
    api.get<DemandForecastRow[]>(
      '/inventory/reports/inventory/demand_forecast/',
      { params },
    ),

  // The predictive reorder-alert notify set: items whose owner opted in
  // (`reorder_alerts_enabled`) *and* that the forecast says are due.
  getReorderAlerts: () =>
    api.get<DemandForecastRow[]>('/inventory/reports/inventory/reorder_alerts/'),

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

  getAssetSuppliesUsed: (params?: DateRangeParams) =>
    api.get('/inventory/reports/assets/supplies_used/', { params }),

  // Asset cost-recovery statement (landlord-billable). JSON for on-screen
  // rendering; blob download for the server-generated CSV / landlord PDF.
  getAssetCostRecovery: (params: CostRecoveryParams) =>
    api.get<AssetCostRecoveryReport>('/inventory/reports/assets/cost_recovery/', {
      params: buildCostRecoveryQuery(params),
    }),

  // Fetch the CSV/PDF as a blob so the request carries the Authorization
  // header (and gets the interceptor's 401 refresh) rather than relying on the
  // session cookie of a raw <a href> navigation.
  downloadAssetCostRecovery: (params: CostRecoveryParams, format: 'csv' | 'pdf') =>
    api.get('/inventory/reports/assets/cost_recovery/', {
      params: buildCostRecoveryQuery(params, format),
      responseType: 'blob',
    }),

  // Build the shareable download URL (same params + ?format=) for callers that
  // want a direct link rather than a blob fetch.
  getAssetCostRecoveryUrl: (params: CostRecoveryParams, format: 'csv' | 'pdf') => {
    const search = new URLSearchParams(buildCostRecoveryQuery(params, format));
    return `${API_BASE_URL}/inventory/reports/assets/cost_recovery/?${search.toString()}`;
  },

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

  getAuditFeed: (params?: {
    domain?: string;
    entity_type?: string;
    entity_id?: string;
    action?: string;
    actor_id?: number | string;
    since?: string;
    until?: string;
    limit?: number;
  }) =>
    api.get<{ count: number; events: AuditFeedEvent[] }>(
      '/dashboard/audit-feed/',
      { params },
    ),
};

export interface AuditFeedEvent {
  domain: string;
  action: string;
  actor_id: number | null;
  actor_username: string | null;
  created_at: string;
  entity_type: string | null;
  entity_id: string | null;
  notes: string;
  metadata: Record<string, unknown>;
}

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
  bin_id: string | null;
  assigned_username: string;
  first_name: string;
  last_name: string;
  email: string;
  display_name: string;
  assigned_at: string | null;
  expires_at: string | null;
  last_verified_at: string | null;
  status:
    | 'valid'
    | 'grace'
    | 'expired'
    | 'unassigned'
    | 'unknown'
    | 'pre_conversion';
  identity_source: '' | 'whmcs' | 'common_api' | 'manual';
  conversion_completed_at: string | null;
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

export interface MakerBoxLookupResult {
  found: boolean;
  identity_source: '' | 'whmcs' | 'common_api' | 'manual';
  username: string;
  first_name: string;
  last_name: string;
  email: string;
  membership_status: '' | 'valid' | 'grace' | 'expired' | 'unknown';
  expires_at: string | null;
  days_remaining: number | null;
}

/** Last-reported on/off state of a single power-relay channel (op-2cr). */
export interface ForgeKeyRelayChannel {
  channel: number;
  on: boolean;
}

/** Last-reported indicator/status-LED sub-state (op-2cr). Empty until reported. */
export interface ForgeKeyIndicatorState {
  color?: string | null;
  pattern?: string | null;
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
  capabilities: string[];
  capabilities_announced_at: string | null;
  // Live device sub-state cached from the firmware status message (op-2cr).
  relay_channels: ForgeKeyRelayChannel[];
  indicator_state: ForgeKeyIndicatorState;
  created_at: string;
  updated_at: string;
}

export interface ForgeKeyOccupancyEvent {
  id: string;
  device: string;
  sensor_kind: string;
  count_in: number;
  count_out: number;
  occupancy_delta: number;
  event_timestamp_utc: string;
  ingested_at: string;
  raw_payload: Record<string, unknown>;
}

export interface ForgeKeyOccupancyResponse {
  device: string;
  since: string;
  current_occupancy: number;
  events: ForgeKeyOccupancyEvent[];
}

export interface ForgeKeyCommandResponse {
  status: string;
  device: string;
  topic: string;
  command_id?: string;
  dispatched_at: string;
}

export type ForgeKeyAckStatus = 'pending' | 'acked' | 'error' | 'timeout';

export interface ForgeKeyDeviceCommand {
  id: string;
  command: string;
  payload: Record<string, unknown>;
  sent_by: number | null;
  sent_by_username: string | null;
  sent_at: string;
  ack_status: ForgeKeyAckStatus;
  effective_ack_status: ForgeKeyAckStatus;
  ack_at: string | null;
  ack_payload: Record<string, unknown> | null;
}

export interface ForgeKeyRecentCommandsResponse {
  device: string;
  results: ForgeKeyDeviceCommand[];
}

export interface ForgeKeyFleetSummary {
  generated_at: string;
  devices: {
    total: number;
    active: number;
    online: number;
    offline: number;
    never_seen: number;
    by_type: Array<{ code: string; name: string; count: number; online: number }>;
    by_capability: Array<{ capability: string; count: number }>;
    by_firmware: Array<{ version: string; count: number }>;
  };
  epaper: { total: number; bound: number; unbound: number; low_battery: number };
  firmware: { updates_in_flight: number; recent_failures: number };
  attention: {
    offline: Array<{
      kind: 'offline';
      device_id: string;
      name: string;
      mac_address: string;
      last_seen: string | null;
    }>;
    low_battery: Array<{
      kind: 'low_battery';
      display_id: string;
      asset_name: string | null;
      battery_percent: number;
      last_battery_at: string | null;
    }>;
    ota_failed: Array<{
      kind: 'ota_failed';
      device_id: string;
      name: string;
      version: string | null;
      error: string;
      requested_at: string;
    }>;
  };
  recent_commands: Array<{
    id: string;
    device_id: string;
    device_name: string;
    command: string;
    ack_status: ForgeKeyAckStatus;
    sent_at: string;
    sent_by: string | null;
  }>;
  recent_updates: Array<{
    id: string;
    device_id: string;
    device_name: string;
    version: string | null;
    status: string;
    requested_at: string;
    requested_by: string | null;
  }>;
}

export interface ForgeKeyTemperatureReading {
  id: string;
  device: string;
  sensor_kind: string;
  temperature_c: number;
  humidity_percent: number | null;
  recorded_at: string;
  raw_payload: Record<string, unknown>;
}

export interface ForgeKeyTemperatureResponse {
  device: string;
  since: string;
  latest_temperature_c: number | null;
  latest_humidity_percent: number | null;
  readings: ForgeKeyTemperatureReading[];
}

export interface ForgeKeyEPaperDisplay {
  id: string;
  device: string | null;
  device_mac_address: string | null;
  asset: string | null;
  asset_name: string | null;
  asset_tag: string | null;
  battery_percent: number | null;
  is_low_battery: boolean;
  last_battery_at: string | null;
  last_image_etag: string;
  last_image_at: string | null;
  firmware_version: string;
  target_firmware_version: string | null;
  target_firmware_version_string: string | null;
  is_active: boolean;
  // Rotation weights that drive the OOS/reservation/PM face picker on
  // each image fetch — defaults 2:1 (event:pm). Setting either to 0
  // disables the rotation in that direction; both zero falls back to
  // the PM face.
  event_face_weight: number;
  pm_face_weight: number;
  rotation_counter: number;
  created_at: string;
  updated_at: string;
}

export interface ForgeKeyFirmwareVersion {
  id: string;
  version: string;
  device_type: number;
  device_type_name?: string | null;
  // Stable identifier (e.g. 'epaper_screen', 'people_counter') — frontend
  // routes rollout creation to the right endpoint based on this rather
  // than the human-readable name.
  device_type_code?: string | null;
  is_active: boolean;
  mandatory?: boolean;
}

export interface ForgeKeyRolloutProgress {
  total: number;
  on_target: number;
  pending: number;
  in_progress: number;
  failed: number;
  remaining: number;
}

export type ForgeKeyRolloutStatus = 'draft' | 'active' | 'paused' | 'completed' | 'cancelled';

export interface ForgeKeyDeviceType {
  id: number;
  name: string;
  code: string;
  description?: string;
  is_active?: boolean;
}

export type ForgeKeyFirmwareBuildStatus =
  | 'queued'
  | 'building'
  | 'succeeded'
  | 'failed'
  | 'cancelled';

export interface ForgeKeyFirmwareBuild {
  id: string;
  device_type: number;
  device_type_name: string | null;
  pio_env: string;
  source_ref: string;
  version: string;
  mandatory: boolean;
  release_notes: string;
  status: ForgeKeyFirmwareBuildStatus;
  ca_fingerprint: string;
  commit_sha: string;
  log: string;
  error_message: string;
  firmware_version: string | null;
  firmware_version_string: string | null;
  requested_by_username: string | null;
  requested_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface ForgeKeyCertificateAuthority {
  id: string;
  name: string;
  common_name: string | null;
  fingerprint_sha256: string | null;
  not_before: string;
  not_after: string;
  is_active: boolean;
  created_at: string;
  active_cert_count: number | null;
  revoked_cert_count: number | null;
}

export interface ForgeKeyDeviceCertificate {
  id: string;
  device: string | null;
  device_chip_id: string | null;
  serial: string;
  subject: string;
  fingerprint_sha256: string;
  not_before: string;
  not_after: string;
  revoked_at: string | null;
  issued_by: string;
  created_at: string;
  status: 'active' | 'expired' | 'revoked';
}

export interface ForgeKeyFirmwareRollout {
  id: string;
  firmware_version: string;
  firmware_version_string: string;
  device_type_name: string;
  name: string;
  status: ForgeKeyRolloutStatus;
  batch_size_percent: number;
  interval_minutes: number;
  created_by: number | null;
  created_by_username: string | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  completed_at: string | null;
  last_advanced_at: string | null;
  progress: ForgeKeyRolloutProgress;
  dispatched?: number;
}

// An ESP32 device attached to an asset (relay, meter, …). ``role`` is free
// text on the backend — ``power_control`` / ``metering`` are the conventional
// values; ``is_primary`` picks which device answers for the asset (drives
// status derivation and the device → asset lookup).
export interface ForgeKeyAssetDevice {
  id: number;
  asset: string;
  asset_name: string;
  device: string;
  device_name: string;
  device_mac_address: string;
  role: string;
  is_primary: boolean;
  power_off_delay_seconds: number;
  created_at: string;
}

export interface ForgeKeyOperationalMode {
  id: number;
  asset: string;
  asset_name: string;
  mode: 'available' | 'classroom' | 'maintenance' | 'locked_out';
  classroom_mode_enabled: boolean;
  classroom_mode_enabled_by: number | null;
  classroom_mode_enabled_by_username: string | null;
  classroom_mode_enabled_at: string | null;
  updated_at: string;
}

export interface ForgeKeyAssetAuthorization {
  id: number;
  asset: string;
  asset_name: string;
  user: number;
  username: string;
  authorized_by: number | null;
  authorized_by_username: string | null;
  authorized_at: string;
  is_active: boolean;
  notes: string;
}

export interface ForgeKeyDeviceLockout {
  id: string;
  asset: string;
  asset_name: string;
  locked_by: number | null;
  locked_by_username: string | null;
  lockout_level: string;
  reason: string;
  locked_at: string;
  unlocked_at: string | null;
  unlocked_by: number | null;
  unlocked_by_username: string | null;
  is_active: boolean;
}

// Indicator-light status management (epic ga-72l). The canonical operational
// statuses an indicator can reflect — must match backend ``IndicatorStatus``.
export type IndicatorStatusValue =
  | 'available'
  | 'in_use'
  | 'unavailable'
  | 'locked_out'
  | 'classroom';

// The ``set_indicator`` payload the backend derives/pushes. Colors may be a
// named string, a hex string, or an [r, g, b] triple; brightness may be a
// 'low'/'high' word or a 0-255 int. Fields are omitted when not applicable
// (e.g. an off pattern carries no color/brightness).
export interface ForgeKeyIndicatorPresentation {
  cmd?: string;
  color?: string | number[] | null;
  brightness?: string | number | null;
  pattern?: string;
  period_ms?: number;
  duration_s?: number;
}

export interface ForgeKeyIndicatorBinding {
  id: string;
  device: string;
  device_mac_address: string;
  device_name: string;
  asset: string | null;
  asset_name: string | null;
  location: number | null;
  location_name: string | null;
  // '' before the first sync; otherwise the last pushed status.
  last_status: IndicatorStatusValue | '';
  last_presentation: ForgeKeyIndicatorPresentation | null;
  last_synced_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ForgeKeyRoomOperationalMode {
  id: number;
  location: number;
  location_name: string;
  mode: IndicatorStatusValue;
  updated_by: number | null;
  updated_by_username: string | null;
  updated_at: string;
}

export interface ForgeKeyIndicatorSyncResponse {
  status: IndicatorStatusValue | '';
  presentation: ForgeKeyIndicatorPresentation | null;
  command_id: string | null;
}

export interface ForgeKeyIndicatorTestResponse {
  status: string;
  device: string;
  command_id: string;
  payload: ForgeKeyIndicatorPresentation;
}

// A row in the ForgeKey access/denial log (access-control interlock, op-vj9).
// ``metadata.reason`` carries the deny reason for ``access_denied`` rows.
export interface ForgeKeyAuditEvent {
  id: string;
  created_at: string;
  action: string;
  action_display: string;
  actor: number | null;
  actor_username: string | null;
  asset: string | null;
  asset_name: string | null;
  device: string | null;
  device_mac: string | null;
  notes: string;
  metadata: Record<string, unknown> | null;
}

// The access-control actions the access-log view filters on, mapped to their
// human labels (must match the backend ``ForgeKeyAuditEvent`` action choices).
export const FORGEKEY_ACCESS_ACTIONS: Record<string, string> = {
  access_granted: 'Access granted',
  access_denied: 'Access denied',
  session_ended: 'Usage session ended',
  badge_enrolled: 'Badge enrolled',
};

// Armed/captured state for the "enroll next scan" badge handshake. ``captured``
// is non-null once the member has tapped and the interlock bound the UID.
export interface ForgeKeyBadgeEnrollmentState {
  armed: boolean;
  armed_user_id: number | null;
  reader_id: string | null;
  captured: { user_id: number; badge_number: string } | null;
}

export interface ForgeKeyBadgeArmResponse {
  armed: boolean;
  user_id: number;
  reader_id: string | null;
  ttl_seconds: number;
}

export interface ForgeKeyBadgeSetResponse {
  user_id: number;
  badge_number: string | null;
}

export const forgekeyAPI = {
  // The fleet is paginated (DRF default, 50/page) — pass ``page`` to walk it
  // when a caller needs every device rather than the first screenful.
  listDevices: (opts: { capability?: string; page?: number } = {}) =>
    api.get<
      { results?: ForgeKeyDevice[]; next?: string | null } | ForgeKeyDevice[]
    >('/forgekey/devices/', {
      params: {
        ...(opts.capability ? { capability: opts.capability } : {}),
        ...(opts.page != null ? { page: opts.page } : {}),
      },
    }),
  getFleetSummary: () =>
    api.get<ForgeKeyFleetSummary>('/forgekey/devices/fleet-summary/'),
  getDevice: (id: string) => api.get<ForgeKeyDevice>(`/forgekey/devices/${id}/`),
  updateDevice: (id: string, data: Partial<ForgeKeyDevice>) =>
    api.patch<ForgeKeyDevice>(`/forgekey/devices/${id}/`, data),
  // Lifecycle: lock/unlock = power off/on, reset = restart (above),
  // retire/reactivate = is_active toggle (staff), delete = remove (staff).
  enableDevice: (id: string) => api.post(`/forgekey/devices/${id}/enable/`),
  disableDevice: (id: string, delaySeconds?: number) =>
    api.post(`/forgekey/devices/${id}/disable/`, delaySeconds ? { delay_seconds: delaySeconds } : {}),
  // Per-channel power-relay control (ga-40w): emits a signed `power_set`
  // command targeting one channel of the 2-channel relay.
  setRelayChannel: (id: string, channel: number, on: boolean) =>
    api.post<ForgeKeyCommandResponse>(`/forgekey/devices/${id}/relay-channel/`, { channel, on }),
  retireDevice: (id: string) => api.post<ForgeKeyDevice>(`/forgekey/devices/${id}/retire/`),
  reactivateDevice: (id: string) => api.post<ForgeKeyDevice>(`/forgekey/devices/${id}/reactivate/`),
  deleteDevice: (id: string) => api.delete(`/forgekey/devices/${id}/`),
  // Asset ↔ device bindings (op-rmic): the "Bound devices" section on the asset
  // detail page attaches/detaches relays and meters without a trip to the admin.
  listAssetDevices: (opts: { asset?: string; device?: string } = {}) =>
    api.get<{ results?: ForgeKeyAssetDevice[] } | ForgeKeyAssetDevice[]>(
      '/forgekey/asset-devices/',
      {
        params: {
          ...(opts.asset ? { asset: opts.asset } : {}),
          ...(opts.device ? { device: opts.device } : {}),
        },
      },
    ),
  createAssetDevice: (body: {
    asset: string;
    device: string;
    role?: string;
    is_primary?: boolean;
  }) => api.post<ForgeKeyAssetDevice>('/forgekey/asset-devices/', body),
  updateAssetDevice: (id: number, data: Partial<ForgeKeyAssetDevice>) =>
    api.patch<ForgeKeyAssetDevice>(`/forgekey/asset-devices/${id}/`, data),
  deleteAssetDevice: (id: number) => api.delete(`/forgekey/asset-devices/${id}/`),
  // Asset access controls (#7b): operational mode + authorizations + lockouts,
  // surfaced on the asset detail page. All keyed to an inventory Asset id.
  listOperationalModes: (assetId: string) =>
    api.get<{ results?: ForgeKeyOperationalMode[] } | ForgeKeyOperationalMode[]>(
      '/forgekey/operational-modes/',
      { params: { asset: assetId } },
    ),
  enableClassroomMode: (id: number) =>
    api.post<ForgeKeyOperationalMode>(
      `/forgekey/operational-modes/${id}/enable_classroom_mode/`,
    ),
  disableClassroomMode: (id: number) =>
    api.post<ForgeKeyOperationalMode>(
      `/forgekey/operational-modes/${id}/disable_classroom_mode/`,
    ),
  listAuthorizations: (assetId: string, opts: { activeOnly?: boolean } = {}) =>
    api.get<{ results?: ForgeKeyAssetAuthorization[] } | ForgeKeyAssetAuthorization[]>(
      '/forgekey/authorizations/',
      { params: { asset: assetId, ...(opts.activeOnly ? { is_active: 'true' } : {}) } },
    ),
  revokeAuthorization: (id: number, notes?: string) =>
    api.post<ForgeKeyAssetAuthorization>(
      `/forgekey/authorizations/${id}/revoke/`,
      notes ? { notes } : {},
    ),
  listLockouts: (assetId: string, opts: { activeOnly?: boolean } = {}) =>
    api.get<{ results?: ForgeKeyDeviceLockout[] } | ForgeKeyDeviceLockout[]>(
      '/forgekey/lockouts/',
      { params: { asset: assetId, ...(opts.activeOnly ? { is_active: 'true' } : {}) } },
    ),
  unlockLockout: (id: string) =>
    api.post<ForgeKeyDeviceLockout>(`/forgekey/lockouts/${id}/unlock/`),
  // Grant a user access to an asset. ``authorized_by`` is set server-side to the
  // caller; an optional ``expires_at`` makes the grant lapse automatically.
  grantAuthorization: (body: {
    asset: string;
    user: number;
    notes?: string;
    expires_at?: string | null;
  }) => api.post<ForgeKeyAssetAuthorization>('/forgekey/authorizations/', body),
  // The grants for one member — backs the per-member "assets I'm authorized for"
  // view. Pass ``activeOnly`` to drop revoked/expired rows.
  listAuthorizationsForUser: (userId: number, opts: { activeOnly?: boolean } = {}) =>
    api.get<{ results?: ForgeKeyAssetAuthorization[] } | ForgeKeyAssetAuthorization[]>(
      '/forgekey/authorizations/',
      { params: { user: userId, ...(opts.activeOnly ? { is_active: 'true' } : {}) } },
    ),
  // Access/denial log (op-vj9). Defaults to the access-control actions only
  // (grant / deny / session-ended / badge-enrolled); pass filters to narrow.
  listAccessLog: (
    opts: {
      accessOnly?: boolean;
      action?: string;
      asset?: string;
      actor?: number;
      device?: string;
    } = {},
  ) =>
    api.get<{ results?: ForgeKeyAuditEvent[] } | ForgeKeyAuditEvent[]>('/forgekey/access-log/', {
      params: {
        ...(opts.accessOnly !== false ? { access_only: 'true' } : {}),
        ...(opts.action ? { action: opts.action } : {}),
        ...(opts.asset ? { asset: opts.asset } : {}),
        ...(opts.actor != null ? { actor: opts.actor } : {}),
        ...(opts.device ? { device: opts.device } : {}),
      },
    }),
  // Badge enrollment (op-vj9). ``getBadgeEnrollmentState`` is the poll target for
  // the arm flow; reading it with a ``userId`` consumes a captured result.
  getBadgeEnrollmentState: (opts: { userId?: number; readerId?: string } = {}) =>
    api.get<ForgeKeyBadgeEnrollmentState>('/forgekey/badge-enrollment/', {
      params: {
        ...(opts.userId != null ? { user_id: opts.userId } : {}),
        ...(opts.readerId ? { reader_id: opts.readerId } : {}),
      },
    }),
  armBadgeEnrollment: (body: { userId: number; readerId?: string }) =>
    api.post<ForgeKeyBadgeArmResponse>('/forgekey/badge-enrollment/arm/', {
      user_id: body.userId,
      ...(body.readerId ? { reader_id: body.readerId } : {}),
    }),
  cancelBadgeEnrollment: (body: { readerId?: string } = {}) =>
    api.post<{ armed: boolean }>('/forgekey/badge-enrollment/cancel/', {
      ...(body.readerId ? { reader_id: body.readerId } : {}),
    }),
  // Set or clear a member's badge directly (manual-entry fallback). Pass
  // ``badgeNumber: null`` to clear.
  setBadge: (body: { userId: number; badgeNumber: string | null }) =>
    api.post<ForgeKeyBadgeSetResponse>('/forgekey/badge-enrollment/set-badge/', {
      user_id: body.userId,
      badge_number: body.badgeNumber,
    }),
  getOccupancy: (id: string, since: string = '24h') =>
    api.get<ForgeKeyOccupancyResponse>(`/forgekey/devices/${id}/occupancy/`, {
      params: { since },
    }),
  getTemperature: (id: string, since: string = '24h') =>
    api.get<ForgeKeyTemperatureResponse>(`/forgekey/devices/${id}/temperature/`, {
      params: { since },
    }),
  restart: (id: string) =>
    api.post<ForgeKeyCommandResponse>(`/forgekey/devices/${id}/command/restart/`),
  capturePhoto: (id: string, uploadUrl?: string) =>
    api.post<ForgeKeyCommandResponse>(
      `/forgekey/devices/${id}/command/capture-photo/`,
      uploadUrl ? { upload_url: uploadUrl } : {},
    ),
  blink: (id: string, opts: { pattern?: string; duration_s?: number } = {}) =>
    api.post<ForgeKeyCommandResponse>(`/forgekey/devices/${id}/command/blink/`, opts),
  firmwareUpdate: (
    id: string,
    body:
      | { firmware_version_id: string }
      | { version: string; url: string },
  ) =>
    api.post<ForgeKeyCommandResponse>(
      `/forgekey/devices/${id}/command/firmware-update/`,
      body,
    ),
  ping: (id: string) =>
    api.post<ForgeKeyCommandResponse>(`/forgekey/devices/${id}/command/ping/`),
  identify: (id: string, opts: { duration_s?: number } = {}) =>
    api.post<ForgeKeyCommandResponse>(
      `/forgekey/devices/${id}/command/identify/`,
      opts,
    ),
  recentCommands: (id: string, limit: number = 10) =>
    api.get<ForgeKeyRecentCommandsResponse>(
      `/forgekey/devices/${id}/recent-commands/`,
      { params: { limit } },
    ),
  // Indicator lights (epic ga-72l): bind an indicator device to an asset XOR a
  // room, drive a room's manual mode, and preview/sync the light.
  listIndicatorBindings: (
    opts: { device?: string; asset?: string; location?: number } = {},
  ) =>
    api.get<{ results?: ForgeKeyIndicatorBinding[] } | ForgeKeyIndicatorBinding[]>(
      '/forgekey/indicator-bindings/',
      {
        params: {
          ...(opts.device ? { device: opts.device } : {}),
          ...(opts.asset ? { asset: opts.asset } : {}),
          ...(opts.location != null ? { location: opts.location } : {}),
        },
      },
    ),
  createIndicatorBinding: (
    body: { device: string; asset: string } | { device: string; location: number },
  ) => api.post<ForgeKeyIndicatorBinding>('/forgekey/indicator-bindings/', body),
  deleteIndicatorBinding: (id: string) =>
    api.delete(`/forgekey/indicator-bindings/${id}/`),
  // Recompute the bound target's status and push it to the device now.
  indicatorSync: (bindingId: string) =>
    api.post<ForgeKeyIndicatorSyncResponse>(
      `/forgekey/indicator-bindings/${bindingId}/sync/`,
    ),
  listRoomOperationalModes: (opts: { location?: number } = {}) =>
    api.get<{ results?: ForgeKeyRoomOperationalMode[] } | ForgeKeyRoomOperationalMode[]>(
      '/forgekey/room-operational-modes/',
      { params: opts.location != null ? { location: opts.location } : undefined },
    ),
  createRoomOperationalMode: (body: { location: number; mode: IndicatorStatusValue }) =>
    api.post<ForgeKeyRoomOperationalMode>('/forgekey/room-operational-modes/', body),
  setRoomOperationalMode: (id: number, mode: IndicatorStatusValue) =>
    api.patch<ForgeKeyRoomOperationalMode>(
      `/forgekey/room-operational-modes/${id}/`,
      { mode },
    ),
  // Explicit color/brightness/pattern preview to an indicator device — bypasses
  // status derivation for a live hardware check.
  indicatorTest: (
    deviceId: string,
    body: {
      color?: string | number[];
      brightness?: string | number;
      pattern?: string;
      period_ms?: number;
      duration_s?: number;
    },
  ) =>
    api.post<ForgeKeyIndicatorTestResponse>(
      `/forgekey/devices/${deviceId}/indicator/test/`,
      body,
    ),
  bindEPaper: (displayId: string, assetId: string) =>
    api.post<{ display_id: string; asset_id: string; asset_name: string }>(
      `/forgekey/epaper/${displayId}/bind/`,
      { asset_id: assetId },
    ),
  listEPaperDisplays: () => api.get<ForgeKeyEPaperDisplay[]>('/forgekey/epaper/'),
  setEPaperActive: (displayId: string, isActive: boolean) =>
    api.post<ForgeKeyEPaperDisplay>(`/forgekey/epaper/${displayId}/set-active/`, {
      is_active: isActive,
    }),
  // Set the per-panel face-rotation knobs. Both fields optional;
  // the backend clips to [0, 100], coerces strings to int, and 400s
  // on negative or non-numeric values.
  setEPaperRotation: (
    displayId: string,
    data: Partial<Pick<ForgeKeyEPaperDisplay, 'event_face_weight' | 'pm_face_weight'>>,
  ) => api.post<ForgeKeyEPaperDisplay>(`/forgekey/epaper/${displayId}/set-rotation/`, data),
  listFirmwareVersions: () =>
    api.get<{ results?: ForgeKeyFirmwareVersion[] } | ForgeKeyFirmwareVersion[]>(
      '/forgekey/firmware-versions/',
    ),
  listFirmwareRollouts: () =>
    api.get<{ results?: ForgeKeyFirmwareRollout[] } | ForgeKeyFirmwareRollout[]>(
      '/forgekey/firmware-rollouts/',
    ),
  createFirmwareRollout: (body: {
    firmware_version: string;
    batch_size_percent: number;
    interval_minutes: number;
    name?: string;
  }) => api.post<ForgeKeyFirmwareRollout>('/forgekey/firmware-rollouts/', body),
  startRollout: (id: string) =>
    api.post<ForgeKeyFirmwareRollout>(`/forgekey/firmware-rollouts/${id}/start/`),
  pauseRollout: (id: string) =>
    api.post<ForgeKeyFirmwareRollout>(`/forgekey/firmware-rollouts/${id}/pause/`),
  cancelRollout: (id: string) =>
    api.post<ForgeKeyFirmwareRollout>(`/forgekey/firmware-rollouts/${id}/cancel/`),
  advanceRollout: (id: string) =>
    api.post<ForgeKeyFirmwareRollout>(`/forgekey/firmware-rollouts/${id}/advance/`),
  // ePaper rollouts — same lifecycle as MQTT rollouts, but dispatch is HTTPS-pull
  // (panel hits /firmware-check/ on each wake) rather than MQTT-push, so `advance`
  // promotes target_firmware_version on the next batch of EPaperDisplay rows
  // instead of publishing OTA messages. Same response shape so the rollouts
  // page can render both rollout kinds uniformly.
  listEpaperFirmwareRollouts: () =>
    api.get<{ results?: ForgeKeyFirmwareRollout[] } | ForgeKeyFirmwareRollout[]>(
      '/forgekey/epaper-firmware-rollouts/',
    ),
  createEpaperFirmwareRollout: (body: {
    firmware_version: string;
    batch_size_percent: number;
    interval_minutes: number;
    name?: string;
  }) =>
    api.post<ForgeKeyFirmwareRollout>('/forgekey/epaper-firmware-rollouts/', body),
  startEpaperRollout: (id: string) =>
    api.post<ForgeKeyFirmwareRollout>(`/forgekey/epaper-firmware-rollouts/${id}/start/`),
  pauseEpaperRollout: (id: string) =>
    api.post<ForgeKeyFirmwareRollout>(`/forgekey/epaper-firmware-rollouts/${id}/pause/`),
  cancelEpaperRollout: (id: string) =>
    api.post<ForgeKeyFirmwareRollout>(`/forgekey/epaper-firmware-rollouts/${id}/cancel/`),
  advanceEpaperRollout: (id: string) =>
    api.post<ForgeKeyFirmwareRollout>(`/forgekey/epaper-firmware-rollouts/${id}/advance/`),
  // Firmware build pipeline (self-hosted build worker).
  listDeviceTypes: () =>
    api.get<{ results?: ForgeKeyDeviceType[] } | ForgeKeyDeviceType[]>('/forgekey/device-types/'),
  createDeviceType: (data: {
    name: string;
    code: string;
    description?: string;
    is_active?: boolean;
  }) => api.post<ForgeKeyDeviceType>('/forgekey/device-types/', data),
  updateDeviceType: (id: number, data: Partial<ForgeKeyDeviceType>) =>
    api.patch<ForgeKeyDeviceType>(`/forgekey/device-types/${id}/`, data),
  listFirmwareBuilds: () =>
    api.get<{ results?: ForgeKeyFirmwareBuild[] } | ForgeKeyFirmwareBuild[]>(
      '/forgekey/firmware-builds/',
    ),
  createFirmwareBuild: (body: {
    device_type: number;
    pio_env: string;
    version: string;
    source_ref?: string;
    mandatory?: boolean;
    release_notes?: string;
  }) => api.post<ForgeKeyFirmwareBuild>('/forgekey/firmware-builds/', body),
  cancelFirmwareBuild: (id: string) =>
    api.post<ForgeKeyFirmwareBuild>(`/forgekey/firmware-builds/${id}/cancel/`),
  // Re-publish a stuck queued / failed build's task to the worker. Use case:
  // the firmware_builder worker was down (or Redis was restarted) at the
  // time the build was enqueued, so the original task message is gone and
  // the row sits in `queued` even though the worker is now alive.
  redispatchFirmwareBuild: (id: string) =>
    api.post<ForgeKeyFirmwareBuild>(`/forgekey/firmware-builds/${id}/redispatch/`),
  // PKI: read-only certificate visibility + CA rotation (staff).
  listCertificateAuthorities: () =>
    api.get<{ results?: ForgeKeyCertificateAuthority[] } | ForgeKeyCertificateAuthority[]>(
      '/forgekey/certificate-authorities/',
    ),
  listDeviceCertificates: () =>
    api.get<{ results?: ForgeKeyDeviceCertificate[] } | ForgeKeyDeviceCertificate[]>(
      '/forgekey/device-certificates/',
    ),
  rotateCA: (body?: { name?: string; common_name?: string; validity_years?: number }) =>
    api.post<ForgeKeyCertificateAuthority>(
      '/forgekey/certificate-authorities/rotate/',
      body || {},
    ),
  // Scan-to-log page: read the panel's recurring maintenance tasks (public)…
  getEPaperServiceInfo: (displayId: string) =>
    api.get<{
      display_id: string;
      bound: boolean;
      asset: {
        id: string;
        name: string;
        asset_tag: string;
        location: string | null;
        location_id: string | null;
      };
      loto: {
        instructions: string;
        energy_sources: Array<{
          source_type: string;
          source_type_display: string;
          magnitude: string;
          isolation_point: string;
          notes: string;
          devices: Array<{ device_type: string; label: string }>;
        }>;
      };
      power: {
        wiring_type: string;
        breaker: {
          label: string;
          position: string;
          amperage: number | null;
          panel: string;
          panel_location: string;
        } | null;
        disconnect: { label: string; type: string; location: string } | null;
        breaker_location: string;
        electrical_box: string;
        suite: string;
      };
      items: Array<{
        id: string;
        title: string;
        interval_days: number | null;
        status: string;
        days_until_due: number | null;
        status_line: string;
        last_completed: string | null;
        instructions: string;
        estimated_time_minutes: number | null;
        steps: Array<{
          order: number;
          title: string;
          description: string;
          is_required: boolean;
        }>;
        tools: Array<{
          name: string;
          quantity: number;
          is_required: boolean;
          notes: string;
          location: string;
          on_hand: number | null;
          sku: string;
          inventory_item_id: string | null;
        }>;
        materials: Array<{
          name: string;
          quantity: string | null;
          unit: string;
          location: string;
          on_hand: number | null;
          sku: string;
          inventory_item_id: string | null;
        }>;
      }>;
      primary_item_id: string | null;
    }>(`/forgekey/epaper/${displayId}/service-info/`),
  // …and log a completed maintenance task (auth required, attributed to the user).
  completeEPaperService: (
    displayId: string,
    payload: { item_id?: string; notes?: string; location_id?: string; photo?: File | null },
  ) => {
    const url = `/forgekey/epaper/${displayId}/complete/`;
    // Multipart when a photo of the work is attached; JSON otherwise.
    if (payload.photo) {
      const form = new FormData();
      if (payload.item_id) form.append('item_id', payload.item_id);
      if (payload.notes) form.append('notes', payload.notes);
      if (payload.location_id) form.append('location_id', payload.location_id);
      form.append('photo', payload.photo);
      return api.post<EPaperCompleteResponse>(url, form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
    }
    const json: { item_id?: string; notes?: string; location_id?: string } = {};
    if (payload.item_id) json.item_id = payload.item_id;
    if (payload.notes) json.notes = payload.notes;
    if (payload.location_id) json.location_id = payload.location_id;
    return api.post<EPaperCompleteResponse>(url, json);
  },
};

interface EPaperCompleteResponse {
  ok: boolean;
  item_id: string;
  title: string;
  status: string;
  status_line: string;
  completed_at: string;
  completed_by: string | null;
  location: string | null;
  photo_attached: boolean;
}

export const makerBoxesAPI = {
  list: () => api.get<{ results: MakerBox[] } | MakerBox[]>('/maker-boxes/'),
  scan: (binId: string, username: string) =>
    api.post<MakerBoxScanResult>('/maker-boxes/scan/', { bin_id: binId, username }),
  labelUrl: (id: number) => `${API_BASE_URL}/maker-boxes/${id}/label/`,
  manualLabel: (data: { username: string; first_name?: string; last_name?: string }) =>
    api.post('/maker-boxes/manual-label/', data, { responseType: 'blob' }),
  emailPickup: (id: number, email?: string) =>
    api.post<{ sent: boolean; to: string }>(`/maker-boxes/${id}/email-pickup/`, email ? { email } : {}),
  // Avery 5371 (10-up business card) sheet PNG. The endpoint caps at 10
  // — callers paginate. Response is a binary PNG, so we ask axios for an
  // arraybuffer and let the caller turn it into a blob URL for download
  // or a new-tab preview.
  printSheet: (binIds: string[]) =>
    api.post<ArrayBuffer>('/maker-boxes/print-sheet/', { bin_ids: binIds }, { responseType: 'arraybuffer' }),
  // PR2 — pre-conversion workflow. ``lookup`` is read-only and used for
  // live preview as staff types or scans on the pre-conversion screen;
  // ``preConvert`` puts the user on the queue (idempotent on resolved
  // username); ``preConversionQueue`` lists the rows waiting for bin
  // allocation.
  lookup: (query: string) =>
    api.post<MakerBoxLookupResult>('/maker-boxes/lookup/', { query }),
  preConvert: (query: string, notes?: string) =>
    api.post<MakerBox>('/maker-boxes/pre-convert/', notes ? { query, notes } : { query }),
  preConversionQueue: () =>
    api.get<MakerBox[]>('/maker-boxes/pre-conversion-queue/'),
  // PR3 — convert a queued row: allocates MBX-NNN and stamps
  // conversion_completed_at. Pass either id (queue table) or
  // assigned_username (scantty).
  convert: (target: { id?: number; assigned_username?: string }) =>
    api.post<MakerBox>('/maker-boxes/convert/', target),
  // Public scan-verification endpoint that the printed QR resolves
  // to. Distinct from the staff /scan/ action because it's AllowAny
  // and read-only.
  verifyScan: (binId: string, username: string) =>
    api.get<MakerBoxScanResult & { detail?: string }>(
      `/maker-boxes/verify/${encodeURIComponent(binId)}/${encodeURIComponent(username)}/`,
    ),
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

  // Disconnects (PR 2/5) — `/api/electrical-circuits/disconnects/`
  listDisconnects: (params?: DisconnectListParams) =>
    api.get<{ results: Disconnect[] } | Disconnect[]>(`${electricalBase}/disconnects/`, { params })
      .then((response) => ({ ...response, data: normalizeResults<Disconnect>(response.data) })),
  getDisconnect: (id: number | string) =>
    api.get<Disconnect>(`${electricalBase}/disconnects/${id}/`),
  createDisconnect: (data: DisconnectWritable) =>
    api.post<Disconnect>(`${electricalBase}/disconnects/`, data),
  updateDisconnect: (id: number | string, data: Partial<DisconnectWritable>) =>
    api.patch<Disconnect>(`${electricalBase}/disconnects/${id}/`, data),
  deleteDisconnect: (id: number | string) =>
    api.delete(`${electricalBase}/disconnects/${id}/`),

};

export type DisconnectType = 'fused' | 'unfused' | 'toggle' | 'integral' | 'none';

export interface DisconnectListParams {
  circuit?: number | string;
  location?: number | string;
  disconnect_type?: DisconnectType;
  needs_review?: boolean;
}

export interface Disconnect {
  id: number;
  circuit: number;
  circuit_label: string;
  panel_name: string;
  breaker_position: string;
  location: number | null;
  location_name: string | null;
  label: string;
  disconnect_type: DisconnectType;
  amperage: number | null;
  fuse_size: string;
  is_lockable: boolean;
  photo: string | null;
  notes: string;
  required_loto_devices: LOTODevice[];
  needs_review: boolean;
  created_at: string;
  updated_at: string;
}

export interface DisconnectWritable {
  circuit: number;
  location?: number | null;
  label: string;
  disconnect_type: DisconnectType;
  amperage?: number | null;
  fuse_size?: string;
  is_lockable?: boolean;
  notes?: string;
  required_loto_device_ids?: number[];
  needs_review?: boolean;
}

export const DISCONNECT_TYPE_OPTIONS: { value: DisconnectType; label: string }[] = [
  { value: 'fused', label: 'Fused safety switch' },
  { value: 'unfused', label: 'Unfused safety switch' },
  { value: 'toggle', label: 'Toggle / snap switch' },
  { value: 'integral', label: 'Integral to the equipment' },
  { value: 'none', label: 'No separate disconnect (breaker serves)' },
];

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

// ---------------------------------------------------------------------
// Power topology safety API (oms-b25 [4/7]). Read-only views over the
// PowerPanel → PowerBreaker → PowerCircuit → PowerOutlet hierarchy plus
// the direct ``Asset.breaker`` association. Used by the [6/7]
// visualization pages.
// ---------------------------------------------------------------------

export type PowerPanelPhase = 'single' | 'split' | 'three';

export interface PowerPanelSummary {
  id: number;
  name: string;
  location_id: number;
  location_name: string;
  phase_configuration: PowerPanelPhase;
  voltage: number;
  main_breaker_amperage: number | null;
  breaker_count: number;
  needs_review: boolean;
}

export interface PowerOutletNode {
  id: number;
  label: string;
  outlet_type: string;
  status: string;
  location_id: number | null;
  location_name: string | null;
  connected_assets: PowerSafetyAsset[];
}

export interface PowerCircuitNode {
  id: number;
  label: string;
  breaker_id: number;
  max_load_amps: number | null;
  conductor_size: string;
  outlets: PowerOutletNode[];
}

export type BreakerReviewStatus = 'ok' | 'needs_attention' | 'circuit_moved';

export interface PowerBreakerNode {
  id: number;
  panel_id: number;
  panel_name: string;
  position: string;
  amperage: number;
  phase: string;
  pole_count: number;
  status: string;
  review_status: BreakerReviewStatus;
  review_note: string;
  label: string;
  circuits: PowerCircuitNode[];
}

export type PanelBreakerType =
  | ''
  | 'SQUARE_D_QO'
  | 'SQUARE_D_HOMELINE'
  | 'EATON_CH'
  | 'EATON_BR'
  | 'SIEMENS_QP'
  | 'GE_Q_LINE'
  | 'FEDERAL_PACIFIC'
  | 'PUSHMATIC'
  | 'DIN_RAIL'
  | 'OTHER';

export type PanelNumberingDirection = 'top_down' | 'bottom_up';

export interface PowerPanelTopology {
  id: number;
  name: string;
  location_id: number;
  location_name: string;
  phase_configuration: PowerPanelPhase;
  voltage: number;
  main_breaker_amperage: number | null;
  breaker_type: PanelBreakerType;
  numbering_direction: PanelNumberingDirection;
  fed_by_summary: PanelFedBySummary | null;
  downstream_panels: Array<{ id: number; name: string }>;
  breakers: PowerBreakerNode[];
}

export interface PowerSafetyAsset {
  id: string;
  name: string;
  asset_tag: string;
  is_critical: boolean;
}

export interface BreakerTripImpact {
  breaker: Omit<PowerBreakerNode, 'circuits'>;
  assets: PowerSafetyAsset[];
  critical_loads: PowerSafetyAsset[];
}

export interface CircuitLoad {
  circuit: Omit<PowerCircuitNode, 'outlets'>;
  connected_device_count: number;
  connected_devices: PowerSafetyAsset[];
  estimated_max_draw_amps: number;
  capacity_amps: number | null;
  capacity_utilization_percent: number | null;
}

export interface PowerChainHop {
  kind: 'panel' | 'breaker' | 'circuit' | 'disconnect';
  id: number;
  type: string;
  label: string | number;
}

export interface AssetPowerChain {
  asset: PowerSafetyAsset;
  chain: PowerChainHop[];
}

export const electricalSafetyAPI = {
  listPanels: () =>
    api.get<{ results: PowerPanelSummary[] }>('/electrical/panels/'),
  getPanelTopology: (panelId: number | string) =>
    api.get<PowerPanelTopology>(`/electrical/panels/${panelId}/topology/`),
  getBreakerTripImpact: (breakerId: number | string) =>
    api.get<BreakerTripImpact>(`/electrical/breakers/${breakerId}/trip-impact/`),
  getCircuitLoad: (circuitId: number | string) =>
    api.get<CircuitLoad>(`/electrical/circuits/${circuitId}/load/`),
  getAssetPowerChain: (assetId: string) =>
    api.get<AssetPowerChain>(`/assets/${assetId}/power-chain/`),
};

// ---------------------------------------------------------------------
// Per-location Safety Sign (oms-gzycmj).
// ---------------------------------------------------------------------

export interface SafetySheetLight {
  switch_id: number;
  switch_label: string;
  panel: string | null;
  position: string | null;
  amperage: number | null;
  breaker_id: number | null;
}

export interface SafetySheetOutletGroupItem {
  id: number;
  identifier: string;
  outlet_type: string;
}

export interface SafetySheetOutletGroup {
  panel: string | null;
  position: string | null;
  amperage: number | null;
  breaker_id: number | null;
  source: 'legacy' | 'power';
  outlet_count: number;
  outlets: SafetySheetOutletGroupItem[];
}

export interface SafetySheetKillBreaker {
  breaker_id: number;
  panel: string;
  position: string;
  amperage: number | null;
  source: 'direct';
}

export interface SafetySheetThermostat {
  id: number;
  label: string;
  manufacturer: string;
  model: string;
  controlled_asset: { id: string; name: string } | null;
  kill_breakers: SafetySheetKillBreaker[];
  needs_review: boolean;
}

export interface SafetySheetBreakerSummary {
  panel: string;
  position: string;
  amperage: number | null;
}

export interface SafetySheet {
  location: { id: number; name: string };
  lights: SafetySheetLight[];
  outlets: SafetySheetOutletGroup[];
  thermostats: SafetySheetThermostat[];
  all_kill_breakers: SafetySheetBreakerSummary[];
}

export const safetySheetAPI = {
  getLocationSafetySheet: (locationId: number | string) =>
    api.get<SafetySheet>(`/inventory/locations/${locationId}/safety-sheet/`),
};

// ---------------------------------------------------------------------
// Climate / thermostats (oms-gzycmj).
// ---------------------------------------------------------------------

export interface Thermostat {
  id: number;
  label: string;
  location: number;
  location_name: string;
  controls_location: number | null;
  controls_location_name: string | null;
  controlled_asset: string | null;
  controlled_asset_name: string | null;
  manufacturer: string;
  model: string;
  notes: string;
  needs_review: boolean;
  created_at: string;
  updated_at: string;
}

export interface ThermostatWritable {
  label: string;
  location: number;
  controls_location: number | null;
  controlled_asset: string | null;
  manufacturer: string;
  model: string;
  notes: string;
}

export const climateAPI = {
  listThermostats: (params?: { location?: number; controls_location?: number }) =>
    api.get<{ results: Thermostat[] } | Thermostat[]>('/climate/thermostats/', { params })
      .then((response) => ({
        ...response,
        data: normalizeResults<Thermostat>(response.data),
      })),
  getThermostat: (id: number | string) =>
    api.get<Thermostat>(`/climate/thermostats/${id}/`),
  createThermostat: (payload: ThermostatWritable) =>
    api.post<Thermostat>('/climate/thermostats/', payload),
  updateThermostat: (id: number | string, payload: Partial<ThermostatWritable>) =>
    api.patch<Thermostat>(`/climate/thermostats/${id}/`, payload),
  deleteThermostat: (id: number | string) =>
    api.delete(`/climate/thermostats/${id}/`),
};

// ---------------------------------------------------------------------
// Power-topology write API (oms-b25 [7/7]).
// CRUD endpoints behind the new frontend form pages. Staff-only on the
// server; the frontend doesn't try to gate UI for non-staff because the
// form pages aren't reachable from the sidebar for non-staff users.
// ---------------------------------------------------------------------

export interface PanelFedBySummary {
  circuit_id: number;
  circuit_label: string;
  breaker_id: number;
  breaker_position: string;
  breaker_amperage: number;
  breaker_pole_count: number;
  panel_id: number;
  panel_name: string;
}

export interface PowerPanelDetail {
  id: number;
  location: number;
  location_name: string;
  name: string;
  phase_configuration: PowerPanelPhase;
  voltage: number;
  main_breaker_amperage: number | null;
  breaker_type: PanelBreakerType;
  numbering_direction: PanelNumberingDirection;
  manufacturer: string;
  model: string;
  install_date: string | null;
  notes: string;
  needs_review: boolean;
  fed_by: number | null;
  fed_by_summary: PanelFedBySummary | null;
  downstream_panel_count: number;
  breaker_count: number;
  created_at: string;
  updated_at: string;
}

export type PowerPanelWritable = Pick<
  PowerPanelDetail,
  | 'location'
  | 'name'
  | 'phase_configuration'
  | 'voltage'
  | 'main_breaker_amperage'
  | 'breaker_type'
  | 'numbering_direction'
  | 'manufacturer'
  | 'model'
  | 'install_date'
  | 'notes'
  | 'needs_review'
  | 'fed_by'
>;

export interface PowerBreakerDetail {
  id: number;
  panel: number;
  panel_name: string;
  position: string;
  pole_count: 1 | 2 | 3;
  amperage: number;
  phase: 'A' | 'B' | 'C' | 'AB' | 'BC' | 'AC' | 'ABC';
  status: 'active' | 'spare' | 'locked_out';
  review_status: BreakerReviewStatus;
  review_note: string;
  label: string;
  notes: string;
  needs_review: boolean;
  circuit_count: number;
  created_at: string;
  updated_at: string;
}

export type PowerBreakerWritable = Pick<
  PowerBreakerDetail,
  | 'panel'
  | 'position'
  | 'pole_count'
  | 'amperage'
  | 'phase'
  | 'status'
  | 'review_status'
  | 'review_note'
  | 'label'
  | 'notes'
  | 'needs_review'
>;

export interface PowerCircuitDetail {
  id: number;
  breaker: number;
  breaker_label: string;
  panel_id: number;
  panel_name: string;
  label: string;
  conductor_size: string;
  conductor_length_ft: number | null;
  max_load_amps: number | null;
  notes: string;
  needs_review: boolean;
  outlet_count: number;
  created_at: string;
  updated_at: string;
}

export type PowerCircuitWritable = Pick<
  PowerCircuitDetail,
  | 'breaker'
  | 'label'
  | 'conductor_size'
  | 'conductor_length_ft'
  | 'max_load_amps'
  | 'notes'
  | 'needs_review'
>;

export interface PowerOutletDetail {
  id: number;
  circuit: number;
  circuit_label: string;
  location: number;
  location_name: string;
  outlet_type: string;
  label: string;
  location_description: string;
  status: 'active' | 'inactive' | 'capped';
  notes: string;
  needs_review: boolean;
  created_at: string;
  updated_at: string;
}

export type PowerOutletWritable = Pick<
  PowerOutletDetail,
  | 'circuit'
  | 'location'
  | 'outlet_type'
  | 'label'
  | 'location_description'
  | 'status'
  | 'notes'
  | 'needs_review'
>;

export const electricalTopologyAPI = {
  // Panels
  listPanels: () =>
    api.get<{ results: PowerPanelDetail[] }>('/electrical/panels-crud/'),
  getPanel: (id: number | string) =>
    api.get<PowerPanelDetail>(`/electrical/panels-crud/${id}/`),
  createPanel: (data: Partial<PowerPanelWritable>) =>
    api.post<PowerPanelDetail>('/electrical/panels-crud/', data),
  updatePanel: (id: number | string, data: Partial<PowerPanelWritable>) =>
    api.patch<PowerPanelDetail>(`/electrical/panels-crud/${id}/`, data),
  deletePanel: (id: number | string) =>
    api.delete(`/electrical/panels-crud/${id}/`),

  // Breakers
  listBreakers: (params?: { panel?: number | string }) =>
    api.get<{ results: PowerBreakerDetail[] }>('/electrical/breakers-crud/', { params }),
  getBreaker: (id: number | string) =>
    api.get<PowerBreakerDetail>(`/electrical/breakers-crud/${id}/`),
  createBreaker: (data: Partial<PowerBreakerWritable>) =>
    api.post<PowerBreakerDetail>('/electrical/breakers-crud/', data),
  updateBreaker: (id: number | string, data: Partial<PowerBreakerWritable>) =>
    api.patch<PowerBreakerDetail>(`/electrical/breakers-crud/${id}/`, data),
  deleteBreaker: (id: number | string) =>
    api.delete(`/electrical/breakers-crud/${id}/`),

  // Circuits
  listCircuits: (params?: { breaker?: number | string }) =>
    api.get<{ results: PowerCircuitDetail[] }>('/electrical/circuits-crud/', { params }),
  getCircuit: (id: number | string) =>
    api.get<PowerCircuitDetail>(`/electrical/circuits-crud/${id}/`),
  createCircuit: (data: Partial<PowerCircuitWritable>) =>
    api.post<PowerCircuitDetail>('/electrical/circuits-crud/', data),
  updateCircuit: (id: number | string, data: Partial<PowerCircuitWritable>) =>
    api.patch<PowerCircuitDetail>(`/electrical/circuits-crud/${id}/`, data),
  deleteCircuit: (id: number | string) =>
    api.delete(`/electrical/circuits-crud/${id}/`),

  // Outlets
  listOutlets: (params?: { circuit?: number | string }) =>
    api.get<{ results: PowerOutletDetail[] }>('/electrical/outlets-crud/', { params }),
  getOutlet: (id: number | string) =>
    api.get<PowerOutletDetail>(`/electrical/outlets-crud/${id}/`),
  createOutlet: (data: Partial<PowerOutletWritable>) =>
    api.post<PowerOutletDetail>('/electrical/outlets-crud/', data),
  updateOutlet: (id: number | string, data: Partial<PowerOutletWritable>) =>
    api.patch<PowerOutletDetail>(`/electrical/outlets-crud/${id}/`, data),
  deleteOutlet: (id: number | string) =>
    api.delete(`/electrical/outlets-crud/${id}/`),

};

// LOTO (lockout / tagout) — oms-78j
export type LOTODeviceType =
  | 'padlock'
  | 'hasp'
  | 'tag'
  | 'key'
  | 'valve_cover'
  | 'breaker_lock'
  | 'plug_lock'
  | 'other';

export type LOTODeviceStatus = 'available' | 'in_use' | 'missing' | 'retired';

export type LOTOEnergySourceType =
  | 'electrical'
  | 'hydraulic'
  | 'pneumatic'
  | 'mechanical'
  | 'thermal'
  | 'chemical'
  | 'gravitational'
  | 'other';

export interface LOTODevice {
  id: number;
  device_type: LOTODeviceType;
  device_type_display: string;
  label: string;
  location: number | null;
  location_name: string | null;
  assigned_to: number | null;
  assigned_to_username: string | null;
  status: LOTODeviceStatus;
  status_display: string;
  notes: string;
  created_at: string;
  updated_at: string;
}

export interface LOTODeviceSummary {
  id: number;
  device_type: LOTODeviceType;
  device_type_display: string;
  label: string;
  status: LOTODeviceStatus;
}

export interface AssetEnergySource {
  id: number;
  asset: string;
  asset_name: string;
  source_type: LOTOEnergySourceType;
  source_type_display: string;
  magnitude: string;
  isolation_point: string;
  required_devices: number[];
  required_devices_detail: LOTODeviceSummary[];
  notes: string;
  created_at: string;
  updated_at: string;
}

export interface AssetLOTORequirements {
  asset_id: string;
  asset_name: string;
  lockout_type: string;
  lockout_type_display: string;
  lockout_instructions: string;
  lockout_responsible: string;
  is_required: boolean;
  energy_sources: AssetEnergySource[];
}

export interface LOTODeviceListParams {
  status?: LOTODeviceStatus;
  device_type?: LOTODeviceType;
  location?: number | string;
  assigned_to?: number | string;
}

const lotoBase = '/loto';

export const lotoAPI = {
  listDevices: (params?: LOTODeviceListParams) =>
    api.get<{ results: LOTODevice[] } | LOTODevice[]>(`${lotoBase}/devices/`, { params })
      .then((response) => ({ ...response, data: normalizeResults<LOTODevice>(response.data) })),
  getDevice: (id: number | string) =>
    api.get<LOTODevice>(`${lotoBase}/devices/${id}/`),
  createDevice: (data: Partial<LOTODevice>) =>
    api.post<LOTODevice>(`${lotoBase}/devices/`, data),
  updateDevice: (id: number | string, data: Partial<LOTODevice>) =>
    api.patch<LOTODevice>(`${lotoBase}/devices/${id}/`, data),
  deleteDevice: (id: number | string) =>
    api.delete(`${lotoBase}/devices/${id}/`),
  listEnergySources: (params?: { asset?: string; source_type?: LOTOEnergySourceType }) =>
    api.get<{ results: AssetEnergySource[] } | AssetEnergySource[]>(
      `${lotoBase}/energy-sources/`,
      { params }
    ).then((response) => ({
      ...response,
      data: normalizeResults<AssetEnergySource>(response.data),
    })),
  createEnergySource: (data: Partial<AssetEnergySource>) =>
    api.post<AssetEnergySource>(`${lotoBase}/energy-sources/`, data),
  updateEnergySource: (id: number | string, data: Partial<AssetEnergySource>) =>
    api.patch<AssetEnergySource>(`${lotoBase}/energy-sources/${id}/`, data),
  deleteEnergySource: (id: number | string) =>
    api.delete(`${lotoBase}/energy-sources/${id}/`),
  getAssetRequirements: (assetId: string) =>
    api.get<AssetLOTORequirements>(`${lotoBase}/assets/${assetId}/loto-requirements/`),
};

export const LOTO_DEVICE_TYPE_OPTIONS: { value: LOTODeviceType; label: string }[] = [
  { value: 'padlock', label: 'Padlock' },
  { value: 'hasp', label: 'Hasp' },
  { value: 'tag', label: 'Tag' },
  { value: 'key', label: 'Key' },
  { value: 'valve_cover', label: 'Valve cover' },
  { value: 'breaker_lock', label: 'Breaker lock' },
  { value: 'plug_lock', label: 'Plug lock' },
  { value: 'other', label: 'Other' },
];

export const LOTO_DEVICE_STATUS_OPTIONS: { value: LOTODeviceStatus; label: string }[] = [
  { value: 'available', label: 'Available' },
  { value: 'in_use', label: 'In use' },
  { value: 'missing', label: 'Missing' },
  { value: 'retired', label: 'Retired' },
];

export const LOTO_ENERGY_SOURCE_OPTIONS: { value: LOTOEnergySourceType; label: string }[] = [
  { value: 'electrical', label: 'Electrical' },
  { value: 'hydraulic', label: 'Hydraulic' },
  { value: 'pneumatic', label: 'Pneumatic' },
  { value: 'mechanical', label: 'Mechanical / stored motion' },
  { value: 'thermal', label: 'Thermal' },
  { value: 'chemical', label: 'Chemical' },
  { value: 'gravitational', label: 'Gravitational / suspended load' },
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

// Analytics API
export interface AnalyticsValueSummary {
  period_start: string;
  period_end: string;
  internal_completed_count: number;
  internal_estimated_external_cost: string;
  internal_estimated_internal_cost: string;
  internal_net_value: string;
  external_closed_count: number;
  external_actual_cost: string;
  external_estimated_cost: string;
  total_value_to_makerspace: string;
}

export interface AnalyticsTrendPoint {
  period: string;
  count: number;
}

export interface AnalyticsTopUser {
  user_id: number;
  username: string;
  display_name: string;
  completed_wo_count: number;
}

export interface AnalyticsUtilizationRow {
  asset_id: string;
  asset_name: string;
  category: string | null;
  hours_used: number;
  completed_wo_count: number;
  status: string;
}

export interface AnalyticsCategorySpendRow {
  category_id: number | null;
  category_name: string | null;
  internal_estimated: string;
  external_estimated: string;
  external_actual: string;
  internal_wo_count: number;
  external_wo_count: number;
}

export interface AnalyticsForecastEntry {
  asset_id: string;
  asset_name: string;
  category: string | null;
  status: string;
  hours_used: number;
  last_completed_wo_at: string | null;
  interval_hours: number | null;
  interval_days: number | null;
  days_since_last_wo: number | null;
  due_reason: 'hours' | 'days' | 'both';
}

export interface AnalyticsPulseResponse {
  summary: AnalyticsValueSummary;
  wo_volume_trend: AnalyticsTrendPoint[];
  top_users: AnalyticsTopUser[];
  utilization: AnalyticsUtilizationRow[];
  category_spend: AnalyticsCategorySpendRow[];
  maintenance_forecast: AnalyticsForecastEntry[];
  monthly_budget: string | null;
}

export interface AnalyticsShareResponse {
  token: string;
  ttl_days: number;
  expires_in_days: number;
}

export const pulseAPI = {
  getPulse: (params?: { start?: string; end?: string; bucket?: 'month' | 'quarter'; token?: string }) =>
    api.get<AnalyticsPulseResponse>('/analytics/pulse/', { params }),

  createShare: (ttlDays?: number) =>
    api.post<AnalyticsShareResponse>('/analytics/share/', ttlDays ? { ttl_days: ttlDays } : {}),
};

// ---------------------------------------------------------------------------
// Project Storage (warden + kiosk)
// ---------------------------------------------------------------------------

export const projectStorageAPI = {
  list: (params?: {
    status?: ProjectStorageStatus;
    ordering?: 'expires_at' | '-expires_at' | 'started_at' | '-started_at';
    page?: number;
    page_size?: number;
  }) =>
    api.get<{
      count: number;
      next: string | null;
      previous: string | null;
      results: ProjectStorageStint[];
    }>('/project-storage/stints/', { params }),

  get: (stintId: string) =>
    api.get<ProjectStorageStint>(`/project-storage/stints/${stintId}/`),

  byMember: (username: string) =>
    api.get<ProjectStorageStint[]>(`/project-storage/stints/by-member/${encodeURIComponent(username)}/`),

  // `slot` takes either a slot pk or its code — a card's QR encodes the
  // code (…/kiosk?slot=1A1) and the warden console holds the pk, and the
  // backend tells them apart (a code always has a letter, a pk never
  // does). Sending it claims that slot outright; a live stint there comes
  // back as 409 `slot_occupied`.
  start: (data: {
    username: string;
    first_name?: string;
    last_name?: string;
    email?: string;
    project_title?: string;
    storage_location_name?: string;
    slot?: string;
    slot_code?: string;
  }) =>
    api.post<ProjectStorageStint>('/project-storage/stints/start/', data),

  sendViolationNotice: (stintId: string) =>
    api.post<ProjectStorageStint>(`/project-storage/stints/${stintId}/send-violation-notice/`, {}),

  moveToPurgatory: (stintId: string, purgatoryLocationName?: string) =>
    api.post<ProjectStorageStint>(`/project-storage/stints/${stintId}/move-to-purgatory/`, {
      purgatory_location_name: purgatoryLocationName ?? '',
    }),

  markRemoved: (stintId: string, note?: string) =>
    api.post<ProjectStorageStint>(`/project-storage/stints/${stintId}/mark-removed/`, {
      note: note ?? '',
    }),

  // Warden re-queues the claim ticket for printing — the backend clears
  // printed_at so the Pi daemon reprints the label on its next poll.
  reprint: (stintId: string, note?: string) =>
    api.post<ProjectStorageStint>(`/project-storage/stints/${stintId}/reprint/`, {
      note: note ?? '',
    }),

  labelUrl: (stintId: string, printer: 'brother_ql' | 'epson_tm' = 'brother_ql') =>
    `${API_BASE_URL}/project-storage/stints/${stintId}/label/?printer=${printer}`,

  generateQr: (stintId: string, includeLogo: boolean = true) =>
    api.post<ProjectStorageStint>(
      `/project-storage/stints/${stintId}/generate-qr/`,
      { include_logo: includeLogo },
    ),
};

// ---------------------------------------------------------------------------
// Storage slots — the physical racking project storage is handed out from.
// Staff/storage-warden only (IsStorageAdminOrStaff on the whole viewset),
// so none of this is reachable from the anonymous kiosk.
// ---------------------------------------------------------------------------

export const storageSlotsAPI = {
  // Filters are hand-rolled query params on the backend (django-filter
  // isn't installed), so booleans go over the wire as "true"/"false"
  // strings — sending a JS boolean would serialize the same way but
  // typing it as a string keeps the two ends honest.
  list: (params?: {
    rack?: number;
    level?: string;
    is_active?: 'true' | 'false';
    requires_pallet_jack?: 'true' | 'false';
    occupied?: 'true' | 'false';
    page?: number;
    page_size?: number;
  }) =>
    api.get<{
      count: number;
      next: string | null;
      previous: string | null;
      results: StorageSlot[];
    }>('/project-storage/slots/', { params }),

  // Addressed by code, not pk — the code is what's printed on the rack
  // and what a scanner reads (lookup_field = "code" on the viewset).
  get: (code: string) =>
    api.get<StorageSlot>(`/project-storage/slots/${encodeURIComponent(code)}/`),

  // `code` is derived from the components server-side, so it's absent here.
  create: (data: {
    rack: number;
    level: string;
    position: number;
    requires_pallet_jack?: boolean;
    is_active?: boolean;
    owning_group?: number | null;
    notes?: string;
  }) => api.post<StorageSlot>('/project-storage/slots/', data),

  update: (code: string, data: Partial<Omit<StorageSlot, 'id' | 'code'>>) =>
    api.patch<StorageSlot>(
      `/project-storage/slots/${encodeURIComponent(code)}/`,
      data,
    ),

  // 409 when a live stint still holds the slot — deactivate instead.
  remove: (code: string) =>
    api.delete(`/project-storage/slots/${encodeURIComponent(code)}/`),

  // Bulk-create one rack from a per-level spec. Idempotent: existing codes
  // come back under `skipped`, so re-running after adding a level is safe.
  generateRack: (data: GenerateRackRequest) =>
    api.post<GenerateRackResult>('/project-storage/slots/generate/', data),

  cardPreview: (code: string) =>
    api.get<StorageSlotCardPreview>(
      `/project-storage/slots/${encodeURIComponent(code)}/card-preview/`,
    ),

  // Batch card sheet (Avery 5388, 3 cards/page). Exactly one selection
  // mode: explicit slot_ids (printed in the order given) XOR a rack
  // filter (printed in code order). Returns the PDF itself, not a link.
  printCards: (
    data:
      | { slot_ids: number[] }
      | { rack: number; level?: string; include_inactive?: boolean },
  ) =>
    api.post('/project-storage/slots/cards/', data, {
      responseType: 'blob',
    }),
};

// The glanceable rack board: one payload shaped like the racking rather
// than like the tables behind it. Staff / storage-admin only — it names
// every member with something on the shelves.
export const storageOverviewAPI = {
  // `rack` narrows to one rack server-side, which is what a phone wants
  // once the shop has more racks than fit on a screen.
  get: (params?: { rack?: number }) =>
    api.get<StorageOverview>('/project-storage/overview/', { params }),
};

// Committee / logistics / class holdings — the staff-assigned half of slot
// occupancy. No expiry, no purgatory: the lifecycle is assign → release,
// and both ends are staff-gated (IsStorageAdminOrStaff).
export const storageAssignmentsAPI = {
  // ?active=true is the console read ("who holds what right now?"); the
  // unfiltered list is every holding the racking has ever had. Booleans go
  // over the wire as strings — the filters are hand-rolled query params.
  list: (params?: {
    active?: 'true' | 'false';
    storage_type?: StorageAssignmentType;
    rack?: number;
    slot_code?: string;
    page?: number;
  }) =>
    api.get<{
      count: number;
      next: string | null;
      previous: string | null;
      results: StorageAssignment[];
    }>('/project-storage/assignments/', { params }),

  // 409 when anything already holds the slot: `slot_occupied` for a
  // member's live stint (the warden can clear that themselves) vs
  // `slot_assigned` for another holding (staff has to hand it back).
  assign: (data: AssignSlotRequest) =>
    api.post<StorageAssignment>('/project-storage/assignments/assign/', data),

  // Frees the slot by stamping released_at, not by deleting: the record of
  // the two years the welding SIG held it survives.
  release: (id: number) =>
    api.post<StorageAssignment>(`/project-storage/assignments/${id}/release/`),
};

// Universal scanner dispatcher — resolves any barcode/QR payload to an
// action descriptor without mutating state. Side effects (reorder,
// receive, asset/location check-in) follow via the per-entity endpoints
// below. AllowAny on the backend so the workshop kiosk can hit it
// without a JWT.
export interface ScanDispatchResult {
  action:
    | 'inventory_reorder'
    | 'inventory_receive'
    | 'asset_checkin'
    | 'location_checkin'
    | 'fixture'
    | 'donation_item'
    | 'project_storage_stint'
    | 'unknown';
  target_type?: string;
  target_id?: string;
  target_name?: string;
  target_url?: string;
  message?: string;
  /**
   * The two keys `ResolvedScan.VENDOR_ONLY_KEYS` withholds from a caller with
   * no session (op-anonymous-read-posture). `dispatch_scan` stays `AllowAny` —
   * it is what a barcode gun hits — but a UPC is printed on the outside of the
   * box, so anyone holding one could otherwise turn it into the name of the
   * vendor we buy that item from.
   *
   * `vendor_data_withheld` is what tells the two absences apart: withheld from
   * you, versus this scan resolved no supplier link at all.
   */
  vendor_data_withheld?: boolean;
  item_supplier_id?: number;
  supplier_name?: string;
  item_id?: string;
  item_name?: string;
  is_package?: boolean;
  current_stock?: number;
  raw_payload: string;
}

export const scannerAPI = {
  dispatch: (payload: string) =>
    api.post<ScanDispatchResult>('/scanner/dispatch/', { payload }),
};

// LocationCheckIn create — used by the universal scanner page to record
// an anonymous "I was here" stamp when someone scans a location QR.
export const locationCheckinsAPI = {
  create: (data: {
    location: number;
    checkin_type?: 'volunteer' | 'contractor' | 'anonymous';
    notes?: string;
  }) =>
    api.post<{ id: string; location: number; checked_in_at: string }>(
      '/location-checkins/check-ins/',
      { checkin_type: 'anonymous', ...data },
    ),
};

// Invite-code self-signup workflow.
//
// Staff mint single-use invite codes (`inviteCodesAdminAPI`) and send the
// resulting redemption URL to an outside person; that person hits
// `/invite/<code>` (anonymous), the page previews the role, and they
// create their own OMS account via `invitePublicAPI.redeem`. New users
// are added to the `intended_groups` on redeem, so ForgeKey door access
// follows automatically via existing group-membership rules.

export type InviteCodeState = 'open' | 'redeemed' | 'revoked' | 'expired';

export interface InviteCodeListEntry {
  id: number;
  code: string;
  intended_label: string;
  intended_group_names: string[];
  notes: string;
  expires_at: string;
  is_active: boolean;
  redeemed: boolean;
  redeemed_at: string | null;
  redeemed_by_username: string | null;
  redeemed_ip: string | null;
  created_at: string;
  created_by_username: string | null;
  state: InviteCodeState;
}

export interface InviteCodeCreatePayload {
  intended_label: string;
  intended_groups?: number[];
  notes?: string;
  expires_at: string;
}

export interface InvitePreviewResponse {
  intended_label: string;
  intended_group_names: string[];
  expires_at: string;
}

export interface InviteRedeemPayload {
  code: string;
  username: string;
  email: string;
  password: string;
  first_name?: string;
  last_name?: string;
}

export interface InviteRedeemResponse {
  message: string;
  username: string;
}

// Admin-only CRUD on invite codes. Hits `IsAdminUser`-gated endpoints.
export const inviteCodesAdminAPI = {
  list: () => api.get<{ results: InviteCodeListEntry[] }>('/membership/invite-codes/'),

  create: (data: InviteCodeCreatePayload) =>
    api.post<InviteCodeListEntry>('/membership/invite-codes/', data),

  revoke: (id: number) =>
    api.post<InviteCodeListEntry>(`/membership/invite-codes/${id}/revoke/`),

  destroy: (id: number) => api.delete(`/membership/invite-codes/${id}/`),
};

// Anonymous endpoints used by the public `/invite/<code>` page. No auth
// header is required; the page is reachable before the user has an
// account.
export const invitePublicAPI = {
  preview: (code: string) =>
    api.get<InvitePreviewResponse>('/membership/invite/preview/', {
      params: { code },
    }),

  redeem: (data: InviteRedeemPayload) =>
    api.post<InviteRedeemResponse>('/membership/invite/redeem/', data),
};

// Anonymous endpoints for the public `/register/<token>` page. An admin
// pre-creates the user + one-time token (Django admin); this page only
// validates the token and lets the new member set their password.
export interface RegistrationTokenUser {
  username: string;
  email: string;
  first_name: string;
  last_name: string;
}

export const registrationPublicAPI = {
  validate: (token: string) =>
    api.post<{ valid: boolean; user: RegistrationTokenUser; expires_at: string }>(
      '/membership/register/validate-token/',
      { token },
    ),

  complete: (data: { token: string; password: string; password2: string }) =>
    api.post<RegistrationTokenUser>('/membership/register/complete/', data),
};

export interface ForgeKeyLockerStatus {
  secure: boolean | null;
  state: string;
  reed_closed: boolean | null;
  latch_locked: boolean | null;
  ir_broken: boolean | null;
  mortise_active: boolean | null;
  item_present: boolean | null;
  last_trigger: string;
  firmware_version: string;
  last_status_at: string | null;
  is_alarm: boolean;
  is_insecure: boolean;
  device_mac: string | null;
  device_is_online: boolean | null;
}

export interface ForgeKeyLockerDevice {
  id: number;
  locker: string;
  device: string;
  device_mac: string;
  device_is_online: boolean;
  role: string;
  role_display: string;
  is_primary: boolean;
  notes: string;
}

export interface ForgeKeyLocker {
  id: string;
  name: string;
  slug: string;
  location: number | null;
  location_name: string | null;
  owning_sig: number | null;
  owning_sig_name: string | null;
  description: string;
  power_source: string;
  current_asset: string | null;
  current_asset_name: string | null;
  is_high_trust: boolean;
  led_count: number;
  required_certifications: number[];
  required_certification_names: string[];
  is_active: boolean;
  created_at: string;
  updated_at: string;
  devices: ForgeKeyLockerDevice[];
  status: ForgeKeyLockerStatus | null;
}

export interface ForgeKeyLockerWriteInput {
  name: string;
  slug?: string;
  location: number;
  owning_sig: number;
  description?: string;
  power_source?: string;
  current_asset?: string | null;
  is_high_trust?: boolean;
  led_count?: number;
  required_certifications?: number[];
  is_active?: boolean;
}

export interface ForgeKeyCertificationOption {
  id: number;
  name: string;
}

export interface ForgeKeyLockerOtp {
  id: string;
  locker: string;
  locker_name: string;
  requesting_user: number | null;
  code: string;
  expires_at: string;
  used_at: string | null;
  revoked_at: string | null;
  revoked_by: number | null;
  created_at: string;
  state: string;
}

export const lockersAPI = {
  listLockers: () => api.get<{ results?: ForgeKeyLocker[] } | ForgeKeyLocker[]>('/lockers/'),
  getLocker: (id: string) => api.get<ForgeKeyLocker>(`/lockers/${id}/`),
  unlock: (id: string) =>
    api.post<{ status: string; topic: string; reason: string }>(`/lockers/${id}/unlock/`),
  issueOtp: (id: string) => api.post<ForgeKeyLockerOtp>(`/lockers/${id}/issue-otp/`),
  listOtps: (id: string) => api.get<ForgeKeyLockerOtp[]>(`/lockers/${id}/otps/`),
  revokeOtp: (id: string, otpId: string) =>
    api.post<ForgeKeyLockerOtp>(`/lockers/${id}/revoke-otp/`, { otp_id: otpId }),
  // Setup / device binding (manager-gated server-side).
  createLocker: (data: ForgeKeyLockerWriteInput) => api.post<ForgeKeyLocker>('/lockers/', data),
  updateLocker: (id: string, data: Partial<ForgeKeyLockerWriteInput>) =>
    api.patch<ForgeKeyLocker>(`/lockers/${id}/`, data),
  deleteLocker: (id: string) => api.delete(`/lockers/${id}/`),
  addLockerDevice: (
    id: string,
    data: { device: string; role: string; is_primary?: boolean; notes?: string },
  ) => api.post<ForgeKeyLockerDevice>(`/lockers/${id}/devices/`, data),
  removeLockerDevice: (id: string, assignmentId: number) =>
    api.delete(`/lockers/${id}/devices/${assignmentId}/`),
  listAvailableCertifications: () =>
    api.get<ForgeKeyCertificationOption[]>('/lockers/available-certifications/'),
};

// ---------------------------------------------------------------------------
// Storage Vision (AC-28 setup + AC-30 capture + review surfaces)
// ---------------------------------------------------------------------------

export interface VisionArea {
  id: number;
  name: string;
  location: number;
  location_name: string;
  description: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface VisionSlot {
  id: number;
  area: number;
  area_name: string;
  item: string;
  item_name: string;
  marker_code: string;
  empty_low_confidence_threshold: string; // Decimal serialized as string
  notes: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface VisionCamera {
  id: number;
  name: string;
  area: number | null;
  area_name: string | null;
  token_fingerprint: string;
  last_seen_at: string | null;
  last_seen_status: Record<string, unknown>;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

/**
 * Returned ONLY from POST /cameras/ and POST /cameras/{id}/rotate-token/.
 * The raw_token field is the bearer the device will use; it's only
 * shown once. List/retrieve responses use the VisionCamera shape.
 */
export interface VisionCameraWithToken extends VisionCamera {
  raw_token: string;
}

export type VisionCaptureSource = 'phone' | 'camera';
export type VisionCaptureStatus =
  | 'queued'
  | 'processing'
  | 'processed'
  | 'failed';

export interface VisionCapture {
  id: number;
  area: number;
  area_name: string;
  source: VisionCaptureSource;
  camera: number | null;
  uploaded_by: number | null;
  original_image: string | null;
  captured_at: string | null;
  received_at: string;
  status: VisionCaptureStatus;
  processor_version: string;
  markers_detected: Array<{
    marker_code: string;
    bbox: [number, number, number, number];
    confidence: number;
    matched_slot_id: number | null;
  }>;
  failure_reason: string;
  failure_code: string;
  queued_at: string;
  processing_at: string | null;
  processed_at: string | null;
  failed_at: string | null;
}

export type VisionObservationClass = 'empty' | 'low' | 'full' | 'unknown';
export type VisionObservationAction = 'review_only' | 'reconcile_empty';
export type VisionObservationStatus =
  | 'pending'
  | 'approved'
  | 'rejected'
  | 'superseded';

export interface VisionObservation {
  id: number;
  capture: number;
  capture_thumbnail: string | null;
  slot: number;
  slot_marker_code: string;
  area_id: number;
  area_name: string;
  item_id: string;
  item_name: string;
  classification: VisionObservationClass;
  confidence: string;
  evidence_crop: string | null;
  model_version: string;
  suggested_action: VisionObservationAction;
  status: VisionObservationStatus;
  duplicate_count: number;
  last_duplicate_at: string | null;
  age_seconds: number;
  created_at: string;
  updated_at: string;
}

export interface VisionObservationApproveResponse extends VisionObservation {
  reconciliation_id: number | null;
  reorder_created: boolean;
}

export interface VisionBulkApproveResponse {
  approved: Array<{
    id: number;
    reconciliation_id: number | null;
    reorder_created: boolean;
  }>;
  skipped: Array<{ id: number; reason: string }>;
  counts: { requested: number; approved: number; skipped: number };
}

export interface VisionAreaInput {
  name: string;
  location: number;
  description?: string;
  is_active?: boolean;
}

export interface VisionSlotInput {
  area: number;
  item: string;
  marker_code: string;
  empty_low_confidence_threshold?: string;
  notes?: string;
  is_active?: boolean;
}

export interface VisionCameraInput {
  name: string;
  area?: number | null;
  is_active?: boolean;
}

export const storageVisionAPI = {
  // Areas (AC-3, AC-4)
  listAreas: () =>
    api.get<{ results: VisionArea[] } | VisionArea[]>('/storage-vision/areas/'),
  createArea: (data: VisionAreaInput) =>
    api.post<VisionArea>('/storage-vision/areas/', data),
  updateArea: (id: number, data: Partial<VisionAreaInput>) =>
    api.patch<VisionArea>(`/storage-vision/areas/${id}/`, data),
  deleteArea: (id: number) => api.delete(`/storage-vision/areas/${id}/`),

  // Slots (AC-5, AC-6)
  listSlots: (params?: { area?: number; item?: string }) =>
    api.get<{ results: VisionSlot[] } | VisionSlot[]>('/storage-vision/slots/', {
      params,
    }),
  createSlot: (data: VisionSlotInput) =>
    api.post<VisionSlot>('/storage-vision/slots/', data),
  updateSlot: (id: number, data: Partial<VisionSlotInput>) =>
    api.patch<VisionSlot>(`/storage-vision/slots/${id}/`, data),
  deleteSlot: (id: number) => api.delete(`/storage-vision/slots/${id}/`),
  /**
   * Returns a printable PNG label as a Blob — the caller turns it
   * into a download or an inline preview (AC-6).
   */
  downloadSlotMarker: (id: number) =>
    api.get<Blob>(`/storage-vision/slots/${id}/marker/`, {
      responseType: 'blob',
    }),

  // Cameras (AC-7, AC-8)
  listCameras: () =>
    api.get<{ results: VisionCamera[] } | VisionCamera[]>(
      '/storage-vision/cameras/',
    ),
  createCamera: (data: VisionCameraInput) =>
    api.post<VisionCameraWithToken>('/storage-vision/cameras/', data),
  updateCamera: (id: number, data: Partial<VisionCameraInput>) =>
    api.patch<VisionCamera>(`/storage-vision/cameras/${id}/`, data),
  deleteCamera: (id: number) => api.delete(`/storage-vision/cameras/${id}/`),
  rotateCameraToken: (id: number) =>
    api.post<VisionCameraWithToken>(
      `/storage-vision/cameras/${id}/rotate-token/`,
    ),

  // Captures (AC-9 phone path; AC-10 camera path goes from the
  // device, not the browser)
  listCaptures: (params?: {
    area?: number;
    status?: VisionCaptureStatus;
  }) =>
    api.get<{ results: VisionCapture[] } | VisionCapture[]>(
      '/storage-vision/captures/',
      { params },
    ),
  getCapture: (id: number) =>
    api.get<VisionCapture>(`/storage-vision/captures/${id}/`),
  uploadCapture: (formData: FormData) =>
    api.post<VisionCapture>('/storage-vision/captures/', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }),

  // Observations (AC-20+)
  listObservations: (params?: {
    status?: VisionObservationStatus;
    area?: number;
    item?: string;
    suggested_action?: VisionObservationAction;
    classification?: VisionObservationClass;
  }) =>
    api.get<{ results: VisionObservation[] } | VisionObservation[]>(
      '/storage-vision/observations/',
      { params },
    ),
  approveObservation: (id: number, reason?: string) =>
    api.post<VisionObservationApproveResponse>(
      `/storage-vision/observations/${id}/approve/`,
      reason ? { reason } : {},
    ),
  rejectObservation: (id: number, reason: string) =>
    api.post<VisionObservation>(
      `/storage-vision/observations/${id}/reject/`,
      { reason },
    ),
  bulkApprove: (observation_ids: number[], reason?: string) =>
    api.post<VisionBulkApproveResponse>(
      '/storage-vision/observations/bulk-approve/',
      reason ? { observation_ids, reason } : { observation_ids },
    ),
};

// ---------------------------------------------------------------------------
// Service status (resilience)
// ---------------------------------------------------------------------------

// Which external dependencies are working right now, derived from the backend
// circuit breakers. Always 200 for an authenticated caller — a degraded
// dependency is reported in the body, never as an error status. Polled by
// ServiceStatusProvider; see contexts/ServiceStatusContext.tsx.
export const resilienceAPI = {
  getStatus: () => api.get<ResilienceStatus>('/resilience/status/'),
};

export default api;
