/**
 * Tests for CSV export utilities
 */
import {
  exportAssetsToCSV,
  exportInventoryItemsToCSV,
  reportMoney,
} from '../../utils/csvExport';

// Mock URL.createObjectURL and Blob for test environment
global.URL.createObjectURL = jest.fn(() => 'mock-url');
global.Blob = jest.fn(function MockBlob(content, options) {
  return { content, options };
}) as any;

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
    const baseItem = {
      name: 'Test Item',
      sku: 'SKU001',
      category_name: 'Electronics',
      location: 'Workshop A',
      current_stock: 10,
      minimum_stock: 5,
      reorder_quantity: 20,
      unit_cost: 10,
      supplier_name: 'Supplier 1',
      needs_reorder: false,
      is_active: true,
    };

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
          unit_cost: 10,
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

    /**
     * `InventoryItem.unit_cost` is a property-backed `ReadOnlyField`, so a
     * donated item sends the NUMBER 0 (op-9m2v). `item.unit_cost || ''`
     * exported that as a blank cell — the spelling this file uses for a price
     * nobody recorded — so a real $0.00 and an unknown price were the same
     * cell in the operator's spreadsheet.
     */
    const csvText = () =>
      ((global.Blob as unknown as jest.Mock).mock.calls[0][0] as string[]).join('');

    it('BEFORE/AFTER: exports a donated item as 0, not as a blank cell', () => {
      exportInventoryItemsToCSV([
        { ...baseItem, name: 'Donated Filament', unit_cost: 0 },
      ]);

      expect(csvText()).toContain('Donated Filament');
      expect(csvText()).toMatch(/Donated Filament[^\n]*,0,/);
    });

    it('CONTROL: an item nobody priced still exports a blank cell', () => {
      exportInventoryItemsToCSV([
        { ...baseItem, name: 'Unpriced Filament', unit_cost: null },
      ]);

      expect(csvText()).toMatch(/Unpriced Filament[^\n]*,,/);
    });

    it('CONTROL: an ordinary price is unchanged', () => {
      exportInventoryItemsToCSV([{ ...baseItem, name: 'Priced', unit_cost: 10 }]);

      expect(csvText()).toMatch(/Priced[^\n]*,10,/);
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
      // formatDateOnly preserves the local calendar day for date-only inputs
      // (no off-by-one drift in TZs west of UTC) and renders en-US 'short' month.
      expect(content).toContain('Jan 1, 2024');
      // Verify it's not in ISO format
      expect(content).not.toContain('2024-01-01T');
      expect(content).not.toContain('T00:00:00Z');
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

/**
 * A price the server did not record must not export as "$0.00" (op-9m2v).
 *
 * A CSV is summed by whoever opens it. "$0.00" for a price nobody recorded
 * counted the unknowns as free in whatever total the operator built on top;
 * a blank cell sums as nothing AND reads as nothing, which is the truth. A
 * genuinely free supplier still exports "$0.00", because that is a price.
 */
describe('reportMoney', () => {
  test('renders a real price, zero included', () => {
    expect(reportMoney(4.5)).toBe('$4.50');
    expect(reportMoney(0)).toBe('$0.00');
  });

  test('renders an absence as a blank cell, never as $0.00', () => {
    expect(reportMoney(null)).toBe('');
    expect(reportMoney(undefined)).toBe('');
  });
});
