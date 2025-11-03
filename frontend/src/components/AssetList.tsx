/**
 * Asset List Component
 * Displays all hard assets with search and status filtering
 */
import React, { useEffect, useState } from 'react';
import { assetsAPI } from '../services/api';
import { Asset } from '../types';
import '../styles/AssetList.css';

const AssetList: React.FC = () => {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState('');
  const [statusFilter, setStatusFilter] = useState<'all' | 'active' | 'maintenance' | 'retired'>('all');

  useEffect(() => {
    console.log('AssetList: Component mounted, loading assets...');
    loadAssets();
  }, []);

  const loadAssets = async () => {
    try {
      console.log('AssetList: Making API call to /api/inventory/assets/');
      setLoading(true);
      const response = await assetsAPI.listAssets();
      console.log('AssetList: API response received:', response.data);
      setAssets(response.data.results);
    } catch (err) {
      console.error('AssetList: Error loading assets:', err);
    } finally {
      setLoading(false);
    }
  };

  const filteredAssets = assets.filter((asset) => {
    // Apply search filter
    const matchesSearch =
      asset.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      (asset.serial_number && asset.serial_number.toLowerCase().includes(searchTerm.toLowerCase())) ||
      (asset.asset_tag && asset.asset_tag.toLowerCase().includes(searchTerm.toLowerCase())) ||
      (asset.inventory_item_name && asset.inventory_item_name.toLowerCase().includes(searchTerm.toLowerCase()));

    // Apply status filter
    const matchesStatus =
      statusFilter === 'all' || asset.status === statusFilter;

    return matchesSearch && matchesStatus;
  });

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

  if (loading) {
    return <div className="loading">Loading assets...</div>;
  }

  return (
    <div className="asset-list-container">
      <div className="asset-header">
        <h2>Makerspace Assets ({assets.length} total)</h2>
        <div className="asset-stats">
          {statusCounts.maintenance > 0 && (
            <span className="stat-badge maintenance">
              {statusCounts.maintenance} in maintenance
            </span>
          )}
        </div>
      </div>

      <div className="asset-controls">
        <input
          type="text"
          placeholder="Search by name, serial number, or asset tag..."
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
          className="search-input"
        />

        <div className="filter-buttons">
          <button
            className={statusFilter === 'all' ? 'active' : ''}
            onClick={() => setStatusFilter('all')}
          >
            All ({statusCounts.all})
          </button>
          <button
            className={statusFilter === 'active' ? 'active' : ''}
            onClick={() => setStatusFilter('active')}
          >
            Active ({statusCounts.active})
          </button>
          <button
            className={statusFilter === 'maintenance' ? 'active' : ''}
            onClick={() => setStatusFilter('maintenance')}
          >
            Maintenance ({statusCounts.maintenance})
          </button>
          <button
            className={statusFilter === 'retired' ? 'active' : ''}
            onClick={() => setStatusFilter('retired')}
          >
            Retired ({statusCounts.retired})
          </button>
        </div>
      </div>

      {filteredAssets.length === 0 ? (
        <div className="no-results">
          {searchTerm ? 'No assets match your search.' : 'No assets found.'}
        </div>
      ) : (
        <div className="asset-grid">
          {filteredAssets.map((asset) => (
            <div key={asset.id} className={`asset-card ${getStatusBadgeClass(asset.status)}`}>
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
          ))}
        </div>
      )}
    </div>
  );
};

export default AssetList;
