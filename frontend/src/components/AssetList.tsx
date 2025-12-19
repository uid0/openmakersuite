/**
 * Asset List Component
 * Displays all hard assets with search and filtering (status, location, SIG)
 */
import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useSwipeable } from 'react-swipeable';
import { assetsAPI, inventoryAPI, sigAPI } from '../services/api';
import '../styles/AssetList.css';
import { Asset, Location, SIG } from '../types';

const AssetList: React.FC = () => {
  const navigate = useNavigate();
  const [assets, setAssets] = useState<Asset[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [locationFilter, setLocationFilter] = useState<number | null>(null);
  const [sigFilter, setSigFilter] = useState<number | null>(null);
  const [locations, setLocations] = useState<Location[]>([]);
  const [sigs, setSigs] = useState<SIG[]>([]);

  useEffect(() => {
    loadInitialData();
    loadAssets();
  }, [statusFilter, locationFilter, sigFilter, searchTerm]);

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
      if (searchTerm) {
        params.search = searchTerm;
      }
      const response = await assetsAPI.listAssets(params);
      setAssets(response.data.results);
    } catch (err: any) {
      console.error('AssetList: Error loading assets:', err);
    } finally {
      setLoading(false);
    }
  };

  const statusCounts = {
    all: assets.length,
    active: assets.filter(a => a.status === 'active').length,
    maintenance: assets.filter(a => a.status === 'maintenance').length,
    retired: assets.filter(a => a.status === 'retired').length,
  };

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

  if (loading) {
    return <div className="loading">Loading assets...</div>;
  }

  return (
    <div className="asset-list-container">
      <div className="asset-header">
        <h2>Makerspace Assets ({assets.length} total)</h2>
        <div className="asset-header-actions">
          <button className="create-asset-button" onClick={handleCreateAsset}>
            + Create Asset
          </button>
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
      </div>

      {assets.length === 0 ? (
        <div className="no-results">
          {searchTerm || locationFilter || sigFilter || statusFilter !== 'all'
            ? 'No assets match your filters.'
            : 'No assets found.'}
        </div>
      ) : (
        <div className="asset-grid">
          {assets.map((asset) => {
            const swipeHandlers = useSwipeable({
              onSwipedLeft: () => handleSwipeLeft(asset.id),
              onSwipedRight: () => handleSwipeRight(asset.id),
              delta: 50, // Minimum swipe distance
              trackMouse: false,
            });

            return (
              <div
                key={asset.id}
                {...swipeHandlers}
                className={`asset-card ${getStatusBadgeClass(asset.status)}`}
                onClick={() => handleAssetClick(asset.id)}
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
          })}
        </div>
      )}
    </div>
  );
};

export default AssetList;
