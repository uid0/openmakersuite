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

/**
 * A purchase/pricing agreement held with a supplier (op-yoos) — contract
 * pricing, a standing quote, a nonprofit discount. A purchase order can be
 * placed *under* one of these; retired agreements have `is_active: false` and
 * are hidden from the PO create picker.
 */
export interface SupplierAgreement {
  id: number;
  supplier: number;
  supplier_name: string;
  name: string;
  notes: string;
  document: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
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

/** One supplier that was on offer for an item and did not win. */
export interface SupplierChoiceAlternative {
  /** The `ItemSupplier.id` — look it up in `suppliers[]` for the full row. */
  id: number;
  supplier_name: string;
}

/**
 * Which supplier the API would buy an item from, and WHY that one (op-3xsp).
 *
 * `inventory/services/supplier_selection.py` owns the question; this is its
 * answer on the wire. Read it — not the flat `supplier_name` — on any surface
 * that names a supplier, because the flat key is this same winner with
 * everything that qualifies it dropped.
 *
 * `supplier_name` is null exactly when `reason` is set: there is nothing to buy
 * from, and the two reasons need different words in front of an operator.
 */
export interface SupplierChoice {
  /** The chosen `ItemSupplier` link's own pk, matching a row in `suppliers[]`. */
  item_supplier_id: number | null;
  /** The chosen supplier, or null when nothing here can be ordered from. */
  supplier_name: string | null;
  /**
   * Why there is no supplier: `'no_suppliers'` (nobody has said where this
   * item comes from) or `'none_orderable'` (every link is inactive or
   * discontinued). Null when one was chosen. These are different facts needing
   * different actions, so do not collapse them into one blank.
   */
  reason: 'no_suppliers' | 'none_orderable' | null;
  /**
   * SIGNED-IN ONLY — the four keys below are ABSENT from an unauthenticated
   * response, not null (`SupplierChoiceSerializer.OPERATOR_ONLY_FIELDS`). They
   * describe how the derivation reached its answer and are addressed to
   * whoever maintains the supplier links.
   *
   * Optional here so the type says what the wire does. Every public reading in
   * `utils/supplierChoice` must behave identically whether a key is absent or
   * `false`, so an unauthenticated payload can never grow a rendered caveat.
   *
   * `'flagged_primary'` — an operator flagged this one and it won outright;
   * `'best_scored'` — nothing orderable was flagged, so price, lead time and
   * delivery record were weighed and this one came top. Null with `reason`.
   */
  basis?: 'flagged_primary' | 'best_scored' | null;
  /** An operator flagged a primary and it was skipped as unbuyable. */
  flagged_primary_unorderable?: boolean;
  /** The scoring picked the winner while knowing no price for it. */
  scored_without_price?: boolean;
  /** The scoring picked the winner though nothing has ever been delivered through it. */
  scored_without_history?: boolean;
  /** Every other link that could have been bought from. Empty means it really was the only one. */
  alternatives: SupplierChoiceAlternative[];
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
  // Days. `ItemSupplier.average_lead_time` is NOT NULL with a default of 7, so
  // a value of 0 means "arrives same day" — a different fact from a narrowed
  // payload that carries no lead time at all. Renderers must not collapse the
  // two into one blank.
  average_lead_time: number;
  is_primary: boolean;
  is_active: boolean;
  // Discontinued BY this supplier: the link still exists (and its history is
  // still worth reading) but nothing can be ordered against it.
  is_discontinued: boolean;
  notes: string;
  created_at: string;
  updated_at: string;
}

// ---- Unit of measure / packaging matrix (op-hzji P1, op-es7c P2a, op-ev14 P2b) ----

/**
 * How an item is counted.
 *
 * `each` is the default and what every legacy item is: quantities are the
 * item's base unit. `by_level` counts whole packs of `count_level` (4 cases);
 * `open_closed` counts sealed packs plus a tally of opened ones.
 */
export type ItemCountMode = 'each' | 'by_level' | 'open_closed';

/**
 * One rung of an item's packaging chain. `sort_order` 0 is the outermost /
 * largest rung and increases toward the base rung, whose `base_units` is 1.
 * `per_parent` ("1 case = 10 reams") is derived server-side and is null for
 * the base rung.
 */
export interface PackagingLevel {
  id: number;
  name: string;
  sort_order: number;
  base_units: number;
  per_parent: number | null;
}

/**
 * `InventoryItem.current_stock` expressed at the item's counting granularity —
 * read-only, computed from the canonical base-unit count. Which optional keys
 * are present depends on `mode`: `each` → unit/base_units, `by_level` →
 * level/level_count/remainder_base, `open_closed` → level/sealed/open. `text`
 * is always present and is the label to render.
 */
export interface ItemOnHandDisplay {
  mode: ItemCountMode;
  text: string;
  unit?: string;
  base_units?: number;
  level?: string;
  level_count?: number;
  remainder_base?: number;
  sealed?: number;
  open?: number;
}

/**
 * Reorder point + current count in ONE unit (op-es7c), so a caller can label
 * the pair without knowing which columns the item's `count_mode` gives meaning
 * to. For a pack-counting item every quantity here is in `unit` (cases/reams).
 */
export interface ItemReorderDisplay {
  mode: ItemCountMode;
  unit: string;
  threshold: number;
  current: number;
  reorder_quantity: number;
  needs_reorder: boolean;
  text: string;
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
  // `null` when nothing records how many units a case holds — no supplier link,
  // or one recording a pack size of 0 (op-c1ke). NOT zero and NOT the base-unit
  // count: the server used to send raw units here, so "10 cases" meant ten
  // loose units and a low item stopped being flagged. Render it as unknown.
  current_cases: number | null;
  supplier: number | null;
  supplier_name: string | null;
  supplier_sku: string | null;
  supplier_url: string | null;
  /**
   * A NUMBER, not a decimal string (op-9m2v).
   *
   * The rule, once, for every price in this file: a price that is a real model
   * `DecimalField` on a `ModelSerializer` (`ItemSupplier.unit_cost`) is
   * serialised by DRF as a decimal STRING, so `"0.00"` is truthy and a
   * truthiness guard on it is safe. A price that is a model PROPERTY named in
   * `Meta.fields` with no explicit declaration becomes a `ReadOnlyField`, which
   * hands the raw `Decimal` to DRF's `JSONEncoder` and arrives as a JSON
   * NUMBER — as does a `SerializerMethodField`. `InventoryItem.unit_cost` is
   * the property kind (`order_unit_price(self).amount`), so a donated item
   * sends `0`, which is falsy AND which React renders as a stray "0".
   * Same attribute name, two wire types, decided by the serializer field.
   */
  unit_cost: number | null;
  average_lead_time: number | null;
  qr_code: string | null;
  is_active: boolean;
  // Retirement (op-jv7r). A retired item is never flagged for reorder and is
  // auto-hidden from the default list once its stock hits 0. `is_retired` is
  // writable (item form/admin toggle); `retired_at` is a read-only audit stamp
  // set by the retire/unretire actions.
  is_retired: boolean;
  retired_at: string | null;
  // Per-item opt-in for ML reorder alerts (op-1). Writable from the item form:
  // when on, the nightly demand forecast surfaces this item in the
  // `reorder_alerts` notify set once it is due to reorder.
  reorder_alerts_enabled: boolean;
  notes: string;
  needs_reorder: boolean;
  // `null` when no supplier records a price, so the stock cannot be valued
  // (op-9m2v). The server used to send "0.00", which claims the shelf is worth
  // nothing. Render the absence, never a $0.00.
  total_value: string | null;
  created_at: string;
  updated_at: string;
  // Ownership fields
  ownership_type: 'user' | 'group' | 'space';
  owning_user: number | null;
  owning_group: number | null;
  // Serialized-component tracking (#818). Exposed read-only; when
  // `is_serialized` is true the item tracks individual units by serial
  // number and `serial_tracking_mode` selects the lifecycle branch.
  // (Union kept inline to avoid a types <-> services/api import cycle; it
  // matches `SerializedTrackingMode` in services/api.ts.)
  is_serialized?: boolean;
  serial_tracking_mode?: 'consumable' | 'reusable';
  // Kit SKUs (op-8n0). When `is_kit` is true this row is a purchasable bundle
  // that DECOMPOSES on receipt: ordering it is one purchase-order line, and
  // receiving it credits its `KitComponent` rows rather than the kit itself.
  // Kits are excluded from `GET /inventory/items/` unless `include_kits=true`
  // or `is_kit=true` is passed.
  is_kit?: boolean;
  // Display-only serialized stock split, present on the item-detail (retrieve)
  // payload for serialized items and null otherwise. `available` = on_hand −
  // installed; `on_hand` counts every physically-present unit. Does not touch
  // the aggregate `current_stock` / generic reorder path.
  serialized_stock?: {
    available: number;
    on_hand: number;
    installed: number;
  } | null;
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
  // Every supplier link for this item, with that supplier's own SKU, UPCs,
  // pricing and lead time. This is the supplier source of truth; the flat
  // `supplier_name` / `supplier_sku` / `supplier_url` / `average_lead_time`
  // keys above are read-only legacy accessors for the ONE link the API says to
  // buy through, and are superseded by this array. Four of the seven still have
  // web readers — `supplier_sku` (the kit list and kit form), `supplier_url`
  // (the admin dashboard's order-pad link), `unit_cost` (every price rendered
  // as a number) and `average_lead_time` (the scan page and the dashboard's
  // Lead Time column); `supplier_name`, `package_cost` and `quantity_per_package`
  // have none. A read off an `ItemSupplier` row below is that row's OWN column,
  // not one of these. That link is never one you
  // cannot order from (inactive or discontinued links are skipped outright);
  // among the rest, a link the operator flagged primary wins, and otherwise the
  // API weighs price AND lead time together — it is not simply the cheapest.
  // Null flats mean "no supplier you can buy from", which is not the same as
  // "no suppliers" — read `suppliers` to tell those apart. See
  // `inventory/services/supplier_selection.py`.
  // A surface that NAMES a supplier reads `supplier_choice` below rather than
  // either of these: the array does not say which link won, and the flat name
  // does not say what else was on offer or what the choice did not know.
  // Optional because list payloads a caller narrowed may omit it; absent is
  // "we were not told", which is not the same as an empty array.
  suppliers?: ItemSupplier[];
  // Which of those links the API would buy through, AND why that one (op-3xsp).
  // The field to read on any surface that NAMES a supplier: the flat
  // `supplier_name` above is this same winner with the derivation thrown away,
  // so it cannot say that four other suppliers were on offer, that the scoring
  // chose this one without knowing a price for it, or that an operator's own
  // flagged primary was skipped as unbuyable. Optional for the same reason
  // `suppliers` is, and because a narrowed payload may omit it.
  supplier_choice?: SupplierChoice;
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
  // Cycle-count tracking (op-c7y4): timestamp of the most recent cycle count
  // and whole days since (null when the item has never been counted).
  last_counted_at: string | null;
  days_since_last_count: number | null;
  // Unit of measure / packaging matrix (op-hzji + op-es7c + op-ev14). Additive
  // and opt-in: an item that sets none of these counts individual base units
  // exactly as it always has, so every field is optional on the wire as far as
  // the web is concerned. `base_unit`/`count_mode`/`count_level`/
  // `open_container_count`/`packaging_levels` are writable; `on_hand_display`
  // and `reorder_display` are server-rendered presentations of the canonical
  // base-unit `current_stock`.
  base_unit?: string;
  count_mode?: ItemCountMode;
  count_level?: number | null;
  open_container_count?: number;
  packaging_levels?: PackagingLevel[];
  on_hand_display?: ItemOnHandDisplay;
  reorder_display?: ItemReorderDisplay;
}

export type InventoryCostTrend = 'up' | 'down' | 'flat' | 'no_history';

// One open work order holding part of an item's committed quantity (op-l4i0).
// The attribution side of QC: which job — and so which machine — the reserved
// stock is going to. Entries arrive oldest work order first and sum to
// `quantity_committed`. `asset_id`/`asset_name` are null on an asset-less WO.
export interface CommittedBreakdownEntry {
  work_order_id: string;
  work_order_short_id: string; // e.g. "WO-1A2B3C4D"
  asset_id: string | null;
  asset_name: string | null;
  quantity: number;
}

// Computed stock + cost metrics for the item-detail metrics row (issue-5).
// Served by GET /api/inventory/items/<id>/metrics/. Quantities are numbers;
// money fields arrive as decimal strings — `unit_cost` here is an explicit
// `DecimalField` on the serializer, like `ItemSupplier.unit_cost` and unlike
// the property-backed `InventoryItem.unit_cost`, which is a number.
export interface InventoryItemMetrics {
  current_stock: number; // QOH — on hand
  quantity_on_order: number; // QOO — open PO units
  quantity_available: number; // QA — on hand minus committed
  quantity_committed: number; // QC — open work-order demand
  committed_breakdown: CommittedBreakdownEntry[]; // which WOs/assets hold QC
  quantity_in_transit: number; // QIT — partially-received (⊆ QOO)
  reorder_point: number; // RP
  lead_time_days: number | null; // Lead
  unit_cost: string | null; // Cost — per-item, or per-case when case-based
  cost_trend: InventoryCostTrend;
  last_po_unit_cost: string | null;
  is_case_based: boolean;
  case_size: number | null; // units per case
  // Why Cost / Lead above may be blank or unbacked (op-2rsp). The supplier
  // scoring neither rewards nor punishes a missing price or an empty delivery
  // record, so a supplier can win WITH one — these say when it did, so a blank
  // Cost cell is not read as "no supplier". Both are false when an operator's
  // own flagged primary took the gate.
  supplier_scored_without_price: boolean;
  supplier_scored_without_history: boolean;
}

// Per-item purchase/receipt provenance (op-96uo) — payload of
// GET /api/inventory/items/<id>/purchase_history/. Both lists are flat, oldest
// first, and carry the PO pk (`purchase_order`) alongside `po_number` because
// po_number is nullable and so is not a safe grouping key. Money fields arrive
// as decimal strings (DRF DecimalField), like ItemSupplier.unit_cost.

// One purchase-order line: what this item cost on that order.
export interface ItemOrderCost {
  purchase_order: number;
  po_number: string | null;
  order_date: string;
  status: string;
  quantity_ordered: number;
  unit_cost_ordered: string;
  unit_cost_actual: string | null;
}

// One delivery of the item. A partially-shipped order yields several rows for
// the same purchase_order, each with its own tracking number.
export interface ItemDelivery {
  purchase_order: number;
  po_number: string | null;
  delivery_date: string;
  tracking_number: string;
  carrier: string;
  quantity_received: number;
  receipt_notes: string;
  is_complete: boolean;
}

export interface ItemPurchaseHistory {
  order_costs: ItemOrderCost[];
  deliveries: ItemDelivery[];
}

export interface UsageLog {
  id: number;
  item: string;
  quantity_used: number;
  usage_date: string;
  notes: string;
}

// Stock-history DTO (op-2dqu) — payload of GET /inventory/items/{id}/stock_history/.
// `series` are weekly StockLevelSnapshot points; `cycle_counts` are real
// StockReconciliation counts (date + count). Both feed the stock line so the
// chart is populated before weekly snapshots accumulate. `reorder_events` are
// ReorderRequest dates (no count). `thresholds.reorder_point` = minimum_stock,
// `thresholds.desired` = minimum_stock + reorder_quantity. All dates are ISO
// YYYY-MM-DD strings; counts are integers.
export interface StockHistoryPoint {
  date: string;
  count: number;
}

export interface StockHistoryEvent {
  date: string;
}

export interface StockHistoryThresholds {
  reorder_point: number;
  desired: number;
}

export interface StockHistory {
  series: StockHistoryPoint[];
  reorder_events: StockHistoryEvent[];
  cycle_counts: StockHistoryPoint[];
  thresholds: StockHistoryThresholds;
  current_stock: number;
}

// Log-usage / consume payload (op-27wa). `charged_group` is a SIG/Group id;
// when set the backend posts a committee charge on the ledger (Bead 1, #920).
export interface LogUsageRequest {
  quantity: number;
  notes?: string;
  charged_group?: number;
  // op-ev14: opt in to reading `quantity` as a count of whole `count_level`
  // packs instead of base units. Omitted (the default) means base units, which
  // is what every each-mode item must keep sending; sending it for an item that
  // is not counted in packs is a 400, never a silent base-unit reading.
  at_level?: boolean;
}

// Response from POST /inventory/items/{id}/log_usage/: the UsageLog plus the
// accounting outcome. Money fields are decimal strings; all are nullable when
// no committee was charged. `warning` is set when the committee was recorded
// but the item has no unit cost, so nothing was posted to the ledger.
export interface LogUsageResponse extends UsageLog {
  charged_group: number | null;
  unit_cost: string | null;
  total_cost: string | null;
  ledger_transaction: number | null;
  warning?: string;
  // op-ev14 echoes the number as sent plus the unit the server read it in
  // (`quantity_used` above is always base units), and the refreshed on-hand.
  entered_quantity?: number;
  entered_unit?: string;
  on_hand_display?: ItemOnHandDisplay;
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

// Light cert summary backend ships alongside Asset.required_certifications
// so the SPA + e-paper render don't need a second round-trip per id.
export interface RequiredCertificationSummary {
  id: number;
  name: string;
  slug: string;
  sig_name: string;
}

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
  // Landlord cost recovery: when set, in-house repair cost on this asset flows
  // into the recoverable (Actual) column of the cost-recovery statement.
  is_cost_recoverable: boolean;
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
  mac_address: string;
  needs_compressed_air: boolean;
  needs_ventilation: boolean;
  is_chargeable: boolean;
  training_required: boolean;
  required_certifications: number[];
  required_certification_details: RequiredCertificationSummary[];
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
  // Usage meters (EAM bead-1) — nested read-only on the asset-detail payload
  meters?: AssetMeter[];
  // Power / electrical — direct FKs to PowerBreaker + Disconnect, plus
  // server-rendered summaries for read-only display.
  breaker: number | null;
  breaker_summary: {
    id: number;
    panel_id: number | null;
    panel_name: string;
    position: string;
    amperage: number;
    label: string;
  } | null;
  disconnect: number | null;
  disconnect_summary: {
    id: number;
    label: string;
    disconnect_type: string;
    is_lockable: boolean;
  } | null;
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
  replacement_serial_number?: string;
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
    is_serialized?: boolean;
  };
  created_at: string;
  updated_at: string;
}

