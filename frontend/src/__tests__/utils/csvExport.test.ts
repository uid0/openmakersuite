/**
 * Tests for CSV export utilities
 */
import { SupplierChoice } from '../../types';
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

      exportInventoryItemsToCSV(items, 'operator');

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
      ], 'operator');

      expect(csvText()).toContain('Donated Filament');
      expect(csvText()).toMatch(/Donated Filament[^\n]*,0,/);
    });

    it('CONTROL: an item nobody priced still exports a blank cell', () => {
      exportInventoryItemsToCSV([
        { ...baseItem, name: 'Unpriced Filament', unit_cost: null },
      ], 'operator');

      expect(csvText()).toMatch(/Unpriced Filament[^\n]*,,/);
    });

    it('CONTROL: an ordinary price is unchanged', () => {
      exportInventoryItemsToCSV([{ ...baseItem, name: 'Priced', unit_cost: 10 }], 'operator');

      expect(csvText()).toMatch(/Priced[^\n]*,10,/);
    });

    /**
     * The supplier columns (op-3xsp).
     *
     * This file leaves the system. A wrong supplier in it cannot be corrected
     * afterwards, because the next thing that happens is somebody ordering
     * from it — there is no screen left to argue with. It used to carry
     * `item.supplier_name`, the read-only legacy accessor, which names the
     * winner of the derivation with the derivation thrown away: an item stocked
     * by three suppliers exported as an item with one, and nothing in the file
     * said the choice was made without a price or that the operator's flagged
     * primary had been skipped.
     */
    describe('supplier columns', () => {
      const choice = (overrides: Partial<SupplierChoice> = {}): SupplierChoice => ({
        item_supplier_id: 1,
        supplier_name: 'Acme Supplies',
        basis: 'best_scored',
        reason: null,
        flagged_primary_unorderable: false,
        scored_without_price: false,
        scored_without_history: false,
        alternatives: [],
        ...overrides,
      });

      const headerRow = () => csvText().split('\n')[0];
      const dataRow = () => csvText().split('\n')[1];

      it('BEFORE/AFTER: exports the supplier the system would buy from, not the legacy key', () => {
        exportInventoryItemsToCSV([
          {
            ...baseItem,
            name: 'Disagreeing',
            // The legacy accessor and the derivation cannot actually disagree
            // on a live server — both resolve `supplier_selection`. They are
            // set apart HERE so the assertion can only pass by reading the
            // right one: an export still wired to `supplier_name` writes
            // "Legacy Accessor Co." into the file somebody orders from.
            supplier_name: 'Legacy Accessor Co.',
            supplier_choice: choice({ supplier_name: 'Derived Supply Co.' }),
          },
        ], 'operator');

        expect(dataRow()).toContain('Derived Supply Co.');
        expect(csvText()).not.toContain('Legacy Accessor Co.');
      });

      it('BEFORE/AFTER: names the other suppliers, so one name is never the only name', () => {
        exportInventoryItemsToCSV([
          {
            ...baseItem,
            name: 'Three Sources',
            supplier_choice: choice({
              alternatives: [
                { id: 2, supplier_name: 'Beta Parts' },
                { id: 3, supplier_name: 'Gamma Wholesale' },
              ],
            }),
          },
        ], 'operator');

        expect(headerRow()).toContain('Other Suppliers');
        expect(dataRow()).toContain('Beta Parts; Gamma Wholesale');
      });

      it('carries the missing-price qualifier into the file', () => {
        exportInventoryItemsToCSV([
          { ...baseItem, name: 'Unpriced Pick', supplier_choice: choice({ scored_without_price: true }) },
        ], 'operator');

        expect(headerRow()).toContain('Supplier Caveats');
        expect(dataRow()).toContain('chosen without a price on file');
      });

      it('carries the skipped flagged primary into the file', () => {
        exportInventoryItemsToCSV([
          {
            ...baseItem,
            name: 'Skipped Primary',
            supplier_choice: choice({ flagged_primary_unorderable: true }),
          },
        ], 'operator');

        expect(dataRow()).toContain('flagged primary supplier cannot be ordered from');
      });

      it('says how the supplier was chosen, so a standing decision reads apart from a score', () => {
        exportInventoryItemsToCSV([
          { ...baseItem, name: 'Flagged', supplier_choice: choice({ basis: 'flagged_primary' }) },
        ], 'operator');

        expect(headerRow()).toContain('Supplier Chosen By');
        expect(dataRow()).toContain('flagged primary');
      });

      it('exports a blank supplier with the REASON, not a bare empty cell', () => {
        exportInventoryItemsToCSV([
          {
            ...baseItem,
            name: 'Nothing Buyable',
            supplier_name: null,
            supplier_choice: choice({ supplier_name: null, reason: 'none_orderable' }),
          },
        ], 'operator');

        expect(dataRow()).toContain('inactive or discontinued');
      });

      it('CONTROL: a clean single-supplier item exports the name and three blanks', () => {
        exportInventoryItemsToCSV([
          { ...baseItem, name: 'Simple', supplier_choice: choice() },
        ], 'operator');

        // Supplier, then a blank Other Suppliers, then the basis (quoted,
        // because the label itself contains commas), then a blank Caveats.
        expect(dataRow()).toContain(
          'Acme Supplies,,"price, lead time and delivery record",,'
        );
      });
    });

    /**
     * Who the file is written FOR (op-3xsp).
     *
     * `/inventory/items` is not behind RequireAuth and its list endpoint is
     * AllowAny, so the visitor pressing Export may be logged out. The three
     * columns above hand them a FILE naming every vendor that stocks each item
     * plus caveats addressed to whoever maintains the links — and a file
     * cannot be taken back the way a screen can. The `Supplier` column is NOT
     * part of that: one name was exportable anonymously long before this
     * branch, and narrowing it here would take away what people already had.
     */
    describe('audience', () => {
      const choice = (overrides: Partial<SupplierChoice> = {}): SupplierChoice => ({
        item_supplier_id: 1,
        supplier_name: 'Acme Supplies',
        basis: 'best_scored',
        reason: null,
        flagged_primary_unorderable: false,
        scored_without_price: false,
        scored_without_history: false,
        alternatives: [],
        ...overrides,
      });

      const disclosingItem = {
        ...baseItem,
        name: 'Three Sources',
        supplier_choice: choice({
          flagged_primary_unorderable: true,
          alternatives: [
            { id: 2, supplier_name: 'Beta Parts' },
            { id: 3, supplier_name: 'Gamma Wholesale' },
          ],
        }),
      };

      const headerRow = () => csvText().split('\n')[0];
      const dataRow = () => csvText().split('\n')[1];

      it('BEFORE/AFTER: an anonymous export has no supplier column beyond the name', () => {
        exportInventoryItemsToCSV([disclosingItem], 'anonymous');

        expect(headerRow()).not.toContain('Other Suppliers');
        expect(headerRow()).not.toContain('Supplier Chosen By');
        expect(headerRow()).not.toContain('Supplier Caveats');
      });

      it('BEFORE/AFTER: an anonymous export names no vendor but the chosen one', () => {
        exportInventoryItemsToCSV([disclosingItem], 'anonymous');

        expect(csvText()).not.toContain('Beta Parts');
        expect(csvText()).not.toContain('Gamma Wholesale');
        expect(csvText()).not.toMatch(/flagged primary/i);
        expect(csvText()).not.toMatch(/price, lead time and delivery record/);
      });

      it('CONTROL: the one supplier name anonymous could always export is still there', () => {
        exportInventoryItemsToCSV([disclosingItem], 'anonymous');

        expect(headerRow()).toContain('Supplier');
        expect(dataRow()).toContain('Acme Supplies');
      });

      it('CONTROL: a signed-in export is unchanged — all four supplier columns', () => {
        exportInventoryItemsToCSV([disclosingItem], 'operator');

        expect(headerRow()).toContain('Supplier');
        expect(headerRow()).toContain('Other Suppliers');
        expect(headerRow()).toContain('Supplier Chosen By');
        expect(headerRow()).toContain('Supplier Caveats');
        expect(dataRow()).toContain('Acme Supplies');
        expect(dataRow()).toContain('Beta Parts; Gamma Wholesale');
        expect(dataRow()).toMatch(/flagged primary supplier cannot be ordered from/);
      });

      // The gate is a column gate, not a row gate: an anonymous export must
      // still be a usable inventory file, not a supplier-shaped hole.
      it('CONTROL: an anonymous export keeps every non-supplier column', () => {
        exportInventoryItemsToCSV([disclosingItem], 'anonymous');

        for (const column of [
          'Name',
          'SKU',
          'Category',
          'Location',
          'Current Stock',
          'Minimum Stock',
          'Reorder Quantity',
          'Unit Cost',
          'Needs Reorder',
          'Is Active',
        ]) {
          expect(headerRow()).toContain(column);
        }
        expect(dataRow()).toContain('Three Sources');
      });
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
