/**
 * Asset QR Code Scan Page
 * - Unauthenticated users: Show basic info and QR code, update last_scanned_at
 * - Authenticated users: Show full info, enable/disable, report problem options
 */
import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { assetsAPI, checklistsAPI } from '../services/api';
import '../styles/ScanPage.css';
import { Asset, Checklist } from '../types';

const AssetScanPage: React.FC = () => {
  const { assetId } = useParams<{ assetId: string }>();
  const navigate = useNavigate();

  // Authentication state
  const [isLoggedIn] = useState<boolean>(() => !!localStorage.getItem('token'));

  // Data state
  const [asset, setAsset] = useState<Asset | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [checklists, setChecklists] = useState<Checklist[]>([]);

  // Form state (authenticated users)
  const [problemDescription, setProblemDescription] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [actionSuccess, setActionSuccess] = useState<string | null>(null);

  const loadAsset = useCallback(async () => {
    try {
      setLoading(true);
      // Call scan endpoint which updates last_scanned_at and returns asset data
      const assetResponse = await assetsAPI.scanAsset(assetId!);
      setAsset(assetResponse.data);
      setError(null);
    } catch (err: any) {
      setError(err.response?.data?.detail || err.response?.data?.error || 'Failed to load asset');
      console.error('Error loading asset:', err);
    } finally {
      setLoading(false);
    }
  }, [assetId]);

  useEffect(() => {
    if (assetId) {
      loadAsset();
      loadChecklists();
    }
  }, [assetId, loadAsset, loadChecklists]);

  const loadChecklists = useCallback(async () => {
    if (!assetId) return;
    try {
      const checklistsResponse = await assetsAPI.getAssetChecklists(assetId);
      setChecklists(checklistsResponse.data);
    } catch (err: any) {
      // Silently fail - checklists are optional
      console.error('Error loading checklists:', err);
    }
  }, [assetId]);

  const handleStartChecklist = async (checklistId: string) => {
    try {
      const userName = prompt('Enter your name (optional):') || '';
      const completion = await checklistsAPI.startChecklist(checklistId, userName || undefined);
      navigate(`/checklist/${checklistId}/complete/${completion.data.id}`);
    } catch (err: any) {
      alert(err.response?.data?.detail || 'Failed to start checklist');
    }
  };

  const handleEnable = async () => {
    if (!asset || !isLoggedIn) return;

    try {
      setSubmitting(true);
      await assetsAPI.enableAsset(asset.id);
      await loadAsset(); // Reload to get updated data
      setActionSuccess('Asset enabled successfully');
      setTimeout(() => setActionSuccess(null), 3000);
    } catch (err: any) {
      alert(err.response?.data?.detail || 'Failed to enable asset');
      console.error('Error enabling asset:', err);
    } finally {
      setSubmitting(false);
    }
  };

  const handleDisable = async () => {
    if (!asset || !isLoggedIn) return;

    if (!window.confirm('Are you sure you want to disable this asset?')) {
      return;
    }

    try {
      setSubmitting(true);
      await assetsAPI.disableAsset(asset.id);
      await loadAsset(); // Reload to get updated data
      setActionSuccess('Asset disabled successfully');
      setTimeout(() => setActionSuccess(null), 3000);
    } catch (err: any) {
      alert(err.response?.data?.detail || 'Failed to disable asset');
      console.error('Error disabling asset:', err);
    } finally {
      setSubmitting(false);
    }
  };

  const handleLock = async () => {
    if (!asset || !isLoggedIn) return;

    if (!window.confirm('Are you sure you want to lock this asset? Non-admins will not be able to use it.')) {
      return;
    }

    try {
      setSubmitting(true);
      await assetsAPI.lockAsset(asset.id);
      await loadAsset(); // Reload to get updated data
      setActionSuccess('Asset locked successfully');
      setTimeout(() => setActionSuccess(null), 3000);
    } catch (err: any) {
      alert(err.response?.data?.error || err.response?.data?.detail || 'Failed to lock asset');
      console.error('Error locking asset:', err);
    } finally {
      setSubmitting(false);
    }
  };

  const handleUnlock = async () => {
    if (!asset || !isLoggedIn) return;

    if (!window.confirm('Are you sure you want to unlock this asset?')) {
      return;
    }

    try {
      setSubmitting(true);
      await assetsAPI.unlockAsset(asset.id);
      await loadAsset(); // Reload to get updated data
      setActionSuccess('Asset unlocked successfully');
      setTimeout(() => setActionSuccess(null), 3000);
    } catch (err: any) {
      alert(err.response?.data?.error || err.response?.data?.detail || 'Failed to unlock asset');
      console.error('Error unlocking asset:', err);
    } finally {
      setSubmitting(false);
    }
  };

  const handleReportProblem = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!asset || !isLoggedIn || !problemDescription.trim()) {
      alert('Please provide a description of the problem');
      return;
    }

    try {
      setSubmitting(true);
      await assetsAPI.reportProblem(asset.id, problemDescription);
      setProblemDescription('');
      setActionSuccess('Problem reported successfully');
      setTimeout(() => setActionSuccess(null), 3000);
    } catch (err: any) {
      alert(err.response?.data?.detail || 'Failed to report problem');
      console.error('Error reporting problem:', err);
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <div className="scan-page">
        <div className="loading">Loading asset details...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="scan-page">
        <div className="error">
          <h2>Error</h2>
          <p>{error}</p>
          <button onClick={() => navigate('/')}>Go Home</button>
        </div>
      </div>
    );
  }

  if (!asset) {
    return (
      <div className="scan-page">
        <div className="error">
          <h2>Asset not found</h2>
          <button onClick={() => navigate('/')}>Go Home</button>
        </div>
      </div>
    );
  }

  return (
    <div className="scan-page">
      <div className="item-card">
        <div className="item-header">
          {asset.image_url && (
            <img src={asset.image_url} alt={asset.name} className="item-image" />
          )}
          <div className="item-title-section">
            <h1>{asset.name}</h1>
            {asset.asset_tag && <p className="sku">Tag: {asset.asset_tag}</p>}
            {asset.serial_number && <p className="sku">Serial: {asset.serial_number}</p>}
          </div>
        </div>

        {/* QR Code Display */}
        {asset.qr_code_url && (
          <div className="qr-code-section">
            <h3>QR Code</h3>
            <img src={asset.qr_code_url} alt="QR Code" className="qr-code-image" />
            <p className="qr-info">Last scanned: {asset.last_scanned_at ? new Date(asset.last_scanned_at).toLocaleString() : 'Never'}</p>
          </div>
        )}

        {/* Checklists Section */}
        {checklists.length > 0 && (
          <div className="checklists-section" style={{ marginTop: '20px', padding: '15px', border: '1px solid #ddd', borderRadius: '5px' }}>
            <h3>Are you completing a checklist?</h3>
            <p>This asset is part of the following checklists:</p>
            <ul style={{ listStyle: 'none', padding: 0 }}>
              {checklists.map((checklist) => (
                <li key={checklist.id} style={{ marginBottom: '10px' }}>
                  <button
                    onClick={() => handleStartChecklist(checklist.id)}
                    style={{
                      width: '100%',
                      padding: '10px',
                      backgroundColor: '#007bff',
                      color: 'white',
                      border: 'none',
                      borderRadius: '4px',
                      cursor: 'pointer',
                    }}
                  >
                    {checklist.name}
                    {checklist.step_count && ` (${checklist.step_count} steps)`}
                  </button>
                  {checklist.description && (
                    <p style={{ marginTop: '5px', fontSize: '0.9em', color: '#666' }}>{checklist.description}</p>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="item-details">
          {asset.description && <p className="description">{asset.description}</p>}

          <div className="info-grid">
            {asset.location_name && (
              <div className="info-item">
                <span className="label">Location:</span>
                <span className="value">{asset.location_name}</span>
              </div>
            )}

            {asset.category_name && (
              <div className="info-item">
                <span className="label">Category:</span>
                <span className="value">{asset.category_name}</span>
              </div>
            )}

            {asset.display_manufacturer && (
              <div className="info-item">
                <span className="label">Manufacturer:</span>
                <span className="value">{asset.display_manufacturer}</span>
              </div>
            )}

            {!asset.report_only && (
              <>
                <div className="info-item">
                  <span className="label">Status:</span>
                  <span className={`value status-${asset.status}`}>{asset.status}</span>
                </div>

                <div className="info-item">
                  <span className="label">Operational Status:</span>
                  <span className={`value operational-status-${asset.operational_status}`}>
                    {asset.operational_status === 'available' && '✓ Available'}
                    {asset.operational_status === 'reserved' && '🔒 Reserved'}
                    {asset.operational_status === 'needs_maintenance' && '⚠️ Needs Maintenance'}
                    {asset.operational_status === 'disabled' && '❌ Disabled'}
                  </span>
                </div>

                {asset.is_locked && asset.lockout_info && (
                  <div className="info-item">
                    <span className="label">Lock Status:</span>
                    <span className="value status-locked">
                      🔒 Locked by {asset.lockout_info.locked_by || 'Unknown'} ({asset.lockout_info.lockout_level})
                      {asset.lockout_info.locked_at && ` on ${new Date(asset.lockout_info.locked_at).toLocaleString()}`}
                    </span>
                  </div>
                )}
              </>
            )}

            {asset.owning_user_name && (
              <div className="info-item">
                <span className="label">Owner:</span>
                <span className="value">{asset.owning_user_name}</span>
              </div>
            )}

            {asset.owning_group_name && (
              <div className="info-item">
                <span className="label">Owned by Group:</span>
                <span className="value">{asset.owning_group_name}</span>
              </div>
            )}

            {asset.circuit && (
              <div className="info-item">
                <span className="label">Circuit:</span>
                <span className="value">{asset.circuit}</span>
              </div>
            )}

            {(asset.needs_compressed_air || asset.needs_ventilation || asset.is_chargeable) && (
              <div className="info-item">
                <span className="label">Requirements:</span>
                <span className="value">
                  {asset.needs_compressed_air && 'Compressed Air '}
                  {asset.needs_ventilation && 'Ventilation '}
                  {asset.is_chargeable && 'Chargeable'}
                </span>
              </div>
            )}
          </div>

          {asset.wiki_page_url && (
            <div className="wiki-link">
              <a href={asset.wiki_page_url} target="_blank" rel="noopener noreferrer">
                📖 View Wiki Page
              </a>
            </div>
          )}

          {/* Authenticated users see full details */}
          {isLoggedIn && (
            <>
              {!asset.report_only && (
                <>
                  {asset.maintenance_plan && (
                    <div className="maintenance-section">
                      <h3>Maintenance Plan</h3>
                      <p className="maintenance-text">{asset.maintenance_plan}</p>
                    </div>
                  )}

                  {asset.condition_notes && (
                    <div className="condition-section">
                      <h3>Condition Notes</h3>
                      <p>{asset.condition_notes}</p>
                    </div>
                  )}

                  {asset.product_url && (
                    <div className="product-link">
                      <a href={asset.product_url} target="_blank" rel="noopener noreferrer">
                        🔗 Product Page
                      </a>
                    </div>
                  )}
                </>
              )}

              {actionSuccess && (
                <div className="alert alert-success">
                  <strong>✓ Success</strong>
                  <p>{actionSuccess}</p>
                </div>
              )}

              <div className="asset-actions">
                <h3>Actions</h3>
                <div className="action-buttons">
                  {/* Only show enable/disable buttons if not report_only and user has permission */}
                  {!asset.report_only && asset.can_enable && (
                    <>
                      {asset.is_active ? (
                        <button
                          onClick={handleDisable}
                          className="btn-disable"
                          disabled={submitting}
                        >
                          Disable Asset
                        </button>
                      ) : (
                        <button
                          onClick={handleEnable}
                          className="btn-enable"
                          disabled={submitting}
                        >
                          Enable Asset
                        </button>
                      )}
                    </>
                  )}

                  {/* Only show lock/unlock buttons if not report_only and user has permission */}
                  {!asset.report_only && asset.can_unlock && (
                    <>
                      {asset.is_locked ? (
                        <button
                          onClick={handleUnlock}
                          className="btn-unlock"
                          disabled={submitting}
                        >
                          Unlock Asset
                        </button>
                      ) : (
                        <button
                          onClick={handleLock}
                          className="btn-lock"
                          disabled={submitting}
                        >
                          Lock Asset
                        </button>
                      )}
                    </>
                  )}

                  {/* Report a Problem form - always available for authenticated users */}
                  <form onSubmit={handleReportProblem} className="problem-form">
                    <h4>Report a Problem</h4>
                    <textarea
                      value={problemDescription}
                      onChange={(e) => setProblemDescription(e.target.value)}
                      placeholder="Describe the problem with this asset..."
                      rows={4}
                      required
                      disabled={submitting}
                    />
                    <button
                      type="submit"
                      className="btn-report"
                      disabled={submitting || !problemDescription.trim()}
                    >
                      {submitting ? 'Submitting...' : 'Report Problem'}
                    </button>
                  </form>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
};

export default AssetScanPage;
