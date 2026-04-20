/**
 * TypeScript type definitions for the application
 */

export interface Supplier {
  id: number;
  name: string;
  supplier_type: 'local' | 'online' | 'national';
  website: string;
  account_number?: string;
  tax_free_paperwork_filed: boolean;
  notes: string;
  created_at: string;
  updated_at: string;
  // Computed fields
  item_count?: number;
  purchase_order_count?: number;
  total_spent?: string;
}

export interface SupplierDetail extends Supplier {
  items?: ItemSupplier[];
  purchase_orders?: any[]; // PurchaseOrder type from reorder_queue
  lead_time_analytics?: {
    average_lead_time: number | null;
    min_lead_time: number | null;
    max_lead_time: number | null;
    average_variance: number | null;
    total_orders: number;
    on_time_percentage: number | null;
    recent_logs?: Array<{
      item_name: string;
      order_date: string;
      expected_delivery_date: string;
      actual_delivery_date: string;
      estimated_lead_time_days: number;
      actual_lead_time_days: number;
      variance_days: number;
      was_late: boolean;
    }>;
  };
  price_trends?: {
    trends: Array<{
      item_id: string;
      item_name: string;
      price_history: Array<{
        recorded_at: string;
        unit_cost: number | null;
        package_cost: number | null;
        change_type: string;
        price_change_percentage: number | null;
      }>;
    }>;
    summary: {
      average_unit_cost: number | null;
      min_unit_cost: number | null;
      max_unit_cost: number | null;
      price_changes_count: number;
    };
  };
}

export interface Category {
  id: number;
  name: string;
  slug: string;
  description: string;
  parent: number | null;
  parent_name?: string | null;
  color?: string;
  item_count?: number;
  children?: Category[];
}

