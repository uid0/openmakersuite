/**
 * Logistics Dashboard - Optimised for FireTV displays in the logistics office.
 * Shows refill requests alongside pending purchase orders with auto-refresh,
 * wake-lock support, and large-format layout for TV readability.
 */
import React, { useEffect, useState } from 'react';
import '../styles/LogisticsDashboard.css';
import { analyticsAPI } from '../services/api';

interface RefillRequest {
  id: number;
  item_name: string;
  location: string;
  category: string | null;
  quantity_requested: number;
  priority: string;
  priority_label: string;
  status: string;
  status_label: string;
  requested_at: string | null;
  days_open: number;
  requested_by: string;
  public_notes: string;
  request_notes: string;
}

interface PendingOrder {
  id: number;
  po_number: string;
  supplier_name: string;
  status: string;
  status_label: string;
  sent_at: string | null;
  expected_delivery_date: string | null;
  days_since_ordered: number;
  total_items: number;
  total_quantity: number;
  received_quantity: number;
  progress_percent: number | null;
  estimated_total: number | null;
  updated_at: string | null;
}

interface LocationRequest {
  id: string;
  type: string;
  type_label: string;
  title: string;
  description: string;
  location: string;
  status: string;
  status_label: string;
  is_urgent: boolean;
  created_at: string | null;
  days_open: number;
}

interface LogisticsSummary {
  pending_requests: number;
  urgent_requests: number;
  awaiting_approval: number;
  pending_orders: number;
  open_order_lines: number;
  location_requests: number;
  urgent_location_requests: number;
}

interface LogisticsDashboardResponse {
  summary: LogisticsSummary;
  refill_requests: RefillRequest[];
  pending_orders: PendingOrder[];
  location_requests: LocationRequest[];
  last_updated: string;
}

const REFRESH_INTERVAL_MS = 45000;

