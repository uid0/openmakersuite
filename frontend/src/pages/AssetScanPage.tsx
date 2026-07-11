/**
 * Asset QR Code Scan Page
 * - Unauthenticated users: Show basic info and QR code, update last_scanned_at
 * - Authenticated users: Show full info, enable/disable, report problem options
 */
import { Button, Group, Image, SimpleGrid, Stack, Text } from '@mantine/core';
import { Dropzone, IMAGE_MIME_TYPE } from '@mantine/dropzone';
import { IconPhoto, IconUpload, IconX } from '@tabler/icons-react';
import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { assetProblemsAPI, assetsAPI, checklistsAPI, maintenanceAPI } from '../services/api';
import '../styles/ScanPage.css';
import { Asset, Checklist, MaintenanceItem } from '../types';
import { confirmDelete, promptInput, showError } from '../utils/dialogs';
import { extractErrorMessage } from '../utils/extractErrorMessage';

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
  const [maintenanceItems, setMaintenanceItems] = useState<MaintenanceItem[]>([]);
  const [expandedTask, setExpandedTask] = useState<string | null>(null);
  const [completingTask, setCompletingTask] = useState<string | null>(null);
  const [completionNotes, setCompletionNotes] = useState('');

  // Form state (authenticated users)
  const [problemDescription, setProblemDescription] = useState('');
  // AssetPart ids the reporter flags as needing replace/fix (optional).
  const [affectedPartIds, setAffectedPartIds] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [actionSuccess, setActionSuccess] = useState<string | null>(null);
  const [problemPhotos, setProblemPhotos] = useState<File[]>([]);
  const [photoPreviews, setPhotoPreviews] = useState<string[]>([]);
  const [uploadProgress, setUploadProgress] = useState<string | null>(null);

  const handlePhotosDrop = (files: File[]) => {
    setProblemPhotos((existing) => [...existing, ...files]);
    setPhotoPreviews((existing) => [
      ...existing,
      ...files.map((f) => URL.createObjectURL(f)),
    ]);
  };

  const handleRemovePhoto = (index: number) => {
    setProblemPhotos((existing) => existing.filter((_, i) => i !== index));
    setPhotoPreviews((existing) => {
      const url = existing[index];
      if (url) URL.revokeObjectURL(url);
      return existing.filter((_, i) => i !== index);
    });
  };

  const toggleAffectedPart = (partId: string) => {
    setAffectedPartIds((existing) =>
      existing.includes(partId)
        ? existing.filter((id) => id !== partId)
        : [...existing, partId],
    );
  };

  useEffect(() => {
    return () => {
      photoPreviews.forEach((url) => URL.revokeObjectURL(url));
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadAsset = useCallback(async () => {
    try {
      setLoading(true);
      // Call scan endpoint which updates last_scanned_at and returns asset data
      const assetResponse = await assetsAPI.scanAsset(assetId!);
      setAsset(assetResponse.data);
      setError(null);
    } catch (err: any) {
      setError(extractErrorMessage(err, 'Failed to load asset'));
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

  const loadMaintenanceItems = useCallback(async () => {
    if (!assetId) return;
    try {
      const response = await assetsAPI.getMaintenanceItems(assetId);
      setMaintenanceItems(response.data);
    } catch (err: any) {
      // Silently fail - maintenance items are optional
      console.error('Error loading maintenance items:', err);
    }
  }, [assetId]);

  useEffect(() => {
    if (assetId) {
      loadAsset();
      loadChecklists();
      loadMaintenanceItems();
    }
  }, [assetId, loadAsset, loadChecklists, loadMaintenanceItems]);

  const handleCompleteTask = async (taskId: string) => {
    try {
      setCompletingTask(taskId);
      await maintenanceAPI.completeItem(taskId, { notes: completionNotes });
      setCompletionNotes('');
      setExpandedTask(null);
      await loadMaintenanceItems();
      setActionSuccess('Maintenance task marked complete');
      setTimeout(() => setActionSuccess(null), 3000);
    } catch (err: any) {
      showError(extractErrorMessage(err, 'Failed to log completion'));
      console.error('Error completing maintenance task:', err);
    } finally {
      setCompletingTask(null);
    }
  };

  const startChecklistWithName = async (checklistId: string, userName: string | undefined) => {
    try {
      const completion = await checklistsAPI.startChecklist(checklistId, userName);
      navigate(`/checklist/${checklistId}/complete/${completion.data.id}`);
    } catch (err: any) {
      showError(extractErrorMessage(err, 'Failed to start checklist'));
    }
  };

  const handleStartChecklist = async (checklistId: string) => {
    // If logged in, use the username from localStorage; otherwise prompt
    if (isLoggedIn) {
      const userName = localStorage.getItem('username') || undefined;
      await startChecklistWithName(checklistId, userName);
      return;
    }
    const promptResult = await promptInput('Start checklist', 'Enter your name (optional):');
    if (promptResult === null) return;
    await startChecklistWithName(checklistId, promptResult || undefined);
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
      showError(extractErrorMessage(err, 'Failed to enable asset'));
      console.error('Error enabling asset:', err);
    } finally {
      setSubmitting(false);
    }
  };

  const handleDisable = () => {
    if (!asset || !isLoggedIn) return;

    confirmDelete('Are you sure you want to disable this asset?', async () => {
      try {
        setSubmitting(true);
        await assetsAPI.disableAsset(asset.id);
        await loadAsset(); // Reload to get updated data
        setActionSuccess('Asset disabled successfully');
        setTimeout(() => setActionSuccess(null), 3000);
      } catch (err: any) {
        showError(extractErrorMessage(err, 'Failed to disable asset'));
        console.error('Error disabling asset:', err);
      } finally {
        setSubmitting(false);
      }
    });
  };

  const handleLock = () => {
    if (!asset || !isLoggedIn) return;

    confirmDelete(
      'Are you sure you want to lock this asset? Non-admins will not be able to use it.',
      async () => {
        try {
          setSubmitting(true);
          await assetsAPI.lockAsset(asset.id);
          await loadAsset(); // Reload to get updated data
          setActionSuccess('Asset locked successfully');
          setTimeout(() => setActionSuccess(null), 3000);
        } catch (err: any) {
          showError(extractErrorMessage(err, 'Failed to lock asset'));
          console.error('Error locking asset:', err);
        } finally {
          setSubmitting(false);
        }
      },
    );
  };

  const handleUnlock = () => {
    if (!asset || !isLoggedIn) return;

    confirmDelete('Are you sure you want to unlock this asset?', async () => {
      try {
        setSubmitting(true);
        await assetsAPI.unlockAsset(asset.id);
        await loadAsset(); // Reload to get updated data
        setActionSuccess('Asset unlocked successfully');
        setTimeout(() => setActionSuccess(null), 3000);
      } catch (err: any) {
        showError(extractErrorMessage(err, 'Failed to unlock asset'));
        console.error('Error unlocking asset:', err);
      } finally {
        setSubmitting(false);
      }
    });
  };

  const handleReportProblem = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!asset || !isLoggedIn || !problemDescription.trim()) {
      showError('Please provide a description of the problem');
      return;
    }

    try {
      setSubmitting(true);
      // Only pass part ids when some are checked so a description-only report
      // sends the exact same 2-argument call as before.
      const reportResp =
        affectedPartIds.length > 0
          ? await assetsAPI.reportProblem(asset.id, problemDescription, affectedPartIds)
          : await assetsAPI.reportProblem(asset.id, problemDescription);
      const problemId = reportResp.data?.id;
      const flaggedParts = reportResp.data?.affected_parts ?? [];

      if (problemId && problemPhotos.length > 0) {
        for (let i = 0; i < problemPhotos.length; i += 1) {
          setUploadProgress(`Uploading photo ${i + 1} of ${problemPhotos.length}...`);
          try {
            // eslint-disable-next-line no-await-in-loop
            await assetProblemsAPI.uploadPhoto(problemId, problemPhotos[i]);
          } catch (uploadErr) {
            // Photo failed but the problem was created — surface but don't roll back.
            console.error('Photo upload failed:', uploadErr);
            showError(`Photo ${i + 1} failed to upload (problem report was saved).`);
          }
        }
        setUploadProgress(null);
      }

      photoPreviews.forEach((url) => URL.revokeObjectURL(url));
      setProblemPhotos([]);
      setPhotoPreviews([]);
      setProblemDescription('');
      setAffectedPartIds([]);
      const parts: string[] = [];
      if (problemPhotos.length > 0) {
        parts.push(`${problemPhotos.length} photo(s)`);
      }
      if (flaggedParts.length > 0) {
        parts.push(
          `flagged: ${flaggedParts.map((p) => p.part_name).join(', ')}`,
        );
      }
      setActionSuccess(
        parts.length > 0
          ? `Problem reported (${parts.join('; ')})`
          : 'Problem reported successfully',
      );
      setTimeout(() => setActionSuccess(null), 3000);
    } catch (err: any) {
      showError(extractErrorMessage(err, 'Failed to report problem'));
      console.error('Error reporting problem:', err);
    } finally {
      setSubmitting(false);
      setUploadProgress(null);
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

        {/* Maintenance Tasks Section */}
        {maintenanceItems.length > 0 && (
          <div className="maintenance-tasks-section" style={{ marginTop: '20px', padding: '15px', border: '2px solid #ff8c00', borderRadius: '5px', backgroundColor: '#fff8f0' }}>
            <h3 style={{ color: '#cc5500', marginTop: 0 }}>🔧 Maintenance Tasks</h3>
            <p style={{ color: '#666', marginBottom: '15px' }}>
              {maintenanceItems.filter(t => t.is_overdue).length > 0
                ? `⚠️ ${maintenanceItems.filter(t => t.is_overdue).length} task(s) overdue`
                : 'All tasks on schedule'}
            </p>
            <ul style={{ listStyle: 'none', padding: 0 }}>
              {maintenanceItems
                .sort((a, b) => (b.is_overdue ? 1 : 0) - (a.is_overdue ? 1 : 0))
                .map((task) => (
                  <li key={task.id} style={{ marginBottom: '12px', border: '1px solid #ddd', borderRadius: '4px', overflow: 'hidden' }}>
                    <button
                      onClick={() => {
                        const next = expandedTask === task.id ? null : task.id;
                        setExpandedTask(next);
                        setCompletionNotes('');
                      }}
                      style={{
                        width: '100%',
                        padding: '12px',
                        backgroundColor: task.is_overdue ? '#ffe0cc' : '#f5f5f5',
                        border: 'none',
                        textAlign: 'left',
                        cursor: 'pointer',
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                      }}
                    >
                      <span>
                        {task.is_overdue && <span style={{ color: '#cc0000', fontWeight: 'bold' }}>⚠️ </span>}
                        <strong>{task.title}</strong>
                        {task.days_overdue != null && (
                          <span style={{ color: '#cc0000', marginLeft: '8px', fontSize: '0.85em' }}>
                            ({task.days_overdue}d overdue)
                          </span>
                        )}
                        {task.next_due_at && !task.is_overdue && (
                          <span style={{ color: '#888', marginLeft: '8px', fontSize: '0.85em' }}>
                            Due: {new Date(task.next_due_at).toLocaleDateString()}
                          </span>
                        )}
                      </span>
                      <span>{expandedTask === task.id ? '▲' : '▼'}</span>
                    </button>

                    {expandedTask === task.id && (
                      <div style={{ padding: '12px', backgroundColor: '#fff' }}>
                        {task.description && (
                          <p style={{ color: '#444', marginBottom: '10px' }}>{task.description}</p>
                        )}

                        {task.instructions && (
                          <div style={{ marginBottom: '12px' }}>
                            <strong>Instructions:</strong>
                            <pre style={{ whiteSpace: 'pre-wrap', fontFamily: 'inherit', margin: '8px 0 0', padding: '10px', backgroundColor: '#f8f8f8', borderRadius: '4px', fontSize: '0.9em' }}>
                              {task.instructions}
                            </pre>
                          </div>
                        )}

                        {task.materials.length > 0 && (
                          <div style={{ marginBottom: '12px' }}>
                            <strong>Materials needed:</strong>
                            <ul style={{ margin: '8px 0 0', paddingLeft: '20px' }}>
                              {task.materials.map((mat) => (
                                <li key={mat.id} style={{ fontSize: '0.9em', marginBottom: '4px' }}>
                                  {mat.name} — {mat.quantity}{mat.unit ? ` ${mat.unit}` : ''}
                                  {parseFloat(mat.estimated_cost_per_unit) > 0 && (
                                    <span style={{ color: '#666' }}> (~${mat.total_estimated_cost})</span>
                                  )}
                                  {mat.notes && <span style={{ color: '#888', display: 'block', paddingLeft: '12px' }}>{mat.notes}</span>}
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}

                        <div style={{ display: 'flex', gap: '12px', fontSize: '0.85em', color: '#666', marginBottom: '12px' }}>
                          {task.estimated_time_minutes && (
                            <span>⏱ ~{task.estimated_time_minutes} min</span>
                          )}
                          {parseFloat(task.estimated_cost) > 0 && (
                            <span>💰 Est. ${task.estimated_cost}</span>
                          )}
                          {task.last_completed_at && (
                            <span>✓ Last done: {new Date(task.last_completed_at).toLocaleDateString()}</span>
                          )}
                        </div>

                        {isLoggedIn && (
                          <div>
                            <textarea
                              value={completionNotes}
                              onChange={(e) => setCompletionNotes(e.target.value)}
                              placeholder="Notes (optional)..."
                              rows={2}
                              style={{ width: '100%', padding: '6px', boxSizing: 'border-box', marginBottom: '8px' }}
                            />
                            <button
                              onClick={() => handleCompleteTask(task.id)}
                              disabled={completingTask === task.id}
                              style={{
                                padding: '8px 16px',
                                backgroundColor: '#28a745',
                                color: 'white',
                                border: 'none',
                                borderRadius: '4px',
                                cursor: 'pointer',
                              }}
                            >
                              {completingTask === task.id ? 'Saving...' : '✓ Mark Complete'}
                            </button>
                          </div>
                        )}
                      </div>
                    )}
                  </li>
                ))}
            </ul>
          </div>
        )}

        <div className="item-details">
          {asset.work_safety_notes && (
            <div className="alert alert-warning" data-testid="scan-work-safety-notes">
              <strong>⚠️ Work Safety Notes</strong>
              {asset.work_safety_notes}
            </div>
          )}

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

            {(asset.needs_compressed_air ||
              asset.needs_ventilation ||
              asset.generates_heat_or_flame ||
              asset.needs_chilling ||
              asset.is_chargeable) && (
              <div className="info-item">
                <span className="label">Requirements:</span>
                <span className="value">
                  {asset.needs_compressed_air && 'Compressed Air '}
                  {asset.needs_ventilation && 'Ventilation '}
                  {asset.generates_heat_or_flame && 'Heat/Flame '}
                  {asset.needs_chilling && 'Chilling '}
                  {asset.is_chargeable && 'Chargeable'}
                </span>
              </div>
            )}

            {asset.special_requirements && (
              <div className="info-item" data-testid="scan-special-requirements">
                <span className="label">Special Requirements:</span>
                <span className="value">{asset.special_requirements}</span>
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
                    {asset.parts && asset.parts.length > 0 && (
                      <fieldset className="affected-parts">
                        <legend>Which components need attention? (optional)</legend>
                        {asset.parts.map((part) => (
                          <label key={part.id} className="affected-part-option">
                            <input
                              type="checkbox"
                              checked={affectedPartIds.includes(String(part.id))}
                              onChange={() => toggleAffectedPart(String(part.id))}
                              disabled={submitting}
                            />
                            <span>
                              {part.part_name}
                              {part.quantity_needed
                                ? ` (qty ${part.quantity_needed})`
                                : ''}
                            </span>
                          </label>
                        ))}
                      </fieldset>
                    )}
                    <Stack gap="xs" mt="sm" mb="sm">
                      <Text size="sm" fw={500}>
                        Add photos (optional)
                      </Text>
                      <Dropzone
                        onDrop={handlePhotosDrop}
                        accept={IMAGE_MIME_TYPE}
                        multiple
                        disabled={submitting}
                        maxSize={10 * 1024 * 1024}
                      >
                        <Group justify="center" gap="md" mih={100} style={{ pointerEvents: 'none' }}>
                          <Dropzone.Accept>
                            <IconUpload size={36} stroke={1.5} />
                          </Dropzone.Accept>
                          <Dropzone.Reject>
                            <IconX size={36} stroke={1.5} />
                          </Dropzone.Reject>
                          <Dropzone.Idle>
                            <IconPhoto size={36} stroke={1.5} />
                          </Dropzone.Idle>
                          <div>
                            <Text size="md" inline>
                              Take or drop photos of the problem
                            </Text>
                            <Text size="xs" c="dimmed" inline mt={4}>
                              Tap to use your camera. Multiple photos OK (max 10MB each).
                            </Text>
                          </div>
                        </Group>
                      </Dropzone>
                      {photoPreviews.length > 0 && (
                        <SimpleGrid cols={{ base: 2, sm: 3, md: 4 }} spacing="xs">
                          {photoPreviews.map((url, idx) => (
                            <Stack key={url} gap={4}>
                              <Image
                                src={url}
                                alt={`Photo ${idx + 1}`}
                                height={100}
                                fit="cover"
                                radius="sm"
                              />
                              <Button
                                size="xs"
                                variant="light"
                                color="red"
                                onClick={() => handleRemovePhoto(idx)}
                                disabled={submitting}
                              >
                                Remove
                              </Button>
                            </Stack>
                          ))}
                        </SimpleGrid>
                      )}
                      {uploadProgress && (
                        <Text size="xs" c="dimmed">
                          {uploadProgress}
                        </Text>
                      )}
                    </Stack>
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
