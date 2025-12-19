/**
 * Location QR Code Scan Page
 * Allows users to:
 * - Check in to a location (volunteer/contractor/anonymous)
 * - Submit feedback about the location
 * - Report security concerns
 * - View and complete tasks (authenticated users)
 */
import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useNotifications } from '../hooks/useNotifications';
import { checklistsAPI, inventoryAPI, locationCheckinAPI } from '../services/api';
import '../styles/ScanPage.css';
import { Checklist } from '../types';

interface Location {
  id: string;
  name: string;
  description: string;
  is_active: boolean;
}

type TabType = 'checkin' | 'feedback' | 'security' | 'tasks';

const LocationScanPage: React.FC = () => {
  const { locationId } = useParams<{ locationId: string }>();
  const navigate = useNavigate();
  const notifications = useNotifications();

  // Authentication state
  const [isLoggedIn] = useState<boolean>(() => !!localStorage.getItem('token'));

  // Data state
  const [location, setLocation] = useState<Location | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<TabType>('checkin');
  const [submitted, setSubmitted] = useState(false);
  const [checklists, setChecklists] = useState<Checklist[]>([]);

  // Form states
  const [checkinType, setCheckinType] = useState<'volunteer' | 'contractor' | 'anonymous'>('anonymous');
  const [checkinNotes, setCheckinNotes] = useState('');
  const [feedbackType, setFeedbackType] = useState<'positive' | 'neutral' | 'negative'>('neutral');
  const [feedbackMessage, setFeedbackMessage] = useState('');
  const [reportType, setReportType] = useState<'cleaning' | 'safety' | null>(null);
  const [isUrgent, setIsUrgent] = useState(false);
  const [reportDescription, setReportDescription] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const loadLocation = useCallback(async () => {
    if (!locationId) return;

    try {
      setLoading(true);
      // Get location from inventory API
      const response = await inventoryAPI.getLocation(locationId);
      setLocation(response.data);
      setError(null);
    } catch (err: any) {
      setError(err.response?.data?.detail || err.response?.data?.error || 'Failed to load location');
      console.error('Error loading location:', err);
    } finally {
      setLoading(false);
    }
  }, [locationId]);

  const loadChecklists = useCallback(async () => {
    if (!locationId) return;
    try {
      const checklistsResponse = await inventoryAPI.getLocationChecklists(locationId);
      setChecklists(checklistsResponse.data);
    } catch (err: any) {
      console.error('Error loading checklists:', err);
    }
  }, [locationId]);

  useEffect(() => {
    if (locationId) {
      loadLocation();
      loadChecklists();
    }
  }, [locationId, loadLocation, loadChecklists]);

  const handleStartChecklist = async (checklistId: string) => {
    try {
      // If logged in, use the username from localStorage; otherwise prompt
      let userName: string | undefined;
      if (isLoggedIn) {
        userName = localStorage.getItem('username') || undefined;
      } else {
        const promptResult = prompt('Enter your name (optional):');
        userName = promptResult || undefined;
      }
      const completion = await checklistsAPI.startChecklist(checklistId, userName);
      navigate(`/checklist/${checklistId}/complete/${completion.data.id}`);
    } catch (err: any) {
      notifications.showError('Failed to start checklist', err.response?.data?.detail);
    }
  };

  const handleCheckin = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!location) return;

    try {
      setSubmitting(true);
      await locationCheckinAPI.checkin(location.id, {
        checkin_type: isLoggedIn ? checkinType : 'anonymous',
        notes: checkinNotes,
      });
      setSubmitted(true);
      setTimeout(() => {
        setSubmitted(false);
        setCheckinNotes('');
      }, 3000);
    } catch (err: any) {
      notifications.showError('Failed to check in', err.response?.data?.error);
      console.error('Error checking in:', err);
    } finally {
      setSubmitting(false);
    }
  };

  const handleFeedback = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!location || !feedbackMessage.trim()) {
      notifications.showWarning('Validation Error', 'Please enter a feedback message');
      return;
    }

    try {
      setSubmitting(true);
      await locationCheckinAPI.submitFeedback(location.id, {
        feedback_type: feedbackType,
        message: feedbackMessage,
      });
      setSubmitted(true);
      setTimeout(() => {
        setSubmitted(false);
        setFeedbackMessage('');
        setFeedbackType('neutral');
      }, 3000);
    } catch (err: any) {
      notifications.showError('Failed to submit feedback', err.response?.data?.error);
      console.error('Error submitting feedback:', err);
    } finally {
      setSubmitting(false);
    }
  };

  const handleReport = async (type: 'cleaning' | 'safety') => {
    if (!location) return;

    try {
      setSubmitting(true);
      await locationCheckinAPI.reportSecurity(location.id, {
        report_type: type,
        is_urgent: isUrgent,
        description: reportDescription,
      });
      setSubmitted(true);
      setTimeout(() => {
        setSubmitted(false);
        setReportType(null);
        setIsUrgent(false);
        setReportDescription('');
      }, 3000);
    } catch (err: any) {
      alert(err.response?.data?.error || 'Failed to submit report');
      console.error('Error submitting report:', err);
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <div className="scan-page">
        <div className="loading-container">
          <div className="loading-spinner"></div>
          <p>Loading location details...</p>
        </div>
      </div>
    );
  }

  if (error || !location) {
    return (
      <div className="scan-page">
        <div className="error-container">
          <h1>Error</h1>
          <p>{error || 'Location not found'}</p>
          <button onClick={() => navigate('/')}>Go Home</button>
        </div>
      </div>
    );
  }

  return (
    <div className="scan-page">
      <div className="scan-container">
        <div className="item-header">
          <h1>{location.name}</h1>
          {location.description && <p className="item-description">{location.description}</p>}
        </div>

        <div className="tabs">
          <button
            className={activeTab === 'checkin' ? 'active' : ''}
            onClick={() => setActiveTab('checkin')}
          >
            Check In
          </button>
          <button
            className={activeTab === 'feedback' ? 'active' : ''}
            onClick={() => setActiveTab('feedback')}
          >
            Feedback
          </button>
          <button
            className={activeTab === 'security' ? 'active' : ''}
            onClick={() => setActiveTab('security')}
          >
            Report Issue
          </button>
          {isLoggedIn && (
            <button
              className={activeTab === 'tasks' ? 'active' : ''}
              onClick={() => setActiveTab('tasks')}
            >
              Tasks
            </button>
          )}
        </div>

        {submitted && (
          <div className="success-message">
            <p>✓ Thank you! Your submission has been recorded.</p>
          </div>
        )}

        {/* Checklists Section */}
        {checklists.length > 0 && (
          <div className="checklists-section" style={{ marginTop: '20px', padding: '15px', border: '1px solid #ddd', borderRadius: '5px', marginBottom: '20px' }}>
            <h3>Are you completing a checklist?</h3>
            <p>This location is part of the following checklists:</p>
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

        {activeTab === 'checkin' && (
          <form onSubmit={handleCheckin} className="scan-form">
            <h2>Check In to {location.name}</h2>
            {isLoggedIn && (
              <div className="form-group">
                <label>Check-in Type</label>
                <select
                  value={checkinType}
                  onChange={(e) => setCheckinType(e.target.value as any)}
                >
                  <option value="volunteer">Volunteer</option>
                  <option value="contractor">Contractor</option>
                  <option value="anonymous">Anonymous</option>
                </select>
              </div>
            )}
            <div className="form-group">
              <label>Notes (optional)</label>
              <textarea
                value={checkinNotes}
                onChange={(e) => setCheckinNotes(e.target.value)}
                placeholder="Any notes about your check-in..."
                rows={3}
              />
            </div>
            <button type="submit" disabled={submitting} className="submit-button">
              {submitting ? 'Submitting...' : 'Check In'}
            </button>
          </form>
        )}

        {activeTab === 'feedback' && (
          <form onSubmit={handleFeedback} className="scan-form">
            <h2>Submit Feedback</h2>
            <div className="form-group">
              <label>Feedback Type</label>
              <select
                value={feedbackType}
                onChange={(e) => setFeedbackType(e.target.value as any)}
              >
                <option value="positive">Positive</option>
                <option value="neutral">Neutral</option>
                <option value="negative">Negative</option>
              </select>
            </div>
            <div className="form-group">
              <label>Your Feedback</label>
              <textarea
                value={feedbackMessage}
                onChange={(e) => setFeedbackMessage(e.target.value)}
                placeholder="Share your thoughts about this location..."
                rows={5}
                required
              />
            </div>
            <button type="submit" disabled={submitting} className="submit-button">
              {submitting ? 'Submitting...' : 'Submit Feedback'}
            </button>
          </form>
        )}

        {activeTab === 'security' && (
          <div className="scan-form">
            <h2>Report an Issue</h2>
            <p>Select the type of issue you'd like to report:</p>

            <div className="report-buttons">
              <button
                type="button"
                className={`report-type-button ${reportType === 'cleaning' ? 'selected' : ''}`}
                onClick={() => setReportType('cleaning')}
                disabled={submitting}
              >
                🧹 This area needs cleaning
              </button>
              <button
                type="button"
                className={`report-type-button ${reportType === 'safety' ? 'selected' : ''}`}
                onClick={() => setReportType('safety')}
                disabled={submitting}
              >
                ⚠️ This area has a safety concern
              </button>
            </div>

            {reportType && (
              <>
                <div className="form-group">
                  <label>
                    <input
                      type="checkbox"
                      checked={isUrgent}
                      onChange={(e) => setIsUrgent(e.target.checked)}
                      disabled={submitting}
                    />
                    Needs immediate attention
                  </label>
                </div>
                <div className="form-group">
                  <label>Additional details (optional)</label>
                  <textarea
                    value={reportDescription}
                    onChange={(e) => setReportDescription(e.target.value)}
                    placeholder="Add any additional details about the issue..."
                    rows={4}
                  />
                </div>
                <div className="form-actions">
                  <button
                    type="button"
                    onClick={() => handleReport(reportType)}
                    disabled={submitting}
                    className="submit-button"
                  >
                    {submitting ? 'Submitting...' : 'Submit Report'}
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setReportType(null);
                      setIsUrgent(false);
                      setReportDescription('');
                    }}
                    disabled={submitting}
                    className="cancel-button"
                  >
                    Cancel
                  </button>
                </div>
              </>
            )}
          </div>
        )}

        {activeTab === 'tasks' && isLoggedIn && (
          <LocationTasksTab locationId={location.id} />
        )}
      </div>
    </div>
  );
};

