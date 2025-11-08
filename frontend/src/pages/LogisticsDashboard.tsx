/**
 * Logistics Dashboard - Optimised for FireTV displays in the logistics office.
 * Shows refill requests alongside pending purchase orders with auto-refresh,
 * wake-lock support, and large-format layout for TV readability.
 */
import React, { useEffect, useMemo, useState } from 'react';
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

interface LogisticsSummary {
  pending_requests: number;
  urgent_requests: number;
  awaiting_approval: number;
  pending_orders: number;
  open_order_lines: number;
}

interface LogisticsDashboardResponse {
  summary: LogisticsSummary;
  refill_requests: RefillRequest[];
  pending_orders: PendingOrder[];
  last_updated: string;
}

const REFRESH_INTERVAL_MS = 45000;

const LogisticsDashboard: React.FC = () => {
  const [data, setData] = useState<LogisticsDashboardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [highlightIndex, setHighlightIndex] = useState(0);

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

  useEffect(() => {
    if (!data?.refill_requests.length) return;
    const interval = setInterval(() => {
      setHighlightIndex((prev) =>
        data.refill_requests.length === 0 ? 0 : (prev + 1) % data.refill_requests.length
      );
    }, 15000);

    return () => clearInterval(interval);
  }, [data?.refill_requests.length]);

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

  const highlightedRequestId = useMemo(() => {
    if (!data?.refill_requests.length) {
      return null;
    }
    return data.refill_requests[Math.min(highlightIndex, data.refill_requests.length - 1)]?.id ?? null;
  }, [data, highlightIndex]);

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

  const { summary, refill_requests, pending_orders, last_updated } = data;

  return (
    <div className="logistics-dashboard">
      <header className="logistics-header">
        <div className="logistics-header-left">
          <h1>Logistics Command Center</h1>
          <p>Realtime overview for FireTV in the logistics office.</p>
        </div>
        <div className="logistics-header-right">
          <div className="clock">{new Date().toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })}</div>
          <div className="last-updated">
            Updated {formatTime(last_updated)} · Next refresh in {Math.round(REFRESH_INTERVAL_MS / 1000)}s
          </div>
        </div>
      </header>

      <section className="logistics-summary">
        <div className="summary-card emphasis">
          <span className="summary-label">Open Requests</span>
          <span className="summary-value">{summary.pending_requests}</span>
          <span className="summary-subtext">{summary.awaiting_approval} awaiting approval</span>
        </div>
        <div className="summary-card alert">
          <span className="summary-label">Urgent Requests</span>
          <span className="summary-value">{summary.urgent_requests}</span>
          <span className="summary-subtext">Flagged as urgent</span>
        </div>
        <div className="summary-card">
          <span className="summary-label">Open Purchase Orders</span>
          <span className="summary-value">{summary.pending_orders}</span>
          <span className="summary-subtext">{summary.open_order_lines} line items in-flight</span>
        </div>
      </section>

      <main className="logistics-content">
        <section className="refill-requests">
          <div className="section-heading">
            <h2>Item Refill Requests</h2>
            <span className="section-subtitle">Prioritised by urgency and oldest requests</span>
          </div>
          {refill_requests.length === 0 ? (
            <div className="empty-state">
              <h3>All caught up 🎉</h3>
              <p>No refill requests requiring action.</p>
            </div>
          ) : (
            <div className="refill-grid">
              {refill_requests.map((request) => (
                <div
                  key={request.id}
                  className={`refill-card priority-${request.priority} ${
                    highlightedRequestId === request.id ? 'highlight' : ''
                  }`}
                >
                  <div className="refill-card-header">
                    <h3>{request.item_name}</h3>
                    <span className={`badge status-${request.status}`}>{request.status_label}</span>
                  </div>
                  <div className="refill-card-body">
                    <div className="refill-meta">
                      <span className="meta-block">
                        <span className="meta-label">Location</span>
                        <span className="meta-value">{request.location}</span>
                      </span>
                      <span className="meta-block">
                        <span className="meta-label">Quantity</span>
                        <span className="meta-value">{request.quantity_requested}</span>
                      </span>
                      <span className="meta-block">
                        <span className="meta-label">Days Open</span>
                        <span className="meta-value">{request.days_open}</span>
                      </span>
                      <span className="meta-block">
                        <span className="meta-label">Priority</span>
                        <span className="meta-value accent">{request.priority_label}</span>
                      </span>
                    </div>
                    {(request.request_notes || request.public_notes) && (
                      <div className="refill-notes">
                        {request.request_notes && <p>{request.request_notes}</p>}
                        {!request.request_notes && request.public_notes && <p>{request.public_notes}</p>}
                      </div>
                    )}
                  </div>
                  <footer className="refill-card-footer">
                    <span>Requested by {request.requested_by || 'Member'}</span>
                    <span>{request.requested_at ? formatDate(request.requested_at) : '—'}</span>
                  </footer>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="pending-orders">
          <div className="section-heading">
            <h2>Purchase Orders In Flight</h2>
            <span className="section-subtitle">Track supplier deliveries and expected arrivals</span>
          </div>
          {pending_orders.length === 0 ? (
            <div className="empty-state">
              <h3>No outstanding orders</h3>
              <p>Logistics has received all purchase orders currently tracked.</p>
            </div>
          ) : (
            <div className="orders-grid">
              {pending_orders.map((order) => (
                <div key={order.id} className={`order-card status-${order.status}`}>
                  <header className="order-card-header">
                    <div>
                      <h3>{order.po_number}</h3>
                      <span className="order-supplier">{order.supplier_name}</span>
                    </div>
                    <span className="badge status">{order.status_label}</span>
                  </header>
                  <div className="order-details">
                    <div className="order-meta">
                      <span>
                        Sent {order.sent_at ? `${formatDate(order.sent_at)} · ${formatTime(order.sent_at)}` : '—'}
                      </span>
                      <span>
                        ETA {formatDate(order.expected_delivery_date)} ({order.days_since_ordered} days in transit)
                      </span>
                    </div>
                    <div className="order-progress">
                      <span className="progress-label">Receiving Progress</span>
                      <div className="progress-bar">
                        <div
                          className="progress-value"
                          style={{ width: `${order.progress_percent ?? 0}%` }}
                        />
                      </div>
                      <span className="progress-meta">
                        {order.received_quantity}/{order.total_quantity || 0} items received
                      </span>
                    </div>
                    <div className="order-financials">
                      <span className="meta-label">Est. Spend</span>
                      <span className="meta-value">{formatCurrency(order.estimated_total)}</span>
                    </div>
                  </div>
                  <footer className="order-card-footer">
                    <span>Lines: {order.total_items}</span>
                    <span>Last update {formatTime(order.updated_at)}</span>
                  </footer>
                </div>
              ))}
            </div>
          )}
        </section>
      </main>

      <footer className="logistics-footer">
        <div>
          Tip: On FireTV, set Settings → Display &amp; Sounds → Screensaver to <strong>Never</strong> to prevent sleep.
        </div>
        <div>
          Browser tip: The Silk menu → Settings → Keep Screen Awake helps ensure the dashboard stays visible.
        </div>
      </footer>
    </div>
  );
};

export default LogisticsDashboard;
