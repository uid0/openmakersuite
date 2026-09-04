/**
 * CSV Export Utility
 * Functions for exporting data to CSV format
 */
import { YARDSTICK_LABEL_TITLE } from '../constants/leadTimeYardstick';
import { formatDateOnly } from './dates';
import {
  SUPPLIER_BASIS_LABELS,
  SupplierAudience,
  alternativeSupplierNames,
  chosenSupplierName,
  supplierChoiceNote,
} from './supplierChoice';

/**
 * A money column, or a blank cell where the server recorded no price.
 *
 * `null` and `0` are different answers (op-9m2v): a supplier that charges
 * nothing and a price nobody recorded both used to export as "$0.00", so a
 * spreadsheet summing the column counted the unknowns as free. An empty cell
 * sums as nothing AND reads as nothing, which is the truth.
 */
export const reportMoney = (amount: number | null | undefined) =>
  amount === null || amount === undefined ? '' : `$${amount.toFixed(2)}`;

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
 *
 * The `Supplier` column names the link the API says to BUY through, read off
 * `supplier_choice` (op-3xsp). It used to be the flat `item.supplier_name`,
 * which is that same winner with the derivation thrown away — so an item with
 * three suppliers exported as an item with one, and the three qualifiers that
 * decide whether the name is safe to act on (there were others; the scoring
 * knew no price for this one; the operator's flagged primary was skipped as
 * unbuyable) were not in the file at all.
 *
 * This is the surface where being wrong is unrecoverable: the file leaves the
 * system and somebody orders from it, with no screen left to correct. Hence
 * three columns rather than a footnote — `Other Suppliers` so a single name
 * never reads as the only option, and `Supplier Chosen By` / `Supplier Caveats`
 * so a purchaser can see whether to check before ordering. Blank in all three
 * is the honest reading "one supplier, chosen cleanly".
 *
 * That unrecoverability cuts both ways, which is why `audience` is a required
 * argument rather than an option with a default. `/inventory/items` is not
 * behind RequireAuth and its list endpoint is AllowAny, so a logged-out visitor
 * can tick rows and press Export; the three columns above would hand them a
 * FILE naming every vendor that stocks each item, plus caveats addressed to
 * whoever maintains the links. An anonymous export therefore carries the
 * pre-`supplier_choice` column set and nothing more — `Supplier`, one name,
 * exactly what they could already export.
 *
 * The audience is the CALLER's to decide: this function stays pure and never
 * reads auth state. Making the argument required is the point — a new call
 * site cannot inherit a permissive default by forgetting the question.
 */
export function exportInventoryItemsToCSV(items: any[], audience: SupplierAudience): void {
  const forOperator = audience === 'operator';

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
    ...(forOperator ? ['Other Suppliers', 'Supplier Chosen By', 'Supplier Caveats'] : []),
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
    // A donated item records a real 0, not an absent price; `|| ''` exported it
    // as a blank cell — the spelling this file uses for "unknown" (op-9m2v).
    'Unit Cost': item.unit_cost ?? '',
    Supplier: chosenSupplierName(item.supplier_choice) ?? '',
    ...(forOperator
      ? {
          'Other Suppliers': alternativeSupplierNames(item.supplier_choice).join('; '),
          'Supplier Chosen By': SUPPLIER_BASIS_LABELS[item.supplier_choice?.basis] ?? '',
          // Covers the no-supplier reasons too, so a blank `Supplier` cell says
          // which of "nobody has said where this comes from" and "everyone we
          // had is dead" it is, rather than leaving a purchaser to guess.
          'Supplier Caveats': supplierChoiceNote(item.supplier_choice) ?? '',
        }
      : {}),
    'Needs Reorder': item.needs_reorder ? 'Yes' : 'No',
    'Is Active': item.is_active ? 'Yes' : 'No',
  }));

  exportToCSV(csvData, { filename: 'inventory-export', headers });
}

/**
 * Export inventory report data to CSV
 */
export function exportInventoryReportToCSV(
  data: any[],
  reportType: 'stock_by_category' | 'reorder_frequency' | 'value_by_location'
): void {
  let headers: string[] = [];
  let csvData: any[] = [];

  if (reportType === 'stock_by_category') {
    headers = [
      'Category',
      'Total Items',
      'Total Stock',
      'Total Value',
      'Unpriced Items',
      'Low Stock Count',
    ];
    csvData = data.map((item) => ({
      Category: item.category_name || '',
      'Total Items': item.total_items || 0,
      'Total Stock': item.total_stock || 0,
      'Total Value': reportMoney(item.total_value),
      'Unpriced Items': item.items_without_price ?? 0,
      'Low Stock Count': item.low_stock_count || 0,
    }));
  } else if (reportType === 'reorder_frequency') {
    headers = ['Item Name', 'SKU', 'Category', 'Reorder Count'];
    csvData = data.map((item) => ({
      'Item Name': item.item_name || '',
      SKU: item.item_sku || '',
      Category: item.category_name || '',
      'Reorder Count': item.reorder_count || 0,
    }));
  } else if (reportType === 'value_by_location') {
    headers = ['Location', 'Total Items', 'Total Stock', 'Total Value', 'Unpriced Items'];
    csvData = data.map((item) => ({
      Location: item.location_name || '',
      'Total Items': item.total_items || 0,
      'Total Stock': item.total_stock || 0,
      'Total Value': reportMoney(item.total_value),
      'Unpriced Items': item.items_without_price ?? 0,
    }));
  }

  exportToCSV(csvData, { filename: `inventory-report-${reportType}`, headers });
}