// Tasks tab component for authenticated users
const LocationTasksTab: React.FC<{ locationId: string }> = ({ locationId }) => {
  const notifications = useNotifications();
  const [tasks, setTasks] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [completing, setCompleting] = useState<string | null>(null);

  const loadTasks = useCallback(async () => {
    try {
      setLoading(true);
      const response = await locationCheckinAPI.getTasks({ location: locationId });
      setTasks(response.data.results || response.data || []);
    } catch (err) {
      console.error('Error loading tasks:', err);
    } finally {
      setLoading(false);
    }
  }, [locationId]);

  useEffect(() => {
    loadTasks();
  }, [loadTasks]);

  const handleCompleteTask = async (taskId: string) => {
    try {
      setCompleting(taskId);
      await locationCheckinAPI.completeTask(taskId);
      await loadTasks();
    } catch (err: any) {
      notifications.showError('Failed to complete task', err.response?.data?.error);
    } finally {
      setCompleting(null);
    }
  };

  if (loading) {
    return <div className="loading-container"><p>Loading tasks...</p></div>;
  }

  if (tasks.length === 0) {
    return <div className="empty-state"><p>No tasks available for this location.</p></div>;
  }

  return (
    <div className="tasks-container">
      <h2>Location Tasks</h2>
      <div className="tasks-list">
        {tasks.map((task) => (
          <div key={task.id} className="task-item">
            <h3>{task.title}</h3>
            <p>{task.description}</p>
            <div className="task-meta">
              <span className={`status status-${task.status}`}>{task.status}</span>
              {task.status !== 'completed' && (
                <button
                  onClick={() => handleCompleteTask(task.id)}
                  disabled={completing === task.id}
                  className="complete-button"
                >
                  {completing === task.id ? 'Completing...' : 'Mark Complete'}
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default LocationScanPage;
