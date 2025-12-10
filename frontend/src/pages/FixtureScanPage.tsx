/**
 * Fixture QR Code Scan Page
 * - Unauthenticated users: Auto-submit refill request → thanks page
 * - Authenticated users: Form to report issue with additional info or resolve (if admin)
 */
import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useNotifications } from '../hooks/useNotifications';
import { fixturesAPI } from '../services/api';
import '../styles/ScanPage.css';
import { Fixture, FixtureRefillRequest } from '../types';

const FixtureScanPage: React.FC = () => {
  const { fixtureId } = useParams<{ fixtureId: string }>();
  const navigate = useNavigate();
  const notifications = useNotifications();

  // Authentication state
  const [isLoggedIn] = useState<boolean>(() => !!localStorage.getItem('token'));
  const [isAdmin] = useState<boolean>(() => localStorage.getItem('is_staff') === 'true');

  // Data state
  const [fixture, setFixture] = useState<Fixture | null>(null);
  const [pendingRequests, setPendingRequests] = useState<FixtureRefillRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Form state (authenticated users)
  const [notes, setNotes] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [resolving, setResolving] = useState(false);

  const loadFixture = useCallback(async () => {
    try {
      setLoading(true);
      const fixtureResponse = await fixturesAPI.getFixture(fixtureId!);
      setFixture(fixtureResponse.data);

      // Load pending requests for authenticated users
      if (isLoggedIn) {
        try {
          const requestsResponse = await fixturesAPI.listRequests({
            fixture: fixtureId,
            status: 'pending',
          });
          setPendingRequests(requestsResponse.data.results);
        } catch (err) {
          // If we can't load requests, continue anyway
          console.error('Error loading pending requests:', err);
        }
      }

      setError(null);
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to load fixture');
      console.error('Error loading fixture:', err);
    } finally {
      setLoading(false);
    }
  }, [fixtureId, isLoggedIn]);

  useEffect(() => {
    if (fixtureId) {
      loadFixture();
    }
  }, [fixtureId, loadFixture]);

  // Auto-submit refill request for non-logged users
  useEffect(() => {
    const autoSubmitRefill = async () => {
      if (!isLoggedIn && fixture && !submitting && !submitted && fixture.is_active) {
        try {
          setSubmitting(true);
          await fixturesAPI.scanFixture(fixture.id);
          setSubmitted(true);
          // Redirect to thanks page after a short delay
          setTimeout(() => {
            navigate('/thanks');
          }, 2000);
        } catch (err: any) {
          console.error('Error auto-submitting refill request:', err);
          setError(err.response?.data?.error || 'Failed to submit refill request');
          setSubmitting(false);
        }
      }
    };

    autoSubmitRefill();
  }, [isLoggedIn, fixture, submitting, submitted, navigate]);

  const handleSubmitRefill = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!fixture || !isLoggedIn) return;

    if (!fixture.is_active) {
      alert('This fixture is inactive and cannot be refilled.');
      return;
    }

    try {
      setSubmitting(true);
      await fixturesAPI.scanFixture(fixture.id, notes || undefined);
      setSubmitted(true);
      setNotes('');
      // Reload fixture to get updated pending requests count
      await loadFixture();
    } catch (err: any) {
      notifications.showError('Failed to submit refill request', err.response?.data?.error);
      console.error('Error submitting refill request:', err);
    } finally {
      setSubmitting(false);
    }
  };

  const handleResolveAll = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!fixture || !isAdmin) return;

    if (!window.confirm('Mark all pending refill requests for this fixture as completed?')) {
      return;
    }

    try {
      setResolving(true);
      await fixturesAPI.resolveFixtureRequest(fixture.id, notes || undefined);
      setSubmitted(true);
      // Reload fixture to get updated data
      await loadFixture();
      setNotes('');
    } catch (err: any) {
      notifications.showError('Failed to resolve requests', err.response?.data?.error);
      console.error('Error resolving requests:', err);
    } finally {
      setResolving(false);
    }
  };

  if (loading) {
    return (
      <div className="scan-page">
        <div className="loading">
          <h2>Loading fixture details...</h2>
        </div>
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

  if (!fixture) {
    return (
      <div className="scan-page">
        <div className="error">
          <h2>Fixture not found</h2>
          <button onClick={() => navigate('/')}>Go Home</button>
        </div>
      </div>
    );
  }

  if (!fixture.is_active) {
    return (
      <div className="scan-page">
        <div className="error">
          <h2>Fixture Inactive</h2>
          <p>This fixture is currently inactive and cannot be refilled.</p>
          <button onClick={() => navigate('/')}>Go Home</button>
        </div>
      </div>
    );
  }

  if (submitted && !isLoggedIn) {
    return (
      <div className="scan-page">
        <div className="success">
          <h2>✓ Refill Request Submitted</h2>
          <p>Your refill request for <strong>{fixture.name}</strong> has been submitted.</p>
          <p>The logistics team will be notified.</p>
          <p className="redirect-message">Redirecting to home...</p>
        </div>
      </div>
    );
  }

  if (submitted && isLoggedIn) {
    return (
      <div className="scan-page">
        <div className="success">
          <h2>✓ {isAdmin ? 'Requests Resolved' : 'Refill Request Submitted'}</h2>
          <p>
            {isAdmin
              ? `All pending refill requests for ${fixture.name} have been marked as completed.`
              : `Your refill request for ${fixture.name} has been submitted.`}
          </p>
          <button onClick={() => navigate('/')}>Go Home</button>
        </div>
      </div>
    );
  }

  return (
    <div className="scan-page">
      <div className="item-card">
        <div className="item-header">
          <div className="item-title-section">
            <h1>{fixture.name}</h1>
            {fixture.asset_tag && <p className="sku">Asset Tag: {fixture.asset_tag}</p>}
          </div>
        </div>

        <div className="item-details">
          {fixture.description && <p className="description">{fixture.description}</p>}

          <div className="info-grid">
            <div className="info-item">
              <span className="label">Location:</span>
              <span className="value">{fixture.location_name}</span>
            </div>

            <div className="info-item">
              <span className="label">Refill Item:</span>
              <span className="value">{fixture.refill_item_name}</span>
            </div>

            {fixture.refill_item_sku && (
              <div className="info-item">
                <span className="label">Refill Item SKU:</span>
                <span className="value">{fixture.refill_item_sku}</span>
              </div>
            )}

            {fixture.pending_requests_count > 0 && (
              <div className="info-item">
                <span className="label">Pending Requests:</span>
                <span className="value low-stock">{fixture.pending_requests_count}</span>
              </div>
            )}
          </div>

          {isLoggedIn && (
            <div className="form-section">
              {pendingRequests.length > 0 && (
                <div className="pending-requests">
                  <h3>Pending Refill Requests</h3>
                  <ul>
                    {pendingRequests.map((request) => (
                      <li key={request.id}>
                        <strong>Requested:</strong> {new Date(request.requested_at).toLocaleString()}
                        {request.requested_by && (
                          <> by <strong>{request.requested_by}</strong></>
                        )}
                        {request.notes && (
                          <>
                            <br />
                            <em>{request.notes}</em>
                          </>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {isAdmin ? (
                <form onSubmit={handleResolveAll} className="refill-form">
                  <h3>Resolve All Pending Requests</h3>
                  <p>Mark all pending refill requests for this fixture as completed.</p>

                  <div className="form-group">
                    <label htmlFor="resolve-notes">Notes (optional):</label>
                    <textarea
                      id="resolve-notes"
                      value={notes}
                      onChange={(e) => setNotes(e.target.value)}
                      placeholder="Add any notes about resolving these requests..."
                      rows={3}
                    />
                  </div>

                  <div className="form-actions">
                    <button type="submit" disabled={resolving} className="btn-primary">
                      {resolving ? 'Resolving...' : 'Resolve All Requests'}
                    </button>
                    <button
                      type="button"
                      onClick={handleSubmitRefill}
                      disabled={submitting}
                      className="btn-secondary"
                    >
                      {submitting ? 'Submitting...' : 'Submit New Request Instead'}
                    </button>
                  </div>
                </form>
              ) : (
                <form onSubmit={handleSubmitRefill} className="refill-form">
                  <h3>Submit Refill Request</h3>
                  <p>Report that this fixture needs to be refilled.</p>

                  <div className="form-group">
                    <label htmlFor="refill-notes">Additional Information (optional):</label>
                    <textarea
                      id="refill-notes"
                      value={notes}
                      onChange={(e) => setNotes(e.target.value)}
                      placeholder="Add any additional information about the refill request..."
                      rows={3}
                    />
                  </div>

                  <div className="form-actions">
                    <button type="submit" disabled={submitting} className="btn-primary">
                      {submitting ? 'Submitting...' : 'Submit Refill Request'}
                    </button>
                  </div>
                </form>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default FixtureScanPage;
