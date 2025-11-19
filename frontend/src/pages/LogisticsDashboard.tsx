/**
 * Logistics Dashboard - Optimized for Fire TV / Silk Browser
 * 32" display in the logistics office
 */
import React, { useEffect, useState } from 'react';
import '../styles/LogisticsDashboard.css';
import { analyticsAPI } from '../services/api';

interface QRScanDay {
  date: string;
  count: number;
}

interface LogisticsDashboardResponse {
  open_item_requests: number;
  open_locations_with_problems: number;
  assets_overdue_maintenance: number;
  qr_scans_total: number;
  qr_scans_by_day: QRScanDay[];
  last_updated: string;
}

const REFRESH_INTERVAL_MS = 60000; // 60 seconds

const LogisticsDashboard: React.FC = () => {
  const [data, setData] = useState<LogisticsDashboardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshProgress, setRefreshProgress] = useState(100);
  const [currentTime, setCurrentTime] = useState(new Date());

  const fetchDashboardData = async () => {
    try {
      const response = await analyticsAPI.getLogisticsDashboard<LogisticsDashboardResponse>();
      setData(response.data);
      setError(null);
      setRefreshProgress(100); // Reset progress bar
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

  // Progress bar countdown
  useEffect(() => {
    if (!data) return;
    
    let startTime = Date.now();
    setRefreshProgress(100);
    
    const progressInterval = setInterval(() => {
      const elapsed = Date.now() - startTime;
      const progress = Math.max(0, 100 - (elapsed / REFRESH_INTERVAL_MS) * 100);
      setRefreshProgress(progress);
      
      if (progress <= 0) {
        startTime = Date.now();
      }
    }, 100);
    
    return () => clearInterval(progressInterval);
  }, [data?.last_updated]);

  // Wake lock for Fire TV
  useEffect(() => {
    let wakeLockSentinel: any;

    const requestWakeLock = async () => {
      try {
        const anyNavigator = navigator as Navigator & { wakeLock?: any };
        if (anyNavigator.wakeLock && anyNavigator.wakeLock.request) {
          wakeLockSentinel = await anyNavigator.wakeLock.request('screen');
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

  // Hide cursor for TV display
  useEffect(() => {
    const originalCursor = document.body.style.cursor;
    document.body.style.cursor = 'none';
    return () => {
      document.body.style.cursor = originalCursor;
    };
  }, []);

  // Sparkline component
  const Sparkline: React.FC<{ data: QRScanDay[] }> = ({ data }) => {
    if (!data || data.length === 0) return null;

    const values = data.map(d => d.count);
    const maxValue = Math.max(...values, 1); // Avoid division by zero
    const width = 200;
    const height = 40;
    const padding = 4;

    const points = values.map((value, index) => {
      const x = (index / (values.length - 1)) * (width - padding * 2) + padding;
      const y = height - (value / maxValue) * (height - padding * 2) - padding;
      return `${x},${y}`;
    }).join(' ');

    return (
      <svg width={width} height={height} className="sparkline">
        <polyline
          points={points}
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
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

  return (
    <div className="logistics-dashboard">
      <div className="dashboard-grid">
        {/* Open Item Requests */}
        <div className="metric-card">
          <div className="metric-label">Open Item Requests</div>
          <div className="metric-value">{data.open_item_requests}</div>
        </div>

        {/* Open Locations with Problems */}
        <div className="metric-card">
          <div className="metric-label">Locations with Problems</div>
          <div className="metric-value">{data.open_locations_with_problems}</div>
        </div>

        {/* Assets Overdue Maintenance */}
        <div className="metric-card">
          <div className="metric-label">Assets Overdue Maintenance</div>
          <div className="metric-value">{data.assets_overdue_maintenance}</div>
        </div>

        {/* QR Code Scans */}
        <div className="metric-card">
          <div className="metric-label">QR Scans (Last 7 Days)</div>
          <div className="metric-value">{data.qr_scans_total}</div>
          <div className="sparkline-container">
            <Sparkline data={data.qr_scans_by_day} />
          </div>
        </div>
      </div>

      <footer className="logistics-footer">
        <div className="footer-time">
          {currentTime.toLocaleTimeString('en-US', { 
            hour: 'numeric', 
            minute: '2-digit', 
            second: '2-digit',
            hour12: false 
          })}
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