export type AssetProblemStatus = 'reported' | 'in_progress' | 'resolved' | 'closed';

export interface AssetProblemPhoto {
  id: string;
  problem: string;
  image: string;
  image_url: string | null;
  caption: string;
  uploaded_by: number | null;
  uploaded_by_name: string | null;
  uploaded_at: string;
}

export type AssetDocumentCategory =
  | 'manual'
  | 'cad_source'
  | 'wiring_diagram'
  | 'cut_sheet_spec'
  | 'cut_ready_template'
  | 'photo'
  | 'other';

export interface AssetDocument {
  id: string;
  asset: string;
  file: string;
  file_url: string | null;
  category: AssetDocumentCategory;
  category_display: string;
  title: string;
  description: string;
  version: number;
  is_current: boolean;
  supersedes: string | null;
  supersedes_title: string | null;
  uploaded_by: number | null;
  uploaded_by_name: string | null;
  uploaded_at: string;
}

export type AssetMeterType =
  | 'runtime_hours'
  | 'volume_gallons'
  | 'cycles'
  | 'kwh'
  | 'generic_count';

export type AssetMeterSource = 'auto_session' | 'auto_telemetry' | 'manual';

export type AssetMeterReadingSource =
  | 'auto_session'
  | 'auto_telemetry'
  | 'manual'
  | 'manual_adjust';

