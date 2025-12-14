/**
 * Asset QR Code Scan Page
 * - Unauthenticated users: Show basic info and QR code, update last_scanned_at
 * - Authenticated users: Show full info, enable/disable, report problem options
 */
import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { assetsAPI, assetPartsAPI, checklistsAPI } from '../services/api';
import '../styles/ScanPage.css';
import { Asset, AssetPart, Checklist } from '../types';

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
  const [assetParts, setAssetParts] = useState<AssetPart[]>([]);

  // Form state (authenticated users)
  const [problemDescription, setProblemDescription] = useState('');
  const [selectedPartsForRepair, setSelectedPartsForRepair] = useState<Set<string>>(new Set());
  const [repairDescription, setRepairDescription] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [actionSuccess, setActionSuccess] = useState<string | null>(null);

  const loadAsset = useCallback(async () => {
    try {
      setLoading(true);
      // Call scan endpoint which updates last_scanned_at and returns asset data
      const assetResponse = await assetsAPI.scanAsset(assetId!);
      const assetData = assetResponse.data;
      setAsset(assetData);
      // Use parts from asset response if available, otherwise load separately
      if (assetData.parts && assetData.parts.length > 0) {
        setAssetParts(assetData.parts);
      } else {
        // Fallback: load parts separately if not in response
        try {
          const partsResponse = await assetPartsAPI.listAssetParts({ asset: assetId! });
          setAssetParts(partsResponse.data.results || []);
        } catch (err: any) {
          // Silently fail - parts are optional
          console.error('Error loading asset parts:', err);
        }
      }
      setError(null);
    } catch (err: any) {
      setError(err.response?.data?.detail || err.response?.data?.error || 'Failed to load asset');
      console.error('Error loading asset:', err);
    } finally {
      setLoading(false);
    }
  }, [assetId]);

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

  const loadAssetParts = useCallback(async () => {
    if (!assetId) return;
    try {
      const partsResponse = await assetPartsAPI.listAssetParts({ asset: assetId });
      setAssetParts(partsResponse.data.results || []);
    } catch (err: any) {
      // Silently fail - parts are optional
      console.error('Error loading asset parts:', err);
    }
  }, [assetId]);

  useEffect(() => {
    if (assetId) {
      loadAsset();
      loadChecklists();
    }
  }, [assetId, loadAsset, loadChecklists]);

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

  const handlePartRepairToggle = (partId: string) => {
    setSelectedPartsForRepair((prev) => {
      const newSet = new Set(prev);
      if (newSet.has(partId)) {
        newSet.delete(partId);
      } else {
        newSet.add(partId);
      }
      return newSet;
    });
  };

  const handleRequestRepair = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!asset || selectedPartsForRepair.size === 0) {
      alert('Please select at least one part that needs repair');
      return;
    }

    try {
      setSubmitting(true);
      
      // Build description with selected parts
      const selectedParts = assetParts.filter((part) => selectedPartsForRepair.has(part.id));
      let description = 'Repair request for the following parts:\n\n';
      selectedParts.forEach((part) => {
        description += `- ${part.part_name}${part.part_sku ? ` (SKU: ${part.part_sku})` : ''}`;
        if (part.quantity_needed > 1) {
          description += ` - Quantity: ${part.quantity_needed}`;
        }
        description += '\n';
        if (part.notes) {
          description += `  Notes: ${part.notes}\n`;
        }
      });
      
      if (repairDescription.trim()) {
        description += `\nAdditional details:\n${repairDescription}`;
      }

      await assetsAPI.reportProblem(asset.id, description);
      setSelectedPartsForRepair(new Set());
      setRepairDescription('');
      setActionSuccess('Repair request submitted successfully');
      setTimeout(() => setActionSuccess(null), 3000);
    } catch (err: any) {
      alert(err.response?.data?.detail || 'Failed to submit repair request');
      console.error('Error submitting repair request:', err);
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

        {/* Asset Parts Repair Request Section - Available to all users */}
        {assetParts.length > 0 && (
          <div className="parts-repair-section" style={{ marginTop: '20px', padding: '15px', border: '1px solid #ddd', borderRadius: '5px', backgroundColor: '#f9f9f9' }}>
            <h3>🔧 Request Part Repair</h3>
            <p>Select the parts that need repair:</p>
            <form onSubmit={handleRequestRepair} style={{ marginTop: '15px' }}>
              <div style={{ marginBottom: '15px' }}>
                {assetParts.map((part) => (
                  <label
                    key={part.id}
                    style={{
                      display: 'flex',
                      alignItems: 'flex-start',
                      padding: '10px',
                      marginBottom: '8px',
                      backgroundColor: selectedPartsForRepair.has(part.id) ? '#e3f2fd' : 'white',
                      border: '1px solid #ddd',
                      borderRadius: '4px',
                      cursor: 'pointer',
                      transition: 'background-color 0.2s',
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={selectedPartsForRepair.has(part.id)}
                      onChange={() => handlePartRepairToggle(part.id)}
                      disabled={submitting}
                      style={{ marginRight: '10px', marginTop: '4px', cursor: 'pointer' }}
                    />
                    <div style={{ flex: 1 }}>
                      <div style={{ fontWeight: 'bold', marginBottom: '4px' }}>{part.part_name}</div>
                      {part.part_sku && (
                        <div style={{ fontSize: '0.9em', color: '#666', marginBottom: '2px' }}>SKU: {part.part_sku}</div>
                      )}
                      {part.quantity_needed > 1 && (
                        <div style={{ fontSize: '0.9em', color: '#666', marginBottom: '2px' }}>
                          Quantity needed: {part.quantity_needed}
                        </div>
                      )}
                      <div style={{ marginTop: '4px' }}>
                        {part.is_required && (
                          <span style={{ fontSize: '0.85em', color: '#d32f2f', fontWeight: 'bold', marginRight: '10px' }}>
                            Required
                          </span>
                        )}
                        {part.needs_replacement && (
                          <span style={{ fontSize: '0.85em', color: '#f57c00', fontWeight: 'bold' }}>
                            ⚠️ Needs Replacement
                          </span>
                        )}
                      </div>
                      {part.notes && (
                        <div style={{ fontSize: '0.85em', color: '#666', marginTop: '4px', fontStyle: 'italic' }}>
                          {part.notes}
                        </div>
                      )}
                    </div>
                  </label>
                ))}
              </div>
              <div style={{ marginBottom: '15px' }}>
                <label style={{ display: 'block', marginBottom: '5px', fontWeight: 'bold' }}>
                  Additional Details (optional):
                </label>
                <textarea
                  value={repairDescription}
                  onChange={(e) => setRepairDescription(e.target.value)}
                  placeholder="Describe what needs to be repaired or any additional information..."
                  rows={3}
                  disabled={submitting}
                  style={{
                    width: '100%',
                    padding: '8px',
                    border: '1px solid #ddd',
                    borderRadius: '4px',
                    fontFamily: 'inherit',
                    resize: 'vertical',
                  }}
                />
              </div>
              <button
                type="submit"
                disabled={submitting || selectedPartsForRepair.size === 0}
                style={{
                  padding: '10px 20px',
                  backgroundColor: selectedPartsForRepair.size === 0 ? '#ccc' : '#28a745',
                  color: 'white',
                  border: 'none',
                  borderRadius: '4px',
                  cursor: selectedPartsForRepair.size === 0 ? 'not-allowed' : 'pointer',
                  fontWeight: 'bold',
                  fontSize: '1em',
                }}
              >
                {submitting ? 'Submitting...' : 'Request Repair'}
              </button>
            </form>
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
