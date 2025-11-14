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

  const { summary } = data;
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
