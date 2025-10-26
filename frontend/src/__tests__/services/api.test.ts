/**
 * Tests for API service
 */
import MockAdapter from 'axios-mock-adapter';
import * as Sentry from '@sentry/react';
import api, { inventoryAPI, reorderAPI, authAPI } from '../../services/api';

jest.mock('@sentry/react', () => ({
  captureException: jest.fn(),
}));

describe('API Service', () => {
  let mock: MockAdapter;

  beforeEach(() => {
    mock = new MockAdapter(api);
    localStorage.clear();
    (Sentry.captureException as jest.Mock).mockClear();
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

      mock.onGet('/api/inventory/items/test-id/').reply(200, mockItem);

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

      mock.onGet('/api/inventory/items/').reply(200, mockItems);

      const response = await inventoryAPI.listItems();

      expect(response.data.results).toHaveLength(2);
      expect(response.status).toBe(200);
    });

    test('getLowStockItems fetches low stock items', async () => {
      const mockItems = [
        { id: '1', name: 'Low Stock Item', current_stock: 2 },
      ];

      mock.onGet('/api/inventory/items/low_stock/').reply(200, mockItems);

      const response = await inventoryAPI.getLowStockItems();

      expect(response.data).toEqual(mockItems);
    });

    test('downloadCard requests blob response', async () => {
      mock
        .onGet('/api/inventory/items/test-id/download_card/')
        .reply((config) => {
          expect(config.responseType).toBe('blob');
          return [200, 'card-bytes'];
        });

      const response = await inventoryAPI.downloadCard('test-id');

      expect(response.data).toBe('card-bytes');
    });

    test('generateQR posts without payload', async () => {
      mock.onPost('/api/inventory/items/test-id/generate_qr/').reply((config) => {
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

      mock.onPost('/api/inventory/items/test-id/log_usage/').reply(200, mockResponse);

      const response = await inventoryAPI.logUsage('test-id', 5, 'Test notes');

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

      mock.onPost('/api/reorders/requests/').reply(201, mockResponse);

      const response = await reorderAPI.createRequest(requestData);

      expect(response.data).toEqual(mockResponse);
      expect(response.status).toBe(201);
    });

    test('getPendingRequests fetches pending requests', async () => {
      const mockRequests = [
        { id: 1, status: 'pending' },
        { id: 2, status: 'pending' },
      ];

      mock.onGet('/api/reorders/requests/pending/').reply(200, mockRequests);

      const response = await reorderAPI.getPendingRequests();

      expect(response.data).toEqual(mockRequests);
    });

    test('approveRequest approves a request', async () => {
      const mockResponse = {
        id: 1,
        status: 'approved',
      };

      mock.onPost('/api/reorders/requests/1/approve/').reply(200, mockResponse);

      const response = await reorderAPI.approveRequest(1, 'Approved');

      expect(response.data.status).toBe('approved');
    });

    test('listRequests passes query parameters', async () => {
      const mockResponse = {
        results: [{ id: 7, status: 'ordered' }],
      };

      mock.onGet('/api/reorders/requests/').reply((config) => {
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

      mock.onPost('/api/reorders/requests/1/mark_received/').reply(200, mockResponse);

      const response = await reorderAPI.markReceived(1);

      expect(response.data.status).toBe('received');
    });

    test('markOrdered sends payload to endpoint', async () => {
      const requestBody = {
        order_number: 'PO-123',
        estimated_delivery: '2024-05-01',
        actual_cost: 42.5,
      };

      mock.onPost('/api/reorders/requests/1/mark_ordered/').reply((config) => {
        expect(JSON.parse(config.data)).toEqual(requestBody);
        return [200, { id: 1, status: 'ordered' }];
      });

      const response = await reorderAPI.markOrdered(1, requestBody);

      expect(response.data.status).toBe('ordered');
    });

    test('cancelRequest posts admin notes', async () => {
      mock.onPost('/api/reorders/requests/1/cancel/').reply((config) => {
        expect(JSON.parse(config.data)).toEqual({ admin_notes: 'No stock' });
        return [200, { id: 1, status: 'cancelled' }];
      });

      const response = await reorderAPI.cancelRequest(1, 'No stock');

      expect(response.data.status).toBe('cancelled');
    });

    test('generateCartLinks fetches generated links', async () => {
      const mockResponse = { links: ['http://example.com'] };

      mock.onGet('/api/reorders/requests/generate_cart_links/').reply(200, mockResponse);

      const response = await reorderAPI.generateCartLinks();

      expect(response.data.links).toHaveLength(1);
    });

    test('getBySupplier returns grouped data', async () => {
      const mockResponse = { supplier: [{ id: 1 }] };

      mock.onGet('/api/reorders/requests/by_supplier/').reply(200, mockResponse);

      const response = reorderAPI.getBySupplier();

      expect(response.data).toEqual(mockResponse);
    });
  });

  describe('Auth API', () => {
    test('login authenticates user', async () => {
      const mockResponse = {
        access: 'access-token',
        refresh: 'refresh-token',
      };

      mock.onPost('/api/token/').reply(200, mockResponse);

      const response = await authAPI.login('testuser', 'password');

      expect(response.data).toEqual(mockResponse);
    });

    test('refresh refreshes access token', async () => {
      const mockResponse = {
        access: 'new-access-token',
      };

      mock.onPost('/api/token/refresh/').reply(200, mockResponse);

      const response = await authAPI.refresh('refresh-token');

      expect(response.data.access).toBe('new-access-token');
    });
  });

  describe('Authentication Interceptor', () => {
    test('adds auth token to requests when available', async () => {
      localStorage.setItem('token', 'test-token');

      mock.onGet('/api/inventory/items/test-id/').reply((config) => {
        expect(config.headers?.Authorization).toBe('Bearer test-token');
        return [200, {}];
      });

      await inventoryAPI.getItem('test-id');
    });

    test('does not add auth token when not available', async () => {
      mock.onGet('/api/inventory/items/test-id/').reply((config) => {
        expect(config.headers?.Authorization).toBeUndefined();
        return [200, {}];
      });

      await inventoryAPI.getItem('test-id');
    });

    test('captures response errors with sentry context', async () => {
      const errorPayload = { detail: 'boom' };

      mock
        .onGet('/api/inventory/items/error/')
        .reply(500, errorPayload);

      await expect(inventoryAPI.getItem('error')).rejects.toBeDefined();

      expect(Sentry.captureException).toHaveBeenCalledWith(expect.any(Error), {
        contexts: expect.objectContaining({
          api: expect.objectContaining({ status: 500, data: errorPayload }),
        }),
      });
    });

    test('captures network errors with sentry context', async () => {
      const handler = (api as any).interceptors.response.handlers[0].rejected;
      const networkError = Object.assign(new Error('Network Error'), {
        request: {},
        config: { url: '/api/inventory/items/network/', method: 'get' },
      });

      await expect(handler(networkError)).rejects.toBe(networkError);

      expect(Sentry.captureException).toHaveBeenCalledWith(networkError, {
        contexts: expect.objectContaining({
          api: expect.objectContaining({
            url: '/api/inventory/items/network/',
            method: 'get',
            error: 'No response received',
          }),
        }),
      });
    });

    test('captures unexpected errors without context', async () => {
      const handler = (api as any).interceptors.response.handlers[0].rejected;
      const setupError = new Error('Config exploded');

      await expect(handler(setupError)).rejects.toBe(setupError);

      expect(Sentry.captureException).toHaveBeenCalledWith(setupError);
    });
  });
});
