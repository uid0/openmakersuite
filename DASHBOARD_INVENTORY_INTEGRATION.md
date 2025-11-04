# Dashboard Inventory Summary Integration

## Overview

The dashboard now has a comprehensive inventory summary endpoint that provides real-time stats for both inventory items and assets.

## API Endpoint

### GET `/api/dashboard/inventory-summary/`

**Authentication:** None required (public endpoint)

**Response:**
```json
{
  "inventory": {
    "total_items": 127,
    "low_stock_count": 8,
    "items_with_pending_reorders": 3,
    "total_value": 15234.50,
    "recently_added": 12,
    "low_stock_items": [
      {
        "id": "uuid-here",
        "name": "18V Lithium Battery",
        "current_stock": 2,
        "minimum_stock": 10,
        "reorder_quantity": 12
      },
      // ... up to 20 items
    ]
  },
  "assets": {
    "total_assets": 45,
    "by_status": {
      "active": 38,
      "maintenance": 3,
      "retired": 2,
      "lost": 1,
      "donated_out": 1
    },
    "needing_maintenance": 3
  },
  "timestamp": "2025-11-03T04:26:48.860759+00:00"
}
```

## Frontend Integration

### 1. Add API Service Method

Add to `frontend/src/services/api.ts`:

```typescript
export const dashboardAPI = {
  getInventorySummary: () => api.get('/dashboard/inventory-summary/'),
  getMessages: () => api.get('/dashboard/messages/'),
  getConfig: () => api.get('/dashboard/config/'),
};
```

### 2. Create Dashboard Stats Component

```tsx
// frontend/src/components/DashboardStats.tsx
import React, { useEffect, useState } from 'react';
import { dashboardAPI } from '../services/api';

interface InventorySummary {
  inventory: {
    total_items: number;
    low_stock_count: number;
    items_with_pending_reorders: number;
    total_value: number;
    recently_added: number;
    low_stock_items: Array<{
      id: string;
      name: string;
      current_stock: number;
      minimum_stock: number;
      reorder_quantity: number;
    }>;
  };
  assets: {
    total_assets: number;
    by_status: Record<string, number>;
    needing_maintenance: number;
  };
  timestamp: string;
}

export const DashboardStats: React.FC = () => {
  const [summary, setSummary] = useState<InventorySummary | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchSummary = async () => {
      try {
        const response = await dashboardAPI.getInventorySummary();
        setSummary(response.data);
      } catch (error) {
        console.error('Failed to load inventory summary:', error);
      } finally {
        setLoading(false);
      }
    };

    fetchSummary();

    // Auto-refresh every 30 seconds
    const interval = setInterval(fetchSummary, 30000);
    return () => clearInterval(interval);
  }, []);

  if (loading || !summary) {
    return <div className="loading">Loading inventory stats...</div>;
  }

  const { inventory, assets } = summary;

  return (
    <div className="dashboard-stats">
      <h2>Inventory Overview</h2>

      <div className="stats-grid">
        {/* Inventory Stats */}
        <div className="stat-card">
          <h3>Total Items</h3>
          <div className="stat-value">{inventory.total_items}</div>
          <div className="stat-label">Active inventory items</div>
        </div>

        <div className="stat-card alert">
          <h3>Low Stock</h3>
          <div className="stat-value">{inventory.low_stock_count}</div>
          <div className="stat-label">Items need reordering</div>
        </div>

        <div className="stat-card">
          <h3>Pending Reorders</h3>
          <div className="stat-value">{inventory.items_with_pending_reorders}</div>
          <div className="stat-label">Items being reordered</div>
        </div>

        <div className="stat-card">
          <h3>Total Value</h3>
          <div className="stat-value">${inventory.total_value.toLocaleString()}</div>
          <div className="stat-label">Current inventory value</div>
        </div>

        <div className="stat-card">
          <h3>Recently Added</h3>
          <div className="stat-value">{inventory.recently_added}</div>
          <div className="stat-label">New items (last 30 days)</div>
        </div>

        {/* Asset Stats */}
        <div className="stat-card">
          <h3>Total Assets</h3>
          <div className="stat-value">{assets.total_assets}</div>
          <div className="stat-label">Tracked assets</div>
        </div>

        <div className="stat-card">
          <h3>Active Assets</h3>
          <div className="stat-value">{assets.by_status.active || 0}</div>
          <div className="stat-label">In use</div>
        </div>

        {assets.needing_maintenance > 0 && (
          <div className="stat-card warning">
            <h3>Maintenance</h3>
            <div className="stat-value">{assets.needing_maintenance}</div>
            <div className="stat-label">Assets under maintenance</div>
          </div>
        )}
      </div>

      {/* Low Stock Items List */}
      {inventory.low_stock_items.length > 0 && (
        <div className="low-stock-section">
          <h3>Items Needing Reorder</h3>
          <table className="low-stock-table">
            <thead>
              <tr>
                <th>Item Name</th>
                <th>Current</th>
                <th>Minimum</th>
                <th>Reorder Qty</th>
              </tr>
            </thead>
            <tbody>
              {inventory.low_stock_items.map((item) => (
                <tr key={item.id}>
                  <td>{item.name}</td>
                  <td className="stock-low">{item.current_stock}</td>
                  <td>{item.minimum_stock}</td>
                  <td>{item.reorder_quantity}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};
```

