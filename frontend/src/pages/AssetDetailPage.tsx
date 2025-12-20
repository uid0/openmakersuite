/**
 * Asset Detail Page
 * Full page view for asset details with part tracking, problem history, maintenance log, QR code, and lock/unlock controls
 */
import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { assetPartsAPI, assetsAPI } from '../services/api';
import '../styles/AssetDetailPage.css';
import { Asset, AssetProblem } from '../types';

const AssetDetailPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [asset, setAsset] = useState<Asset | null>(null);
  const [problems, setProblems] = useState<AssetProblem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [problemStatusFilter, setProblemStatusFilter] = useState<string>('all');
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [isLoggedIn, setIsLoggedIn] = useState<boolean>(false);
  const [showReportForm, setShowReportForm] = useState<boolean>(false);
  const [problemDescription, setProblemDescription] = useState<string>('');
  const [submittingProblem, setSubmittingProblem] = useState<boolean>(false);
  const [resolvingProblemId, setResolvingProblemId] = useState<string | null>(null);
  const [resolutionNotes, setResolutionNotes] = useState<string>('');
  const [resolvingStatus, setResolvingStatus] = useState<string>('resolved');

  const loadAssetDetails = useCallback(async () => {
    if (!id) return;

    try {
      setLoading(true);
      setError(null);
      const [assetResponse, problemsResponse] = await Promise.all([
        assetsAPI.getAsset(id),
        assetsAPI.getAssetProblems(id),
      ]);
      setAsset(assetResponse.data);
      setProblems(problemsResponse.data);
    } catch (err: any) {
      console.error('Error loading asset details:', err);
      setError(err.response?.data?.detail || 'Failed to load asset details');
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    if (id) {
      loadAssetDetails();
    }
  }, [id, loadAssetDetails]);

  useEffect(() => {
    // Check authentication status
    const checkAuth = () => {
      const token = localStorage.getItem('token');
      setIsLoggedIn(!!token);
    };

    checkAuth();

    // Listen for auth changes
    const handleAuthChange = () => {
      checkAuth();
    };

    window.addEventListener('authChange', handleAuthChange);
    window.addEventListener('storage', (e) => {
      if (e.key === 'token') {
        checkAuth();
      }
    });

    return () => {
      window.removeEventListener('authChange', handleAuthChange);
    };
  }, []);

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

  const formatDateTime = (dateString: string | null | undefined): string => {
    if (!dateString) return 'N/A';
    try {
      return new Date(dateString).toLocaleString('en-US', {
        year: 'numeric',
        month: 'long',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
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
      case 'implementing': return 'status-implementing';
      case 'testing': return 'status-testing';
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
      case 'implementing': return 'Implementing';
      case 'testing': return 'Testing';
      default: return status;
    }
  };

  const getProblemStatusBadgeClass = (status: string) => {
    switch (status) {
      case 'reported': return 'problem-reported';
      case 'in_progress': return 'problem-in-progress';
      case 'resolved': return 'problem-resolved';
      case 'closed': return 'problem-closed';
      default: return '';
    }
  };

  const getProblemStatusLabel = (status: string) => {
    switch (status) {
      case 'reported': return 'Reported';
      case 'in_progress': return 'In Progress';
      case 'resolved': return 'Resolved';
      case 'closed': return 'Closed';
      default: return status;
    }
  };

  const handleLock = async () => {
    if (!id) return;
    try {
      setActionLoading('lock');
      await assetsAPI.lockAsset(id);
      await loadAssetDetails();
    } catch (err: any) {
      alert(err.response?.data?.detail || 'Failed to lock asset');
    } finally {
      setActionLoading(null);
    }
  };

  const handleUnlock = async () => {
    if (!id) return;
    try {
      setActionLoading('unlock');
      await assetsAPI.unlockAsset(id);
      await loadAssetDetails();
    } catch (err: any) {
      alert(err.response?.data?.detail || 'Failed to unlock asset');
    } finally {
      setActionLoading(null);
    }
  };

  const handleMarkPartReplaced = async (partId: string) => {
    try {
      setActionLoading(`part-${partId}`);
      await assetPartsAPI.markReplaced(partId);
      await loadAssetDetails();
    } catch (err: any) {
      alert(err.response?.data?.detail || 'Failed to mark part as replaced');
    } finally {
      setActionLoading(null);
    }
  };

  const handleEdit = () => {
    if (id) {
      navigate(`/assets/${id}/edit`);
    }
  };

  const handleReportProblem = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!id || !problemDescription.trim()) {
      alert('Please provide a description of the problem');
      return;
    }

    try {
      setSubmittingProblem(true);
      await assetsAPI.reportProblem(id, problemDescription);
      setProblemDescription('');
      setShowReportForm(false);
      await loadAssetDetails();
    } catch (err: any) {
      alert(err.response?.data?.detail || 'Failed to report problem');
      console.error('Error reporting problem:', err);
    } finally {
      setSubmittingProblem(false);
    }
  };

  const handleResolveProblem = async (problemId: string) => {
    if (!id) return;

    try {
      setActionLoading(`resolve-${problemId}`);
      await assetsAPI.resolveProblem(id, problemId, resolutionNotes, resolvingStatus);
      setResolvingProblemId(null);
      setResolutionNotes('');
      setResolvingStatus('resolved');
      await loadAssetDetails();
    } catch (err: any) {
      alert(err.response?.data?.detail || 'Failed to resolve problem');
      console.error('Error resolving problem:', err);
    } finally {
      setActionLoading(null);
    }
  };

  const startResolving = (problemId: string) => {
    setResolvingProblemId(problemId);
    setResolutionNotes('');
    setResolvingStatus('resolved');
  };

  const cancelResolving = () => {
    setResolvingProblemId(null);
    setResolutionNotes('');
    setResolvingStatus('resolved');
  };

  const filteredProblems = problems.filter((problem) => {
    if (problemStatusFilter === 'all') return true;
    return problem.status === problemStatusFilter;
  });

  if (loading) {
    return <div className="asset-detail-loading">Loading asset details...</div>;
  }

  if (error || !asset) {
    return (
      <div className="asset-detail-error">
        <p>Error: {error || 'Asset not found'}</p>
        <button onClick={() => navigate(-1)}>Go Back</button>
      </div>
    );
  }

  return (
    <div className="asset-detail-page">
      {/* Header */}
      <div className="asset-detail-header">
        <div className="asset-detail-header-left">
          <button className="back-button" onClick={() => navigate(-1)}>
            ← Back
          </button>
          <h1>{asset.name}</h1>
          <span className={`status-badge ${getStatusBadgeClass(asset.status)}`}>
            {getStatusLabel(asset.status)}
          </span>
        </div>
        <div className="asset-detail-header-right">
          {asset.can_unlock && (
            <>
              {asset.is_locked ? (
                <button
                  className="action-button unlock-button"
                  onClick={handleUnlock}
                  disabled={actionLoading === 'unlock'}
                >
                  {actionLoading === 'unlock' ? 'Unlocking...' : 'Unlock Asset'}
                </button>
              ) : (
                <button
                  className="action-button lock-button"
                  onClick={handleLock}
                  disabled={actionLoading === 'lock'}
                >
                  {actionLoading === 'lock' ? 'Locking...' : 'Lock Asset'}
                </button>
              )}
            </>
          )}
          <button className="action-button edit-button" onClick={handleEdit}>
            Edit Asset
          </button>
        </div>
      </div>

      {/* Asset Image */}
      {asset.image_url && (
        <div className="asset-detail-image">
          <img src={asset.image_url} alt={asset.name} />
        </div>
      )}

      <div className="asset-detail-content">
        {/* Basic Information */}
        <section className="asset-detail-section">
          <h2>Basic Information</h2>
          <div className="info-grid">
            {asset.description && (
              <div className="info-item full-width">
                <span className="info-label">Description:</span>
                <span className="info-value">{asset.description}</span>
              </div>
            )}
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
                <span className="info-value">
                  {asset.inventory_item ? (
                    <button
                      className="inventory-item-link"
                      onClick={() => navigate(`/inventory/items/${asset.inventory_item}`)}
                      type="button"
                    >
                      {asset.inventory_item_name}
                    </button>
                  ) : (
                    asset.inventory_item_name
                  )}
                </span>
              </div>
            )}
            {asset.display_manufacturer && (
              <div className="info-item">
                <span className="info-label">Manufacturer:</span>
                <span className="info-value">{asset.display_manufacturer}</span>
              </div>
            )}
            {asset.category_name && (
              <div className="info-item">
                <span className="info-label">Category:</span>
                <span className="info-value">{asset.category_name}</span>
              </div>
            )}
            {asset.location_name && (
              <div className="info-item">
                <span className="info-label">Location:</span>
                <span className="info-value">{asset.location_name}</span>
              </div>
            )}
            {asset.owning_group_name && (
              <div className="info-item">
                <span className="info-label">Owner:</span>
                <span className="info-value">{asset.owning_group_name}</span>
              </div>
            )}
          </div>
        </section>

        {/* Acquisition Information */}
        <section className="asset-detail-section">
          <h2>Acquisition Information</h2>
          <div className="info-grid">
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

        {/* Operational Requirements */}
        {(asset.circuit || asset.needs_compressed_air || asset.needs_ventilation || asset.is_chargeable) && (
          <section className="asset-detail-section">
            <h2>Operational Requirements</h2>
            <div className="info-grid">
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

        {/* Part Replacement Tracking */}
        {asset.parts && asset.parts.length > 0 && (
          <section className="asset-detail-section">
            <h2>Part Replacement Tracking</h2>
            <div className="parts-table">
              <table>
                <thead>
                  <tr>
                    <th>Part Name</th>
                    <th>Quantity Needed</th>
                    <th>Last Replaced</th>
                    <th>Days Since</th>
                    <th>Maintenance Interval</th>
                    <th>Status</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {asset.parts.map((part) => (
                    <tr key={part.id} className={part.needs_replacement ? 'needs-replacement' : ''}>
                      <td>{part.part_name}</td>
                      <td>{part.quantity_needed}</td>
                      <td>{formatDate(part.last_replaced_at)}</td>
                      <td>
                        {part.days_since_replacement !== null
                          ? `${part.days_since_replacement} days`
                          : 'N/A'}
                      </td>
                      <td>
                        {part.maintenance_interval_days
                          ? `Every ${part.maintenance_interval_days} days`
                          : 'N/A'}
                      </td>
                      <td>
                        {part.needs_replacement ? (
                          <span className="replacement-badge needs">Needs Replacement</span>
                        ) : (
                          <span className="replacement-badge ok">OK</span>
                        )}
                      </td>
                      <td>
                        <button
                          className="mark-replaced-button"
                          onClick={() => handleMarkPartReplaced(part.id)}
                          disabled={actionLoading === `part-${part.id}`}
                        >
                          {actionLoading === `part-${part.id}` ? 'Updating...' : 'Mark Replaced'}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {/* Problem History */}
        <section className="asset-detail-section">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
            <h2>Problem History</h2>
            {isLoggedIn && (
              <button
                className="action-button"
                onClick={() => setShowReportForm(!showReportForm)}
                style={{ padding: '0.5rem 1rem' }}
              >
                {showReportForm ? 'Cancel' : '+ Report Problem'}
              </button>
            )}
          </div>

          {/* Report Problem Form */}
          {isLoggedIn && showReportForm && (
            <div className="problem-form" style={{ marginBottom: '1.5rem', padding: '1rem', border: '1px solid #ddd', borderRadius: '4px' }}>
              <h3>Report a Problem</h3>
              <form onSubmit={handleReportProblem}>
                <textarea
                  value={problemDescription}
                  onChange={(e) => setProblemDescription(e.target.value)}
                  placeholder="Describe the problem with this asset..."
                  rows={4}
                  required
                  disabled={submittingProblem}
                  style={{ width: '100%', padding: '0.5rem', marginBottom: '0.5rem', fontFamily: 'inherit' }}
                />
                <div style={{ display: 'flex', gap: '0.5rem' }}>
                  <button
                    type="submit"
                    className="action-button"
                    disabled={submittingProblem || !problemDescription.trim()}
                  >
                    {submittingProblem ? 'Submitting...' : 'Submit Problem'}
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setShowReportForm(false);
                      setProblemDescription('');
                    }}
                    disabled={submittingProblem}
                    style={{ padding: '0.5rem 1rem', background: '#ccc', border: 'none', borderRadius: '4px', cursor: 'pointer' }}
                  >
                    Cancel
                  </button>
                </div>
              </form>
            </div>
          )}

          <div className="problem-filters">
            <label htmlFor="problem-status-filter">Filter by Status:</label>
            <select
              id="problem-status-filter"
              value={problemStatusFilter}
              onChange={(e) => setProblemStatusFilter(e.target.value)}
              className="filter-select"
            >
              <option value="all">All</option>
              <option value="reported">Reported</option>
              <option value="in_progress">In Progress</option>
              <option value="resolved">Resolved</option>
              <option value="closed">Closed</option>
            </select>
          </div>
          {filteredProblems.length === 0 ? (
            <p className="no-problems">No problems found.</p>
          ) : (
            <div className="problems-list">
              {filteredProblems.map((problem) => (
                <div key={problem.id} className="problem-item">
                  <div className="problem-header">
                    <span className={`problem-status-badge ${getProblemStatusBadgeClass(problem.status)}`}>
                      {getProblemStatusLabel(problem.status)}
                    </span>
                    <span className="problem-date">{formatDateTime(problem.created_at)}</span>
                  </div>
                  <div className="problem-body">
                    <p className="problem-description">{problem.description}</p>
                    {problem.reported_by && (
                      <p className="problem-reported-by">Reported by: {problem.reported_by}</p>
                    )}
                    {problem.resolution_notes && (
                      <div className="problem-resolution">
                        <strong>Resolution:</strong>
                        <p>{problem.resolution_notes}</p>
                      </div>
                    )}
                    {problem.resolved_at && (
                      <p className="problem-resolved-date">Resolved: {formatDateTime(problem.resolved_at)}</p>
                    )}

                    {/* Resolve Problem UI */}
                    {isLoggedIn && resolvingProblemId === problem.id ? (
                      <div className="resolve-problem-form" style={{ marginTop: '1rem', padding: '1rem', border: '1px solid #ddd', borderRadius: '4px', backgroundColor: '#f9f9f9' }}>
                        <h4>Resolve Problem</h4>
                        <div style={{ marginBottom: '0.5rem' }}>
                          <label htmlFor={`resolve-status-${problem.id}`} style={{ display: 'block', marginBottom: '0.25rem' }}>
                            Status:
                          </label>
                          <select
                            id={`resolve-status-${problem.id}`}
                            value={resolvingStatus}
                            onChange={(e) => setResolvingStatus(e.target.value)}
                            style={{ width: '100%', padding: '0.5rem' }}
                          >
                            <option value="resolved">Resolved</option>
                            <option value="closed">Closed</option>
                          </select>
                        </div>
                        <div style={{ marginBottom: '0.5rem' }}>
                          <label htmlFor={`resolution-notes-${problem.id}`} style={{ display: 'block', marginBottom: '0.25rem' }}>
                            Resolution Notes (optional):
                          </label>
                          <textarea
                            id={`resolution-notes-${problem.id}`}
                            value={resolutionNotes}
                            onChange={(e) => setResolutionNotes(e.target.value)}
                            placeholder="Add notes about how this problem was resolved..."
                            rows={3}
                            style={{ width: '100%', padding: '0.5rem', fontFamily: 'inherit' }}
                          />
                        </div>
                        <div style={{ display: 'flex', gap: '0.5rem' }}>
                          <button
                            className="action-button"
                            onClick={() => handleResolveProblem(problem.id)}
                            disabled={actionLoading === `resolve-${problem.id}`}
                          >
                            {actionLoading === `resolve-${problem.id}` ? 'Resolving...' : 'Resolve'}
                          </button>
                          <button
                            type="button"
                            onClick={cancelResolving}
                            disabled={actionLoading === `resolve-${problem.id}`}
                            style={{ padding: '0.5rem 1rem', background: '#ccc', border: 'none', borderRadius: '4px', cursor: 'pointer' }}
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    ) : (
                      isLoggedIn && problem.status !== 'resolved' && problem.status !== 'closed' && (
                        <div style={{ marginTop: '0.5rem' }}>
                          <button
                            className="action-button"
                            onClick={() => startResolving(problem.id)}
                            disabled={!!actionLoading}
                            style={{ padding: '0.5rem 1rem', fontSize: '0.9rem' }}
                          >
                            Resolve Problem
                          </button>
                        </div>
                      )
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* Maintenance Log */}
        <section className="asset-detail-section">
          <h2>Maintenance Log</h2>
          {asset.maintenance_plan ? (
            <div className="maintenance-plan">
              <h3>Maintenance Plan</h3>
              <pre className="maintenance-plan-text">{asset.maintenance_plan}</pre>
            </div>
          ) : (
            <p className="no-maintenance-plan">No maintenance plan available.</p>
          )}
          {asset.condition_notes && (
            <div className="condition-notes">
              <h3>Condition Notes</h3>
              <p>{asset.condition_notes}</p>
            </div>
          )}
          {asset.parts && asset.parts.length > 0 && (
            <div className="maintenance-history">
              <h3>Part Replacement History</h3>
              <ul className="maintenance-list">
                {asset.parts
                  .filter((part) => part.last_replaced_at)
                  .sort((a, b) => {
                    if (!a.last_replaced_at || !b.last_replaced_at) return 0;
                    return new Date(b.last_replaced_at).getTime() - new Date(a.last_replaced_at).getTime();
                  })
                  .map((part) => (
                    <li key={part.id} className="maintenance-item">
                      <div className="maintenance-item-header">
                        <span className="maintenance-part-name">{part.part_name}</span>
                        <span className="maintenance-date">{formatDate(part.last_replaced_at)}</span>
                      </div>
                      {part.notes && <div className="maintenance-notes">{part.notes}</div>}
                    </li>
                  ))}
              </ul>
            </div>
          )}
        </section>

        {/* QR Code */}
        <section className="asset-detail-section">
          <h2>QR Code</h2>
          {asset.qr_code_url ? (
            <div className="qr-code-section">
              <img src={asset.qr_code_url} alt="QR Code" className="qr-code-image" />
              <div className="qr-code-actions">
                <a
                  href={asset.qr_code_url}
                  download={`${asset.name}-qr-code.png`}
                  className="download-button"
                >
                  Download QR Code
                </a>
                {asset.qr_code_scan_url && (
                  <a
                    href={asset.qr_code_scan_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="scan-link"
                  >
                    View Scan Page
                  </a>
                )}
              </div>
            </div>
          ) : (
            <div className="qr-code-section">
              <p>QR code not generated yet.</p>
              <button
                className="generate-qr-button"
                onClick={async () => {
                  if (id) {
                    try {
                      await assetsAPI.generateQR(id);
                      await loadAssetDetails();
                    } catch (err: any) {
                      alert(err.response?.data?.detail || 'Failed to generate QR code');
                    }
                  }
                }}
              >
                Generate QR Code
              </button>
            </div>
          )}
        </section>

        {/* Links & Resources */}
        {(asset.product_url || asset.wiki_page_url || asset.manual_pdf_url) && (
          <section className="asset-detail-section">
            <h2>Links & Resources</h2>
            <div className="links-list">
              {asset.product_url && (
                <a href={asset.product_url} target="_blank" rel="noopener noreferrer" className="resource-link">
                  Product Page
                </a>
              )}
              {asset.wiki_page_url && (
                <a href={asset.wiki_page_url} target="_blank" rel="noopener noreferrer" className="resource-link">
                  Wiki Page
                </a>
              )}
              {asset.manual_pdf_url && (
                <a href={asset.manual_pdf_url} target="_blank" rel="noopener noreferrer" className="resource-link">
                  Manual (PDF)
                </a>
              )}
            </div>
          </section>
        )}

        {/* Additional Notes */}
        {asset.notes && (
          <section className="asset-detail-section">
            <h2>Additional Notes</h2>
            <p className="asset-notes">{asset.notes}</p>
          </section>
        )}
      </div>
    </div>
  );
};

export default AssetDetailPage;
