/**
 * Logistics Dashboard - Optimized for Fire TV / Silk Browser
 * 32" display in the logistics office
 */
import React, { useEffect, useState, useRef } from 'react';
import '../styles/LogisticsDashboard.css';
import { analyticsAPI, customizationAPI } from '../services/api';
import { SiteSettings } from '../types';

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
  const [siteSettings, setSiteSettings] = useState<SiteSettings | null>(null);
  const logoRef = useRef<HTMLDivElement>(null);
  const animationFrameRef = useRef<number>();
  const positionRef = useRef({ x: 100, y: 100 });
  const velocityRef = useRef({ x: 2, y: 2 });

  const fetchDashboardData = async () => {
    try {
      const response = await analyticsAPI.getLogisticsDashboard<LogisticsDashboardResponse>();
      console.log('Logistics Dashboard API Full Response:', response);
      console.log('Logistics Dashboard API Response Data:', response.data);
      console.log('Response Data Type:', typeof response.data);
      console.log('Response Data Keys:', response.data ? Object.keys(response.data) : 'No data');
      
      // Handle both direct data and nested data structures
      const rawData = response.data || {};
      
      // Ensure all values are numbers, defaulting to 0 if undefined/null
      const dashboardData: LogisticsDashboardResponse = {
        open_item_requests: Number(rawData.open_item_requests) || 0,
        open_locations_with_problems: Number(rawData.open_locations_with_problems) || 0,
        assets_overdue_maintenance: Number(rawData.assets_overdue_maintenance) || 0,
        qr_scans_total: Number(rawData.qr_scans_total) || 0,
        qr_scans_by_day: Array.isArray(rawData.qr_scans_by_day) ? rawData.qr_scans_by_day : [],
        last_updated: rawData.last_updated || new Date().toISOString(),
      };
      
      console.log('Processed Dashboard Data:', dashboardData);
      
      setData(dashboardData);
      setError(null);
      setRefreshProgress(100); // Reset progress bar
    } catch (err: any) {
      console.error('Failed to load logistics dashboard data', err);
      console.error('Error response:', err.response);
      console.error('Error details:', err.response?.data || err.message);
      setError(`Unable to load logistics data: ${err.response?.data?.detail || err.message || 'Unknown error'}`);
    } finally {
      setLoading(false);
    }
  };

  // Fetch site settings for logo
  useEffect(() => {
    const fetchSiteSettings = async () => {
      try {
        const response = await customizationAPI.getSiteSettings();
        setSiteSettings(response.data);
      } catch (err) {
        console.warn('Failed to load site settings:', err);
      }
    };
    fetchSiteSettings();
  }, []);

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

  // Bouncing DVD logo animation
  useEffect(() => {
    if (!logoRef.current || !siteSettings?.logo_url) return;

    const logo = logoRef.current;
    const logoWidth = 120;
    const logoHeight = 60;
    const speed = 2;

    const animate = () => {
      const container = logo.parentElement;
      if (!container) return;

      const containerRect = container.getBoundingClientRect();
      const maxX = containerRect.width - logoWidth;
      const maxY = containerRect.height - logoHeight;

      // Skip if container not properly sized
      if (maxX <= 0 || maxY <= 0) {
        animationFrameRef.current = requestAnimationFrame(animate);
        return;
      }

      // Update position
      positionRef.current.x += velocityRef.current.x * speed;
      positionRef.current.y += velocityRef.current.y * speed;

      // Bounce off walls
      if (positionRef.current.x <= 0) {
        velocityRef.current.x = Math.abs(velocityRef.current.x);
        positionRef.current.x = 0;
      } else if (positionRef.current.x >= maxX) {
        velocityRef.current.x = -Math.abs(velocityRef.current.x);
        positionRef.current.x = maxX;
      }

      if (positionRef.current.y <= 0) {
        velocityRef.current.y = Math.abs(velocityRef.current.y);
        positionRef.current.y = 0;
      } else if (positionRef.current.y >= maxY) {
        velocityRef.current.y = -Math.abs(velocityRef.current.y);
        positionRef.current.y = maxY;
      }

      // Apply position
      logo.style.left = `${positionRef.current.x}px`;
      logo.style.top = `${positionRef.current.y}px`;

      animationFrameRef.current = requestAnimationFrame(animate);
    };

    // Initialize random position and velocity - wait for container to be sized
    const initializeAnimation = () => {
      const container = logo.parentElement;
      if (!container) return;

      const containerRect = container.getBoundingClientRect();
      const maxX = containerRect.width - logoWidth;
      const maxY = containerRect.height - logoHeight;
      
      // Wait for container to be properly sized
      if (maxX <= 0 || maxY <= 0) {
        setTimeout(initializeAnimation, 50);
        return;
      }
      
      positionRef.current.x = Math.max(0, Math.min(maxX, Math.random() * maxX));
      positionRef.current.y = Math.max(0, Math.min(maxY, Math.random() * maxY));
      velocityRef.current.x = Math.random() > 0.5 ? speed : -speed;
      velocityRef.current.y = Math.random() > 0.5 ? speed : -speed;
      
      // Set initial position
      logo.style.left = `${positionRef.current.x}px`;
      logo.style.top = `${positionRef.current.y}px`;
      
      // Start animation
      animate();
    };

    // Start initialization after a short delay to ensure DOM is ready
    const timeoutId = setTimeout(initializeAnimation, 200);

    return () => {
      clearTimeout(timeoutId);
      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current);
      }
    };
  }, [siteSettings?.logo_url]);

  // Burn-in prevention: subtle pixel shifting
  useEffect(() => {
    const shiftInterval = setInterval(() => {
      const dashboard = document.querySelector('.logistics-dashboard');
      if (dashboard) {
        // Subtle 1-2 pixel shift every 5 minutes
        const shiftX = Math.random() * 2 - 1; // -1 to 1
        const shiftY = Math.random() * 2 - 1; // -1 to 1
        (dashboard as HTMLElement).style.transform = `translate(${shiftX}px, ${shiftY}px)`;
        
        // Reset after a short time
        setTimeout(() => {
          (dashboard as HTMLElement).style.transform = 'translate(0, 0)';
        }, 1000);
      }
    }, 300000); // Every 5 minutes

    return () => clearInterval(shiftInterval);
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
          <p>{error || 'No data available'}</p>
          <p className="logistics-error-tip">Ensure the backend is reachable at the configured API URL.</p>
          {data && (
            <pre style={{ marginTop: '1rem', fontSize: '0.8rem', textAlign: 'left' }}>
              {JSON.stringify(data, null, 2)}
            </pre>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="logistics-dashboard">
      {/* Bouncing DVD Logo */}
      {siteSettings?.logo_url && (
        <div className="bouncing-logo-container">
          <div ref={logoRef} className="bouncing-logo">
            <img 
              src={siteSettings.logo_url} 
              alt={siteSettings.logo_alt_text || 'Logo'} 
              style={{ maxWidth: '120px', maxHeight: '60px', objectFit: 'contain' }}
            />
          </div>
        </div>
      )}

      <div className="dashboard-grid">
        {/* Open Item Requests */}
        <div className="metric-card">
          <div className="metric-label">Open Item Requests</div>
          <div className="metric-value">
            {typeof data.open_item_requests === 'number' ? data.open_item_requests : 0}
          </div>
        </div>

        {/* Open Locations with Problems */}
        <div className="metric-card">
          <div className="metric-label">Locations with Problems</div>
          <div className="metric-value">
            {typeof data.open_locations_with_problems === 'number' ? data.open_locations_with_problems : 0}
          </div>
        </div>

        {/* Assets Overdue Maintenance */}
        <div className="metric-card">
          <div className="metric-label">Assets Overdue Maintenance</div>
          <div className="metric-value">
            {typeof data.assets_overdue_maintenance === 'number' ? data.assets_overdue_maintenance : 0}
          </div>
        </div>

        {/* QR Code Scans */}
        <div className="metric-card">
          <div className="metric-label">QR Scans (Last 7 Days)</div>
          <div className="metric-value">
            {typeof data.qr_scans_total === 'number' ? data.qr_scans_total : 0}
          </div>
          <div className="sparkline-container">
            <Sparkline data={Array.isArray(data.qr_scans_by_day) ? data.qr_scans_by_day : []} />
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
