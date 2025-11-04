# Frontend Asset Scan Implementation Guide

## Overview

Asset QR codes point to: `{FRONTEND_URL}/scan/asset/{assetId}`

The frontend should handle the scan with authentication-aware behavior:
- **Unauthenticated users**: Show basic info and redirect/link to wiki page
- **Authenticated users**: Show full asset details including maintenance plan

## Backend API

The asset data is available at: `GET /api/inventory/assets/{assetId}/`

Returns all fields including:
- `wiki_page_url` - Link to wiki page (for all users)
- `maintenance_plan` - Maintenance schedule (show only to authenticated users)

## Recommended Implementation

### 1. Add Route

Add to your React Router configuration:

```tsx
<Route path="/scan/asset/:assetId" element={<AssetScanPage />} />
```

### 2. Create AssetScanPage Component

```tsx
// frontend/src/pages/AssetScanPage.tsx
import React, { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { assetsAPI } from '../services/api';

interface Asset {
  id: string;
  name: string;
  description: string;
  serial_number: string;
  asset_tag: string;
  wiki_page_url: string;
  maintenance_plan: string;
  manufacturer_name: string;
  display_manufacturer: string;
  status: string;
  location_name: string;
  // ... other fields
}

const AssetScanPage: React.FC = () => {
  const { assetId } = useParams<{ assetId: string }>();
  const navigate = useNavigate();

  const [asset, setAsset] = useState<Asset | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Check if user is authenticated
  const isAuthenticated = !!localStorage.getItem('token');

  useEffect(() => {
    const loadAsset = async () => {
      try {
        setLoading(true);
        const response = await assetsAPI.getAsset(assetId!);
        const assetData = response.data;
        setAsset(assetData);

        // If not authenticated and wiki exists, redirect to wiki
        if (!isAuthenticated && assetData.wiki_page_url) {
          window.location.href = assetData.wiki_page_url;
          return;
        }

        setError(null);
      } catch (err: any) {
        setError(err.response?.data?.detail || 'Failed to load asset');
      } finally {
        setLoading(false);
      }
    };

    if (assetId) {
      loadAsset();
    }
  }, [assetId, isAuthenticated]);

  if (loading) {
    return <div className="loading">Loading asset...</div>;
  }

  if (error) {
    return <div className="error">Error: {error}</div>;
  }

  if (!asset) {
    return <div className="error">Asset not found</div>;
  }

  return (
    <div className="asset-scan-page">
      <div className="asset-header">
        <h1>{asset.name}</h1>
        <span className={`status-badge status-${asset.status}`}>
          {asset.status}
        </span>
      </div>

      <div className="asset-details">
        <div className="detail-row">
          <strong>Asset Tag:</strong> {asset.asset_tag}
        </div>

        {asset.serial_number && (
          <div className="detail-row">
            <strong>Serial Number:</strong> {asset.serial_number}
          </div>
        )}

        <div className="detail-row">
          <strong>Manufacturer:</strong> {asset.display_manufacturer}
        </div>

        {asset.location_name && (
          <div className="detail-row">
            <strong>Location:</strong> {asset.location_name}
          </div>
        )}

        <div className="detail-row">
          <strong>Description:</strong>
          <p>{asset.description}</p>
        </div>

        {/* Wiki Link - Show to all users */}
        {asset.wiki_page_url && (
          <div className="wiki-section">
            <a
              href={asset.wiki_page_url}
              target="_blank"
              rel="noopener noreferrer"
              className="wiki-link-button"
            >
              📚 View Documentation & Training
            </a>
          </div>
        )}

        {/* Maintenance Plan - Only for authenticated users */}
        {isAuthenticated && asset.maintenance_plan && (
          <div className="maintenance-section">
            <h2>Maintenance Plan</h2>
            <pre className="maintenance-plan">
              {asset.maintenance_plan}
            </pre>
          </div>
        )}

        {/* Prompt to login if not authenticated */}
        {!isAuthenticated && (
          <div className="login-prompt">
            <p>🔒 Log in to view maintenance schedule and full asset details</p>
            <button onClick={() => navigate('/login')}>
              Log In
            </button>
          </div>
        )}
      </div>

      <div className="asset-actions">
        {isAuthenticated && (
          <>
            <button onClick={() => navigate(`/admin/assets/${asset.id}`)}>
              Edit Asset
            </button>
            <button onClick={() => navigate('/admin')}>
              Back to Admin
            </button>
          </>
        )}
      </div>
    </div>
  );
};

export default AssetScanPage;
```

### 3. Add API Service Method

Add to `frontend/src/services/api.ts`:

```tsx
// Add to existing API services
export const assetsAPI = {
  getAsset: (id: string) => api.get(`/inventory/assets/${id}/`),
  listAssets: (params?: any) => api.get('/inventory/assets/', { params }),
  createAsset: (data: any) => api.post('/inventory/assets/', data),
  updateAsset: (id: string, data: any) => api.patch(`/inventory/assets/${id}/`, data),
  deleteAsset: (id: string) => api.delete(`/inventory/assets/${id}/`),
  generateQR: (id: string) => api.post(`/inventory/assets/${id}/generate_qr/`),
};
```

### 4. Add TypeScript Type

Add to `frontend/src/types.ts`:

```tsx
export interface Asset {
  id: string;
  name: string;
  description: string;
  serial_number: string;
  asset_tag: string;

  // Relationships
  inventory_item: string | null;
  inventory_item_name: string | null;
  category: string | null;
  category_name: string | null;
  location: string | null;
  location_name: string | null;

  // Manufacturer
  manufacturer: string | null;
  manufacturer_name: string;
  manufacturer_name_display: string | null;
  display_manufacturer: string;

  // Acquisition
  date_received: string | null;
  amount_paid: string;
  is_donation: boolean;
  donor_name: string;
  acquisition_display: string;
  age_in_days: number | null;

  // Product & Wiki
  product_url: string;
  wiki_page_url: string;

  // Maintenance
  maintenance_plan: string;

  // Media
  image: string | null;
  image_url: string | null;
  thumbnail_url: string | null;
  manual_pdf: string | null;
  manual_pdf_url: string | null;
  qr_code: string | null;
  qr_code_url: string | null;

  // Status
  status: 'active' | 'maintenance' | 'retired' | 'lost' | 'donated_out';
  condition_notes: string;

  // Metadata
  is_active: boolean;
  notes: string;
  created_at: string;
  updated_at: string;
}
```

## User Flow

### Unauthenticated User Scans QR Code:
1. QR code points to: `https://yoursite.com/scan/asset/{id}`
2. Frontend loads asset data from API
3. If `wiki_page_url` exists → redirect to wiki
4. If no wiki → show basic asset info + login prompt

### Authenticated User Scans QR Code:
1. QR code points to: `https://yoursite.com/scan/asset/{id}`
2. Frontend loads asset data from API
3. Show full asset details including:
   - All basic info (name, serial, manufacturer, location)
   - Wiki link (if available)
   - **Maintenance plan** (with nice formatting)
   - Action buttons (edit, view in admin, etc.)

## Styling Suggestions

```css
/* AssetScanPage.css */
.asset-scan-page {
  max-width: 800px;
  margin: 0 auto;
  padding: 2rem;
}

.asset-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 2rem;
  border-bottom: 2px solid #e0e0e0;
  padding-bottom: 1rem;
}

.status-badge {
  padding: 0.5rem 1rem;
  border-radius: 4px;
  font-weight: bold;
  color: white;
}

.status-active { background-color: #28a745; }
.status-maintenance { background-color: #ffc107; color: #000; }
.status-retired { background-color: #6c757d; }
.status-lost { background-color: #dc3545; }
.status-donated_out { background-color: #17a2b8; }

.wiki-section {
  margin: 2rem 0;
}

.wiki-link-button {
  display: inline-block;
  padding: 1rem 2rem;
  background: #007bff;
  color: white;
  text-decoration: none;
  border-radius: 8px;
  font-size: 1.1rem;
  font-weight: bold;
  transition: background 0.3s;
}

.wiki-link-button:hover {
  background: #0056b3;
}

.maintenance-section {
  margin: 2rem 0;
  padding: 1.5rem;
  background: #f8f9fa;
  border-radius: 8px;
  border-left: 4px solid #007bff;
}

.maintenance-plan {
  white-space: pre-wrap;
  font-family: 'Courier New', monospace;
  background: white;
  padding: 1rem;
  border-radius: 4px;
  overflow-x: auto;
}

.login-prompt {
  margin: 2rem 0;
  padding: 1.5rem;
  background: #fff3cd;
  border: 1px solid #ffc107;
  border-radius: 8px;
  text-align: center;
}

.login-prompt button {
  margin-top: 1rem;
  padding: 0.75rem 2rem;
  background: #007bff;
  color: white;
  border: none;
  border-radius: 4px;
  font-size: 1rem;
  cursor: pointer;
}
```

## Summary

With this implementation:

✅ **Backend provides data** via REST API
✅ **Frontend handles UI logic** based on authentication
✅ **Clean separation of concerns**
✅ **Better user experience** with proper UI instead of redirects
✅ **Flexible** - easy to add features like "Report Issue", "Request Maintenance", etc.

The QR codes will now point to your frontend (`/scan/asset/{id}`), which provides a much better experience than backend redirects.
