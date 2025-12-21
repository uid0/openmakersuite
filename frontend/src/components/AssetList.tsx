/**
 * Asset List Component
 * Displays all hard assets with search and filtering (status, location, SIG)
 * Supports both Card view and Table view with smart client/server-side mode switching
 */
import { Button, Group } from '@mantine/core';
import { IconLayoutGrid, IconTable } from '@tabler/icons-react';
import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useSwipeable } from 'react-swipeable';
import { assetsAPI, inventoryAPI, sigAPI } from '../services/api';
import '../styles/AssetList.css';
import { Asset, Location, SIG } from '../types';
import AssetTableView from './AssetTableView';

interface AssetCardProps {
  asset: Asset;
  onClick: (assetId: string) => void;
  onSwipeLeft: (assetId: string) => void;
  onSwipeRight: (assetId: string) => void;
  getStatusBadgeClass: (status: string) => string;
  getStatusLabel: (status: string) => string;
}

const AssetCard: React.FC<AssetCardProps> = ({
  asset,
  onClick,
  onSwipeLeft,
  onSwipeRight,
  getStatusBadgeClass,
  getStatusLabel,
}) => {
  const swipeHandlers = useSwipeable({
    onSwipedLeft: () => onSwipeLeft(asset.id),
    onSwipedRight: () => onSwipeRight(asset.id),
    delta: 50,
    trackMouse: false,
  });

  return (
    <div
      {...swipeHandlers}
      className={`asset-card ${getStatusBadgeClass(asset.status)}`}
      onClick={() => onClick(asset.id)}
    >
      {asset.image_url && (
        <div className="asset-image">
          <img src={asset.thumbnail_url || asset.image_url} alt={asset.name} />
        </div>
      )}

      <div className="asset-details">
        <h3 className="asset-name">{asset.name}</h3>

        {asset.inventory_item_name && (
          <p className="asset-type">Type: {asset.inventory_item_name}</p>
        )}

        <div className="asset-meta">
          {asset.asset_tag && (
            <p className="asset-tag">Tag: {asset.asset_tag}</p>
          )}
          {asset.serial_number && (
            <p className="asset-serial">S/N: {asset.serial_number}</p>
          )}
        </div>

        <span className={`status-badge ${getStatusBadgeClass(asset.status)}`}>
          {getStatusLabel(asset.status)}
        </span>

        {asset.category_name && (
          <span className="asset-category">{asset.category_name}</span>
        )}

        <div className="asset-info">
          {asset.display_manufacturer && (
            <div className="info-row">
              <span className="info-label">Manufacturer:</span>
              <span className="info-value">{asset.display_manufacturer}</span>
            </div>
          )}

          {asset.acquisition_display && (
            <div className="info-row">
              <span className="info-label">Acquired:</span>
              <span className="info-value">{asset.acquisition_display}</span>
            </div>
          )}

          {asset.location_name && (
            <div className="info-row">
              <span className="info-label">Location:</span>
              <span className="info-value">📍 {asset.location_name}</span>
            </div>
          )}

          {asset.age_in_days !== undefined && (
            <div className="info-row">
              <span className="info-label">Age:</span>
              <span className="info-value">{Math.floor(asset.age_in_days / 365)} years</span>
            </div>
          )}
        </div>

        {asset.maintenance_plan && (
          <div className="maintenance-badge">
            🔧 Has maintenance plan
          </div>
        )}

        {asset.wiki_page_url && (
          <div className="wiki-badge">
            📖 Wiki available
          </div>
        )}
      </div>
    </div>
  );
};

const CLIENT_SIDE_THRESHOLD = 250;
const ASSET_VIEW_MODE_KEY = 'assetViewMode';

// Helper function to get view mode from localStorage
const getStoredViewMode = (): 'card' | 'table' => {
  try {
    const stored = localStorage.getItem(ASSET_VIEW_MODE_KEY);
    if (stored === 'card' || stored === 'table') {
      return stored;
    }
  } catch (error) {
    console.error('Error reading view mode from localStorage:', error);
  }
  return 'card'; // Default to card view
};

// Helper function to save view mode to localStorage
const saveViewMode = (mode: 'card' | 'table'): void => {
  try {
    localStorage.setItem(ASSET_VIEW_MODE_KEY, mode);
  } catch (error) {
    console.error('Error saving view mode to localStorage:', error);
  }
};