const LogisticsDashboard: React.FC = () => {
  const [data, setData] = useState<LogisticsDashboardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshProgress, setRefreshProgress] = useState(0);
  const [currentTime, setCurrentTime] = useState(new Date());

  const fetchDashboardData = async () => {
    try {
      const response = await analyticsAPI.getLogisticsDashboard<LogisticsDashboardResponse>();
      setData(response.data);
      setError(null);
    } catch (err: any) {
      console.error('Failed to load logistics dashboard data', err);
      setError('Unable to load logistics data. Retrying...');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchDashboardData();
    const interval = setInterval(fetchDashboardData, REFRESH_INTERVAL_MS);
    return () => clearInterval(interval);
  }, []);

  // Update current time every second
  useEffect(() => {
    const timeInterval = setInterval(() => {
      setCurrentTime(new Date());
    }, 1000);
    return () => clearInterval(timeInterval);
  }, []);

  // Progress bar for refresh countdown
  useEffect(() => {
    if (!data) return;
    
    let startTime = Date.now();
    setRefreshProgress(0);
    
    const progressInterval = setInterval(() => {
      const elapsed = Date.now() - startTime;
      const progress = Math.min((elapsed / REFRESH_INTERVAL_MS) * 100, 100);
      setRefreshProgress(progress);
      
      if (progress >= 100) {
        startTime = Date.now();
        setRefreshProgress(0);
      }
    }, 100);
    
    return () => clearInterval(progressInterval);
  }, [data?.last_updated]);

  // Removed highlight rotation for TV display - no mouse interaction needed

  useEffect(() => {
    let wakeLockSentinel: any;

    const requestWakeLock = async () => {
      try {
        const anyNavigator = navigator as Navigator & { wakeLock?: any };
        if (anyNavigator.wakeLock && anyNavigator.wakeLock.request) {
          wakeLockSentinel = await anyNavigator.wakeLock.request('screen');
          wakeLockSentinel.addEventListener?.('release', () => {
            console.log('Screen wake lock released');
          });
        }
      } catch (wakeLockError) {
        console.warn('Wake lock request failed:', wakeLockError);
      }
    };

    requestWakeLock();

    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible' && !wakeLockSentinel) {
        requestWakeLock();
      }
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);

    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      if (wakeLockSentinel) {
        wakeLockSentinel.release?.();
      }
    };
  }, []);

  useEffect(() => {
    const originalCursor = document.body.style.cursor;
    document.body.style.cursor = 'none';
    return () => {
      document.body.style.cursor = originalCursor;
    };
  }, []);


  const formatDate = (value: string | null, fallback = '—') => {
    if (!value) return fallback;
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return fallback;
    return date.toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
    });
  };

  const formatTime = (value: string | null) => {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '—';
    return date.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
  };

  const formatCurrency = (value: number | null) => {
    if (value === null || Number.isNaN(value)) return '—';
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(
      value
    );
  };

  if (loading) {
    return (
      <div className="logistics-dashboard logistics-loading">
        <div className="logistics-loader">Loading Logistics Dashboard…</div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="logistics-dashboard logistics-error">
        <div className="logistics-error-card">
          <h1>🚧 Logistics Dashboard</h1>
          <p>{error}</p>
          <p className="logistics-error-tip">Ensure the backend is reachable at the configured API URL.</p>
        </div>
      </div>
    );
  }

  const { summary, refill_requests, pending_orders, location_requests } = data;
  const hasUrgentRequests = (summary.urgent_requests + summary.urgent_location_requests) > 0;

  return (
    <div className={`logistics-dashboard ${hasUrgentRequests ? 'urgent-background' : ''}`}>
      <section className="logistics-summary">
        <div className="summary-card emphasis">
          <span className="summary-label">Open Requests</span>
          <span className="summary-value">{summary.pending_requests}</span>
        </div>
        <div className="summary-card alert">
          <span className="summary-label">Urgent Requests</span>
          <span className="summary-value">{summary.urgent_requests + summary.urgent_location_requests}</span>
        </div>
        <div className="summary-card">
          <span className="summary-label">Location Requests</span>
          <span className="summary-value">{summary.location_requests}</span>
        </div>
        <div className="summary-card">
          <span className="summary-label">Open Purchase Orders</span>
          <span className="summary-value">{summary.pending_orders}</span>
        </div>
      </section>

      <div className="logistics-content">
        {/* Refill Requests Section */}
        <section className="refill-requests">
          <div className="section-heading">
            <h2>Refill Requests</h2>
            <span className="section-subtitle">Top priority items needing restock</span>
          </div>
          {refill_requests.length === 0 ? (
            <div className="empty-state">
              <p>No pending refill requests</p>
            </div>
          ) : (
            <div className="refill-grid tv-limited">
              {refill_requests.map((request) => (
                <div
                  key={request.id}
                  className={`refill-card priority-${request.priority}`}
                >
                  <div className="refill-card-header">
                    <h3>{request.item_name}</h3>
                    <div>
                      <span className={`badge status-${request.status} ${request.priority === 'urgent' ? 'urgent-badge' : ''}`}>
                        {request.priority_label}
                      </span>
                    </div>
                  </div>
                  <div className="refill-card-body">
                    <div className="refill-meta">
                      <div className="meta-block">
                        <span className="meta-label">Location</span>
                        <span className="meta-value">{request.location}</span>
                      </div>
                      <div className="meta-block">
                        <span className="meta-label">Quantity</span>
                        <span className="meta-value accent">{request.quantity_requested}</span>
                      </div>
                      {request.category && (
                        <div className="meta-block">
                          <span className="meta-label">Category</span>
                          <span className="meta-value">{request.category}</span>
                        </div>
                      )}
                      <div className="meta-block">
                        <span className="meta-label">Days Open</span>
                        <span className="meta-value">{request.days_open}</span>
                      </div>
                    </div>
                    {(request.request_notes || request.public_notes) && (
                      <div className="refill-notes">
                        <p>{request.request_notes || request.public_notes}</p>
                      </div>
                    )}
                  </div>
                  <div className="refill-card-footer">
                    <span>Status: {request.status_label}</span>
                    <span>Requested by: {request.requested_by}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* Pending Orders and Location Requests Section */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem', flex: 1, minHeight: 0, overflow: 'hidden' }}>
          {/* Pending Orders Section */}
          <section className="pending-orders">
            <div className="section-heading">
              <h2>Pending Orders</h2>
              <span className="section-subtitle">Orders awaiting delivery</span>
            </div>
            {pending_orders.length === 0 ? (
              <div className="empty-state">
                <p>No pending purchase orders</p>
              </div>
            ) : (
              <div className="orders-grid">
                {pending_orders.map((order) => (
                  <div key={order.id} className="order-card">
                    <div className="order-card-header">
                      <div>
                        <h3>{order.po_number}</h3>
                        <span className="order-supplier">{order.supplier_name}</span>
                      </div>
                      <span className={`badge status ${order.status}`}>
                        {order.status_label}
                      </span>
                    </div>
                    <div className="order-details">
                      <div className="order-meta">
                        <span>Items: {order.total_items}</span>
                        <span>Total Qty: {order.total_quantity}</span>
                        <span>Received: {order.received_quantity}</span>
                      </div>
                      {order.progress_percent !== null && (
                        <div className="order-progress">
                          <div className="progress-label">Progress</div>
                          <div className="progress-bar">
                            <div
                              className="progress-value"
                              style={{ width: `${order.progress_percent}%` }}
                            />
                          </div>
                          <div className="progress-meta">
                            {order.progress_percent.toFixed(1)}% complete
                          </div>
                        </div>
                      )}
                      {order.estimated_total !== null && (
                        <div className="order-financials">
                          <span className="meta-value">Est. Total: {formatCurrency(order.estimated_total)}</span>
                        </div>
                      )}
                    </div>
                    <div className="order-card-footer">
                      {order.expected_delivery_date && (
                        <span>Expected: {formatDate(order.expected_delivery_date)}</span>
                      )}
                      <span>Days since ordered: {order.days_since_ordered}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* Location Requests Section */}
          <section className="location-requests">
            <div className="section-heading">
              <h2>Location Requests</h2>
              <span className="section-subtitle">Tasks and feedback from locations</span>
            </div>
            {location_requests.length === 0 ? (
              <div className="empty-state">
                <p>No location requests</p>
              </div>
            ) : (
              <div className="orders-grid">
                {location_requests.map((request) => (
                  <div
                    key={request.id}
                    className={`order-card ${request.is_urgent ? 'priority-urgent' : ''}`}
                  >
                    <div className="order-card-header">
                      <div>
                        <h3>{request.title}</h3>
                        <span className="order-supplier">{request.location}</span>
                      </div>
                      <div>
                        {request.is_urgent && (
                          <span className="badge urgent-badge">Urgent</span>
                        )}
                        <span className={`badge status-${request.status}`}>
                          {request.status_label}
                        </span>
                      </div>
                    </div>
                    <div className="order-details">
                      <div className="order-meta">
                        <span>Type: {request.type_label}</span>
                        <span>Days Open: {request.days_open}</span>
                      </div>
                      {request.description && (
                        <div className="refill-notes">
                          <p>{request.description}</p>
                        </div>
                      )}
                    </div>
                    <div className="order-card-footer">
                      {request.created_at && (
                        <span>Created: {formatDate(request.created_at)}</span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>
      </div>

      <footer className="logistics-footer">
        <div className="footer-time">
          {currentTime.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', second: '2-digit' })}
        </div>
        <div className="refresh-progress-container">
          <div className="refresh-progress-bar">
            <div 
              className="refresh-progress-fill" 
              style={{ width: `${refreshProgress}%` }}
            />
          </div>
        </div>
      </footer>
    </div>
  );
};

export default LogisticsDashboard;
