/**
 * TypeScript type definitions for the application
 */

export interface Supplier {
  id: number;
  name: string;
  supplier_type: 'hdsupply' | 'grainger' | 'amazon' | 'other';
  website: string;
  notes: string;
  created_at: string;
  updated_at: string;
}

export interface Category {
  id: number;
  name: string;
  slug: string;
  description: string;
  parent: number | null;
}

export interface ItemSupplier {
  id: number;
  item: string;
  item_name: string;
  supplier: number;
  supplier_name: string;
  supplier_sku: string;
  supplier_url: string;
  package_upc: string;
  unit_upc: string;
  quantity_per_package: number;
  // Dimensional fields (US units)
  package_height: string | null;
  package_width: string | null;
  package_length: string | null;
  package_weight: string | null;
  // Calculated dimensional properties
  package_volume: string | null;
  unit_weight: string | null;
  package_dimensions_display: string;
  // Pricing
  unit_cost: string | null;
  package_cost: string | null;
  average_lead_time: number;
  is_primary: boolean;
  is_active: boolean;
  notes: string;
  created_at: string;
  updated_at: string;
}

export interface InventoryItem {
  id: string;
  name: string;
  description: string;
  sku: string;
  image: string | null;
  thumbnail: string | null;
  category: number | null;
  category_name: string;
  location: string;
  reorder_quantity: number;
  current_stock: number;
  minimum_stock: number;
  supplier: number | null;
  supplier_name: string;
  supplier_sku: string;
  supplier_url: string;
  unit_cost: string | null;
  average_lead_time: number;
  qr_code: string | null;
  is_active: boolean;
  notes: string;
  needs_reorder: boolean;
  total_value: string;
  created_at: string;
  updated_at: string;
  // Reorder status and tracking
  reorder_status: string;
  has_pending_reorder: boolean;
  expected_delivery_date: string | null;
  active_reorder_request: {
    id: number;
    status: string;
    quantity: number;
    requested_at: string;
    ordered_at: string | null;
    requested_by: string;
    priority: string;
    // Review/approval information
    reviewed_by: string | null;
    reviewed_at: string | null;
  } | null;
  // Supplier relationships with dimensional data
  item_suppliers?: ItemSupplier[];
}

export interface UsageLog {
  id: number;
  item: string;
  quantity_used: number;
  usage_date: string;
  notes: string;
}

export type ReorderStatus = 'pending' | 'approved' | 'ordered' | 'received' | 'cancelled';
export type ReorderPriority = 'low' | 'normal' | 'high' | 'urgent';

export interface ReorderRequest {
  id: number;
  item: string;
  item_details: InventoryItem;
  quantity: number;
  status: ReorderStatus;
  priority: ReorderPriority;
  requested_by: string;
  request_notes: string;
  requested_at: string;
  reviewed_by: number | null;
  reviewed_by_username: string | null;
  reviewed_at: string | null;
  admin_notes: string;
  ordered_at: string | null;
  estimated_delivery: string | null;
  actual_delivery: string | null;
  order_number: string;
  actual_cost: string | null;
  estimated_cost: string | null;
  days_pending: number;
  updated_at: string;
}

export interface CreateReorderRequest {
  item: string;
  quantity: number;
  requested_by?: string;
  request_notes?: string;
  priority?: ReorderPriority;
  preferred_supplier?: number;
  package_quantity?: number;
}
