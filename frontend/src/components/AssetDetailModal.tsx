/**
 * Asset Detail Modal Component
 * Displays full details about an asset including cost, maintenance, and other information
 */
import React, { useCallback, useEffect, useState } from 'react';
import { assetsAPI } from '../services/api';
import { Asset } from '../types';
import '../styles/AssetDetailModal.css';
import { extractErrorMessage } from '../utils/extractErrorMessage';

interface AssetDetailModalProps {
  assetId: string | null;
  isOpen: boolean;
  onClose: () => void;
}

const AssetDetailModal: React.FC<AssetDetailModalProps> = ({ assetId, isOpen, onClose }) => {
  const [asset, setAsset] = useState<Asset | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadAssetDetails = useCallback(async () => {
    if (!assetId) return;

    try {
      setLoading(true);
      setError(null);
      const response = await assetsAPI.getAsset(assetId);
      setAsset(response.data);
    } catch (err: any) {
      console.error('Error loading asset details:', err);
      setError(extractErrorMessage(err, 'Failed to load asset details'));
    } finally {
      setLoading(false);
    }
  }, [assetId]);

  useEffect(() => {
    if (isOpen && assetId) {
      loadAssetDetails();
    } else {
      setAsset(null);
      setError(null);
    }
  }, [isOpen, assetId, loadAssetDetails]);

  const formatDate = (dateString: string | null | undefined): string => {
    if (!dateString) return 'N/A';
    try {
      return new Date(dateString).toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'long',
        day: 'numeric',
      });
    } catch {
      return dateString;
    }
  };

  const formatCurrency = (amount: string | null | undefined): string => {
    if (!amount) return 'N/A';
    const numAmount = parseFloat(amount);
    if (isNaN(numAmount)) return amount;
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
    }).format(numAmount);
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

  if (!isOpen) return null;

  return (
    <div className="asset-modal-overlay" onClick={onClose}>
      <div className="asset-modal-content" onClick={(e) => e.stopPropagation()}>
        <div className="asset-modal-header">
          <h2>Asset Details</h2>
          <button className="asset-modal-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        <div className="asset-modal-body">
          {loading && (
            <div className="asset-modal-loading">Loading asset details...</div>
          )}

          {error && (
            <div className="asset-modal-error">
              <p>Error: {error}</p>
              <button onClick={loadAssetDetails}>Retry</button>
            </div>
          )}

          {!loading && !error && asset && (
            <>
              {/* Asset Image */}
              {asset.image_url && (
                <div className="asset-modal-image">
                  <img src={asset.image_url} alt={asset.name} />
                </div>
              )}

              {/* Basic Information */}
              <section className="asset-modal-section">
                <h3>{asset.name}</h3>
                {asset.description && <p className="asset-modal-description">{asset.description}</p>}

                <div className="asset-modal-badges">
                  <span className={`status-badge ${getStatusBadgeClass(asset.status)}`}>
                    {getStatusLabel(asset.status)}
                  </span>
                  {asset.category_name && (
                    <span className="asset-category-badge">{asset.category_name}</span>
                  )}
                </div>
              </section>

              {/* Cost Information */}
              <section className="asset-modal-section">
                <h4>Cost Information</h4>
                <div className="asset-modal-info-grid">
                  <div className="info-item">
                    <span className="info-label">Amount Paid:</span>
                    <span className="info-value">{formatCurrency(asset.amount_paid)}</span>
                  </div>
                  {asset.is_donation && (
                    <div className="info-item">
                      <span className="info-label">Donation:</span>
                      <span className="info-value">Yes{asset.donor_name ? ` (${asset.donor_name})` : ''}</span>
                    </div>
                  )}
                  {asset.date_received && (
                    <div className="info-item">
                      <span className="info-label">Date Received:</span>
                      <span className="info-value">{formatDate(asset.date_received)}</span>
                    </div>
                  )}
                  {asset.age_in_days !== undefined && (
                    <div className="info-item">
                      <span className="info-label">Age:</span>
                      <span className="info-value">
                        {Math.floor(asset.age_in_days / 365)} years, {asset.age_in_days % 365} days
                      </span>
                    </div>
                  )}
                </div>
              </section>

              {/* Maintenance Information */}
              <section className="asset-modal-section">
                <h4>Maintenance</h4>
                {asset.maintenance_plan ? (
                  <div className="maintenance-plan">
                    <strong>Maintenance Plan:</strong>
                    <pre className="maintenance-plan-text">{asset.maintenance_plan}</pre>
                  </div>
                ) : (
                  <p className="no-maintenance-plan">No maintenance plan available.</p>
                )}

                {/* Maintenance History from Parts */}
                {asset.parts && asset.parts.length > 0 && (
                  <div className="maintenance-history">
                    <strong>Parts Maintenance History:</strong>
                    <ul className="maintenance-list">
                      {asset.parts.map((part) => (
                        <li key={part.id} className="maintenance-item">
                          <div className="maintenance-item-header">
                            <span className="maintenance-part-name">{part.part_name}</span>
                            {part.needs_replacement && (
                              <span className="needs-replacement-badge">Needs Replacement</span>
                            )}
                          </div>
                          {part.last_replaced_at && (
                            <div className="maintenance-item-details">
                              <span>Last Replaced: {formatDate(part.last_replaced_at)}</span>
                              {part.days_since_replacement !== undefined && (
                                <span className="days-since">
                                  ({part.days_since_replacement} days ago)
                                </span>
                              )}
                            </div>
                          )}
                          {part.maintenance_interval_days && (
                            <div className="maintenance-interval">
                              Maintenance Interval: Every {part.maintenance_interval_days} days
                            </div>
                          )}
                          {part.notes && (
                            <div className="maintenance-notes">{part.notes}</div>
                          )}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {asset.condition_notes && (
                  <div className="condition-notes">
                    <strong>Condition Notes:</strong>
                    <p>{asset.condition_notes}</p>
                  </div>
                )}
              </section>

              {/* Asset Details */}
              <section className="asset-modal-section">
                <h4>Asset Details</h4>
                <div className="asset-modal-info-grid">
                  {asset.asset_tag && (
                    <div className="info-item">
                      <span className="info-label">Asset Tag:</span>
                      <span className="info-value">{asset.asset_tag}</span>
                    </div>
                  )}
                  {asset.serial_number && (
                    <div className="info-item">
                      <span className="info-label">Serial Number:</span>
                      <span className="info-value">{asset.serial_number}</span>
                    </div>
                  )}
                  {asset.inventory_item_name && (
                    <div className="info-item">
                      <span className="info-label">Type:</span>
                      <span className="info-value">{asset.inventory_item_name}</span>
                    </div>
                  )}
                  {asset.display_manufacturer && (
                    <div className="info-item">
                      <span className="info-label">Manufacturer:</span>
                      <span className="info-value">{asset.display_manufacturer}</span>
                    </div>
                  )}
                  {asset.location_name && (
                    <div className="info-item">
                      <span className="info-label">Location:</span>
                      <span className="info-value">{asset.location_name}</span>
                    </div>
                  )}
                  {asset.last_scanned_at && (
                    <div className="info-item">
                      <span className="info-label">Last Scanned:</span>
                      <span className="info-value">{formatDate(asset.last_scanned_at)}</span>
                    </div>
                  )}
                </div>
              </section>

              {/* Operational Requirements */}
              {(asset.circuit || asset.needs_compressed_air || asset.needs_ventilation || asset.is_chargeable) && (
                <section className="asset-modal-section">
                  <h4>Operational Requirements</h4>
                  <div className="asset-modal-info-grid">
                    {asset.circuit && (
                      <div className="info-item">
                        <span className="info-label">Circuit:</span>
                        <span className="info-value">{asset.circuit}</span>
                      </div>
                    )}
                    {asset.needs_compressed_air && (
                      <div className="info-item">
                        <span className="info-label">Needs Compressed Air:</span>
                        <span className="info-value">Yes</span>
                      </div>
                    )}
                    {asset.needs_ventilation && (
                      <div className="info-item">
                        <span className="info-label">Needs Ventilation:</span>
                        <span className="info-value">Yes</span>
                      </div>
                    )}
                    {asset.is_chargeable && (
                      <div className="info-item">
                        <span className="info-label">Chargeable:</span>
                        <span className="info-value">Yes</span>
                      </div>
                    )}
                  </div>
                </section>
              )}

              {/* Links */}
              {(asset.product_url || asset.wiki_page_url || asset.manual_pdf_url) && (
                <section className="asset-modal-section">
                  <h4>Links & Resources</h4>
                  <div className="asset-modal-links">
                    {asset.product_url && (
                      <a href={asset.product_url} target="_blank" rel="noopener noreferrer">
                        Product Page
                      </a>
                    )}
                    {asset.wiki_page_url && (
                      <a href={asset.wiki_page_url} target="_blank" rel="noopener noreferrer">
                        Wiki Page
                      </a>
                    )}
                    {asset.manual_pdf_url && (
                      <a href={asset.manual_pdf_url} target="_blank" rel="noopener noreferrer">
                        Manual (PDF)
                      </a>
                    )}
                  </div>
                </section>
              )}

              {/* Additional Notes */}
              {asset.notes && (
                <section className="asset-modal-section">
                  <h4>Additional Notes</h4>
                  <p className="asset-notes">{asset.notes}</p>
                </section>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
};

export default AssetDetailModal;

