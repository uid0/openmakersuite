/**
 * Tests for CSV export utility
 */
import { arrayToCSV, exportInventoryItemsToCSV, exportToCSV } from '../../utils/csvExport';

// Mock URL.createObjectURL and document methods
global.URL.createObjectURL = jest.fn(() => 'blob:mock-url');
global.URL.revokeObjectURL = jest.fn();

// Create a proper mock link element with getters/setters
const createMockLinkElement = () => {
  const link = {
    href: '',
    download: '',
    click: jest.fn(),
  };
  return link;
};

const originalCreateElement = document.createElement;
beforeAll(() => {
  (document.createElement as any) = jest.fn((tagName: string) => {
    if (tagName === 'a') {
      return createMockLinkElement();
    }
    return originalCreateElement.call(document, tagName);
  });
});

afterAll(() => {
  document.createElement = originalCreateElement;
});

describe('CSV Export Utility', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    jest.spyOn(document.body, 'appendChild').mockImplementation(() => ({} as any));
    jest.spyOn(document.body, 'removeChild').mockImplementation(() => ({} as any));
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  describe('arrayToCSV', () => {
    it('converts array to CSV string', () => {
      const data = [
        { name: 'Item 1', stock: 10 },
        { name: 'Item 2', stock: 20 },
      ];

      const result = arrayToCSV(data, ['name', 'stock']);

      expect(result).toContain('name,stock');
      expect(result).toContain('Item 1,10');
      expect(result).toContain('Item 2,20');
    });

    it('handles empty array', () => {
      const result = arrayToCSV([], ['name', 'stock']);
      expect(result).toBe('');
    });

    it('escapes values with commas', () => {
      const data = [{ name: 'Item, with comma', stock: 10 }];
      const result = arrayToCSV(data, ['name', 'stock']);

      expect(result).toContain('"Item, with comma"');
    });

    it('escapes values with quotes', () => {
      const data = [{ name: 'Item "quoted"', stock: 10 }];
      const result = arrayToCSV(data, ['name', 'stock']);

      expect(result).toContain('"Item ""quoted"""');
    });

    it('uses object keys as headers when headers not provided', () => {
      const data = [{ name: 'Item 1', stock: 10 }];
      const result = arrayToCSV(data);

      expect(result).toContain('name,stock');
    });
  });

  describe('exportToCSV', () => {
    it('triggers download with correct filename', () => {
      const data = [{ name: 'Item 1', stock: 10 }];
      const mockLink = {
        href: '',
        download: '',
        click: jest.fn(),
      };
      
      Object.defineProperty(mockLink, 'href', {
        writable: true,
        value: '',
      });
      Object.defineProperty(mockLink, 'download', {
        writable: true,
        value: '',
      });

      const createElementSpy = jest.spyOn(document, 'createElement').mockReturnValue(mockLink as any);
      const appendChildSpy = jest.spyOn(document.body, 'appendChild').mockImplementation(() => mockLink as any);
      const removeChildSpy = jest.spyOn(document.body, 'removeChild').mockImplementation(() => mockLink as any);

      exportToCSV(data, { filename: 'test-export' });

      expect(createElementSpy).toHaveBeenCalledWith('a');
      expect(mockLink.download).toBe('test-export.csv');
      expect(mockLink.click).toHaveBeenCalled();
      expect(appendChildSpy).toHaveBeenCalled();
      expect(removeChildSpy).toHaveBeenCalled();

      createElementSpy.mockRestore();
      appendChildSpy.mockRestore();
      removeChildSpy.mockRestore();
    });

    it('uses default filename when not provided', () => {
      const data = [{ name: 'Item 1', stock: 10 }];
      const mockLink = {
        href: '',
        download: '',
        click: jest.fn(),
      };
      
      Object.defineProperty(mockLink, 'download', {
        writable: true,
        value: '',
      });

      const createElementSpy = jest.spyOn(document, 'createElement').mockReturnValue(mockLink as any);
      jest.spyOn(document.body, 'appendChild').mockImplementation(() => mockLink as any);
      jest.spyOn(document.body, 'removeChild').mockImplementation(() => mockLink as any);

      exportToCSV(data);

      expect(mockLink.download).toBe('export.csv');

      createElementSpy.mockRestore();
    });
  });

  describe('exportInventoryItemsToCSV', () => {
    it('exports inventory items with correct columns', () => {
      const items = [
        {
          id: '1',
          name: 'Test Item',
          sku: 'TEST-001',
          category_name: 'Tools',
          location: 'Shelf A',
          current_stock: 10,
          minimum_stock: 5,
          reorder_quantity: 20,
          unit_cost: '15.99',
          supplier_name: 'Test Supplier',
          needs_reorder: false,
          is_active: true,
        },
      ];

      const mockLink = {
        href: '',
        download: '',
        click: jest.fn(),
      };
      
      Object.defineProperty(mockLink, 'download', {
        writable: true,
        value: '',
      });

      const createElementSpy = jest.spyOn(document, 'createElement').mockReturnValue(mockLink as any);
      jest.spyOn(document.body, 'appendChild').mockImplementation(() => mockLink as any);
      jest.spyOn(document.body, 'removeChild').mockImplementation(() => mockLink as any);

      exportInventoryItemsToCSV(items);

      expect(createElementSpy).toHaveBeenCalled();
      expect(mockLink.download).toBe('inventory-export.csv');

      createElementSpy.mockRestore();
    });
  });
});