/**
 * Export purchasing report data to CSV
 */
export function exportPurchasingReportToCSV(
  data: any[],
  reportType: 'spend_by_supplier' | 'spend_by_category' | 'lead_time_analysis' | 'price_trends'
): void {
  let headers: string[] = [];
  let csvData: any[] = [];

  if (reportType === 'spend_by_supplier') {
    headers = ['Supplier', 'Total Orders', 'Total Spend', 'Avg Order Value'];
    csvData = data.map((item) => ({
      Supplier: item.supplier_name || '',
      'Total Orders': item.total_orders || 0,
      'Total Spend': item.total_spend ? `$${item.total_spend.toFixed(2)}` : '$0.00',
      'Avg Order Value': item.avg_order_value ? `$${item.avg_order_value.toFixed(2)}` : '$0.00',
    }));
  } else if (reportType === 'spend_by_category') {
    headers = ['Category', 'Total Items', 'Total Quantity', 'Total Spend'];
    csvData = data.map((item) => ({
      Category: item.category_name || '',
      'Total Items': item.total_items || 0,
      'Total Quantity': item.total_quantity || 0,
      'Total Spend': item.total_spend ? `$${item.total_spend.toFixed(2)}` : '$0.00',
    }));
  } else if (reportType === 'lead_time_analysis') {
    // Variance and rate are both against the supplier link's standing quoted
    // lead time, not against the delivery dates confirmed on the orders. A
    // spreadsheet built on "On-Time Rate" alone reads as missed agreed dates and
    // sends the buyer after the wrong vendors, so each header names the quote —
    // in the web's single copy of those words, not a fourth restatement.
    const quotedLeadTime = `Avg ${YARDSTICK_LABEL_TITLE} (days)`;
    const varianceVsQuote = `Avg Variance vs. ${YARDSTICK_LABEL_TITLE} (days)`;
    const withinQuote = `Within ${YARDSTICK_LABEL_TITLE} (%)`;
    headers = [
      'Supplier',
      'Item Name',
      'Total Orders',
      quotedLeadTime,
      'Avg Actual Lead Time (days)',
      varianceVsQuote,
      withinQuote,
    ];
    csvData = data.map((item) => ({
      Supplier: item.supplier_name || '',
      'Item Name': item.item_name || '',
      'Total Orders': item.total_orders || 0,
      [quotedLeadTime]: item.avg_estimated_lead_time || 0,
      'Avg Actual Lead Time (days)': item.avg_actual_lead_time || 0,
      [varianceVsQuote]: item.avg_variance || 0,
      [withinQuote]: item.on_time_rate ? `${item.on_time_rate.toFixed(1)}%` : '0%',
    }));
  } else if (reportType === 'price_trends') {
    headers = [
      'Item Name',
      'Supplier',
      'Price Changes',
      'Min Unit Cost',
      'Max Unit Cost',
      'Latest Unit Cost',
      'Price Change %',
    ];
    csvData = data.map((item) => ({
      'Item Name': item.item_name || '',
      Supplier: item.supplier_name || '',
      'Price Changes': item.price_changes || 0,
      'Min Unit Cost': reportMoney(item.min_unit_cost),
      'Max Unit Cost': reportMoney(item.max_unit_cost),
      'Latest Unit Cost': reportMoney(item.latest_unit_cost),
      'Price Change %':
        item.price_change_percentage === null || item.price_change_percentage === undefined
          ? 'N/A'
          : `${item.price_change_percentage > 0 ? '+' : ''}${item.price_change_percentage.toFixed(2)}%`,
    }));
  }

  exportToCSV(csvData, { filename: `purchasing-report-${reportType}`, headers });
}

/**
 * Export asset report data to CSV
 */
