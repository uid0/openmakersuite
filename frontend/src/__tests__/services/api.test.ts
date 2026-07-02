/**
 * Tests for API service
 */
import MockAdapter from 'axios-mock-adapter';
import api, { assetPartsAPI, assetsAPI, authAPI, checklistsAPI, inventoryAPI, purchaseOrderAPI, reorderAPI, resolveApiBaseUrl, workOrderAPI } from '../../services/api';

describe('API Service', () => {
  let mock: MockAdapter;

  beforeEach(() => {
    mock = new MockAdapter(api);
    localStorage.clear();
  });

  afterEach(() => {
    mock.restore();
  });

  describe('Inventory API', () => {
    test('getItem fetches a single item', async () => {
      const mockItem = {
        id: 'test-id',
        name: 'Test Item',
        description: 'Test description',
      };

      mock.onGet('/inventory/items/test-id/').reply(200, mockItem);

      const response = await inventoryAPI.getItem('test-id');

      expect(response.data).toEqual(mockItem);
      expect(response.status).toBe(200);
    });

    test('listItems fetches multiple items', async () => {
      const mockItems = {
        results: [
          { id: '1', name: 'Item 1' },
          { id: '2', name: 'Item 2' },
        ],
      };

      mock.onGet('/inventory/items/').reply(200, mockItems);

      const response = await inventoryAPI.listItems();

      expect(response.data.results).toHaveLength(2);
      expect(response.status).toBe(200);
    });

    test('listLocations normalizes array responses', async () => {
      const mockLocations = [
        { id: 1, name: 'Main Workshop' },
        { id: 2, name: 'Electronics Lab' },
      ];

      mock.onGet('/inventory/locations/').reply(200, mockLocations);

      const response = await inventoryAPI.listLocations();

      expect(response.data.results).toEqual(mockLocations);
    });

    test('getLowStockItems fetches low stock items', async () => {
      const mockItems = [
        { id: '1', name: 'Low Stock Item', current_stock: 2 },
      ];

      mock.onGet('/inventory/items/low_stock/').reply(200, mockItems);

      const response = await inventoryAPI.getLowStockItems();

      expect(response.data).toEqual(mockItems);
    });

    test('downloadCard requests blob response', async () => {
      mock
        .onGet('/inventory/items/test-id/download_card/')
        .reply((config) => {
          expect(config.responseType).toBe('blob');
          return [200, 'card-bytes'];
        });

      const response = await inventoryAPI.downloadCard('test-id');

      expect(response.data).toBe('card-bytes');
    });

    test('generateQR posts without payload', async () => {
      mock.onPost('/inventory/items/test-id/generate_qr/').reply((config) => {
        expect(config.data).toBeUndefined();
        return [201, { qr: 'data' }];
      });

      const response = await inventoryAPI.generateQR('test-id');

      expect(response.status).toBe(201);
      expect(response.data).toEqual({ qr: 'data' });
    });

    test('logUsage logs item usage', async () => {
      const mockResponse = {
        id: 1,
        item: 'test-id',
        quantity_used: 5,
      };

      mock.onPost('/inventory/items/test-id/log_usage/').reply(200, mockResponse);

      const response = await inventoryAPI.logUsage('test-id', 5, 'Test notes');

      expect(response.data).toEqual(mockResponse);
    });

    test('createItem creates new item with FormData', async () => {
      const formData = new FormData();
      formData.append('name', 'New Item');
      formData.append('current_stock', '10');

      const mockResponse = {
        id: 'new-id',
        name: 'New Item',
        current_stock: 10,
      };

      mock.onPost('/inventory/items/').reply((config) => {
        expect(config.headers['Content-Type']).toContain('multipart/form-data');
        return [201, mockResponse];
      });

      const response = await inventoryAPI.createItem(formData);

      expect(response.data).toEqual(mockResponse);
      expect(response.status).toBe(201);
    });

    test('createItem creates new item with JSON data', async () => {
      const itemData = {
        name: 'New Item',
        current_stock: 10,
        minimum_stock: 5,
        reorder_quantity: 20,
      };

      const mockResponse = {
        id: 'new-id',
        ...itemData,
      };

      mock.onPost('/inventory/items/').reply(201, mockResponse);

      const response = await inventoryAPI.createItem(itemData);

      expect(response.data).toEqual(mockResponse);
    });

    test('updateItem updates item with FormData', async () => {
      const formData = new FormData();
      formData.append('name', 'Updated Item');

      const mockResponse = {
        id: 'test-id',
        name: 'Updated Item',
      };

      mock.onPatch('/inventory/items/test-id/').reply((config) => {
        expect(config.headers['Content-Type']).toContain('multipart/form-data');
        return [200, mockResponse];
      });

      const response = await inventoryAPI.updateItem('test-id', formData);

      expect(response.data).toEqual(mockResponse);
    });

    test('updateStock updates stock only', async () => {
      const mockResponse = {
        id: 'test-id',
        current_stock: 15,
      };

      mock.onPatch('/inventory/items/test-id/').reply((config) => {
        expect(JSON.parse(config.data)).toEqual({ current_stock: 15 });
        return [200, mockResponse];
      });

      const response = await inventoryAPI.updateStock('test-id', 15);

      expect(response.data.current_stock).toBe(15);
    });

    test('getUsageLogs fetches usage logs for item', async () => {
      const mockLogs = {
        results: [
          {
            id: 1,
            item: 'test-id',
            quantity_used: 5,
            usage_date: '2024-01-15T00:00:00Z',
            notes: 'Test usage',
          },
        ],
      };

      mock.onGet('/inventory/usage-logs/?item_id=test-id').reply(200, mockLogs);

      const response = await inventoryAPI.getUsageLogs('test-id');

      expect(response.data.results).toHaveLength(1);
      expect(response.data.results[0].quantity_used).toBe(5);
    });

    test('createCategory creates new category', async () => {
      const categoryData = {
        name: 'New Category',
        description: 'Category description',
      };

      const mockResponse = {
        id: 1,
        ...categoryData,
        slug: 'new-category',
        parent: null,
      };

      mock.onPost('/inventory/categories/').reply(201, mockResponse);

      const response = await inventoryAPI.createCategory(categoryData);

      expect(response.data).toEqual(mockResponse);
    });
  });

  describe('Reorder API', () => {
    test('createRequest creates a reorder request', async () => {
      const requestData = {
        item: 'test-id',
        quantity: 25,
        requested_by: 'Test User',
      };

      const mockResponse = {
        id: 1,
        ...requestData,
        status: 'pending',
      };

      mock.onPost('/reorders/requests/').reply(201, mockResponse);

      const response = await reorderAPI.createRequest(requestData);

      expect(response.data).toEqual(mockResponse);
      expect(response.status).toBe(201);
    });

    test('getPendingRequests fetches pending requests', async () => {
      const mockRequests = [
        { id: 1, status: 'pending' },
        { id: 2, status: 'pending' },
      ];

      mock.onGet('/reorders/requests/pending/').reply(200, mockRequests);

      const response = await reorderAPI.getPendingRequests();

      expect(response.data).toEqual(mockRequests);
    });

    test('approveRequest approves a request', async () => {
      const mockResponse = {
        id: 1,
        status: 'approved',
      };

      mock.onPost('/reorders/requests/1/approve/').reply(200, mockResponse);

      const response = await reorderAPI.approveRequest(1, 'Approved');

      expect(response.data.status).toBe('approved');
    });

    test('listRequests passes query parameters', async () => {
      const mockResponse = {
        results: [{ id: 7, status: 'ordered' }],
      };

      mock.onGet('/reorders/requests/').reply((config) => {
        expect(config.params).toEqual({ status: 'ordered' });
        return [200, mockResponse];
      });

      const response = await reorderAPI.listRequests({ status: 'ordered' });

      expect(response.data.results[0].status).toBe('ordered');
    });

    test('markReceived marks request as received', async () => {
      const mockResponse = {
        id: 1,
        status: 'received',
      };

      mock.onPost('/reorders/requests/1/mark_received/').reply(200, mockResponse);

      const response = await reorderAPI.markReceived(1);

      expect(response.data.status).toBe('received');
    });

    test('markOrdered sends payload to endpoint', async () => {
      const requestBody = {
        order_number: 'PO-123',
        estimated_delivery: '2024-05-01',
        actual_cost: 42.5,
      };

      mock.onPost('/reorders/requests/1/mark_ordered/').reply((config) => {
        expect(JSON.parse(config.data)).toEqual(requestBody);
        return [200, { id: 1, status: 'ordered' }];
      });

      const response = await reorderAPI.markOrdered(1, requestBody);

      expect(response.data.status).toBe('ordered');
    });

    test('cancelRequest posts admin notes', async () => {
      mock.onPost('/reorders/requests/1/cancel/').reply((config) => {
        expect(JSON.parse(config.data)).toEqual({ admin_notes: 'No stock' });
        return [200, { id: 1, status: 'cancelled' }];
      });

      const response = await reorderAPI.cancelRequest(1, 'No stock');

      expect(response.data.status).toBe('cancelled');
    });

    test('generateCartLinks fetches generated links', async () => {
      const mockResponse = { links: ['http://example.com'] };

      mock.onGet('/reorders/requests/generate_cart_links/').reply(200, mockResponse);

      const response = await reorderAPI.generateCartLinks();

      expect(response.data.links).toHaveLength(1);
    });

    test('getBySupplier returns grouped data', async () => {
      const mockResponse = { supplier: [{ id: 1 }] };

      mock.onGet('/reorders/requests/by_supplier/').reply(200, mockResponse);

      // eslint-disable-next-line testing-library/no-await-sync-query
      const response = await reorderAPI.getBySupplier();

      expect(response.data).toEqual(mockResponse);
    });
  });

  describe('Purchase Order API', () => {
    test('sendToSupplier posts to the send_to_supplier action without a body', async () => {
      const mockResponse = { id: 'po-1', status: 'sent', status_label: 'Sent' };

      mock.onPost('/reorders/purchase-orders/po-1/send_to_supplier/').reply((config) => {
        expect(config.data).toBeUndefined();
        return [200, mockResponse];
      });

      const response = await purchaseOrderAPI.sendToSupplier('po-1');

      expect(response.status).toBe(200);
      expect(response.data.status).toBe('sent');
    });

    test('confirmOrder posts to the confirm_order action with no body by default', async () => {
      const mockResponse = { id: 'po-1', status: 'confirmed', status_label: 'Confirmed' };

      mock.onPost('/reorders/purchase-orders/po-1/confirm_order/').reply((config) => {
        expect(config.data).toBeUndefined();
        return [200, mockResponse];
      });

      const response = await purchaseOrderAPI.confirmOrder('po-1');

      expect(response.data.status).toBe('confirmed');
    });

    test('confirmOrder forwards an expected_delivery_date when supplied', async () => {
      const mockResponse = { id: 'po-1', status: 'confirmed' };

      mock.onPost('/reorders/purchase-orders/po-1/confirm_order/').reply((config) => {
        expect(JSON.parse(config.data)).toEqual({ expected_delivery_date: '2026-06-01' });
        return [200, mockResponse];
      });

      const response = await purchaseOrderAPI.confirmOrder('po-1', {
        expected_delivery_date: '2026-06-01',
      });

      expect(response.data.status).toBe('confirmed');
    });
  });

  describe('Auth API', () => {
    test('login authenticates user', async () => {
      const mockResponse = {
        access: 'access-token',
        refresh: 'refresh-token',
      };

      mock.onPost('/auth/login/').reply(200, mockResponse);

      const response = await authAPI.login('testuser', 'password');

      expect(response.data).toEqual(mockResponse);
    });

    test('refresh refreshes access token', async () => {
      const mockResponse = {
        access: 'new-access-token',
      };

      mock.onPost('/auth/refresh/').reply(200, mockResponse);

      const response = await authAPI.refresh('refresh-token');

      expect(response.data.access).toBe('new-access-token');
    });

    test('logout posts to the unified logout endpoint', async () => {
      mock.onPost('/auth/logout/').reply(200, { detail: 'Logged out' });

      const response = await authAPI.logout();

      expect(response.status).toBe(200);
      expect(response.data.detail).toBe('Logged out');
    });

    test('requests include credentials so the session cookie rides along', () => {
      // withCredentials must be true on the shared axios instance so that the
      // Django session cookie set by /api/auth/login/ is sent on subsequent
      // requests — this is how one sign-in covers admin + DRF browsable API.
      expect(api.defaults.withCredentials).toBe(true);
    });
  });

  describe('Authentication Interceptor', () => {
    test('adds auth token to requests when available', async () => {
      localStorage.setItem('token', 'test-token');

      mock.onGet('/inventory/items/test-id/').reply((config) => {
        expect(config.headers?.Authorization).toBe('Bearer test-token');
        return [200, {}];
      });

      await inventoryAPI.getItem('test-id');
    });

    test('does not add auth token when not available', async () => {
      mock.onGet('/inventory/items/test-id/').reply((config) => {
        expect(config.headers?.Authorization).toBeUndefined();
        return [200, {}];
      });

      await inventoryAPI.getItem('test-id');
    });
  });

  describe('Checklists API', () => {
    test('getAvailableChecklists fetches available checklists', async () => {
      const mockChecklists = [
        { id: '1', name: 'Monthly Walk', description: 'Monthly safety walk' },
        { id: '2', name: 'Weekly Check', description: 'Weekly equipment check' },
      ];

      mock.onGet('/checklists/checklists/available/').reply(200, mockChecklists);

      const response = await checklistsAPI.getAvailableChecklists();

      expect(response.data).toEqual(mockChecklists);
      expect(response.status).toBe(200);
    });

    test('getAvailableChecklists passes query parameters', async () => {
      const mockChecklists = [{ id: '1', name: 'Test Checklist' }];

      mock.onGet('/checklists/checklists/available/').reply((config) => {
        expect(config.params).toEqual({ asset_id: 'test-asset-id' });
        return [200, mockChecklists];
      });

      const response = await checklistsAPI.getAvailableChecklists({ asset_id: 'test-asset-id' });

      expect(response.data).toEqual(mockChecklists);
    });

    test('getChecklist fetches a single checklist', async () => {
      const mockChecklist = {
        id: 'test-checklist-id',
        name: 'Test Checklist',
        description: 'Test description',
        steps: [],
      };

      mock.onGet('/checklists/checklists/test-checklist-id/detail/').reply(200, mockChecklist);

      const response = await checklistsAPI.getChecklist('test-checklist-id');

      expect(response.data).toEqual(mockChecklist);
      expect(response.status).toBe(200);
    });

    test('startChecklist creates a new completion', async () => {
      const mockCompletion = {
        id: 'completion-id',
        checklist: 'test-checklist-id',
        status: 'in_progress',
        user_name: 'John Doe',
      };

      mock.onPost('/checklists/checklists/test-checklist-id/start/').reply((config) => {
        expect(JSON.parse(config.data)).toEqual({ user_name: 'John Doe' });
        return [201, mockCompletion];
      });

      const response = await checklistsAPI.startChecklist('test-checklist-id', 'John Doe');

      expect(response.data).toEqual(mockCompletion);
      expect(response.status).toBe(201);
    });

    test('getCompletion fetches a completion record', async () => {
      const mockCompletion = {
        id: 'completion-id',
        checklist: 'test-checklist-id',
        status: 'in_progress',
        step_completions: [],
      };

      mock.onGet('/checklists/completions/completion-id/').reply(200, mockCompletion);

      const response = await checklistsAPI.getCompletion('completion-id');

      expect(response.data).toEqual(mockCompletion);
    });

    test('scanStep records a step scan', async () => {
      const mockCompletion = {
        id: 'completion-id',
        status: 'in_progress',
        step_completions: [{ step: 'step-id', scanned_asset: 'asset-id' }],
      };

      mock.onPost('/checklists/completions/completion-id/scan/').reply((config) => {
        const data = JSON.parse(config.data);
        expect(data).toEqual({
          step_id: 'step-id',
          asset_id: 'asset-id',
          notes: 'Test notes',
        });
        return [200, mockCompletion];
      });

      const response = await checklistsAPI.scanStep(
        'completion-id',
        'step-id',
        { asset_id: 'asset-id' },
        'Test notes'
      );

      expect(response.data).toEqual(mockCompletion);
    });

    test('completeChecklist marks checklist as completed', async () => {
      const mockCompletion = {
        id: 'completion-id',
        status: 'completed',
        completed_at: '2024-01-01T00:00:00Z',
      };

      mock.onPost('/checklists/completions/completion-id/complete/').reply(200, mockCompletion);

      const response = await checklistsAPI.completeChecklist('completion-id');

      expect(response.data.status).toBe('completed');
    });
  });

  describe('Assets API', () => {
    test('listAssets fetches assets with filters', async () => {
      const mockAssets = {
        results: [
          { id: '1', name: 'Asset 1', status: 'active' },
          { id: '2', name: 'Asset 2', status: 'maintenance' },
        ],
      };

      mock.onGet('/inventory/assets/').reply((config) => {
        expect(config.params).toEqual({ status: 'active', location: 1 });
        return [200, mockAssets];
      });

      const response = await assetsAPI.listAssets({ status: 'active', location: 1 });

      expect(response.data.results).toHaveLength(2);
      expect(response.status).toBe(200);
    });

    test('getAsset fetches a single asset', async () => {
      const mockAsset = {
        id: 'test-id',
        name: 'Test Asset',
        status: 'active',
      };

      mock.onGet('/inventory/assets/test-id/').reply(200, mockAsset);

      const response = await assetsAPI.getAsset('test-id');

      expect(response.data).toEqual(mockAsset);
      expect(response.status).toBe(200);
    });

    test('getAssetProblems fetches problems for an asset', async () => {
      const mockProblems = [
        {
          id: '1',
          asset: 'test-id',
          description: 'Test problem',
          status: 'reported',
        },
      ];

      mock.onGet('/inventory/assets/test-id/get_problems/').reply(200, mockProblems);

      const response = await assetsAPI.getAssetProblems('test-id');

      expect(response.data).toEqual(mockProblems);
      expect(response.status).toBe(200);
    });

    test('createAsset creates new asset', async () => {
      const assetData = {
        name: 'New Asset',
        status: 'active',
      };

      const mockResponse = {
        id: 'new-id',
        ...assetData,
      };

      mock.onPost('/inventory/assets/').reply(201, mockResponse);

      const response = await assetsAPI.createAsset(assetData);

      expect(response.data).toEqual(mockResponse);
      expect(response.status).toBe(201);
    });

    test('updateAsset updates asset', async () => {
      const assetData = {
        name: 'Updated Asset',
      };

      const mockResponse = {
        id: 'test-id',
        ...assetData,
      };

      mock.onPatch('/inventory/assets/test-id/').reply(200, mockResponse);

      const response = await assetsAPI.updateAsset('test-id', assetData);

      expect(response.data).toEqual(mockResponse);
    });

    test('lockAsset locks an asset', async () => {
      const mockResponse = {
        id: 'test-id',
        is_locked: true,
      };

      mock.onPost('/inventory/assets/test-id/lock/').reply(200, mockResponse);

      const response = await assetsAPI.lockAsset('test-id');

      expect(response.data.is_locked).toBe(true);
    });

    test('unlockAsset unlocks an asset', async () => {
      const mockResponse = {
        id: 'test-id',
        is_locked: false,
      };

      mock.onPost('/inventory/assets/test-id/unlock/').reply(200, mockResponse);

      const response = await assetsAPI.unlockAsset('test-id');

      expect(response.data.is_locked).toBe(false);
    });

    test('reportProblem reports a problem', async () => {
      const mockResponse = {
        id: '1',
        asset: 'test-id',
        description: 'Broken part',
        status: 'reported',
      };

      mock.onPost('/inventory/assets/test-id/report_problem/').reply((config) => {
        expect(JSON.parse(config.data)).toEqual({ description: 'Broken part' });
        return [201, mockResponse];
      });

      const response = await assetsAPI.reportProblem('test-id', 'Broken part');

      expect(response.data.description).toBe('Broken part');
    });

    test('generateQR generates QR code', async () => {
      mock.onPost('/inventory/assets/test-id/generate_qr/').reply(200, {});

      const response = await assetsAPI.generateQR('test-id');

      expect(response.status).toBe(200);
    });
  });

  describe('Asset Parts API', () => {
    test('listAssetParts fetches parts for an asset', async () => {
      const mockParts = {
        results: [
          {
            id: '1',
            asset: 'test-id',
            part: 'part-id',
            part_name: 'Test Part',
          },
        ],
      };

      mock.onGet('/inventory/asset-parts/').reply((config) => {
        expect(config.params).toEqual({ asset: 'test-id' });
        return [200, mockParts];
      });

      const response = await assetPartsAPI.listAssetParts({ asset: 'test-id' });

      expect(response.data.results).toHaveLength(1);
    });

    test('markReplaced marks a part as replaced', async () => {
      const mockResponse = {
        id: '1',
        last_replaced_at: '2024-01-01T00:00:00Z',
      };

      mock.onPost('/inventory/asset-parts/1/mark_replaced/').reply(200, mockResponse);

      const response = await assetPartsAPI.markReplaced('1');

      expect(response.data.last_replaced_at).toBeDefined();
    });
  });

  describe('resolveApiBaseUrl (deployment-configurable API base URL)', () => {
    const ORIGINAL_LOCATION = window.location;

    const setHostname = (hostname: string) => {
      Object.defineProperty(window, 'location', {
        configurable: true,
        value: { ...ORIGINAL_LOCATION, hostname },
      });
    };

    beforeEach(() => {
      vi.unstubAllEnvs();
    });

    afterEach(() => {
      vi.unstubAllEnvs();
      Object.defineProperty(window, 'location', {
        configurable: true,
        value: ORIGINAL_LOCATION,
      });
    });

    test('uses absolute VITE_API_URL and appends /api when missing', () => {
      vi.stubEnv('VITE_API_URL', 'https://api.example.com');
      expect(resolveApiBaseUrl()).toBe('https://api.example.com/api');
    });

    test('does not duplicate /api when VITE_API_URL already ends with /api', () => {
      vi.stubEnv('VITE_API_URL', 'https://api.example.com/api');
      expect(resolveApiBaseUrl()).toBe('https://api.example.com/api');
    });

    test('strips trailing slashes before adding /api', () => {
      vi.stubEnv('VITE_API_URL', 'https://api.example.com/');
      expect(resolveApiBaseUrl()).toBe('https://api.example.com/api');
    });

    test('honors a relative /api value (the documented prod default)', () => {
      vi.stubEnv('VITE_API_URL', '/api');
      expect(resolveApiBaseUrl()).toBe('/api');
    });

    test('falls back to localhost Django backend only when running on localhost without env override', () => {
      setHostname('localhost');
      expect(resolveApiBaseUrl()).toBe('http://localhost:8000/api');
    });

    test('falls back to relative /api on a deployed host without env override (no localhost coupling)', () => {
      setHostname('makerspace.example.org');
      expect(resolveApiBaseUrl()).toBe('/api');
    });

    test('does not bake private container hostnames into the resolved URL', () => {
      // The frontend bundle ships to browsers; it must never resolve to
      // docker-network names like "backend" or "django" that only exist
      // inside the cluster.
      setHostname('makerspace.example.org');
      const url = resolveApiBaseUrl();
      expect(url).not.toMatch(/\bbackend\b/);
      expect(url).not.toMatch(/\bdjango\b/);
    });
  });

  describe('workOrderAPI.getPdfUrl uses the resolved API base URL', () => {
    test('produces a URL prefixed by the resolved API base URL', () => {
      const url = workOrderAPI.getPdfUrl('abc-123');
      // The module-level API_BASE_URL is captured on import. In jsdom the
      // hostname is "localhost" and REACT_APP_API_URL is unset by default,
      // so the test environment resolves to http://localhost:8000/api.
      expect(url).toBe('http://localhost:8000/api/inventory/work-orders/abc-123/pdf/');
    });
  });
});
