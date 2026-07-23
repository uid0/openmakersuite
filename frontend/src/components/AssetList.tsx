/**
 * Asset List Component
 * Displays all hard assets with search and filtering (status, location, SIG).
 * Supports Card and Table views, both lazily loaded via infinite scroll with
 * server-side filtering and sorting.
 */
import { Alert, Button, Group, Loader, Paper, Select, Text } from '@mantine/core';
import { IconLayoutGrid, IconTable } from '@tabler/icons-react';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useSwipeable } from 'react-swipeable';
import { assetsAPI, inventoryAPI, sigAPI } from '../services/api';
import '../styles/AssetList.css';
import { Asset, Category, Location, SIG } from '../types';
import { extractErrorMessage } from '../utils/extractErrorMessage';
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

const SEARCH_DEBOUNCE_MS = 300;

const AssetList: React.FC = () => {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [assets, setAssets] = useState<Asset[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [locationFilter, setLocationFilter] = useState<number | null>(null);
  const [sigFilter, setSigFilter] = useState<number | null>(null);
  const [inventoryItemFilter, setInventoryItemFilter] = useState<string | null>(null);
  const [locations, setLocations] = useState<Location[]>([]);
  const [sigs, setSigs] = useState<SIG[]>([]);
  // Bulk cost-recovery flag by category (staff only). Flagging the landlord's
  // HVAC equipment one asset at a time is not workable, so the whole category
  // goes in a single request.
  const [categories, setCategories] = useState<Category[]>([]);
  const [bulkCategoryId, setBulkCategoryId] = useState<string | null>(null);
  const [bulkSaving, setBulkSaving] = useState<boolean>(false);
  const [bulkResult, setBulkResult] = useState<string | null>(null);
  const [bulkError, setBulkError] = useState<string | null>(null);

  // View mode and infinite-scroll pagination state.
  const [viewMode, setViewMode] = useState<'card' | 'table'>(getStoredViewMode());
  const [totalCount, setTotalCount] = useState<number>(0);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
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

  // Debounce the free-text search so each keystroke doesn't hit the API.
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(searchTerm), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [searchTerm]);

  const loadInitialData = async () => {
    try {
      const [locationsRes, sigsRes, categoriesRes] = await Promise.all([
        inventoryAPI.listLocations(),
        sigAPI.listMySIGs(),
        inventoryAPI.listCategories(),
      ]);
      setLocations((locationsRes.data.results || []) as Location[]);
      setSigs((sigsRes.data.results || []) as SIG[]);
      setCategories((categoriesRes?.data?.results || []) as Category[]);
    } catch (err) {
      console.error('Error loading initial data:', err);
    }
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
      'age_in_days': 'date_received', // Age is calculated from date_received
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

    // Special handling for age_in_days: older assets (higher age) = earlier
    // date_received, so ascending age = descending date_received.
    if (sortField === 'age_in_days') {
      return sortDirection === 'asc' ? `-${fieldWithoutMinus}` : fieldWithoutMinus;
    }

    return sortDirection === 'desc' ? `-${fieldWithoutMinus}` : fieldWithoutMinus;
  };

  const buildAssetParams = (targetPage: number) => {
    const params: Parameters<typeof assetsAPI.listAssets>[0] = { page: targetPage };
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
    if (debouncedSearch.trim()) {
      params.search = debouncedSearch.trim();
    }
    const backendSortField = getBackendSortField();
    if (backendSortField) {
      params.ordering = backendSortField;
    }
    return params;
  };

  // (Re)load the first page whenever the filters, search, or sort change.
  useEffect(() => {
    const loadFirstPage = async () => {
      try {
        setLoading(true);
        const response = await assetsAPI.listAssets(buildAssetParams(1));
        const data = response.data;
        setAssets(data.results);
        setTotalCount(data.count ?? data.results.length);
        setHasMore(Boolean(data.next));
        setPage(1);
      } catch (err) {
        console.error('AssetList: Error loading assets:', err);
      } finally {
        setLoading(false);
      }
    };
    loadFirstPage();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearch, statusFilter, locationFilter, sigFilter, inventoryItemFilter, sortField, sortDirection]);

  const loadMore = async () => {
    if (loadingMore || loading || !hasMore) {
      return;
    }
    try {
      setLoadingMore(true);
      const nextPage = page + 1;
      const response = await assetsAPI.listAssets(buildAssetParams(nextPage));
      const data = response.data;
      setAssets((prev) => [...prev, ...data.results]);
      setTotalCount(data.count ?? totalCount);
      setHasMore(Boolean(data.next));
      setPage(nextPage);
    } catch (err) {
      console.error('AssetList: Error loading more assets:', err);
    } finally {
      setLoadingMore(false);
    }
  };

  // Keep the latest loadMore reachable from the (stable) observer callback.
  const loadMoreRef = useRef(loadMore);
  loadMoreRef.current = loadMore;

  // Callback ref: attach an IntersectionObserver as soon as the sentinel
  // mounts and tear it down when it unmounts.
  const observerRef = useRef<IntersectionObserver | null>(null);
  const sentinelRef = useCallback((node: HTMLDivElement | null) => {
    if (observerRef.current) {
      observerRef.current.disconnect();
      observerRef.current = null;
    }
    if (node && typeof IntersectionObserver !== 'undefined') {
      observerRef.current = new IntersectionObserver(
        (entries) => {
          if (entries[0]?.isIntersecting) {
            loadMoreRef.current();
          }
        },
        { rootMargin: '200px' }
      );
      observerRef.current.observe(node);
    }
  }, []);

  // Loaded-page maintenance tally (approximate until the full list scrolls in).
  const maintenanceCount = assets.filter((a) => a.status === 'maintenance').length;

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
    navigate(`/assets/${assetId}?action=report`);
  };

  const handleSort = (field: string) => {
    if (sortField === field) {
      setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortDirection('asc');
    }
    // The first-page effect resets pagination when sort changes.
  };

  // Fetch every asset matching the current filters (used by CSV export).
  const handleExportAllAssets = async (): Promise<Asset[]> => {
    const all: Asset[] = [];
    let targetPage = 1;
    for (;;) {
      const response = await assetsAPI.listAssets(buildAssetParams(targetPage));
      all.push(...response.data.results);
      if (!response.data.next) {
        break;
      }
      targetPage += 1;
    }
    return all;
  };

  const hasActiveFilters = Boolean(
    searchTerm || locationFilter || sigFilter || statusFilter !== 'all' || inventoryItemFilter
  );

  // Staff-only: the flag decides what in-house work gets billed to the landlord.
  const isStaff =
    localStorage.getItem('is_staff') === 'true' ||
    localStorage.getItem('is_superuser') === 'true';

  const handleBulkCostRecoverable = async (value: boolean) => {
    if (!bulkCategoryId) return;
    try {
      setBulkSaving(true);
      setBulkError(null);
      setBulkResult(null);
      const response = await assetsAPI.setCostRecoverableByCategory(Number(bulkCategoryId), value);
      const { updated, matched, category_name: categoryName } = response.data;
      setBulkResult(
        `${value ? 'Flagged' : 'Cleared'} ${updated} of ${matched} asset${matched === 1 ? '' : 's'} in ${categoryName}.`
      );
    } catch (err) {
      setBulkError(extractErrorMessage(err, 'Failed to update cost recovery for that category.'));
    } finally {
      setBulkSaving(false);
    }
  };

  return (
    <div className="asset-list-container">
      <div className="asset-header">
        <h2>Makerspace Assets ({totalCount} total)</h2>
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
            {maintenanceCount > 0 && (
              <span className="stat-badge maintenance">
                {maintenanceCount} in maintenance
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
        {isStaff && (
          <Paper withBorder p="sm" mt="sm" data-testid="bulk-cost-recoverable">
            <Text size="sm" fw={500} mb={4}>
              Cost recovery by category
            </Text>
            <Text size="xs" c="dimmed" mb="xs">
              Flag every asset in a category as landlord-billable in one go — in-house
              repair cost on flagged assets then counts toward the recoverable total on
              the cost-recovery statement.
            </Text>
            <Group align="flex-end" gap="sm" wrap="wrap">
              <Select
                label="Category"
                placeholder="Pick a category"
                data={categories.map((c) => ({ value: String(c.id), label: c.name }))}
                value={bulkCategoryId}
                onChange={setBulkCategoryId}
                searchable
                clearable
                data-testid="bulk-cost-recoverable-category"
                style={{ minWidth: 220 }}
              />
              <Button
                onClick={() => handleBulkCostRecoverable(true)}
                disabled={!bulkCategoryId || bulkSaving}
                loading={bulkSaving}
                data-testid="bulk-cost-recoverable-set"
              >
                Mark cost-recoverable
              </Button>
              <Button
                variant="default"
                onClick={() => handleBulkCostRecoverable(false)}
                disabled={!bulkCategoryId || bulkSaving}
                data-testid="bulk-cost-recoverable-clear"
              >
                Clear flag
              </Button>
            </Group>
            {bulkResult && (
              <Text size="sm" mt="xs" data-testid="bulk-cost-recoverable-result">
                {bulkResult}
              </Text>
            )}
            {bulkError && (
              <Alert color="red" mt="xs" data-testid="bulk-cost-recoverable-error">
                {bulkError}
              </Alert>
            )}
          </Paper>
        )}
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
          assets={assets}
          loading={loading}
          totalCount={totalCount}
          sortField={sortField}
          sortDirection={sortDirection}
          onSort={handleSort}
          onExport={handleExportAllAssets}
        />
      ) : loading && assets.length === 0 ? (
        <div className="no-results">Loading assets…</div>
      ) : assets.length === 0 ? (
        <div className="no-results">
          {hasActiveFilters ? 'No assets match your filters.' : 'No assets found.'}
        </div>
      ) : (
        <div className="asset-grid">
          {assets.map((asset) => (
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

      {/* Shared infinite-scroll sentinel for both card and table views. */}
      {assets.length > 0 && (
        <div ref={sentinelRef} data-testid="asset-scroll-sentinel" style={{ padding: '1rem', textAlign: 'center' }}>
          {loadingMore && (
            <Group justify="center" gap="xs">
              <Loader size="sm" />
              <Text size="sm" c="dimmed">
                Loading more…
              </Text>
            </Group>
          )}
          {!hasMore && (
            <Text size="sm" c="dimmed">
              All {totalCount} asset{totalCount === 1 ? '' : 's'} loaded.
            </Text>
          )}
        </div>
      )}
    </div>
  );
};

export default AssetList;
