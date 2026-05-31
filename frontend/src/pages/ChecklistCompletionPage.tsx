/**
 * Checklist Completion Page
 * Shows checklist steps and allows users to scan QR codes to complete steps
 */
import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import QRScanner, { QRScannerError } from '../components/QRScanner';
import { useNotifications } from '../hooks/useNotifications';
import { checklistsAPI, scannerAPI } from '../services/api';
import '../styles/ScanPage.css';
import { Checklist, ChecklistCompletion, ChecklistStep } from '../types';
import { confirmAction } from '../utils/dialogs';
import { extractErrorMessage } from '../utils/extractErrorMessage';

interface PendingScan {
  step: ChecklistStep;
  scannedItem: { asset_id?: string; location_id?: number; item_id?: string };
}

const ChecklistCompletionPage: React.FC = () => {
  const { checklistId, completionId } = useParams<{ checklistId: string; completionId: string }>();
  const navigate = useNavigate();
  const notifications = useNotifications();

  const [checklist, setChecklist] = useState<Checklist | null>(null);
  const [completion, setCompletion] = useState<ChecklistCompletion | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);
  const [showScanner, setShowScanner] = useState(false);
  const [pendingScan, setPendingScan] = useState<PendingScan | null>(null);
  const [photoFile, setPhotoFile] = useState<File | null>(null);
  const [photoPreview, setPhotoPreview] = useState<string | null>(null);
  const [photoCaption, setPhotoCaption] = useState('');
  const [submittingPhoto, setSubmittingPhoto] = useState(false);

  const loadData = useCallback(async () => {
    if (!checklistId || !completionId) return;

    try {
      setLoading(true);
      const [checklistResponse, completionResponse] = await Promise.all([
        checklistsAPI.getChecklist(checklistId),
        checklistsAPI.getCompletion(completionId),
      ]);
      setChecklist(checklistResponse.data);
      setCompletion(completionResponse.data);
      setError(null);
    } catch (err: any) {
      setError(extractErrorMessage(err, 'Failed to load checklist'));
      console.error('Error loading checklist:', err);
    } finally {
      setLoading(false);
    }
  }, [checklistId, completionId]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const processScannedCode = async (code: string) => {
    if (!completion) return;

    try {
      setScanning(true);
      // Resolve the scan through the unified scanner dispatcher, which handles
      // our QR URLs, UPC barcodes, and location codes.
      const lookupResponse = await scannerAPI.dispatch(code.trim());
      const { target_type: type, target_id: id } = lookupResponse.data;

      if (!id) {
        notifications.showWarning(
          'Code not recognized',
          lookupResponse.data.message || "Couldn't identify this scan.",
        );
        return;
      }

      // Find the current step that needs to be scanned
      const completedStepIds = new Set(completion.step_completions.map(sc => sc.step));
      const nextStep = checklist?.steps
        .filter(step => !completedStepIds.has(step.id))
        .sort((a, b) => a.step_number - b.step_number)[0];

      if (!nextStep) {
        notifications.showInfo('All steps completed', 'All steps are completed!');
        return;
      }

      // Verify the scanned item matches the step
      let matches = false;
      let scannedItem: { asset_id?: string; location_id?: number; item_id?: string } = {};

      if (type === 'asset' && nextStep.asset === id) {
        matches = true;
        scannedItem = { asset_id: id };
      } else if (type === 'location' && nextStep.location === parseInt(id)) {
        matches = true;
        scannedItem = { location_id: parseInt(id) };
      } else if (type === 'inventory_item' && nextStep.inventory_item === id) {
        matches = true;
        scannedItem = { item_id: id };
      }

      if (!matches) {
        notifications.showWarning('Wrong QR Code', `This QR code doesn't match the next step. Please scan the correct item.`);
        return;
      }

      if (nextStep.requires_photo) {
        setPendingScan({ step: nextStep, scannedItem });
        setPhotoFile(null);
        setPhotoPreview(null);
        setPhotoCaption('');
        return;
      }

      // Record the scan
      await checklistsAPI.scanStep(completionId!, nextStep.id, scannedItem);

      // Reload and prompt for completion
      await finalizeScan();
    } catch (err: any) {
      notifications.showError('Failed to scan code', extractErrorMessage(err, ''));
      console.error('Error scanning code:', err);
    } finally {
      setScanning(false);
    }
  };

  const finalizeScan = async () => {
    await loadData();

    const updatedCompletion = await checklistsAPI.getCompletion(completionId!);
    const allRequiredComplete =
      updatedCompletion.data.required_steps_completed >= updatedCompletion.data.required_steps_total;

    if (allRequiredComplete) {
      confirmAction(
        'Finish checklist?',
        'All required steps are complete! Would you like to finish the checklist?',
        async () => {
          try {
            await checklistsAPI.completeChecklist(completionId!);
            await loadData();
            notifications.showSuccess('Checklist Completed', 'Checklist completed successfully!');
          } catch (err: any) {
            notifications.showError('Failed to complete checklist', extractErrorMessage(err, ''));
            console.error('Error completing checklist:', err);
          }
        },
        { labels: { confirm: 'Finish', cancel: 'Not yet' } },
      );
    }
  };

  const handlePhotoSelect = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0] || null;
    setPhotoFile(file);
    setPhotoPreview(file ? URL.createObjectURL(file) : null);
  };

  const handleCancelPhoto = () => {
    if (photoPreview) URL.revokeObjectURL(photoPreview);
    setPendingScan(null);
    setPhotoFile(null);
    setPhotoPreview(null);
    setPhotoCaption('');
  };

  const handleSubmitPhoto = async () => {
    if (!pendingScan || !photoFile || !completionId) return;
    try {
      setSubmittingPhoto(true);
      await checklistsAPI.scanStep(
        completionId,
        pendingScan.step.id,
        pendingScan.scannedItem,
        undefined,
        { file: photoFile, caption: photoCaption },
      );
      if (photoPreview) URL.revokeObjectURL(photoPreview);
      setPendingScan(null);
      setPhotoFile(null);
      setPhotoPreview(null);
      setPhotoCaption('');
      await finalizeScan();
    } catch (err: any) {
      notifications.showError('Failed to upload photo', extractErrorMessage(err, ''));
      console.error('Error uploading photo:', err);
    } finally {
      setSubmittingPhoto(false);
    }
  };

  const handleScanCode = () => {
    setShowScanner(true);
    // Prevent body scroll when scanner is open
    document.body.classList.add('qr-scanner-open');
  };

  const handleScanSuccess = (decodedText: string) => {
    setShowScanner(false);
    document.body.classList.remove('qr-scanner-open');
    processScannedCode(decodedText);
  };

  const handleScanError = (scanError: QRScannerError) => {
    const titleByKind: Record<QRScannerError['kind'], string> = {
      'permission-denied': 'Camera access denied',
      'no-camera': 'No camera detected',
      'insecure-context': 'HTTPS required',
      unsupported: 'Browser not supported',
      unknown: 'Scan error',
    };
    notifications.showError(titleByKind[scanError.kind], scanError.message);
  };

  const handleCloseScanner = () => {
    setShowScanner(false);
    document.body.classList.remove('qr-scanner-open');
  };

  const handleCompleteChecklist = () => {
    if (!completionId) return;

    confirmAction(
      'Complete checklist?',
      'Are you sure you want to complete this checklist?',
      async () => {
        try {
          await checklistsAPI.completeChecklist(completionId);
          await loadData();
          notifications.showSuccess('Checklist Completed', 'Checklist completed successfully!');
        } catch (err: any) {
          notifications.showError('Failed to complete checklist', extractErrorMessage(err, ''));
          console.error('Error completing checklist:', err);
        }
      },
    );
  };

  if (loading) {
    return (
      <div className="scan-page">
        <div className="loading">Loading checklist...</div>
      </div>
    );
  }

  if (error || !checklist || !completion) {
    return (
      <div className="scan-page">
        <div className="error">
          <h2>Error</h2>
          <p>{error || 'Checklist not found'}</p>
          <button onClick={() => navigate('/')}>Go Home</button>
        </div>
      </div>
    );
  }

  const completedStepIds = new Set(completion.step_completions.map(sc => sc.step));
  const allRequiredComplete = completion.required_steps_completed >= completion.required_steps_total;

  return (
    <>
      {showScanner && (
        <QRScanner
          onScanSuccess={handleScanSuccess}
          onScanError={handleScanError}
          onClose={handleCloseScanner}
        />
      )}
      {pendingScan && (
        <div
          role="dialog"
          aria-label="Photo required"
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'rgba(0,0,0,0.6)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
            padding: '16px',
          }}
        >
          <div
            style={{
              backgroundColor: 'white',
              borderRadius: '8px',
              padding: '20px',
              maxWidth: '480px',
              width: '100%',
            }}
          >
            <h3 style={{ marginTop: 0 }}>Photo required</h3>
            <p style={{ marginBottom: '12px' }}>
              Step {pendingScan.step.step_number}: {pendingScan.step.name}
            </p>
            <input
              type="file"
              accept="image/*"
              capture="environment"
              onChange={handlePhotoSelect}
              data-testid="checklist-photo-input"
            />
            {photoPreview && (
              <div style={{ marginTop: '10px' }}>
                <img
                  src={photoPreview}
                  alt="Selected"
                  style={{
                    maxWidth: '100%',
                    maxHeight: '240px',
                    borderRadius: '4px',
                    border: '1px solid #ccc',
                  }}
                />
              </div>
            )}
            <label
              htmlFor="checklist-photo-caption"
              style={{ display: 'block', marginTop: '12px', fontSize: '0.9em' }}
            >
              Caption (optional)
            </label>
            <input
              id="checklist-photo-caption"
              type="text"
              value={photoCaption}
              maxLength={500}
              onChange={(e) => setPhotoCaption(e.target.value)}
              style={{
                width: '100%',
                padding: '8px',
                marginTop: '4px',
                border: '1px solid #ccc',
                borderRadius: '4px',
                boxSizing: 'border-box',
              }}
            />
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginTop: '16px' }}>
              <button
                type="button"
                onClick={handleCancelPhoto}
                disabled={submittingPhoto}
                style={{
                  padding: '8px 16px',
                  backgroundColor: '#eee',
                  border: 'none',
                  borderRadius: '4px',
                  cursor: 'pointer',
                }}
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleSubmitPhoto}
                disabled={!photoFile || submittingPhoto}
                style={{
                  padding: '8px 16px',
                  backgroundColor: !photoFile || submittingPhoto ? '#ccc' : '#007bff',
                  color: 'white',
                  border: 'none',
                  borderRadius: '4px',
                  cursor: !photoFile || submittingPhoto ? 'not-allowed' : 'pointer',
                }}
              >
                {submittingPhoto ? 'Uploading…' : 'Submit'}
              </button>
            </div>
          </div>
        </div>
      )}
      <div className="scan-page">
        <div className="item-card">
        <div className="item-header">
          <div className="item-title-section">
            <h1>{checklist.name}</h1>
            <p className="description">{checklist.description}</p>
          </div>
        </div>

        <div className="item-details">
          <div style={{ marginBottom: '20px' }}>
            <h3>Progress</h3>
            <p>
              {completion.completed_steps_count} of {completion.total_steps_count} steps completed
              {completion.required_steps_total > 0 && (
                <span> ({completion.required_steps_completed} of {completion.required_steps_total} required)</span>
              )}
            </p>
            <div style={{ width: '100%', backgroundColor: '#e0e0e0', borderRadius: '4px', height: '20px', marginTop: '10px' }}>
              <div
                style={{
                  width: `${(completion.completed_steps_count / completion.total_steps_count) * 100}%`,
                  backgroundColor: '#4caf50',
                  height: '100%',
                  borderRadius: '4px',
                  transition: 'width 0.3s',
                }}
              />
            </div>
          </div>

          <div style={{ marginBottom: '20px' }}>
            <h3>Steps</h3>
            <ol style={{ paddingLeft: '20px' }}>
              {checklist.steps.map((step) => {
                const isCompleted = completedStepIds.has(step.id);
                const stepCompletion = completion.step_completions.find(sc => sc.step === step.id);

                return (
                  <li
                    key={step.id}
                    style={{
                      marginBottom: '15px',
                      padding: '10px',
                      backgroundColor: isCompleted ? '#e8f5e9' : '#fff3e0',
                      border: '1px solid #ddd',
                      borderRadius: '4px',
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <div style={{ flex: 1 }}>
                        <strong>
                          Step {step.step_number}: {step.name}
                          {step.required && <span style={{ color: 'red' }}> *</span>}
                          {step.requires_photo && (
                            <span
                              style={{ marginLeft: '6px', fontSize: '0.8em', color: '#1976d2' }}
                              title="Photo required"
                            >
                              📷
                            </span>
                          )}
                        </strong>
                        {step.notes && <p style={{ marginTop: '5px', fontSize: '0.9em' }}>{step.notes}</p>}
                        {step.asset && <p style={{ marginTop: '5px', fontSize: '0.9em', color: '#666' }}>Asset: {step.asset}</p>}
                        {step.location && <p style={{ marginTop: '5px', fontSize: '0.9em', color: '#666' }}>Location: {step.location}</p>}
                        {step.inventory_item && <p style={{ marginTop: '5px', fontSize: '0.9em', color: '#666' }}>Item: {step.inventory_item}</p>}
                        {stepCompletion && (
                          <p style={{ marginTop: '5px', fontSize: '0.9em', color: '#4caf50' }}>
                            ✓ Completed at {new Date(stepCompletion.scanned_at).toLocaleString()}
                          </p>
                        )}
                        {stepCompletion?.photo_url && (
                          <div style={{ marginTop: '8px' }}>
                            <a href={stepCompletion.photo_url} target="_blank" rel="noopener noreferrer">
                              <img
                                src={stepCompletion.photo_url}
                                alt={stepCompletion.photo_caption || 'Step photo'}
                                style={{
                                  maxWidth: '160px',
                                  maxHeight: '160px',
                                  borderRadius: '4px',
                                  border: '1px solid #ccc',
                                }}
                              />
                            </a>
                            {stepCompletion.photo_caption && (
                              <p style={{ fontSize: '0.85em', color: '#555', marginTop: '4px' }}>
                                {stepCompletion.photo_caption}
                              </p>
                            )}
                          </div>
                        )}
                      </div>
                      <div style={{ marginLeft: '10px' }}>
                        {isCompleted ? (
                          <span style={{ color: '#4caf50', fontSize: '1.5em' }}>✓</span>
                        ) : (
                          <span style={{ color: '#ff9800' }}>○</span>
                        )}
                      </div>
                    </div>
                  </li>
                );
              })}
            </ol>
          </div>

          <div style={{ marginTop: '20px', textAlign: 'center' }}>
            <button
              onClick={handleScanCode}
              disabled={scanning || allRequiredComplete}
              style={{
                padding: '12px 24px',
                fontSize: '16px',
                backgroundColor: scanning ? '#ccc' : '#007bff',
                color: 'white',
                border: 'none',
                borderRadius: '4px',
                cursor: scanning ? 'not-allowed' : 'pointer',
                marginRight: '10px',
              }}
            >
              {scanning ? 'Scanning...' : 'Scan QR Code'}
            </button>

            {allRequiredComplete && (
              <button
                onClick={handleCompleteChecklist}
                disabled={completion.status === 'completed'}
                style={{
                  padding: '12px 24px',
                  fontSize: '16px',
                  backgroundColor: completion.status === 'completed' ? '#ccc' : '#4caf50',
                  color: 'white',
                  border: 'none',
                  borderRadius: '4px',
                  cursor: completion.status === 'completed' ? 'not-allowed' : 'pointer',
                }}
              >
                {completion.status === 'completed' ? 'Completed' : 'Complete Checklist'}
              </button>
            )}
          </div>

          {completion.status === 'completed' && (
            <div style={{ marginTop: '20px', padding: '15px', backgroundColor: '#e8f5e9', borderRadius: '4px' }}>
              <h3 style={{ color: '#4caf50' }}>✓ Checklist Completed</h3>
              <p>Completed at: {completion.completed_at ? new Date(completion.completed_at).toLocaleString() : 'N/A'}</p>
            </div>
          )}
        </div>
      </div>
      </div>
    </>
  );
};

export default ChecklistCompletionPage;

