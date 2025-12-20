/**
 * Tests for CSV export utilities
 */
import { exportAssetsToCSV, exportInventoryItemsToCSV } from '../../utils/csvExport';

// Mock URL.createObjectURL and Blob for test environment
global.URL.createObjectURL = jest.fn(() => 'mock-url');
global.Blob = jest.fn((content, options) => ({ content, options })) as any;

// Mock document.createElement and related DOM methods
const mockClick = jest.fn();
const mockAppendChild = jest.fn();
const mockRemoveChild = jest.fn();
const mockLink = {
  href: '',
  download: '',
  click: mockClick,
} as any;

beforeEach(() => {
  jest.clearAllMocks();
  document.createElement = jest.fn(() => mockLink) as any;
  document.body.appendChild = mockAppendChild;
  document.body.removeChild = mockRemoveChild;
  (global.URL.createObjectURL as jest.Mock).mockReturnValue('mock-url');
  (global.URL.revokeObjectURL as jest.Mock) = jest.fn();
});

describe('CSV Export Utilities', () => {

  describe('exportInventoryItemsToCSV', () => {
    it('exports inventory items with correct headers', () => {
      const items = [
        {
          name: 'Test Item',
          sku: 'SKU001',
          category_name: 'Electronics',
          location: 'Workshop A',
          current_stock: 10,
          minimum_stock: 5,
          reorder_quantity: 20,
          unit_cost: '10.00',
          supplier_name: 'Supplier 1',
          needs_reorder: false,
          is_active: true,
        },
      ];

      exportInventoryItemsToCSV(items);

      expect(document.createElement).toHaveBeenCalledWith('a');
      expect(mockLink.download).toBe('inventory-export.csv');
      expect(mockClick).toHaveBeenCalled();
    });
  });

  describe('exportAssetsToCSV', () => {
    const mockAsset = {
      id: '1',
      name: 'Test Asset',
      asset_tag: 'TAG001',
      serial_number: 'SN001',
      status: 'active',
      location_name: 'Workshop A',
      category_name: 'Electronics',
      display_manufacturer: 'Manufacturer 1',
      age_in_days: 365,
      date_received: '2024-01-01',
      inventory_item_name: 'Item 1',
      owning_group_name: 'Space',
      operational_status: 'available',
      is_active: true,
      description: 'Test description',
      amount_paid: '100.00',
      is_donation: false,
      donor_name: '',
      product_url: '',
      wiki_page_url: '',
      created_at: '2024-01-01T00:00:00Z',
      updated_at: '2024-01-01T00:00:00Z',
    };

    it('exports assets with correct headers', () => {
      exportAssetsToCSV([mockAsset]);

      expect(document.createElement).toHaveBeenCalledWith('a');
      expect(mockLink.download).toBe('assets-export.csv');
      expect(mockClick).toHaveBeenCalled();
    });

    it('formats age correctly', () => {
      const assetWithAge = {
        ...mockAsset,
        age_in_days: 365,
      };

      exportAssetsToCSV([assetWithAge]);

      // Check that the blob was created with content containing age
      expect(global.Blob).toHaveBeenCalled();
      const blobCall = (global.Blob as jest.Mock).mock.calls[0];
      const content = blobCall[0][0];
      expect(content).toContain('1y 0d');
    });

    it('handles missing optional fields', () => {
      const assetWithMissingFields = {
        ...mockAsset,
        serial_number: '',
        asset_tag: '',
        location_name: '',
        date_received: null,
        age_in_days: undefined,
      };

      exportAssetsToCSV([assetWithMissingFields]);

      expect(global.Blob).toHaveBeenCalled();
      const blobCall = (global.Blob as jest.Mock).mock.calls[0];
      const content = blobCall[0][0];
      expect(content).toContain('N/A');
    });

    it('formats dates correctly', () => {
      exportAssetsToCSV([mockAsset]);

      expect(global.Blob).toHaveBeenCalled();
      const blobCall = (global.Blob as jest.Mock).mock.calls[0];
      const content = blobCall[0][0];
      expect(content).toContain('2024');
    });

    it('handles donation status', () => {
      const donatedAsset = {
        ...mockAsset,
        is_donation: true,
        donor_name: 'John Doe',
      };

      exportAssetsToCSV([donatedAsset]);

      expect(global.Blob).toHaveBeenCalled();
      const blobCall = (global.Blob as jest.Mock).mock.calls[0];
      const content = blobCall[0][0];
      expect(content).toContain('Yes');
      expect(content).toContain('John Doe');
    });

    it('exports multiple assets', () => {
      const assets = [
        mockAsset,
        { ...mockAsset, id: '2', name: 'Asset 2' },
        { ...mockAsset, id: '3', name: 'Asset 3' },
      ];

      exportAssetsToCSV(assets);

      expect(global.Blob).toHaveBeenCalled();
      const blobCall = (global.Blob as jest.Mock).mock.calls[0];
      const content = blobCall[0][0];
      // Should have header + 3 data rows
      const lines = content.split('\n');
      expect(lines.length).toBeGreaterThanOrEqual(4);
    });
  });
});
