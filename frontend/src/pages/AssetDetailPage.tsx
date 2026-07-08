/**
 * Asset Detail Page
 * Full page view for asset details with part tracking, problem history, maintenance, QR code, and lock/unlock controls
 */
import { Badge, Button, Group, Paper, Text } from '@mantine/core';
import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import AssetForgeKeyAccessCard from '../components/AssetForgeKeyAccessCard';
import AssetReservationsAndOOSSection from '../components/AssetReservationsAndOOSSection';
import AssetDocumentsSection from '../components/assets/AssetDocumentsSection';
import AssetMetersSection from '../components/assets/AssetMetersSection';
import MaintenanceHistorySection from '../components/assets/MaintenanceHistorySection';
import AssetSerializedComponentsSection from '../components/inventory/AssetSerializedComponentsSection';
import WorkspacePage from '../components/landing/WorkspacePage';
import {
  AssetLOTORequirements,
  assetPartsAPI,
  assetsAPI,
  lotoAPI,
  maintenanceAPI,
  workOrderAPI,
} from '../services/api';
import '../styles/AssetDetailPage.css';
import { Asset, AssetPart, AssetProblem, MaintenanceItem, WorkOrder } from '../types';
import { formatDateOnly } from '../utils/dates';
import { showError } from '../utils/dialogs';
import { extractErrorMessage } from '../utils/extractErrorMessage';

const AssetDetailPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [asset, setAsset] = useState<Asset | null>(null);
  const [problems, setProblems] = useState<AssetProblem[]>([]);
  const [maintenanceItems, setMaintenanceItems] = useState<MaintenanceItem[]>([]);
  const [recentWorkOrders, setRecentWorkOrders] = useState<WorkOrder[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [problemStatusFilter, setProblemStatusFilter] = useState<string>('all');
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [isLoggedIn, setIsLoggedIn] = useState<boolean>(false);
  const [showReportForm, setShowReportForm] = useState<boolean>(false);
  const [problemDescription, setProblemDescription] = useState<string>('');
  // AssetPart ids the reporter flags as needing replace/fix (optional).
  const [affectedPartIds, setAffectedPartIds] = useState<string[]>([]);
  const [submittingProblem, setSubmittingProblem] = useState<boolean>(false);
  const [resolvingProblemId, setResolvingProblemId] = useState<string | null>(null);
  const [resolutionNotes, setResolutionNotes] = useState<string>('');
  const [resolvingStatus, setResolvingStatus] = useState<string>('resolved');
  const [cloneSourceId, setCloneSourceId] = useState<string | null>(null);
  const [cloneTargetOptions, setCloneTargetOptions] = useState<Asset[]>([]);
  const [cloneTargetId, setCloneTargetId] = useState<string>('');
  const [cloneSubmitting, setCloneSubmitting] = useState<boolean>(false);
  const [cloneError, setCloneError] = useState<string | null>(null);
  const [cloneToast, setCloneToast] = useState<string | null>(null);
  const [tagSize, setTagSize] = useState<'standard' | 'large'>('standard');
  const [loto, setLoto] = useState<AssetLOTORequirements | null>(null);
  // Serialized parts prompt for the new unit's serial number before recording
  // a replacement; non-serialized parts stay one-click.
  const [serialModalPart, setSerialModalPart] = useState<AssetPart | null>(null);
  const [replacementSerial, setReplacementSerial] = useState<string>('');

  const loadAssetDetails = useCallback(async () => {
    if (!id) return;

    try {
      setLoading(true);
      setError(null);
      const [assetResponse, problemsResponse, maintenanceResponse, workOrdersResponse] =
        await Promise.all([
          assetsAPI.getAsset(id),
          assetsAPI.getAssetProblems(id),
          assetsAPI.getMaintenanceItems(id),
          workOrderAPI.listWorkOrders({ asset: id }),
        ]);
      setAsset(assetResponse.data);
      setProblems(problemsResponse.data);
      setMaintenanceItems(maintenanceResponse.data);
      setRecentWorkOrders(workOrdersResponse.data?.results ?? []);

      try {
        const lotoResp = await lotoAPI.getAssetRequirements(id);
        setLoto(lotoResp.data);
      } catch (lotoErr) {
        // Non-fatal — surface as no LOTO data rather than blocking the page.
        console.warn('LOTO requirements unavailable for asset', id, lotoErr);
        setLoto(null);
      }
    } catch (err: any) {
      console.error('Error loading asset details:', err);
      setError(extractErrorMessage(err, 'Failed to load asset details'));
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

  const formatDate = (dateString: string | null | undefined): string =>
    formatDateOnly(
      dateString,
      { year: 'numeric', month: 'long', day: 'numeric' },
      'N/A',
    );

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
      showError(extractErrorMessage(err, 'Failed to lock asset'));
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
      showError(extractErrorMessage(err, 'Failed to unlock asset'));
    } finally {
      setActionLoading(null);
    }
  };

  const handleMarkPartReplaced = async (
    partId: string,
    replacementSerialNumber?: string,
  ): Promise<boolean> => {
    try {
      setActionLoading(`part-${partId}`);
      // Only forward the serial when one was captured so non-serialized parts
      // keep posting an empty (one-click) body exactly as before.
      if (replacementSerialNumber) {
        await assetPartsAPI.markReplaced(partId, replacementSerialNumber);
      } else {
        await assetPartsAPI.markReplaced(partId);
      }
      await loadAssetDetails();
      return true;
    } catch (err: any) {
      showError(extractErrorMessage(err, 'Failed to mark part as replaced'));
      return false;
    } finally {
      setActionLoading(null);
    }
  };

  // Serialized parts open a prompt for the replacement unit's serial number;
  // everything else keeps the original one-click behaviour.
  const handleMarkReplacedClick = (part: AssetPart) => {
    if (part.part_details?.is_serialized) {
      setReplacementSerial('');
      setSerialModalPart(part);
    } else {
      handleMarkPartReplaced(part.id);
    }
  };

  const closeSerialModal = () => {
    setSerialModalPart(null);
    setReplacementSerial('');
  };

  const handleSerialModalSubmit = async () => {
    if (!serialModalPart) return;
    const trimmed = replacementSerial.trim();
    const ok = await handleMarkPartReplaced(serialModalPart.id, trimmed || undefined);
    if (ok) closeSerialModal();
  };

  const handleEdit = () => {
    if (id) {
      navigate(`/assets/${id}/edit`);
    }
  };

  const openCloneModal = async (maintenanceItemId: string) => {
    setCloneSourceId(maintenanceItemId);
    setCloneTargetId('');
    setCloneError(null);
    try {
      const response = await assetsAPI.listAssets({ page_size: 500 });
      const options = response.data.results.filter((a) => a.id !== id);
      setCloneTargetOptions(options);
    } catch (err: any) {
      setCloneError(extractErrorMessage(err, 'Failed to load assets'));
    }
  };

  const closeCloneModal = () => {
    setCloneSourceId(null);
    setCloneTargetId('');
    setCloneTargetOptions([]);
    setCloneError(null);
  };

  const handleCloneSubmit = async () => {
    if (!cloneSourceId || !cloneTargetId) return;
    try {
      setCloneSubmitting(true);
      setCloneError(null);
      const response = await maintenanceAPI.cloneItem(cloneSourceId, cloneTargetId);
      const target = cloneTargetOptions.find((a) => a.id === cloneTargetId);
      setCloneToast(
        `Cloned "${response.data.title}" to ${target ? target.name : 'target asset'}.`,
      );
      closeCloneModal();
    } catch (err: any) {
      setCloneError(extractErrorMessage(err, 'Failed to clone maintenance item'));
    } finally {
      setCloneSubmitting(false);
    }
  };

  const toggleAffectedPart = (partId: string) => {
    setAffectedPartIds((existing) =>
      existing.includes(partId)
        ? existing.filter((pid) => pid !== partId)
        : [...existing, partId],
    );
  };

  const handleReportProblem = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!id || !problemDescription.trim()) {
      showError('Please provide a description of the problem');
      return;
    }

    try {
      setSubmittingProblem(true);
      // Only pass part ids when some are checked so a description-only report
      // sends the exact same 2-argument call as before.
      if (affectedPartIds.length > 0) {
        await assetsAPI.reportProblem(id, problemDescription, affectedPartIds);
      } else {
        await assetsAPI.reportProblem(id, problemDescription);
      }
      setProblemDescription('');
      setAffectedPartIds([]);
      setShowReportForm(false);
      await loadAssetDetails();
    } catch (err: any) {
      showError(extractErrorMessage(err, 'Failed to report problem'));
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
      showError(extractErrorMessage(err, 'Failed to resolve problem'));
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
    return (
      <WorkspacePage
        testId="asset-detail-page"
        hero={{ eyebrow: 'Assets', title: 'Asset', description: 'Loading…' }}
      >
        <Paper withBorder p="md">
          <Text c="dimmed">Loading asset details…</Text>
        </Paper>
      </WorkspacePage>
    );
  }

  if (error || !asset) {
    return (
      <WorkspacePage
        testId="asset-detail-page"
        hero={{
          eyebrow: 'Assets',
          title: 'Asset',
          description: error || 'Not found.',
          action: (
            <Button variant="default" onClick={() => navigate(-1)}>
              Go back
            </Button>
          ),
        }}
      >
        <Paper withBorder p="md" radius="md" bg="red.0" c="red.9">
          <Text>{error || 'Asset not found'}</Text>
        </Paper>
      </WorkspacePage>
    );
  }

  return (
    <WorkspacePage
      testId="asset-detail-page"
      hero={{
        eyebrow: asset.asset_tag ? `Assets · ${asset.asset_tag}` : 'Assets',
        title: asset.name,
        description: getStatusLabel(asset.status),
        action: (
          <Group gap="sm">
            {asset.can_unlock && (
              asset.is_locked ? (
                <Button
                  variant="filled"
                  onClick={handleUnlock}
                  disabled={actionLoading === 'unlock'}
                  loading={actionLoading === 'unlock'}
                >
                  Unlock asset
                </Button>
              ) : (
                <Button
                  variant="default"
                  onClick={handleLock}
                  disabled={actionLoading === 'lock'}
                  loading={actionLoading === 'lock'}
                >
                  Lock asset
                </Button>
              )
            )}
            <Button onClick={handleEdit}>Edit asset</Button>
          </Group>
        ),
      }}
    >
      <div className="asset-detail-page">
        <Group gap="sm">
          <Badge size="lg" radius="sm" variant="light">
            {getStatusLabel(asset.status)}
          </Badge>
        </Group>

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
        {(asset.circuit || asset.mac_address || asset.needs_compressed_air || asset.needs_ventilation || asset.is_chargeable || asset.training_required || (asset.required_certification_details && asset.required_certification_details.length > 0)) && (
          <section className="asset-detail-section">
            <h2>Operational Requirements</h2>
            <div className="info-grid">
              {asset.circuit && (
                <div className="info-item">
                  <span className="info-label">Circuit:</span>
                  <span className="info-value">{asset.circuit}</span>
                </div>
              )}
              {asset.mac_address && (
                <div className="info-item">
                  <span className="info-label">MAC Address:</span>
                  <span className="info-value">{asset.mac_address}</span>
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
              {asset.training_required && (
                <div className="info-item" data-testid="training-required-row">
                  <span className="info-label">Training required:</span>
                  <span className="info-value">Yes</span>
                </div>
              )}
              {asset.required_certification_details &&
                asset.required_certification_details.length > 0 && (
                  <div className="info-item" data-testid="required-certifications-row">
                    <span className="info-label">Required certifications:</span>
                    <span className="info-value">
                      {asset.required_certification_details
                        .map((c) => c.name)
                        .join(', ')}
                    </span>
                  </div>
                )}
            </div>
          </section>
        )}

        {/* Lockout / Tagout */}
        <section className="asset-detail-section" data-testid="asset-loto-section">
          <h2>Lockout / Tagout (LOTO)</h2>
          {loto && loto.is_required ? (
            <>
              <div className="info-grid">
                {loto.lockout_type_display && (
                  <div className="info-item">
                    <span className="info-label">Lockout Type:</span>
                    <span className="info-value">{loto.lockout_type_display}</span>
                  </div>
                )}
                {loto.lockout_responsible && (
                  <div className="info-item">
                    <span className="info-label">Responsible:</span>
                    <span className="info-value">{loto.lockout_responsible}</span>
                  </div>
                )}
              </div>
              {loto.lockout_instructions && (
                <div className="info-item" style={{ marginTop: '0.5rem' }}>
                  <span className="info-label">Procedure:</span>
                  <span
                    className="info-value"
                    style={{ whiteSpace: 'pre-wrap' }}
                  >
                    {loto.lockout_instructions}
                  </span>
                </div>
              )}
              {loto.energy_sources.length > 0 && (
                <div style={{ marginTop: '0.75rem' }}>
                  <h3>Energy Sources</h3>
                  <table className="parts-table">
                    <thead>
                      <tr>
                        <th>Type</th>
                        <th>Magnitude</th>
                        <th>Isolation Point</th>
                        <th>Required Devices</th>
                      </tr>
                    </thead>
                    <tbody>
                      {loto.energy_sources.map((src) => (
                        <tr key={src.id}>
                          <td>{src.source_type_display}</td>
                          <td>{src.magnitude || '—'}</td>
                          <td>{src.isolation_point || '—'}</td>
                          <td>
                            {src.required_devices_detail.length === 0
                              ? '—'
                              : src.required_devices_detail
                                  .map(
                                    (d) =>
                                      `${d.device_type_display} ${d.label}`
                                  )
                                  .join(', ')}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          ) : (
            <p data-testid="asset-loto-empty">No LOTO required.</p>
          )}
        </section>

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
                          onClick={() => handleMarkReplacedClick(part)}
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
                {asset.parts && asset.parts.length > 0 && (
                  <fieldset
                    className="affected-parts"
                    style={{ border: '1px solid #ddd', borderRadius: '4px', padding: '0.5rem', marginBottom: '0.5rem' }}
                  >
                    <legend>Which components need attention? (optional)</legend>
                    {asset.parts.map((part) => (
                      <label
                        key={part.id}
                        className="affected-part-option"
                        style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginBottom: '0.25rem' }}
                      >
                        <input
                          type="checkbox"
                          checked={affectedPartIds.includes(String(part.id))}
                          onChange={() => toggleAffectedPart(String(part.id))}
                          disabled={submittingProblem}
                        />
                        <span>
                          {part.part_name}
                          {part.quantity_needed ? ` (qty ${part.quantity_needed})` : ''}
                        </span>
                      </label>
                    ))}
                  </fieldset>
                )}
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
                      setAffectedPartIds([]);
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
                    {problem.affected_parts && problem.affected_parts.length > 0 && (
                      <div className="problem-affected-parts">
                        <strong>Components flagged:</strong>
                        <ul>
                          {problem.affected_parts.map((part) => (
                            <li key={part.id}>
                              {part.part_name}
                              {part.quantity_needed ? ` (qty ${part.quantity_needed})` : ''}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {problem.resolution_notes && (
                      <div className="problem-resolution">
                        <strong>Resolution:</strong>
                        <p>{problem.resolution_notes}</p>
                      </div>
                    )}
                    {problem.resolved_at && (
                      <p className="problem-resolved-date">
                        Resolved: {formatDateTime(problem.resolved_at)}
                        {problem.resolved_by && ` by ${problem.resolved_by}`}
                      </p>
                    )}

                    {problem.photos && problem.photos.length > 0 && (
                      <div className="problem-photos">
                        <strong>Photos:</strong>
                        <div
                          style={{
                            display: 'grid',
                            gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))',
                            gap: '0.5rem',
                            marginTop: '0.5rem',
                          }}
                        >
                          {problem.photos.map((photo) => (
                            <a
                              key={photo.id}
                              href={photo.image_url || '#'}
                              target="_blank"
                              rel="noopener noreferrer"
                              title={photo.caption || 'Open photo'}
                              style={{ display: 'block' }}
                            >
                              <img
                                src={photo.image_url || ''}
                                alt={photo.caption || 'Problem photo'}
                                style={{
                                  width: '100%',
                                  height: 100,
                                  objectFit: 'cover',
                                  borderRadius: 4,
                                  border: '1px solid #ddd',
                                  cursor: 'zoom-in',
                                }}
                              />
                            </a>
                          ))}
                        </div>
                      </div>
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

        {/* Maintenance — single source of scheduled (preventive) and unscheduled (work order) maintenance */}
        <section className="asset-detail-section">
          <div className="section-header-row">
            <h2>Maintenance</h2>
            {isLoggedIn && (
              <button
                className="add-button"
                onClick={() => navigate(`/assets/${id}/maintenance/new`)}
              >
                + Add PM Task
              </button>
            )}
          </div>

          <h3>Scheduled</h3>
          {maintenanceItems.length === 0 ? (
            <p className="no-maintenance-plan">No scheduled maintenance tasks defined.</p>
          ) : (
            <ul className="maintenance-list">
              {maintenanceItems.map((item) => (
                <li key={item.id} className="maintenance-item">
                  <div className="maintenance-item-header">
                    <span className="maintenance-part-name">{item.title}</span>
                    <span
                      className={`maintenance-status-badge ${
                        item.is_overdue ? 'status-overdue' : item.next_due_at ? 'status-upcoming' : 'status-asneeded'
                      }`}
                    >
                      {item.is_overdue
                        ? item.days_overdue
                          ? `Overdue ${item.days_overdue}d`
                          : 'Overdue'
                        : item.next_due_at
                        ? `Due ${new Date(item.next_due_at).toLocaleDateString()}`
                        : 'As needed'}
                    </span>
                    {isLoggedIn && (
                      <>
                        <button
                          className="edit-link-button"
                          onClick={() => navigate(`/assets/${id}/maintenance/${item.id}/edit`)}
                        >
                          Edit
                        </button>
                        <button
                          className="edit-link-button"
                          onClick={() => openCloneModal(item.id)}
                        >
                          Clone to asset...
                        </button>
                      </>
                    )}
                  </div>
                  {item.description && (
                    <div className="maintenance-notes">{item.description}</div>
                  )}
                  {item.estimated_time_minutes && (
                    <div className="maintenance-meta">
                      Est. time: {item.estimated_time_minutes} min
                      {item.estimated_cost && parseFloat(item.estimated_cost) > 0
                        ? ` · Est. cost: $${parseFloat(item.estimated_cost).toFixed(2)}`
                        : ''}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}

          <h3>Recent Work Orders</h3>
          {recentWorkOrders.length === 0 ? (
            <p className="no-maintenance-plan">No work orders for this asset.</p>
          ) : (
            <ul className="maintenance-list">
              {recentWorkOrders.map((wo) => (
                <li key={wo.id} className="maintenance-item">
                  <div className="maintenance-item-header">
                    <button
                      className="edit-link-button"
                      onClick={() => navigate(`/maintenance/work-orders/${wo.id}`)}
                    >
                      {wo.maintenance_item_title || `Work order ${wo.short_id}`}
                    </button>
                    <span
                      className={`maintenance-status-badge ${
                        wo.status === 'completed'
                          ? 'status-asneeded'
                          : wo.is_overdue
                          ? 'status-overdue'
                          : 'status-upcoming'
                      }`}
                    >
                      {wo.status === 'completed'
                        ? `Completed ${formatDate(wo.completed_at)}`
                        : wo.is_overdue
                        ? 'Overdue'
                        : wo.due_date
                        ? `Due ${formatDateOnly(wo.due_date, undefined, '')}`
                        : wo.status.replace('_', ' ')}
                    </span>
                  </div>
                  {wo.assigned_to_name && (
                    <div className="maintenance-meta">Assigned: {wo.assigned_to_name}</div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>

        {id && (
          <AssetMetersSection
            meters={asset.meters ?? []}
            assetId={id}
            canManage={
              isLoggedIn &&
              (localStorage.getItem('is_staff') === 'true' ||
                localStorage.getItem('is_superuser') === 'true')
            }
            onChanged={loadAssetDetails}
          />
        )}

        {id && <AssetReservationsAndOOSSection assetId={id} />}

        {id && <AssetSerializedComponentsSection assetId={id} />}

        {id && (
          <AssetDocumentsSection
            assetId={id}
            canManage={
              isLoggedIn &&
              (localStorage.getItem('is_staff') === 'true' ||
                localStorage.getItem('is_superuser') === 'true')
            }
          />
        )}

        {id && (
          <MaintenanceHistorySection
            assetId={id}
            canManage={isLoggedIn && localStorage.getItem('is_staff') === 'true'}
          />
        )}

        {id &&
          (localStorage.getItem('is_staff') === 'true' ||
            localStorage.getItem('is_superuser') === 'true') && (
            <AssetForgeKeyAccessCard assetId={id} />
          )}

        {id &&
          (localStorage.getItem('is_staff') === 'true' ||
            localStorage.getItem('is_superuser') === 'true') && (
            <section className="asset-detail-section">
              <h2>Power</h2>
              {(() => {
                const b = asset?.breaker_summary;
                const d = asset?.disconnect_summary;
                return (
                  <Text size="sm" fw={500} mt={0} mb="xs" data-testid="power-summary">
                    {b
                      ? `Breaker: ${b.panel_name} · ${b.position}${b.label ? ` (${b.label})` : ''}`
                      : 'No breaker recorded.'}
                    {d && ` · Disconnect: ${d.label}`}
                  </Text>
                );
              })()}
              <p style={{ marginTop: 0, color: '#666' }}>
                Set the feeding breaker (and, for lock-out/tag-out, the upstream disconnect) on the
                asset's edit form. Full chain view at{' '}
                <a href={`/facilities/electrical/power-chain/${id}`}>
                  /facilities/electrical/power-chain
                </a>
                .
              </p>
            </section>
          )}

        {/* Asset Tag (the single QR — auto-generated with the tag, rendered on read) */}
        {isLoggedIn && id && (
          <section className="asset-detail-section" data-testid="asset-tag-section">
            <h2>Asset QR &amp; Tag</h2>
            <div className="asset-tag-section">
              <img
                src={assetsAPI.getTagUrl(id, tagSize)}
                alt={`Asset tag (${tagSize})`}
                className="asset-tag-preview"
                style={{ maxWidth: '100%', border: '1px solid #ccc', background: '#fff' }}
              />
              <div className="asset-tag-actions" style={{ marginTop: '0.5rem' }}>
                <label htmlFor="asset-tag-size" style={{ marginRight: '0.5rem' }}>
                  Size:
                </label>
                <select
                  id="asset-tag-size"
                  value={tagSize}
                  onChange={(e) => setTagSize(e.target.value as 'standard' | 'large')}
                >
                  <option value="standard">Standard 1.5&quot; x 1&quot;</option>
                  <option value="large">Large 3&quot; x 1.5&quot;</option>
                </select>
                <a
                  href={assetsAPI.getTagUrl(id, tagSize, true)}
                  download={`asset-tag-${id}-${tagSize}.png`}
                  className="download-button"
                  style={{ marginLeft: '0.75rem' }}
                >
                  Download PNG
                </a>
              </div>
              <p className="asset-tag-note" style={{ marginTop: '0.5rem', fontSize: '0.85rem', color: '#555' }}>
                Scan the QR to open this asset&rsquo;s page. Designed for rivet mounting
                &mdash; holes at &frac14;&quot; from each short edge for a 3/32&quot; rivet.
              </p>
            </div>
          </section>
        )}

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

      {cloneToast && (
        <div className="toast toast-success" role="status" onClick={() => setCloneToast(null)}>
          {cloneToast}
        </div>
      )}

      {cloneSourceId && (
        <div
          className="modal-backdrop"
          role="dialog"
          aria-modal="true"
          aria-labelledby="clone-pm-title"
          onClick={closeCloneModal}
        >
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3 id="clone-pm-title">Clone maintenance plan to another asset</h3>
            <label htmlFor="clone-target-asset">Target asset</label>
            <select
              id="clone-target-asset"
              aria-label="Target asset"
              value={cloneTargetId}
              onChange={(e) => setCloneTargetId(e.target.value)}
            >
              <option value="">Select an asset...</option>
              {cloneTargetOptions.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                  {a.asset_tag ? ` (${a.asset_tag})` : ''}
                </option>
              ))}
            </select>
            {cloneError && <p className="error">{cloneError}</p>}
            <div className="modal-actions">
              <button type="button" onClick={closeCloneModal} disabled={cloneSubmitting}>
                Cancel
              </button>
              <button
                type="button"
                className="primary"
                onClick={handleCloneSubmit}
                disabled={!cloneTargetId || cloneSubmitting}
              >
                {cloneSubmitting ? 'Cloning...' : 'Clone'}
              </button>
            </div>
          </div>
        </div>
      )}

      {serialModalPart && (
        <div
          className="modal-backdrop"
          role="dialog"
          aria-modal="true"
          aria-labelledby="replace-serial-title"
          onClick={closeSerialModal}
        >
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3 id="replace-serial-title">Record replacement serial number</h3>
            <p>
              {serialModalPart.part_name} is a serialized part. Enter the serial number of
              the new unit you installed (optional).
            </p>
            <label htmlFor="replacement-serial-input">Replacement serial number</label>
            <input
              id="replacement-serial-input"
              type="text"
              aria-label="Replacement serial number"
              value={replacementSerial}
              maxLength={200}
              autoFocus
              onChange={(e) => setReplacementSerial(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleSerialModalSubmit();
              }}
            />
            <div className="modal-actions">
              <button
                type="button"
                onClick={closeSerialModal}
                disabled={actionLoading === `part-${serialModalPart.id}`}
              >
                Cancel
              </button>
              <button
                type="button"
                className="primary"
                onClick={handleSerialModalSubmit}
                disabled={actionLoading === `part-${serialModalPart.id}`}
              >
                {actionLoading === `part-${serialModalPart.id}` ? 'Saving...' : 'Mark Replaced'}
              </button>
            </div>
          </div>
        </div>
      )}
      </div>
    </WorkspacePage>
  );
};

export default AssetDetailPage;
