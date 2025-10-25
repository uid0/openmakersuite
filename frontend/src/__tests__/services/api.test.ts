/**
 * Tests for API service
 */
import axios from 'axios';
import MockAdapter from 'axios-mock-adapter';
import { inventoryAPI, reorderAPI, authAPI } from '../../services/api';

describe('API Service', () => {
  let mock: MockAdapter;

  beforeEach(() => {
    mock = new MockAdapter(axios);
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

    test('markReceived marks request as received', async () => {
      const mockResponse = {
        id: 1,
        status: 'received',
      };

      mock.onPost('/api/reorders/requests/1/mark_received/').reply(200, mockResponse);

      const response = await reorderAPI.markReceived(1);

      expect(response.data.status).toBe('received');
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
  });
});
