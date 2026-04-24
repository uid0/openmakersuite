/**
 * Checklist Completion Page
 * Shows checklist steps and allows users to scan QR codes to complete steps
 */
import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import QRScanner from '../components/QRScanner';
import { useNotifications } from '../hooks/useNotifications';
import { checklistsAPI, inventoryAPI } from '../services/api';
import '../styles/ScanPage.css';
import { Checklist, ChecklistCompletion } from '../types';
import { confirmAction } from '../utils/dialogs';

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
      setError(err.response?.data?.detail || err.response?.data?.error || 'Failed to load checklist');
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
      // Extract code from URL if it's a full URL
      let codeToUse = code;
      if (code.includes('/')) {
        // Extract the last part of the URL path
        const urlParts = code.split('/');
        codeToUse = urlParts[urlParts.length - 1];
      }

      // Look up the code
      const lookupResponse = await inventoryAPI.lookupByCode(codeToUse.toUpperCase());
      const { type, id } = lookupResponse.data;

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
      } else if (type === 'item' && nextStep.inventory_item === id) {
        matches = true;
        scannedItem = { item_id: id };
      }

      if (!matches) {
        notifications.showWarning('Wrong QR Code', `This QR code doesn't match the next step. Please scan the correct item.`);
        return;
      }

      // Record the scan
      await checklistsAPI.scanStep(completionId!, nextStep.id, scannedItem);
      
      // Reload data
      await loadData();

      // Check if all required steps are complete
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
              notifications.showError('Failed to complete checklist', err.response?.data?.detail);
              console.error('Error completing checklist:', err);
            }
          },
          { labels: { confirm: 'Finish', cancel: 'Not yet' } },
        );
      }
    } catch (err: any) {
      notifications.showError('Failed to scan code', err.response?.data?.detail);
      console.error('Error scanning code:', err);
    } finally {
      setScanning(false);
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

  const handleScanError = (error: string) => {
    notifications.showError('Scan Error', error);
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
          notifications.showError('Failed to complete checklist', err.response?.data?.detail);
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