const AssetList: React.FC = () => {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [assets, setAssets] = useState<Asset[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [locationFilter, setLocationFilter] = useState<number | null>(null);
  const [sigFilter, setSigFilter] = useState<number | null>(null);
  const [inventoryItemFilter, setInventoryItemFilter] = useState<string | null>(null);
  const [locations, setLocations] = useState<Location[]>([]);
  const [sigs, setSigs] = useState<SIG[]>([]);
  
  // View mode and pagination state - initialize from localStorage
  const [viewMode, setViewMode] = useState<'card' | 'table'>(getStoredViewMode());
  const [serverMode, setServerMode] = useState(false);
  const [totalCount, setTotalCount] = useState<number>(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [sortField, setSortField] = useState<string | null>(null);
  const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('asc');

  useEffect(() => {
    // Check URL params for inventory_item filter
    const inventoryItemParam = searchParams.get('inventory_item');
    if (inventoryItemParam) {
      setInventoryItemFilter(inventoryItemParam);
    }
  }, [searchParams]);

  useEffect(() => {
    loadInitialData();
  }, []);

  // Reset to page 1 when filters or search change
  useEffect(() => {
    setCurrentPage(1);
  }, [statusFilter, locationFilter, sigFilter, inventoryItemFilter, searchTerm]);

  useEffect(() => {
    loadAssets();
  }, [statusFilter, locationFilter, sigFilter, inventoryItemFilter, searchTerm, currentPage, sortField, sortDirection]);

  const loadInitialData = async () => {
    try {
      const [locationsRes, sigsRes] = await Promise.all([
        inventoryAPI.listLocations(),
        sigAPI.listMySIGs(),
      ]);
      setLocations((locationsRes.data.results || []) as Location[]);
      setSigs((sigsRes.data.results || []) as SIG[]);
    } catch (err) {
      console.error('Error loading initial data:', err);
    }
  };

  const loadAssets = async () => {
    try {
      setLoading(true);
      const params: any = {};
      if (statusFilter !== 'all') {
        params.status = statusFilter;
      }
      if (locationFilter) {
        params.location = locationFilter;
      }
      if (sigFilter) {
        params.owning_group = sigFilter;
      }
      if (inventoryItemFilter) {
        params.inventory_item = inventoryItemFilter;
      }
      if (searchTerm) {
        params.search = searchTerm;
      }
      
      // Add sorting
      const backendSortField = getBackendSortField();
      if (backendSortField) {
        params.ordering = backendSortField;
      }
      
      // For initial load or server mode, use pagination
      if (serverMode || currentPage > 1) {
        params.page = currentPage;
      }
      
      const response = await assetsAPI.listAssets(params);
      const responseData = response.data;
      
      // Check if we should switch modes
      const count = responseData.count ?? responseData.results.length;
      setTotalCount(count);
      
      // Determine mode on first load or when filters change significantly
      const shouldUseServerMode = count >= CLIENT_SIDE_THRESHOLD;
      
      if (shouldUseServerMode && !serverMode) {
        // Switch to server mode
        setServerMode(true);
        setCurrentPage(1); // Reset to first page
        setAssets(responseData.results);
      } else if (!shouldUseServerMode && serverMode) {
        // Switch to client mode - fetch all pages
        setServerMode(false);
        setCurrentPage(1);
        const allAssets: Asset[] = [...responseData.results];
        let page = 2;
        while (responseData.next && page <= Math.ceil(count / 50)) {
          params.page = page;
          const nextResponse = await assetsAPI.listAssets(params);
          allAssets.push(...nextResponse.data.results);
          if (!nextResponse.data.next) break;
          page++;
        }
        setAssets(allAssets);
      } else if (serverMode) {
        // Server mode: just use current page results
        setAssets(responseData.results);
      } else {
        // Client-side mode: fetch all pages if needed
        if (count > responseData.results.length && responseData.next) {
          // Need to fetch all pages
          const allAssets: Asset[] = [...responseData.results];
          let page = 2;
          while (responseData.next && page <= Math.ceil(count / 50)) {
            params.page = page;
            const nextResponse = await assetsAPI.listAssets(params);
            allAssets.push(...nextResponse.data.results);
            if (!nextResponse.data.next) break;
            page++;
          }
          setAssets(allAssets);
        } else {
          setAssets(responseData.results);
        }
      }
    } catch (err: any) {
      console.error('AssetList: Error loading assets:', err);
    } finally {
      setLoading(false);
    }
  };

  // Client-side filtering for card view when in client mode
  const filteredAssets = useMemo(() => {
    if (serverMode || viewMode === 'table') {
      // Server-side filtering or table view handles its own display
      return assets;
    }
    // Client-side mode: apply filters locally (though they're already applied server-side)
    return assets;
  }, [assets, serverMode, viewMode]);

  const statusCounts = useMemo(() => {
    if (serverMode) {
      // In server mode, we can't calculate accurate counts without fetching all data
      // For now, just use the current page's counts
      return {
        all: totalCount,
        active: assets.filter(a => a.status === 'active').length,
        maintenance: assets.filter(a => a.status === 'maintenance').length,
        retired: assets.filter(a => a.status === 'retired').length,
      };
    }
    return {
      all: assets.length,
      active: assets.filter(a => a.status === 'active').length,
      maintenance: assets.filter(a => a.status === 'maintenance').length,
      retired: assets.filter(a => a.status === 'retired').length,
    };
  }, [assets, serverMode, totalCount]);

  const getStatusBadgeClass = (status: string) => {
    switch (status) {
      case 'active': return 'status-active';
      case 'maintenance': return 'status-maintenance';
      case 'retired': return 'status-retired';
      case 'lost': return 'status-lost';
      case 'donated_out': return 'status-donated';
      default: return '';
    }
  };

  const getStatusLabel = (status: string) => {
    switch (status) {
      case 'active': return 'Active';
      case 'maintenance': return 'Maintenance';
      case 'retired': return 'Retired';
      case 'lost': return 'Lost';
      case 'donated_out': return 'Donated';
      default: return status;
    }
  };

  const handleAssetClick = (assetId: string) => {
    navigate(`/assets/${assetId}`);
  };

  const handleCreateAsset = () => {
    navigate('/assets/new');
  };

  const handleSwipeLeft = (assetId: string) => {
    // Swipe left to view details
    handleAssetClick(assetId);
  };

  const handleSwipeRight = (assetId: string) => {
    // Swipe right to report issue or quick action
    // For now, navigate to asset detail page where user can report issues
    navigate(`/assets/${assetId}?action=report`);
  };

  const handleSort = (field: string) => {
    if (sortField === field) {
      setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortDirection('asc');
    }
    // Reset to first page when sorting changes
    setCurrentPage(1);
  };

  const handlePageChange = (page: number) => {
    setCurrentPage(page);
    // Scroll to top when page changes
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  // Map table sort fields to backend ordering fields
  const mapSortFieldToBackend = (field: string): string => {
    const fieldMap: Record<string, string> = {
      'name': 'name',
      'asset_tag': 'asset_tag',
      'serial_number': 'serial_number',
      'status': 'status',
      'location_name': 'location__name',
      'category_name': 'category__name',
      'display_manufacturer': 'manufacturer__name',
      'date_received': 'date_received',
      'age_in_days': 'date_received', // Age is calculated from date_received, we'll handle direction separately
      'inventory_item_name': 'inventory_item__name',
      'owning_group_name': 'owning_group__name',
      'operational_status': 'operational_status',
      'is_active': 'is_active',
    };
    return fieldMap[field] || field;
  };

  // Get the backend sort field for API calls
  const getBackendSortField = (): string | null => {
    if (!sortField) return null;
    const backendField = mapSortFieldToBackend(sortField);
    // Remove leading minus if present (we'll add it based on direction)
    const fieldWithoutMinus = backendField.startsWith('-') ? backendField.slice(1) : backendField;
    
    // Special handling for age_in_days: older assets (higher age) = earlier date_received
    // So ascending age = descending date_received, and vice versa
    if (sortField === 'age_in_days') {
      return sortDirection === 'asc' ? `-${fieldWithoutMinus}` : fieldWithoutMinus;
    }
    
    return sortDirection === 'desc' ? `-${fieldWithoutMinus}` : fieldWithoutMinus;
  };

  // Export function to fetch all assets for CSV export
  const handleExportAllAssets = async (): Promise<Asset[]> => {
    const params: any = {};
    if (statusFilter !== 'all') {
      params.status = statusFilter;
    }
    if (locationFilter) {
      params.location = locationFilter;
    }
    if (sigFilter) {
      params.owning_group = sigFilter;
    }
    if (inventoryItemFilter) {
      params.inventory_item = inventoryItemFilter;
    }
    if (searchTerm) {
      params.search = searchTerm;
    }
    
    // Add sorting
    const backendSortField = getBackendSortField();
    if (backendSortField) {
      params.ordering = backendSortField;
    }

    // Fetch all pages
    const allAssets: Asset[] = [];
    let page = 1;
    let hasMore = true;

    while (hasMore) {
      params.page = page;
      const response = await assetsAPI.listAssets(params);
      const responseData = response.data;
      allAssets.push(...responseData.results);
      
      if (!responseData.next || responseData.results.length === 0) {
        hasMore = false;
      } else {
        page++;
      }
    }

    return allAssets;
  };

  const displayCount = serverMode ? totalCount : assets.length;

  return (
    <div className="asset-list-container">
      <div className="asset-header">
        <h2>Makerspace Assets ({displayCount} total)</h2>
        <div className="asset-header-actions">
          <Group gap="md">
            <Group gap={0}>
              <Button
                variant={viewMode === 'card' ? 'filled' : 'default'}
                onClick={() => {
                  setViewMode('card');
                  saveViewMode('card');
                }}
                leftSection={<IconLayoutGrid size={16} />}
                style={{ borderTopRightRadius: 0, borderBottomRightRadius: 0 }}
              >
                Card View
              </Button>
              <Button
                variant={viewMode === 'table' ? 'filled' : 'default'}
                onClick={() => {
                  setViewMode('table');
                  saveViewMode('table');
                }}
                leftSection={<IconTable size={16} />}
                style={{ borderTopLeftRadius: 0, borderBottomLeftRadius: 0, marginLeft: -1 }}
              >
                Table View
              </Button>
            </Group>
            <Button onClick={handleCreateAsset}>
              + Create Asset
            </Button>
          </Group>
          <div className="asset-stats">
            {statusCounts.maintenance > 0 && (
              <span className="stat-badge maintenance">
                {statusCounts.maintenance} in maintenance
              </span>
            )}
          </div>
        </div>
      </div>

      <div className="asset-controls">
        <div className="search-section">
          <input
            type="text"
            placeholder="Search by name, serial number, or asset tag..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="search-input"
          />
        </div>

        <div className="filters-section">
          <div className="filter-group">
            <label>Status:</label>
            <div className="filter-buttons">
              <button
                className={statusFilter === 'all' ? 'active' : ''}
                onClick={() => setStatusFilter('all')}
              >
                All
              </button>
              <button
                className={statusFilter === 'active' ? 'active' : ''}
                onClick={() => setStatusFilter('active')}
              >
                Active
              </button>
              <button
                className={statusFilter === 'maintenance' ? 'active' : ''}
                onClick={() => setStatusFilter('maintenance')}
              >
                Maintenance
              </button>
              <button
                className={statusFilter === 'retired' ? 'active' : ''}
                onClick={() => setStatusFilter('retired')}
              >
                Retired
              </button>
            </div>
          </div>

          <div className="filter-group">
            <label>Location:</label>
            <select
              value={locationFilter || ''}
              onChange={(e) => setLocationFilter(e.target.value ? Number(e.target.value) : null)}
              className="filter-select"
            >
              <option value="">All Locations</option>
              {locations.map((loc) => (
                <option key={loc.id} value={loc.id}>
                  {loc.name}
                </option>
              ))}
            </select>
          </div>

          <div className="filter-group">
            <label>SIG:</label>
            <select
              value={sigFilter || ''}
              onChange={(e) => setSigFilter(e.target.value ? Number(e.target.value) : null)}
              className="filter-select"
            >
              <option value="">All SIGs</option>
              {sigs.map((sig) => (
                <option key={sig.id} value={sig.id}>
                  {sig.name}
                </option>
              ))}
            </select>
          </div>
        </div>
        {inventoryItemFilter && (
          <div style={{ marginTop: '0.5rem', padding: '0.5rem', backgroundColor: '#f0f0f0', borderRadius: '4px' }}>
            <span>Filtered by Inventory Item. </span>
            <button
              onClick={() => {
                setInventoryItemFilter(null);
                const newSearchParams = new URLSearchParams(searchParams);
                newSearchParams.delete('inventory_item');
                const newUrl = newSearchParams.toString() 
                  ? `/inventory/assets?${newSearchParams.toString()}`
                  : '/inventory/assets';
                navigate(newUrl, { replace: true });
              }}
              style={{
                background: 'none',
                border: 'none',
                color: '#0066cc',
                cursor: 'pointer',
                textDecoration: 'underline'
              }}
            >
              Clear filter
            </button>
          </div>
        )}
      </div>

      {viewMode === 'table' ? (
        <AssetTableView
          assets={filteredAssets}
          loading={loading}
          totalCount={totalCount}
          currentPage={currentPage}
          pageSize={50}
          onPageChange={handlePageChange}
          sortField={sortField}
          sortDirection={sortDirection}
          onSort={handleSort}
          serverMode={serverMode}
          onExport={handleExportAllAssets}
        />
      ) : (
        <>
          {filteredAssets.length === 0 ? (
            <div className="no-results">
              {searchTerm || locationFilter || sigFilter || statusFilter !== 'all'
                ? 'No assets match your filters.'
                : 'No assets found.'}
            </div>
          ) : (
            <div className="asset-grid">
              {filteredAssets.map((asset) => (
                <AssetCard
                  key={asset.id}
                  asset={asset}
                  onClick={handleAssetClick}
                  onSwipeLeft={handleSwipeLeft}
                  onSwipeRight={handleSwipeRight}
                  getStatusBadgeClass={getStatusBadgeClass}
                  getStatusLabel={getStatusLabel}
                />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
};

export default AssetList;
