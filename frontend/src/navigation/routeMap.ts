/**
 * Single source of truth for breadcrumb labels and click-target routability.
 *
 * Each entry maps a URL path (without leading slash) to:
 *   - label: the human-readable name shown in breadcrumbs and (later) the
 *     homepage capability cards.
 *   - clickable: whether breadcrumb segments matching this path are
 *     rendered as a <Link>. Set false when the path has no route handler
 *     and clicking would dead-end. The current page is always rendered as
 *     a non-clickable <span> regardless of this flag (see Breadcrumbs).
 *
 * Adding a new top-level workspace? Add an entry here AND register a
 * route in App.tsx. The entryPoints test in
 * __tests__/navigation/entryPoints.test.tsx asserts that every clickable
 * route map entry mounts a non-error page.
 */

export interface RouteEntry {
  /** Path without leading slash, e.g. "inventory/assets". */
  path: string;
  /** Human-readable label shown in breadcrumbs. */
  label: string;
  /**
   * Whether this path is a clickable navigation target. Set false for
   * structural URL segments that have no route handler (e.g. parents
   * intentionally left as namespacing-only). Default is true.
   */
  clickable?: boolean;
}

const ENTRIES: RouteEntry[] = [
  // Top-level workspaces — every entry below resolves to a real page.
  { path: 'inventory', label: 'Inventory' },
  { path: 'inventory/assets', label: 'Assets' },
  { path: 'inventory/admin', label: 'Admin Dashboard' },
  { path: 'inventory/code-entry', label: 'Code Entry' },
  { path: 'inventory/transparency', label: 'Transparency' },
  { path: 'inventory/scan', label: 'Scan Items' },
  { path: 'inventory/items', label: 'Items' },
  { path: 'inventory/suppliers', label: 'Suppliers' },
  { path: 'inventory/categories', label: 'Categories' },
  { path: 'inventory/locations', label: 'Locations' },

  { path: 'dashboard', label: 'Dashboard' },

  { path: 'purchasing', label: 'Purchasing' },
  { path: 'purchasing/orders', label: 'Purchase Orders' },

  { path: 'assets', label: 'Assets' },

  { path: 'facilities', label: 'Facilities' },
  { path: 'facilities/tv-dashboard', label: 'TV Dashboard' },
  { path: 'facilities/logistics', label: 'Logistics' },
  { path: 'facilities/checklist', label: 'Checklist' },
  { path: 'facilities/screens', label: 'Screens' },
  { path: 'facilities/maker-boxes', label: 'Maker Boxes' },
  { path: 'facilities/electrical', label: 'Electrical' },
  // Power-topology visualization ([6/7]). Resource leaves are not their
  // own listing pages — they're surfaced via the panel detail view — so
  // their breadcrumb segments are non-clickable.
  { path: 'facilities/electrical/panels', label: 'Panels' },
  { path: 'facilities/electrical/breakers', label: 'Breakers', clickable: false },
  { path: 'facilities/electrical/circuits', label: 'Circuits', clickable: false },
  { path: 'facilities/electrical/power-chain', label: 'Power Chain' },
  // Legacy fixture inventory (oms-a5f). Kept under the same /electrical
  // root so existing bookmarks don't break; sub-resources point back to
  // the tabbed listing rather than dead-ending.
  { path: 'facilities/electrical/fixtures', label: 'Fixtures' },
  { path: 'facilities/electrical/outlets', label: 'Outlets', clickable: false },
  { path: 'facilities/electrical/light-switches', label: 'Light Switches', clickable: false },
  { path: 'facilities/electrical/network-drops', label: 'Network Drops', clickable: false },
  { path: 'facilities/forgekey-devices', label: 'ForgeKey Devices' },

  { path: 'maintenance', label: 'Maintenance' },
  { path: 'maintenance/dashboard', label: 'PM Dashboard' },
  { path: 'maintenance/work-orders', label: 'Work Orders', clickable: false },
  { path: 'maintenance/location-problems', label: 'Location Problems' },
  { path: 'maintenance/third-party', label: 'Third-Party Work Orders', clickable: false },

  { path: 'sigs', label: 'SIGs' },
  { path: 'sigs/dashboard', label: 'SIG Dashboard' },

  { path: 'reports', label: 'Reports' },
  { path: 'reports/inventory', label: 'Inventory Report' },
  { path: 'reports/purchasing', label: 'Purchasing Report' },
  { path: 'reports/assets', label: 'Asset Report' },

  { path: 'analytics', label: 'Analytics Pulse' },

  { path: 'settings', label: 'Settings' },
  { path: 'settings/profile', label: 'Profile' },
  { path: 'settings/site', label: 'Site' },
  { path: 'settings/webhooks', label: 'Webhooks' },
  { path: 'settings/tax-receipt', label: 'Tax Receipt', clickable: false },
  { path: 'settings/tax-receipt/lookup', label: 'Tax Receipt Lookup' },
];

const BY_PATH: Map<string, RouteEntry> = new Map(ENTRIES.map((e) => [e.path, e]));

/** Returns the route entry for an exact path key, or undefined. */
export function getRouteEntry(pathKey: string): RouteEntry | undefined {
  return BY_PATH.get(pathKey);
}

/**
 * Title-cases a slug-style segment as a fallback when the path is not in
 * the map. Drops the trailing/leading slash. ``"work-orders" → "Work Orders"``.
 */
export function titleCaseSegment(segment: string): string {
  return segment
    .split('-')
    .map((word) => (word ? word[0].toUpperCase() + word.slice(1) : ''))
    .join(' ');
}

/**
 * Returns the label for a path segment, preferring the route map. Used by
 * breadcrumbs to avoid raw slug formatting where a canonical label exists.
 */
export function labelFor(pathKey: string, fallbackSegment: string): string {
  const entry = BY_PATH.get(pathKey);
  return entry ? entry.label : titleCaseSegment(fallbackSegment);
}

/**
 * Returns true when a breadcrumb segment for this path should be a
 * clickable <Link>. False when the path is a structural-only segment
 * (no route handler) and clicking would dead-end.
 */
export function isClickable(pathKey: string): boolean {
  const entry = BY_PATH.get(pathKey);
  if (!entry) return true;
  return entry.clickable !== false;
}

/** All registered entries — exposed for tests + the homepage in PR2. */
export const ROUTE_MAP_ENTRIES: ReadonlyArray<RouteEntry> = ENTRIES;