export interface Location {
  id: number;
  name: string;
  description: string;
  is_active: boolean;
  parent: number | null;
  parent_name?: string | null;
  children?: Location[];
  fixture_count?: number;
  qr_code?: string | null;
  qr_code_url?: string | null;
  access_code?: string | null;
  created_at?: string;
  updated_at?: string;
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
  // Case-based reordering fields
  use_case_based_reorder: boolean;
  minimum_cases: number;
  reorder_cases: number;
  current_cases: number;
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
  // Ownership fields
  ownership_type: 'user' | 'group' | 'space';
  owning_user: number | null;
  owning_group: number | null;
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
  // Hazmat fields
  is_hazardous: boolean;
  msds_url: string | null;
  nfpa_health_hazard: number | null;
  nfpa_fire_hazard: number | null;
  nfpa_instability_hazard: number | null;
  nfpa_special_hazards: string;
  nfpa_fire_diamond_display: string;
  hazmat_compliance_status: string;
  has_complete_nfpa_data: boolean;
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

export type AssetStatus = 'active' | 'maintenance' | 'retired' | 'lost' | 'donated_out';
export type OperationalStatus = 'available' | 'reserved' | 'needs_maintenance' | 'disabled';

export interface Asset {
  id: string;
  name: string;
  description: string;
  serial_number: string;
  asset_tag: string;
  inventory_item: string | null;
  inventory_item_name: string;
  manufacturer: number | null;
  manufacturer_name: string;
  display_manufacturer: string;
  date_received: string | null;
  amount_paid: string;
  is_donation: boolean;
  donor_name: string;
  acquisition_display: string;
  category: number | null;
  category_name: string;
  location: number | null;
  location_name: string;
  product_url: string;
  wiki_page_url: string;
  maintenance_plan: string;
  image: string | null;
  image_url: string | null;
  thumbnail_url: string | null;
  manual_pdf: string | null;
  manual_pdf_url: string | null;
  qr_code: string | null;
  qr_code_url: string | null;
  qr_code_scan_url: string | null;
  status: AssetStatus;
  condition_notes: string;
  age_in_days: number;
  is_active: boolean;
  report_only: boolean;
  notes: string;
  circuit: string;
  needs_compressed_air: boolean;
  needs_ventilation: boolean;
  is_chargeable: boolean;
  last_scanned_at: string | null;
  // Group ownership and locking
  ownership_type: 'user' | 'group' | 'space';
  owning_group: number | null;
  owning_group_name: string | null;
  owning_user: number | null;
  owning_user_name: string | null;
  groups_can_enable: number[];
  is_locked: boolean;
  lockout_info: {
    locked_by: string | null;
    locked_at: string | null;
    lockout_level: string;
    reason: string | null;
  } | null;
  // Authorization
  can_enable: boolean;
  can_unlock: boolean;
  // Operational status
  operational_status: OperationalStatus;
  // Parts/consumables
  parts?: AssetPart[];
  created_at: string;
  updated_at: string;
}

export interface AssetPart {
  id: string;
  asset: string;
  asset_name: string;
  asset_tag: string;
  part: string;
  part_name: string;
  part_sku: string;
  quantity_needed: number;
  is_required: boolean;
  maintenance_interval_days: number | null;
  last_replaced_at: string | null;
  days_since_replacement: number | null;
  needs_replacement: boolean;
  notes: string;
  part_details?: {
    id: string;
    name: string;
    sku: string;
    current_stock: number;
    minimum_stock: number;
    needs_reorder: boolean;
  };
  created_at: string;
  updated_at: string;
}

export type AssetProblemStatus = 'reported' | 'in_progress' | 'resolved' | 'closed';

export interface AssetProblem {
  id: string;
  asset: string;
  asset_name: string;
  asset_tag: string;
  reported_by: string;
  description: string;
  status: AssetProblemStatus;
  resolution_notes: string;
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
  resolved_by: string;
}

export interface MaintenanceMaterial {
  id: string;
  maintenance_item: string;
  inventory_item: string | null;
  inventory_item_name: string | null;
  inventory_item_sku: string | null;
  inventory_current_stock: number | null;
  inventory_reorder_status: string | null;
  has_pending_reorder: boolean;
  name: string;
  display_name: string;
  quantity: string;
  unit: string;
  estimated_cost_per_unit: string;
  total_estimated_cost: string;
  notes: string;
  created_at: string;
}

export interface MaintenanceItem {
  id: string;
  asset: string;
  asset_name: string;
  asset_tag: string;
  title: string;
  description: string;
  estimated_time_minutes: number | null;
  estimated_cost: string;
  interval_days: number | null;
  last_completed_at: string | null;
  is_active: boolean;
  is_overdue: boolean;
  days_overdue: number | null;
  next_due_at: string | null;
  materials: MaintenanceMaterial[];
  tasks: MaintenanceTask[];
  created_at: string;
  updated_at: string;
}

export interface MaintenanceLog {
  id: string;
  maintenance_item: string;
  maintenance_item_title: string;
  asset_name: string;
  completed_by: number | null;
  completed_by_name: string | null;
  completed_at: string;
  time_spent_minutes: number | null;
  cost_incurred: string | null;
  notes: string;
  created_at: string;
}

export interface MaintenanceTask {
  id: string;
  maintenance_item: string;
  order: number;
  title: string;
  description: string;
  is_required: boolean;
  created_at: string;
}

export type WorkOrderStatus = 'open' | 'in_progress' | 'blocked' | 'completed';

export interface WorkOrderTaskCompletion {
  id: string;
  work_order: string;
  task: string | null;
  task_title: string;
  task_order: number;
  is_required: boolean;
  is_completed: boolean;
  completed_by: number | null;
  completed_by_name: string | null;
  completed_at: string | null;
  notes: string;
  created_at: string;
}

export interface WorkOrderMaterialUsage {
  id: string;
  work_order: string;
  material: string | null;
  material_name: string;
  quantity_planned: string;
  unit: string;
  was_used: boolean;
  created_at: string;
}

export interface WorkOrderPhoto {
  id: string;
  work_order: string;
  image: string;
  image_url: string | null;
  caption: string;
  uploaded_by: number | null;
  uploaded_by_name: string | null;
  uploaded_at: string;
}

export interface WorkOrder {
  id: string;
  short_id: string;
  maintenance_item: string;
  maintenance_item_title: string;
  asset_name: string;
  asset_tag: string;
  asset_id: string;
  status: WorkOrderStatus;
  due_date: string | null;
  assigned_to: number | null;
  assigned_to_name: string | null;
  completed_by_name: string;
  completed_at: string | null;
  notes: string;
  is_overdue: boolean;
  task_completions: WorkOrderTaskCompletion[];
  material_usage: WorkOrderMaterialUsage[];
  photos: WorkOrderPhoto[];
  task_completion_count?: number;
  task_total_count?: number;
  created_at: string;
  updated_at: string;
}

export interface SiteSettings {
  site_name: string;
  site_tagline: string;
  logo_url: string | null;
  logo_alt_text: string;
  favicon_url: string | null;
  primary_color: string;
  secondary_color: string;
  footer_text: string;
  contact_email: string;
  contact_phone: string;
  website_url: string;
  dashboard_title: string;
  dashboard_subtitle: string;
  show_logo_on_dashboard: boolean;
}

export interface Fixture {
  id: string;
  name: string;
  description: string;
  location: number;
  location_name: string;
  refill_item: string;
  refill_item_name: string;
  refill_item_sku: string;
  asset_tag: string | null;
  is_active: boolean;
  pending_requests_count: number;
  qr_code_url: string;
  created_at: string;
  updated_at: string;
}

export type FixtureRefillRequestStatus = 'pending' | 'in_progress' | 'completed' | 'cancelled';

export interface FixtureRefillRequest {
  id: string;
  fixture: string;
  fixture_name: string;
  fixture_location: string;
  refill_item_name: string;
  refill_item_sku: string;
  status: FixtureRefillRequestStatus;
  requested_at: string;
  requested_by: string;
  resolved_at: string | null;
  resolved_by: string;
  notes: string;
  time_to_resolve: number | null;
}

// SIG (Special Interest Group) types
export interface SIG {
  id: number;
  name: string;
  member_count: number;
  asset_count: number;
  inventory_count: number;
  admins: SIGAdmin[];
  is_user_admin: boolean;
}

export interface SIGAdmin {
  id: number;
  username: string;
  email: string;
  handle: string;
}

export interface SIGMember {
  id: number;
  username: string;
  email: string;
  handle: string;
  is_sig_admin: boolean;
}

// Checklist types
export interface ChecklistStep {
  id: string;
  step_number: number;
  name: string;
  asset: string | null;
  location: number | null;
  inventory_item: string | null;
  required: boolean;
  notes: string;
}

export interface Checklist {
  id: string;
  name: string;
  description: string;
  sig: number;
  sig_name: string;
  is_active: boolean;
  is_public: boolean;
  created_by: number | null;
  created_by_username: string | null;
  steps: ChecklistStep[];
  step_count?: number;
  created_at: string;
  updated_at: string;
}

export interface ChecklistStepCompletion {
  id: string;
  step: string;
  step_name: string;
  step_number: number;
  scanned_at: string;
  scanned_asset: string | null;
  scanned_asset_name: string | null;
  scanned_location: number | null;
  scanned_location_name: string | null;
  scanned_item: string | null;
  scanned_item_name: string | null;
  notes: string;
}

export type ChecklistCompletionStatus = 'in_progress' | 'completed' | 'abandoned';

export interface ChecklistCompletion {
  id: string;
  checklist: string;
  checklist_name: string;
  user: number | null;
  user_username: string | null;
  user_name: string;
  started_at: string;
  completed_at: string | null;
  status: ChecklistCompletionStatus;
  step_completions: ChecklistStepCompletion[];
  completed_steps_count: number;
  total_steps_count: number;
  required_steps_completed: number;
  required_steps_total: number;
  created_at: string;
  updated_at: string;
}

export type DonationItemStatus = 'pending_review' | 'usable' | 'unusable' | 'processing' | 'disposed';
export type DonationItemCondition = 'excellent' | 'good' | 'fair' | 'poor' | 'unusable';

export interface DonationItem {
  id: string;
  donation: string;
  donation_number: string;
  donor_name: string;
  name: string;
  description: string;
  quantity: number;
  condition: DonationItemCondition;
  status: DonationItemStatus;
  access_code: string | null;
  qr_code: string | null;
  asset: string | null;
  inventory_item: string | null;
  notes: string;
  remaining_quantity: number;
  created_at: string;
  updated_at: string;
}

export type DispositionType = 'kept' | 'sold' | 'auctioned' | 'donated_out' | 'recycled' | 'disposed' | 'returned' | 'parted_out' | 'other';
export type SaleMethod = 'direct' | 'auction';
export type KeptDestination = 'makerspace' | 'sig';

export interface TaxReceipt {
  id: string;
  serial_number: string;
  donation: string;
  donation_number: string;
  donor_name: string;
  donor_email: string;
  issued_date: string;
  issued_by: string;
  issued_by_username: string;
  pdf_file: string | null;
  is_copy: boolean;
  created_at: string;
  updated_at: string;
}

export interface SearchResult {
  id: string;
  type: 'inventory' | 'asset' | 'purchase_order' | 'supplier' | 'location';
  title: string;
  subtitle?: string | null;
  url: string;
}

export interface RecentSearch {
  id: number;
  query: string;
  result_type: 'inventory' | 'asset' | 'purchase_order' | 'supplier' | 'location';
  result_id: string;
  result_title: string;
  searched_at: string;
}

export interface Disposition {
  id: string;
  donation_item: string;
  donation_item_name: string;
  donation_number: string;
  disposition_type: DispositionType;
  quantity: number;
  disposition_date: string;
  disposed_by: number | null;
  sale_method: SaleMethod | null;
  sale_price: string | null;
  kept_destination: KeptDestination | null;
  kept_for_sig: number | null;
  notes: string;
  recipient_name: string;
  created_asset: string | null;
  created_at: string;
  updated_at: string;
}

// Report Types
export interface InventoryStockByCategory {
  category_id: number | null;
  category_name: string;
  total_items: number;
  total_stock: number;
  total_value: number;
  low_stock_count: number;
}

export interface InventoryReorderFrequency {
  item_id: string;
  item_name: string;
  item_sku: string;
  category_name: string;
  reorder_count: number;
}

export interface InventoryValueByLocation {
  location_id: number | null;
  location_name: string;
  total_items: number;
  total_stock: number;
  total_value: number;
}

export interface PurchasingSpendBySupplier {
  supplier_id: number;
  supplier_name: string;
  total_orders: number;
  total_spend: number;
  avg_order_value: number;
}

export interface PurchasingSpendByCategory {
  category_id: number | null;
  category_name: string;
  total_items: number;
  total_quantity: number;
  total_spend: number;
}

export interface PurchasingLeadTimeAnalysis {
  supplier_id: number;
  supplier_name: string;
  item_name: string;
  total_orders: number;
  avg_estimated_lead_time: number;
  avg_actual_lead_time: number;
  avg_variance: number;
  on_time_rate: number;
}

export interface PurchasingPriceTrends {
  item_id: string;
  item_name: string;
  supplier_name: string;
  price_changes: number;
  min_unit_cost: number;
  max_unit_cost: number;
  latest_unit_cost: number;
  price_change_percentage: number | null;
}

export interface AssetAssetsByStatus {
  status: string;
  status_display: string;
  count: number;
}

export interface AssetMaintenanceDue {
  asset_id: string;
  asset_name: string;
  asset_tag: string;
  part_id: string | null;
  part_name: string | null;
  part_sku: string | null;
  maintenance_interval_days: number | null;
  days_since_replacement: number | null;
  days_overdue: number | null;
  last_replaced_at: string | null;
  status?: string;
}

export interface AssetUtilization {
  asset_id: string;
  asset_name: string;
  asset_tag: string;
  total_sessions: number;
  total_hours: number;
  avg_hours_per_session: number;
}

// Dashboard Widget Types
export type WidgetType = 'low_stock' | 'pending_reorders' | 'asset_problems' | 'qr_scans' | 'deliveries';

export interface DashboardWidget {
  id: number;
  widget_type: WidgetType;
  position_x: number;
  position_y: number;
  width: number;
  height: number;
  is_visible: boolean;
  order: number;
  settings: Record<string, any>;
  created_at: string;
  updated_at: string;
}

export interface LowStockData {
  count: number;
  items: Array<{
    id: string;
    name: string;
    current_stock: number;
    minimum_stock: number;
    reorder_quantity: number;
    category__name: string | null;
    location__name: string | null;
  }>;
  timestamp: string;
}

export interface PendingReordersData {
  count: number;
  requests: Array<{
    id: number;
    item_id: string;
    item_name: string;
    quantity: number;
    priority: string;
    requested_by: string;
    requested_at: string;
    category: string | null;
  }>;
  timestamp: string;
}

export interface AssetProblemsData {
  count: number;
  problems: Array<{
    id: string;
    asset_id: string;
    asset_name: string;
    asset_tag: string;
    status: string;
    reported_by: string;
    description: string;
    created_at: string;
  }>;
  timestamp: string;
}

export interface QRScansData {
  total_scans: number;
  asset_scans: number;
  item_scans: number;
  daily_scans: Array<{
    date: string;
    assets: number;
    items: number;
    total: number;
  }>;
  timestamp: string;
}

export interface DeliveriesData {
  count: number;
  deliveries: Array<{
    id: number;
    delivery_date: string;
    supplier_name: string | null;
    received_by: string | null;
    items_count: number;
    total_quantity: number;
    purchase_order_id: number;
  }>;
  timestamp: string;
}

// User Profile types
export interface UserProfile {
  id: number;
  username: string;
  email: string;
  first_name: string;
  last_name: string;
  handle: string | null;
  discord_username: string | null;
  discourse_username: string | null;
  signature_image_url: string | null;
}

export interface ChangePasswordRequest {
  old_password: string;
  new_password: string;
  new_password2: string;
}

export interface NotificationPreferences {
  id: number;
  email_enabled: boolean;
  in_app_enabled: boolean;
  supply_alerts: boolean;
  maintenance_alerts: boolean;
  order_updates: boolean;
  system_notifications: boolean;
  recent_pages_limit: number;
  created_at: string;
  updated_at: string;
}

// Webhook types
export type WebhookEventType =
  | 'reorder_request_created'
  | 'reorder_request_approved'
  | 'reorder_request_ordered'
  | 'reorder_request_received'
  | 'item_low_stock'
  | 'purchase_order_created'
  | 'delivery_received'
  | 'fixture_refill_requested'
  | 'location_checkin'
  | 'location_feedback'
  | 'security_report';

export interface Webhook {
  id: number;
  name: string;
  description: string;
  url: string;
  event_type: WebhookEventType;
  event_type_display: string;
  is_active: boolean;
  secret?: string; // Only returned on create/update, not in list
  headers: Record<string, string> | null;
  last_triggered_at: string | null;
  success_count: number;
  failure_count: number;
  success_rate: number | null;
  total_triggers: number;
  last_error: string;
  created_at: string;
  updated_at: string;
}

export interface WebhookTestResult {
  webhook_id?: number | null;
  webhook_name?: string | null;
  task_id?: string | null;
  task_status?: string | null;
  success?: boolean | null;
  status_code?: number | null;
  response_time_ms?: number | null;
  error_message?: string | null;
  response_body?: string | null;
  tested_at?: string | null;
}