export function exportAssetReportToCSV(
  data: any[],
  reportType: 'assets_by_status' | 'maintenance_due' | 'utilization' | 'tco' | 'supplies_used'
): void {
  let headers: string[] = [];
  let csvData: any[] = [];

  if (reportType === 'assets_by_status') {
    headers = ['Status', 'Count'];
    csvData = data.map((item) => ({
      Status: item.status_display || item.status || '',
      Count: item.count || 0,
    }));
  } else if (reportType === 'maintenance_due') {
    headers = [
      'Asset Name',
      'Asset Tag',
      'Part Name',
      'Part SKU',
      'Maintenance Interval (days)',
      'Days Since Replacement',
      'Days Overdue',
      'Last Replaced At',
    ];
    csvData = data.map((item) => ({
      'Asset Name': item.asset_name || '',
      'Asset Tag': item.asset_tag || '',
      'Part Name': item.part_name || 'N/A',
      'Part SKU': item.part_sku || 'N/A',
      'Maintenance Interval (days)': item.maintenance_interval_days || 'N/A',
      'Days Since Replacement': item.days_since_replacement || 'N/A',
      'Days Overdue': item.days_overdue || 0,
      'Last Replaced At': item.last_replaced_at || 'N/A',
    }));
  } else if (reportType === 'utilization') {
    headers = ['Asset Name', 'Asset Tag', 'Total Sessions', 'Total Hours', 'Avg Hours per Session'];
    csvData = data.map((item) => ({
      'Asset Name': item.asset_name || '',
      'Asset Tag': item.asset_tag || '',
      'Total Sessions': item.total_sessions || 0,
      'Total Hours': item.total_hours ? item.total_hours.toFixed(2) : '0.00',
      'Avg Hours per Session': item.avg_hours_per_session ? item.avg_hours_per_session.toFixed(2) : '0.00',
    }));
  } else if (reportType === 'tco') {
    headers = [
      'Asset Name',
      'Asset Tag',
      'Days in Maintenance (last 90d)',
      'Scheduled Maintenance Cost',
      'Unscheduled Maintenance Cost',
      'Repair Cost',
      'Total Cost of Ownership',
    ];
    csvData = data.map((item) => ({
      'Asset Name': item.asset_name || '',
      'Asset Tag': item.asset_tag || '',
      'Days in Maintenance (last 90d)': item.maintenance_days_last_90 ?? 0,
      'Scheduled Maintenance Cost': item.scheduled_maintenance_cost ?? '0.00',
      'Unscheduled Maintenance Cost': item.unscheduled_maintenance_cost ?? '0.00',
      'Repair Cost': item.repair_cost ?? '0.00',
      'Total Cost of Ownership': item.tco ?? '0.00',
    }));
  } else if (reportType === 'supplies_used') {
    // Union of both source shapes; source-specific columns are blank on rows
    // where they do not apply (serialized has no qty/cost, consumable no serial).
    headers = [
      'Asset',
      'Source',
      'Item',
      'Serial Number',
      'Quantity',
      'Unit',
      'Action',
      'Used At',
      'Work Order',
      'Estimated Cost',
    ];
    csvData = data.map((item) => ({
      Asset: item.asset_name || '',
      Source: item.source || '',
      Item: item.item_name || '',
      'Serial Number': item.serial_number || '',
      Quantity: item.quantity ?? '',
      Unit: item.unit || '',
      Action: item.action_display || item.action || '',
      'Used At': item.used_at || '',
      'Work Order': item.work_order_id || '',
      'Estimated Cost': item.estimated_cost != null ? `$${item.estimated_cost}` : '',
    }));
  }

  exportToCSV(csvData, { filename: `asset-report-${reportType}`, headers });
}

/**
 * Format age in days to readable string
 */
function formatAge(ageInDays: number | undefined): string {
  if (ageInDays === undefined || ageInDays === null) return 'N/A';
  const years = Math.floor(ageInDays / 365);
  const days = ageInDays % 365;
  if (years > 0) {
    return `${years}y ${days}d`;
  }
  return `${days}d`;
}

/**
 * Format date string to readable format
 */
function formatDate(dateString: string | null | undefined): string {
  return formatDateOnly(dateString, undefined, 'N/A');
}

/**
 * Export assets to CSV with standard columns
 */
export function exportAssetsToCSV(assets: any[]): void {
  const headers = [
    'Name',
    'Asset Tag',
    'Serial Number',
    'Status',
    'Location',
    'Category',
    'Manufacturer',
    'Age',
    'Date Received',
    'Inventory Item',
    'Owning Group',
    'Operational Status',
    'Is Active',
    'Description',
    'Amount Paid',
    'Is Donation',
    'Donor Name',
    'Product URL',
    'Wiki Page URL',
    'Created At',
    'Updated At',
  ];

  const csvData = assets.map((asset) => ({
    Name: asset.name || '',
    'Asset Tag': asset.asset_tag || '',
    'Serial Number': asset.serial_number || '',
    Status: asset.status || '',
    Location: asset.location_name || '',
    Category: asset.category_name || '',
    Manufacturer: asset.display_manufacturer || asset.manufacturer_name || '',
    Age: formatAge(asset.age_in_days),
    'Date Received': formatDate(asset.date_received),
    'Inventory Item': asset.inventory_item_name || '',
    'Owning Group': asset.owning_group_name || '',
    'Operational Status': asset.operational_status || '',
    'Is Active': asset.is_active ? 'Yes' : 'No',
    Description: asset.description || '',
    'Amount Paid': asset.amount_paid || '',
    'Is Donation': asset.is_donation ? 'Yes' : 'No',
    'Donor Name': asset.donor_name || '',
    'Product URL': asset.product_url || '',
    'Wiki Page URL': asset.wiki_page_url || '',
    'Created At': formatDate(asset.created_at),
    'Updated At': formatDate(asset.updated_at),
  }));

  exportToCSV(csvData, { filename: 'assets-export', headers });
}
