/**
 * Quick actions for the command palette
 */

export interface QuickAction {
  id: string;
  label: string;
  description?: string;
  icon?: string;
  keywords: string[];
  action: () => void;
  category: 'create' | 'navigate';
}

/**
 * Get quick actions for the command palette
 */
export function getQuickActions(navigate: (path: string) => void): QuickAction[] {
  return [
    // Create actions
    {
      id: 'create-inventory',
      label: 'Create Inventory Item',
      description: 'Add a new inventory item',
      keywords: ['create', 'new', 'add', 'inventory', 'item'],
      category: 'create',
      action: () => navigate('/inventory/items/new'),
    },
    {
      id: 'create-asset',
      label: 'Create Asset',
      description: 'Add a new asset',
      keywords: ['create', 'new', 'add', 'asset'],
      category: 'create',
      action: () => navigate('/assets/new'),
    },
    {
      id: 'create-supplier',
      label: 'Create Supplier',
      description: 'Add a new supplier',
      keywords: ['create', 'new', 'add', 'supplier', 'vendor'],
      category: 'create',
      action: () => navigate('/inventory/suppliers/new'),
    },
    {
      id: 'create-location',
      label: 'Create Location',
      description: 'Add a new location',
      keywords: ['create', 'new', 'add', 'location'],
      category: 'create',
      action: () => navigate('/inventory/locations/new'),
    },
    {
      id: 'create-po',
      label: 'Create Purchase Order',
      description: 'Create a new purchase order',
      keywords: ['create', 'new', 'add', 'purchase', 'order', 'po'],
      category: 'create',
      action: () => navigate('/purchasing/orders'),
    },
    // Navigation actions
    {
      id: 'nav-inventory',
      label: 'Go to Inventory',
      description: 'View all inventory items',
      keywords: ['inventory', 'items', 'stock', 'go', 'navigate'],
      category: 'navigate',
      action: () => navigate('/inventory/items'),
    },
    {
      id: 'nav-assets',
      label: 'Go to Assets',
      description: 'View all assets',
      keywords: ['assets', 'equipment', 'go', 'navigate'],
      category: 'navigate',
      action: () => navigate('/assets'),
    },
    {
      id: 'nav-suppliers',
      label: 'Go to Suppliers',
      description: 'View all suppliers',
      keywords: ['suppliers', 'vendors', 'go', 'navigate'],
      category: 'navigate',
      action: () => navigate('/inventory/suppliers'),
    },
    {
      id: 'nav-locations',
      label: 'Go to Locations',
      description: 'View all locations',
      keywords: ['locations', 'places', 'go', 'navigate'],
      category: 'navigate',
      action: () => navigate('/inventory/locations'),
    },
    {
      id: 'nav-pos',
      label: 'Go to Purchase Orders',
      description: 'View all purchase orders',
      keywords: ['purchase', 'orders', 'pos', 'go', 'navigate'],
      category: 'navigate',
      action: () => navigate('/purchasing/orders'),
    },
    {
      id: 'nav-admin',
      label: 'Go to Admin Dashboard',
      description: 'View admin dashboard',
      keywords: ['admin', 'dashboard', 'go', 'navigate'],
      category: 'navigate',
      action: () => navigate('/inventory/admin'),
    },
    {
      id: 'nav-home',
      label: 'Go to Home',
      description: 'Return to home page',
      keywords: ['home', 'main', 'go', 'navigate'],
      category: 'navigate',
      action: () => navigate('/'),
    },
  ];
}

/**
 * Filter quick actions by query
 */
export function filterQuickActions(actions: QuickAction[], query: string): QuickAction[] {
  if (!query.trim()) {
    return actions;
  }

  const lowerQuery = query.toLowerCase();
  return actions.filter((action) => {
    // Check if query matches label, description, or keywords
    const matchesLabel = action.label.toLowerCase().includes(lowerQuery);
    const matchesDescription = action.description?.toLowerCase().includes(lowerQuery);
    const matchesKeywords = action.keywords.some((keyword) => keyword.toLowerCase().includes(lowerQuery));

    return matchesLabel || matchesDescription || matchesKeywords;
  });
}