export interface AssetMeter {
  id: string;
  asset: string;
  name: string;
  meter_type: AssetMeterType;
  meter_type_display: string;
  unit: string;
  source: AssetMeterSource;
  source_display: string;
  current_value: string;
  current_is_estimated: boolean;
  rollup_watermark_at: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface AssetMeterReading {
  id: string;
  meter: string;
  source: AssetMeterReadingSource;
  source_display: string;
  delta: string;
  value_after: string;
  is_estimated: boolean;
  observed_at: string;
  recorded_at: string;
  recorded_by: number | null;
  recorded_by_name: string | null;
  source_ref: string;
  notes: string;
}

export interface AssetProblem {
  id: string;
  asset: string;
  asset_name: string;
  asset_tag: string;
  reported_by: string;
  description: string;
  status: AssetProblemStatus;
  // Set once the report is promoted to real work: an in-house corrective
  // WorkOrder or a vendor ThirdPartyWorkOrder (never both from one promote).
  work_order?: string | null;
  work_order_short_id?: string | null;
  third_party_work_order?: string | null;
  third_party_work_order_short_id?: string | null;
  resolution_notes: string;
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
  resolved_by: string;
  photos: AssetProblemPhoto[];
  // Components (AssetParts) the reporter flagged as needing replace/fix.
  // The API returns a compact shape (id, part_name, part_sku, quantity_needed,
  // is_required) via AffectedAssetPartSerializer.
  affected_parts?: AssetPart[];
}

export type LocationProblemStatus = 'reported' | 'in_progress' | 'resolved' | 'closed';
export type LocationProblemSeverity = 'low' | 'medium' | 'high' | 'urgent';

export interface LocationProblem {
  id: string;
  location: number;
  location_name: string;
  reported_by: string;
  description: string;
  status: LocationProblemStatus;
  status_display: string;
  severity: LocationProblemSeverity;
  severity_display: string;
  photo: string | null;
  photo_url: string | null;
  paper_form_attachment: string | null;
  paper_form_url: string | null;
  work_order: string | null;
  work_order_short_id: string | null;
  third_party_work_order: string | null;
  third_party_work_order_short_id: string | null;
  resolution_notes: string;
  reported_at: string;
  updated_at: string;
  resolved_at: string | null;
  resolved_by: string;
}

export type ActiveMaintenanceKind = 'work_order' | 'asset_problem' | 'location_problem';

export interface ActiveMaintenanceRow {
  kind: ActiveMaintenanceKind;
  id: string;
  short_id: string;
  title: string;
  status: string;
  status_display: string;
  asset_id: string | null;
  asset_name: string | null;
  location_id: number | null;
  location_name: string | null;
  severity: LocationProblemSeverity | null;
  due_date: string | null;
  opened_at: string;
}

export interface MaintenanceMaterialInventoryDetail {
  id: string;
  name: string;
  current_stock: number;
  minimum_stock: number;
  reorder_quantity: number;
}

export interface MaintenanceMaterial {
  id: string;
  maintenance_item: string;
  inventory_item: string | null;
  inventory_item_detail: MaintenanceMaterialInventoryDetail | null;
  name: string;
  quantity: string;
  unit: string;
  estimated_cost_per_unit: string;
  total_estimated_cost: string;
  notes: string;
  created_at: string;
}

/**
 * A tool the maintainer must gather before starting a PM task — as opposed to
 * a {@link MaintenanceMaterial}, which gets consumed. Field names are a pinned
 * API contract (ScanTTY decodes the same payload); do not rename them.
 */
export interface MaintenanceTool {
  id: string;
  maintenance_item: string;
  inventory_item: string | null;
  inventory_item_detail: MaintenanceMaterialInventoryDetail | null;
  name: string;
  /** Whole units — the backend field is a PositiveIntegerField. */
  quantity: number;
  location_hint: string;
  is_required: boolean;
  notes: string;
  created_at: string;
}

/**
 * The trimmed tool shape a work order carries for display + print. The WO
 * serializer deliberately omits the template-side keys (`maintenance_item`,
 * `inventory_item*`, `created_at`) — this Pick keeps the two in step.
 *
 * Pinned key set: ScanTTY decodes this payload for the e-paper work order, so
 * it never grows. Whatever the row's location resolves to for this job arrives
 * under `location_hint`. For the editable surface — which rows exist, which are
 * ad-hoc — read `WorkOrder.tool_rows` instead.
 */
export type WorkOrderTool = Pick<
  MaintenanceTool,
  'id' | 'name' | 'quantity' | 'location_hint' | 'is_required' | 'notes'
>;

/**
 * op-0v4: a work order's OWN tool row — what to grab, and where it is staged
 * for THIS job. Template-derived rows (`is_ad_hoc: false`) are frozen copies of
 * the PM template's tools, made at generation; ad-hoc rows are added during the
 * job and are the only kind a corrective work order can have — and the only
 * kind that can be removed.
 *
 * `location_hint` is the sole editable field; `resolved_location` is what to
 * display (the hint, else the linked inventory item's location, else '').
 */
export interface WorkOrderToolRow {
  id: string;
  work_order: string;
  /** Provenance: the template tool this was copied from. Null when ad-hoc. */
  tool: string | null;
  inventory_item: string | null;
  inventory_item_name: string | null;
  /** Added during the job rather than copied from the template. */
  is_ad_hoc: boolean;
  name: string;
  quantity: number;
  /** Per-job staging spot. Blank means "use the inventory item's location". */
  location_hint: string;
  /** What every surface shows — never write to this. */
  resolved_location: string;
  is_required: boolean;
  notes: string;
  created_at: string;
}

/** Body of `workOrderAPI.addTool` (op-0v4). Only `name` is required. */
export interface WorkOrderAdHocToolInput {
  name: string;
  quantity?: number;
  inventory_item?: string | null;
  location_hint?: string;
  is_required?: boolean;
  notes?: string;
}

export interface LowStockAlert {
  material_id: string;
  item_id: string;
  name: string;
  current: number;
  minimum: number;
  reorder_qty: number;
}

export interface CheckMaterialStockResponse {
  low_stock_alerts: LowStockAlert[];
}

export interface MaintenanceItem {
  id: string;
  asset: string;
  asset_name: string;
  asset_tag: string;
  title: string;
  description: string;
  instructions: string;
  estimated_time_minutes: number | null;
  estimated_cost: string;
  interval_days: number | null;
  last_completed_at: string | null;
  is_active: boolean;
  is_overdue: boolean;
  days_overdue: number | null;
  next_due_at: string | null;
  materials: MaintenanceMaterial[];
  tools?: MaintenanceTool[];
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
  /**
   * The step's instructional photo — "what this should look like". Write-only
   * on the API (send the File as multipart); read it back through
   * `reference_image_url`.
   */
  reference_image?: File | null;
  /** Absolute URL of the reference photo, null when the step has none. */
  reference_image_url?: string | null;
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
  /**
   * The template step's reference photo, read through `task.reference_image`.
   * Null when the step has no photo or the template row was deleted.
   */
  task_reference_image_url?: string | null;
  /** Photos the tech pinned to this step while doing the work. */
  evidence_photos?: WorkOrderEvidencePhoto[];
  /**
   * Seconds on this step's stopwatch, LIVE: the server adds any segment still
   * running, so this is the running total at fetch time — tick a display over
   * it, never accumulate into it.
   */
  elapsed_seconds?: number;
  /** Whether this step's stopwatch is running right now. */
  is_timing?: boolean;
  created_at: string;
}

/**
 * The trimmed photo shape a *step* carries. The nested serializer omits the
 * keys the parent step already implies (`work_order`, `task_completion`) — this
 * Pick keeps the two in step, same as `WorkOrderTool` does for tools.
 */
export type WorkOrderEvidencePhoto = Pick<
  WorkOrderPhoto,
  'id' | 'image_url' | 'caption' | 'uploaded_at' | 'uploaded_by_name'
>;

export interface WorkOrderMaterialUsage {
  id: string;
  work_order: string;
  material: string | null;
  material_name: string;
  quantity_planned: string;
  /** Quantity actually consumed; drives the inventory decrement when used. */
  quantity_used: string;
  unit: string;
  was_used: boolean;
  /** Whole stock units decremented from the linked item; null when not applied. */
  applied_quantity: number | null;
  /** True when a stock decrement is currently applied for this usage. */
  stock_applied: boolean;
  /**
   * op-768w: added *during* the job rather than copied from the PM template.
   * Only ad-hoc lines can be removed — a template line is the frozen copy of
   * what the job was supposed to be, and it prints on the sign-off sheet.
   */
  is_ad_hoc?: boolean;
  /** Direct stock link of an ad-hoc line (null for an out-of-pocket buy). */
  inventory_item?: string | null;
  /** Name of whichever item this line draws from, either kind of row. */
  inventory_item_name?: string | null;
  /** op-bu80: set only by the receipt bridge — this line mirrors a PO line. */
  purchase_order_item?: string | null;
  /** Real price paid per unit; null when nobody priced the line. */
  unit_cost?: string | null;
  /** `quantity_used × unit_cost`; null when no cost was recorded. */
  actual_cost?: string | null;
  /** Proof-of-purchase photo backing an out-of-pocket buy. */
  receipt_url?: string | null;
  created_at: string;
}

/**
 * Body of `workOrderAPI.addMaterial` (op-768w). `unit_cost` defaults from the
 * linked item's current cost when an item is given and no price is supplied —
 * a default, not a lock. Omit `inventory_item` for an out-of-pocket buy: the
 * line then records the spend and moves no stock.
 */
export interface WorkOrderAdHocMaterialInput {
  material_name: string;
  quantity_used?: number | string;
  unit?: string;
  unit_cost?: number | string | null;
  inventory_item?: string | null;
}

/**
 * op-bu80: a purchase-order line bought to complete this work order — the
 * *ordering* view of the material, which is what says "the part you are
 * waiting on is still in transit". Received lines also show up as material
 * rows, posted by the receipt bridge.
 */
export interface WorkOrderPurchaseLine {
  id: string;
  purchase_order_id: string;
  po_number: string;
  po_status: string;
  supplier_name: string | null;
  name: string;
  item_type: string;
  quantity_ordered: number;
  quantity_received: number;
  quantity_pending: number;
  is_fully_received: boolean;
  /**
   * Whether receiving is FINISHED with this line — received in full,
   * over-received, or written off short. Not the same question as
   * `is_fully_received`, which asks whether the ordered quantity arrived: a
   * line closed short is settled and not fully received, and only `is_settled`
   * answers "is anything still on its way?".
   */
  is_settled: boolean;
  receipt_state: string;
  receipt_state_label: string;
  /** Signed: negative short, positive over. Never floored. */
  quantity_variance: number;
  unit_cost: string;
  expected_delivery_date: string | null;
  expected_shipment_date: string | null;
}

export interface WorkOrderLotoCompletion {
  id: string;
  work_order: string;
  energy_source: number | null;
  /** Denormalized energy-source type code (preserved if source deleted). */
  source_type: string;
  /** Denormalized human label, e.g. 'Electrical (240V)'. */
  source_label: string;
  isolation_point: string;
  /** Comma-joined list of required lockout devices. */
  required_devices: string;
  is_completed: boolean;
  completed_by: number | null;
  completed_by_name: string | null;
  completed_at: string | null;
  notes: string;
  created_at: string;
}

export interface WorkOrderPhoto {
  id: string;
  work_order: string;
  /**
   * The step this photo documents (evidence), or null for a work-order-level
   * photo. Set at upload time by posting `task_completion` to `add_photo`.
   */
  task_completion?: string | null;
  image: string;
  image_url: string | null;
  caption: string;
  uploaded_by: number | null;
  uploaded_by_name: string | null;
  uploaded_at: string;
}

/**
 * A general file hung off a work order (op-7pjj / op-rjsv) — a receipt, a
 * datasheet page, a nameplate photo. Distinct from `WorkOrderPhoto` (per-step
 * or WO-level *evidence* images) and from the asset's document library: this is
 * the internal WO's own catch-all attachments list, the same shape the purchase
 * order and third-party work order carry. `file` is the multipart write field;
 * `file_url` / `file_name` are what the list renders. Served from the top-level
 * `inventory/work-order-attachments/?work_order=` route, not nested on the WO.
 */
export interface WorkOrderAttachment {
  id: string;
  work_order: string;
  file: string;
  file_url: string | null;
  file_name: string | null;
  kind: 'photo' | 'document' | 'other';
  kind_display: string;
  description: string;
  uploaded_by: number | null;
  uploaded_by_name: string | null;
  uploaded_at: string;
}

export interface WorkOrderSubmissionPendingChange {
  kind: string;
  target_id: string | null;
  value: unknown;
  confidence: number;
  label: string;
  // OMR scan (bead-2): a URL to a warped crop of the mark, and whether the
  // mark was auto-pre-checked (confidence >= 0.999) vs queued for review.
  crop_url?: string | null;
  auto_applied?: boolean;
}

export interface WorkOrderSubmission {
  id: string;
  pdf_url: string | null;
  received_at: string;
  status: 'received' | 'applied' | 'failed' | 'pending_review';
  source: 'email' | 'manual' | 'scan';
  from_email: string;
  subject: string;
  submitted_by: number | null;
  submitted_by_name: string | null;
  parse_error: string;
  pending_changes: WorkOrderSubmissionPendingChange[];
}

export interface WorkOrderElectricalOutlet {
  id: number;
  identifier: string;
  outlet_type: string;
  outlet_type_display: string;
  description: string;
  plugged_in_notes: string;
  breaker:
    | {
        id: number;
        panel: string;
        breaker_number: string;
        amperage: number;
        voltage: number;
        label: string;
      }
    | null;
}

export interface WorkOrderElectricalBreaker {
  id: number;
  panel: string;
  breaker_number: string;
  amperage: number;
  voltage: number;
  poles: number;
  description: string;
  label: string;
}

export interface WorkOrderNetworkDrop {
  id: number;
  identifier: string;
  drop_type: string;
  drop_type_display: string;
  patch_panel: string;
  patch_port: string;
  ip_address: string | null;
  description: string;
}

export interface WorkOrderElectricalContext {
  rows: [string, string][];
  outlets: WorkOrderElectricalOutlet[];
  breakers: WorkOrderElectricalBreaker[];
  network_drops: WorkOrderNetworkDrop[];
  is_empty: boolean;
}

export interface WorkOrderLotoContext {
  lockout_type: string;
  lockout_type_code: string;
  lockout_instructions: string;
  lockout_responsible: string;
  is_required: boolean;
  is_empty: boolean;
}

/**
 * op-pzae: an older version of a reference document, from the backend's walk
 * of the `AssetDocument.supersedes` chain (newest-first).
 */
export interface ReferenceDocumentRevision {
  id: string;
  version: number;
  /** Absolute URL, or null when the row outlived its file. */
  file_url: string | null;
  uploaded_at: string | null;
}

/** A current document in the asset's library, with its revision history. */
export interface ReferenceDocument {
  id: string;
  category: string;
  category_display: string;
  title: string;
  version: number;
  file_url: string | null;
  uploaded_at: string | null;
  revisions: ReferenceDocumentRevision[];
}

export interface ReferenceLink {
  label: string;
  url: string;
}

/**
 * Docs a tech can reach while performing/signing the work order — the asset's
 * current documents (manual first) plus its quick links. Read-only projection
 * of the existing document library; the work order stores no links of its own.
 */
export interface ReferenceDocuments {
  documents: ReferenceDocument[];
  links: ReferenceLink[];
}

export interface WorkOrderValidationRecord {
  id: string;
  work_order: string;
  validated_by: number | null;
  validated_by_name: string | null;
  validated_at: string;
  electrical_acknowledged: boolean;
  loto_acknowledged: boolean;
  required_fields_acknowledged: boolean;
  is_complete: boolean;
  notes: string;
}

export interface WorkOrder {
  id: string;
  short_id: string;
  maintenance_item: string;
  maintenance_item_title: string;
  /** Template title, else the reported problem, else the asset — always set. */
  display_title?: string;
  asset_name: string;
  asset_tag: string;
  asset_id: string;
  status: WorkOrderStatus;
  due_date: string | null;
  assigned_to: number | null;
  assigned_to_name: string | null;
  completed_by_name: string;
  /** When work first started (first timer start) — not moved by a later resume. */
  started_at?: string | null;
  completed_at: string | null;
  /**
   * Seconds on the work-order stopwatch, LIVE (includes a running segment).
   * Wall-time-on-job: setup and cleanup too, so it exceeds the sum of the steps.
   */
  elapsed_seconds?: number;
  /** Whether the work-order stopwatch is running right now. */
  is_timing?: boolean;
  /** The template's estimate, for the actual-vs-estimate comparison. */
  estimated_time_minutes?: number | null;
  notes: string;
  /** Free-text LOTO completion note (structured boxes are loto_completions). */
  loto_completion_note: string;
  is_overdue: boolean;
  task_completions: WorkOrderTaskCompletion[];
  material_usage: WorkOrderMaterialUsage[];
  /**
   * op-768w: real money spent on materials — the sum of `actual_cost` over the
   * lines actually used. Server-owned; the page never accumulates into it.
   */
  actual_material_cost?: string | null;
  /** op-bu80: PO lines ordered for this job, on order *and* received. */
  purchase_order_lines?: WorkOrderPurchaseLine[];
  loto_completions: WorkOrderLotoCompletion[];
  photos: WorkOrderPhoto[];
  /** op-67q5: tools to gather, required first — reference only, no OMR box. */
  tools?: WorkOrderTool[];
  /**
   * op-0v4: the work order's own tool rows, in full. Empty on a work order
   * generated before per-job tools, whose `tools` falls back to the PM
   * template and is therefore read-only.
   */
  tool_rows?: WorkOrderToolRow[];
  submissions: WorkOrderSubmission[];
  electrical?: WorkOrderElectricalContext;
  loto?: WorkOrderLotoContext;
  validation?: WorkOrderValidationRecord | null;
  /** op-pzae: manual / revision history / reference links, shown at sign-off. */
  reference_documents?: ReferenceDocuments;
  task_completion_count?: number;
  task_total_count?: number;
  // op-o6rs: number of submissions still awaiting human review (drives the
  // per-WO "scanned — needs review" badge on the list row + detail header).
  pending_review_count?: number;
  has_pending_review?: boolean;
  created_at: string;
  updated_at: string;
}

export interface WorkOrderUploadCompletedItem {
  id: string;
  task_title: string;
}

export interface WorkOrderUploadResult {
  submission_id: string;
  status: 'received' | 'applied' | 'failed';
  work_order_id: string | null;
  completed_items: WorkOrderUploadCompletedItem[];
  errors: string[];
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
  group_email?: string;
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
  requires_photo: boolean;
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
  photo_url: string | null;
  photo_caption: string;
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
  // The value of the stock this report CAN price. `items_without_price` is how
  // many items it could not — a total that omits them is a lower bound, not a
  // valuation (op-9m2v).
  total_value: number;
  items_without_price: number;
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
  // See InventoryStockByCategory — same partial total, same honesty count.
  total_value: number;
  items_without_price: number;
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
  // `null` where nothing is recorded (op-9m2v). These used to be `0` for a
  // price nobody recorded, for a supplier that charges nothing, AND — on
  // `latest_unit_cost` — for an item with no price history at all. A `0` here
  // now means the supplier is free.
  min_unit_cost: number | null;
  max_unit_cost: number | null;
  latest_unit_cost: number | null;
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

export interface AssetTco {
  asset_id: string;
  asset_name: string;
  asset_tag: string;
  maintenance_days_last_90: number;
  scheduled_maintenance_cost: string;
  unscheduled_maintenance_cost: string;
  repair_cost: string;
  tco: string;
  preventive_maintenance_cost: string;
  vendor_maintenance_cost: string;
  total_maintenance_cost_90d: string;
}

// One supply used on an asset over a date window. The backend merges two
// sources into a single flat row list; common keys are always present and the
// remaining keys are source-specific (see inventory/views.py supplies_used).
export interface AssetSuppliesUsed {
  asset_id: string;
  asset_name: string;
  source: 'serialized' | 'consumable';
  item_name: string;
  used_at: string;
  // serialized only — a serial-numbered unit put into service on / consumed by the asset
  serial_number?: string;
  action?: string;
  action_display?: string;
  actor?: string | null;
  // consumable only — a bulk material used while closing a PM work order
  quantity?: string;
  unit?: string;
  work_order_id?: string;
  estimated_cost?: string | null;
}

// Asset cost-recovery statement (reports/assets/cost_recovery). Mirrors the
// merged backend serializers exactly (inventory/serializers.py:
// AssetCostRecoveryServiceSerializer / AssetCostRecoveryReportSerializer). All
// money fields are DRF DecimalField values, serialized as strings.
export interface AssetCostRecoveryService {
  date: string; // YYYY-MM-DD
  source: 'pm' | 'vendor' | 'manual';
  description: string;
  // Internal PM carries an estimate but no actual; vendor/manual carry the
  // actual (recoverable) but no estimate — hence each may be null.
  estimated_cost: string | null;
  // What in-house work really cost (captured material actuals, else the
  // estimate). Informational on every asset — it only reaches the recoverable
  // column when the asset is flagged is_cost_recoverable. Null on vendor/manual
  // rows, which are not in-house work. Optional so a response from before B5
  // still type-checks.
  internal_cost?: string | null;
  actual_cost: string | null;
}

export interface AssetCostRecoveryAsset {
  asset_id: string;
  asset_tag: string;
  name: string;
  serial_number: string;
  date_received: string | null; // "date installed" on the statement
  status: string;
  status_display: string;
  category: string | null;
  // Whether in-house cost on this asset is billable to the landlord (B5).
  is_cost_recoverable?: boolean;
  services: AssetCostRecoveryService[];
  subtotal_estimated: string;
  subtotal_internal?: string; // in-house spend, recoverable only when flagged
  subtotal_actual: string; // recoverable amount for this asset
}

export interface AssetCostRecoveryReport {
  // Echo of the request window/selection.
  period: 'past_week' | 'past_month' | 'past_year' | null;
  start_date: string;
  end_date: string;
  asset_ids: string[];
  category_ids: number[];
  // Ownership/all-assets selection echo. Optional so a cached response from
  // before these filters shipped still type-checks.
  all_assets?: boolean;
  ownership_type?: 'user' | 'group' | 'space' | null;
  owning_group?: number | null;
  asset_count: number;
  service_count: number;
  grand_total_estimated: string;
  grand_total_internal?: string; // total in-house spend across the selection
  grand_total_actual: string; // recoverable total billed to the landlord
  assets: AssetCostRecoveryAsset[];
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

// Interactive Screens / Kiosk Display
export type ScreenBlockType =
  | 'tool_status'
  | 'asset_usage'
  | 'custom_text'
  | 'announcement'
  | 'weather'
  | 'shared_weather'
  | 'traffic'
  | 'shared_traffic'
  | 'member_count'
  | 'board_meeting';

export interface ScreenContentBlock {
  id: string;
  screen: string;
  block_type: ScreenBlockType;
  block_type_display?: string;
  title: string;
  body: string;
  config: Record<string, unknown>;
  order: number;
  is_enabled: boolean;
  created_at?: string;
  updated_at?: string;
}

export type SystemMessageLevel = 'info' | 'warning' | 'critical';

export interface SystemMessage {
  id: string;
  title: string;
  body: string;
  level: SystemMessageLevel;
  level_display?: string;
  is_active: boolean;
  starts_at?: string | null;
  ends_at?: string | null;
  created_at?: string;
  updated_at?: string;
  is_currently_visible?: boolean;
}

export interface Screen {
  id: string;
  slug: string;
  name: string;
  description: string;
  location?: number | null;
  location_name?: string;
  sig?: number | null;
  sig_name?: string;
  is_active: boolean;
  rotation_interval_seconds: number;
  refresh_interval_seconds: number;
  access_token: string;
  is_online: boolean;
  last_heartbeat_at?: string | null;
  content_blocks: ScreenContentBlock[];
  created_at?: string;
  updated_at?: string;
}

export interface ScreenStatusEntry {
  id: string;
  slug: string;
  name: string;
  is_active: boolean;
  is_online: boolean;
  last_heartbeat_at?: string | null;
  sig_name?: string;
  location_name?: string;
  updated_at?: string;
}

export interface KioskPayload {
  screen: {
    id: string;
    slug: string;
    name: string;
    description: string;
    rotation_interval_seconds: number;
    refresh_interval_seconds: number;
  };
  system_messages: Array<{
    id: string;
    title: string;
    body: string;
    level: SystemMessageLevel;
  }>;
  content_blocks: Array<{
    id: string;
    block_type: ScreenBlockType;
    title: string;
    body: string;
    config: Record<string, unknown>;
    order: number;
  }>;
  weather_url?: string;
  generated_at: string;
}

// Electrical Circuits & Network Drops (oms-tt5 / oms-a5f)

export type OutletType =
  | 'standard'
  | '240v'
  | 'nema_5_15'
  | 'nema_5_20'
  | 'nema_6_15'
  | 'nema_6_20'
  | 'nema_l6_30'
  | 'nema_14_30'
  | 'nema_14_50'
  | 'usb'
  | 'other';

export type NetworkDropType =
  | 'data'
  | 'voice'
  | 'patch_panel'
  | 'ap'
  | 'camera'
  | 'iot'
  | 'other';

export interface Breaker {
  id: number;
  location: number;
  location_name: string;
  panel: string;
  breaker_number: string;
  amperage: number;
  voltage: number;
  poles: number;
  description: string;
  notes: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface Outlet {
  id: number;
  location: number;
  location_name: string;
  identifier: string;
  breaker: number | null;
  breaker_label: string | null;
  outlet_type: OutletType;
  description: string;
  plugged_in_notes: string;
  photo: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface LightSwitch {
  id: number;
  location: number;
  location_name: string;
  identifier: string;
  controls_location: number | null;
  controls_location_name: string | null;
  breaker: number | null;
  breaker_label: string | null;
  description: string;
  notes: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface NetworkDrop {
  id: number;
  location: number;
  location_name: string;
  identifier: string;
  drop_type: NetworkDropType;
  patch_panel: string;
  patch_port: string;
  mac_address: string;
  ip_address: string | null;
  description: string;
  notes: string;
  photo: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

// ---------------------------------------------------------------------------
// Project Storage
// ---------------------------------------------------------------------------

export type ProjectStorageStatus =
  | 'active'
  | 'expiring_soon'
  | 'expired'
  | 'purgatory_warned'
  | 'purgatory'
  | 'removed';

export type ProjectStorageEventType =
  | 'created'
  | 'scanned'
  | 'notice_sent'
  | 'moved_to_purgatory'
  | 'removed'
  | 'note_added';

export interface ProjectStorageEvent {
  id: number;
  event_type: ProjectStorageEventType;
  actor_username: string;
  actor_label: string;
  note: string;
  created_at: string;
}

export interface ProjectStorageStint {
  id: number;
  stint_id: string;
  username: string;
  first_name: string;
  last_name: string;
  email: string;
  display_name: string;
  project_title: string;
  started_at: string;
  expires_at: string;
  removed_at: string | null;
  notice_sent_at: string | null;
  moved_to_purgatory_at: string | null;
  storage_location_name: string;
  purgatory_location_name: string;
  /** Racking slot claimed by this stint (pk), or null for ad-hoc storage. */
  slot: number | null;
  /** Canonical code of that slot ("1A1"), or "" when there is none. */
  slot_code: string;
  /** slot_code when racked, else the free-text storage_location_name. */
  location_display: string;
  notes: string;
  status: ProjectStorageStatus;
  purgatory_at: string | null;
  expiry_week: number;
  expiry_day_of_year: number;
  events: ProjectStorageEvent[];
  qr_code_url: string | null;
  april_tag_id: number | null;
  created_at: string;
  updated_at: string;
}

// ---------------------------------------------------------------------------
// Storage slots (the physical racking behind project storage)
// ---------------------------------------------------------------------------

/**
 * The live stint sitting in a slot — the trimmed shape
 * SlotOccupantSerializer returns, not a whole ProjectStorageStint.
 */
export interface StorageSlotOccupant {
  id: number;
  stint_id: string;
  username: string;
  display_name: string;
  project_title: string;
  started_at: string;
  expires_at: string;
  status: ProjectStorageStatus;
}

export interface StorageSlot {
  id: number;
  /** Derived server-side from rack/level/position — read-only. */
  code: string;
  rack: number;
  /** Single upper-case letter. */
  level: string;
  position: number;
  requires_pallet_jack: boolean;
  is_active: boolean;
  owning_group: number | null;
  owning_group_name: string;
  notes: string;
  /** Permanent tag36h10 marker printed on the card; null if the pool ran dry. */
  april_tag_id: number | null;
  /** A member's live project stint (type P), if one holds the slot. */
  current_stint: StorageSlotOccupant | null;
  /** A live committee/logistics/class holding (C/L/E), if one does instead. */
  current_assignment: StorageSlotAssignmentSummary | null;
  /** Letter of whichever occupancy is live — P/C/L/E, null when free. */
  occupancy_type: StorageTypeLetter | null;
  /** True for either kind of occupant: neither slot is free to hand out. */
  is_occupied: boolean;
  created_at: string;
  updated_at: string;
}

/** One level of a rack in a bulk-generate request. */
export interface RackLevelSpec {
  level: string;
  positions: number;
  requires_pallet_jack?: boolean;
}

export interface GenerateRackRequest {
  rack: number;
  levels: RackLevelSpec[];
  owning_group?: number | null;
  notes?: string;
}

/**
 * Bulk-generate report. `created`/`skipped` are codes: re-running a rack
 * after adding a level reports the pre-existing slots as skipped rather
 * than failing, and `without_tag` is non-empty only when the tag family
 * ran dry mid-run (the slots work, they just have no marker yet).
 */
export interface GenerateRackResult {
  rack: number;
  created: string[];
  skipped: string[];
  created_count: number;
  skipped_count: number;
  without_tag: string[];
  slots: StorageSlot[];
}

/** Single-card preview payload (base64 PDF + what the card encodes). */
export interface StorageSlotCardPreview {
  slot_id: number;
  code: string;
  filename: string;
  content_type: string;
  preview: string;
  kiosk_url: string;
  april_tag_id: number | null;
}

// ---------------------------------------------------------------------------
// Storage assignments (the C/L/E half of occupancy) + the overview grid
// ---------------------------------------------------------------------------

/**
 * The letter a slot paints in the overview: P for a member's project stint,
 * C/L/E for the three staff-assigned kinds. "E" is class because C is
 * already committee's — the grid has one character per cell to work with.
 */
export type StorageTypeLetter = 'P' | 'C' | 'L' | 'E';

/** The three non-Project storage kinds. Staff-assigned, long-term. */
export type StorageAssignmentType = 'committee' | 'logistics' | 'class';

/**
 * The live C/L/E holding of a slot, as StorageSlotSerializer nests it —
 * enough to say who has the slot and to release it, without the audit and
 * notes fields the full assignment carries.
 */
export interface StorageSlotAssignmentSummary {
  id: number;
  storage_type: StorageAssignmentType;
  type_letter: StorageTypeLetter;
  occupant_display: string;
  assigned_at: string;
}

/**
 * One committee/logistics/class holding, full shape.
 *
 * Unlike a member's stint this has no clock: no expiry, no violation
 * notice, no purgatory. It runs until staff release it, and
 * `released_at` (null while live) is what "active" means.
 */
export interface StorageAssignment {
  id: number;
  slot: number;
  slot_code: string;
  storage_type: StorageAssignmentType;
  storage_type_display: string;
  type_letter: StorageTypeLetter;
  /** The committee (an auth.Group / SIG), for committee assignments. */
  owning_group: number | null;
  owning_group_name: string;
  /** Free-text occupant, for logistics/class. */
  occupant_label: string;
  /** Group name if there is one, else the label — who to show in the grid. */
  occupant_display: string;
  assigned_by: number | null;
  assigned_by_name: string;
  assigned_at: string;
  released_at: string | null;
  is_active: boolean;
  notes: string;
  created_at: string;
  updated_at: string;
}

/** Payload for POST /project-storage/assignments/assign/. */
export interface AssignSlotRequest {
  /** Slot pk or code — "1A1" and 12 are both accepted. */
  slot: string;
  storage_type: StorageAssignmentType;
  owning_group?: number | null;
  occupant_label?: string;
  notes?: string;
}

/**
 * One cell of the overview grid.
 *
 * `color` is set by the server and **only ever for type P** — a committee
 * slot has been the committee's for two years and will be tomorrow, so
 * painting it would drown out the one late member project the screen
 * exists to surface.
 */
export interface StorageOverviewCell {
  code: string;
  slot_id: number;
  position: number;
  /** null for an empty slot. */
  type: StorageTypeLetter | null;
  /** A stint's own status for P; 'occupied' for C/L/E; 'empty' otherwise. */
  status: ProjectStorageStatus | 'occupied' | 'empty';
  color: 'yellow' | 'red' | null;
  occupant: string;
  /** The slot's in-service flag — a retired slot is empty but not available. */
  is_active: boolean;
}

export interface StorageOverviewRow {
  level: string;
  /**
   * Dense and 1-indexed: entry *i* is position *i + 1*, and a hole in the
   * racking is null, so every row of a rack lines up without the renderer
   * re-deriving which columns exist.
   */
  cells: (StorageOverviewCell | null)[];
}

export interface StorageOverviewRack {
  rack: number;
  /** Descending — high shelf first, ground level last, like the steel. */
  levels: string[];
  max_position: number;
  rows: StorageOverviewRow[];
}

export interface StorageOverview {
  racks: StorageOverviewRack[];
  generated_at: string;
}

// ---------------------------------------------------------------------------
// Service status — GET /api/resilience/status/ (backend: resilience/services.py)
// ---------------------------------------------------------------------------

/**
 * Aggregate circuit-breaker state for one capability. `half_open` means the
 * dependency is on trial after an outage — still degraded, calls may fail.
 */
export type ServiceStatusState = 'closed' | 'half_open' | 'open';

/**
 * Stable machine keys, mirroring the backend SERVICE_REGISTRY. Call sites
 * switch on these to gate a control; a key the backend adds later is still
 * reported in the banner by its label, it just gates nothing until a call
 * site opts in.
 */
export type ServiceKey =
  | 'device_control'
  | 'webhooks'
  | 'whmcs'
  | 'common_api'
  | 'email';

export interface ServiceStatus {
  key: ServiceKey;
  /** User-facing service name, e.g. "Device control". */
  label: string;
  /** What the user loses while this is degraded. */
  description: string;
  state: ServiceStatusState;
  healthy: boolean;
  /** When the service entered its current state; null if it never transitioned. */
  since: string | null;
  /**
   * Error detail from that transition. Internal detail — never render this in
   * a member-facing surface; it exists for a staff status view only.
   */
  last_error: string | null;
  /** Member breakers currently open or half-open (families like webhooks). */
  degraded_count: number;
  /** Member breakers with a known state; 1 for a single-breaker service. */
  total_count: number;
}

export interface ResilienceStatus {
  degraded: boolean;
  checked_at: string;
  services: ServiceStatus[];
}

/**
 * One line of a kit's bill of materials (op-8n0).
 *
 * `id` is the KitComponent row's own identifier and survives edits — the kit
 * API upserts on `component` rather than delete-and-recreate — so the editor
 * can safely address rows by it.
 */
export interface KitComponent {
  id: number;
  component: string;
  component_name: string;
  component_sku: string;
  component_current_stock: number;
  component_needs_reorder: boolean;
  quantity: number;
  notes?: string;
}

/**
 * A kit SKU: an InventoryItem with `is_kit=true` plus its components.
 *
 * Shaped as InventoryItem because that is literally what the backend returns —
 * `KitSerializer` subclasses `InventoryItemSerializer`.
 */
export interface Kit extends InventoryItem {
  is_kit: true;
  components: KitComponent[];
  component_count: number;
}

/** Purchase terms written alongside a kit so it can be ordered in one request. */
export interface KitSupplierTerms {
  supplier: number;
  supplier_sku: string;
  unit_cost: string | number;
  supplier_url?: string;
  average_lead_time?: number;
}

/** Compact "this component comes in these kits" row. */
export interface KitSummary {
  id: string;
  name: string;
  sku: string;
  is_active: boolean;
  quantity_in_kit: number | null;
  supplier_name: string | null;
  supplier_sku: string | null;
  /**
   * A NUMBER, unlike every other price in this file (op-9m2v).
   *
   * `KitSummarySerializer.get_unit_cost` is a `SerializerMethodField` returning
   * a `Decimal`, which DRF's `JSONEncoder` renders as a JSON number rather than
   * the decimal string a plain `ModelSerializer` field would send. Declaring it
   * `string | null` is what made `{kit.unit_cost && ...}` read as safe here
   * while being genuinely safe on the string-valued twins.
   */
  unit_cost: number | null;
  component_count: number;
}

/**
 * What receiving a kit purchase-order line will credit, as rendered on the
 * line itself. `null` on ordinary item, asset and freeform lines.
 */
export interface KitLineComponent {
  component: string;
  component_name: string;
  component_sku: string;
  quantity_per_kit: number;
  quantity: number;
}
