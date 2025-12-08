/**
 * CSV Export Utility
 * Functions for exporting data to CSV format
 */

export interface CSVExportOptions {
  filename?: string;
  headers?: string[];
}

/**
 * Convert array of objects to CSV string
 */
export function arrayToCSV<T extends Record<string, any>>(
  data: T[],
  headers?: string[]
): string {
  if (data.length === 0) {
    return '';
  }

  // Use provided headers or extract from first object
  const csvHeaders = headers || Object.keys(data[0]);

  // Create header row
  const headerRow = csvHeaders.map((header) => escapeCSVValue(header)).join(',');

  // Create data rows
  const dataRows = data.map((row) => {
    return csvHeaders
      .map((header) => {
        const value = row[header];
        return escapeCSVValue(value !== null && value !== undefined ? String(value) : '');
      })
      .join(',');
  });

  return [headerRow, ...dataRows].join('\n');
}

/**
 * Escape CSV value (handle commas, quotes, newlines)
 */
function escapeCSVValue(value: string): string {
  if (value.includes(',') || value.includes('"') || value.includes('\n')) {
    // Escape quotes by doubling them, then wrap in quotes
    return `"${value.replace(/"/g, '""')}"`;
  }
  return value;
}

/**
 * Download CSV file
 */
export function downloadCSV(csvContent: string, filename: string = 'export.csv'): void {
  // Add BOM for Excel compatibility with special characters
  const BOM = '\uFEFF';
  const blob = new Blob([BOM + csvContent], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename.endsWith('.csv') ? filename : `${filename}.csv`;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

/**
 * Export array of objects to CSV file
 */
export function exportToCSV<T extends Record<string, any>>(
  data: T[],
  options: CSVExportOptions = {}
): void {
  const { filename = 'export', headers } = options;
  const csvContent = arrayToCSV(data, headers);
  downloadCSV(csvContent, filename);
}

/**
 * Export inventory items to CSV with standard columns
 */
export function exportInventoryItemsToCSV(items: any[]): void {
  const headers = [
    'Name',
    'SKU',
    'Category',
    'Location',
    'Current Stock',
    'Minimum Stock',
    'Reorder Quantity',
    'Unit Cost',
    'Supplier',
    'Needs Reorder',
    'Is Active',
  ];

  const csvData = items.map((item) => ({
    Name: item.name || '',
    SKU: item.sku || '',
    Category: item.category_name || '',
    Location: item.location || '',
    'Current Stock': item.current_stock || 0,
    'Minimum Stock': item.minimum_stock || 0,
    'Reorder Quantity': item.reorder_quantity || 0,
    'Unit Cost': item.unit_cost || '',
    Supplier: item.supplier_name || '',
    'Needs Reorder': item.needs_reorder ? 'Yes' : 'No',
    'Is Active': item.is_active ? 'Yes' : 'No',
  }));

  exportToCSV(csvData, { filename: 'inventory-export', headers });
}