### 3. Add to Existing Dashboard

Update your main dashboard page (likely `HomePage.tsx` or `AdminDashboard.tsx`):

```tsx
import { DashboardStats } from '../components/DashboardStats';

// In your dashboard component:
<div className="dashboard-container">
  <h1>Makerspace Dashboard</h1>

  {/* Add the inventory stats component */}
  <DashboardStats />

  {/* ... rest of your dashboard content */}
</div>
```

### 4. Styling

```css
/* DashboardStats.css */
.dashboard-stats {
  padding: 2rem;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 1.5rem;
  margin-bottom: 2rem;
}

.stat-card {
  background: white;
  padding: 1.5rem;
  border-radius: 8px;
  box-shadow: 0 2px 4px rgba(0,0,0,0.1);
  border-left: 4px solid #007bff;
}

.stat-card.alert {
  border-left-color: #dc3545;
}

.stat-card.warning {
  border-left-color: #ffc107;
}

.stat-card h3 {
  margin: 0 0 0.5rem 0;
  font-size: 0.875rem;
  color: #666;
  text-transform: uppercase;
}

.stat-value {
  font-size: 2rem;
  font-weight: bold;
  color: #333;
  margin-bottom: 0.25rem;
}

.stat-label {
  font-size: 0.875rem;
  color: #999;
}

.low-stock-section {
  margin-top: 2rem;
  background: white;
  padding: 1.5rem;
  border-radius: 8px;
  box-shadow: 0 2px 4px rgba(0,0,0,0.1);
}

.low-stock-section h3 {
  margin-top: 0;
  margin-bottom: 1rem;
  color: #dc3545;
}

.low-stock-table {
  width: 100%;
  border-collapse: collapse;
}

.low-stock-table th {
  text-align: left;
  padding: 0.75rem;
  border-bottom: 2px solid #e0e0e0;
  font-weight: 600;
  color: #666;
}

.low-stock-table td {
  padding: 0.75rem;
  border-bottom: 1px solid #f0f0f0;
}

.stock-low {
  color: #dc3545;
  font-weight: bold;
}

@media (max-width: 768px) {
  .stats-grid {
    grid-template-columns: 1fr;
  }
}
```

## Features

### Inventory Metrics
- **Total Items**: Count of all active inventory items
- **Low Stock Count**: Items at or below minimum stock level
- **Pending Reorders**: Items with active reorder requests
- **Total Value**: Sum of all inventory based on lowest unit cost
- **Recently Added**: Items added in the last 30 days
- **Low Stock List**: Top 20 items needing reorder (with details)

### Asset Metrics
- **Total Assets**: Count of all active assets
- **By Status**: Breakdown of assets by status (active, maintenance, retired, etc.)
- **Needing Maintenance**: Count of assets currently under maintenance

## Use Cases

### 1. Main Dashboard
Show inventory health at a glance with key metrics

### 2. TV Dashboard
Display rotating stats for public viewing

### 3. Admin Overview
Quick snapshot of inventory status before diving into details

### 4. Mobile App
Lightweight stats for mobile dashboard

## Real-Time Updates

The endpoint is fast and can be polled frequently:
- Auto-refresh every 30 seconds for live dashboards
- On-demand refresh when user navigates to dashboard
- Use as health check for inventory system

## Example Queries

```bash
# Get current inventory status
curl http://localhost:8000/api/dashboard/inventory-summary/

# Use in scripts
#!/bin/bash
LOW_STOCK=$(curl -s http://localhost:8000/api/dashboard/inventory-summary/ | jq '.inventory.low_stock_count')
if [ "$LOW_STOCK" -gt 10 ]; then
  echo "Warning: $LOW_STOCK items need reordering!"
fi
```

## Next Steps

1. Add the `DashboardStats` component to your frontend
2. Style it to match your theme
3. Consider adding charts/graphs for trends
4. Add click-through to view detailed item lists
5. Add notifications when low stock count increases

The endpoint is now live and ready to use!
