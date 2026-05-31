/**
 * Recent Pages Component
 * Tracks and displays recently visited pages
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { notificationsAPI } from '../services/api';
import '../styles/RecentPages.css';

interface RecentPage {
  path: string;
  label: string;
  timestamp: number;
}

const DEFAULT_RECENT_PAGES_LIMIT = 8;
const STORAGE_KEY = 'recentPages';
const HEIGHT_STORAGE_KEY = 'recentPagesHeight';
const MIN_HEIGHT = 100;
const MAX_HEIGHT = 600;
const DEFAULT_HEIGHT = 200;
const COLLAPSED_HEIGHT = 80;

const clampHeight = (value: number): number =>
  Math.max(MIN_HEIGHT, Math.min(MAX_HEIGHT, value));

const RecentPages: React.FC<{ isCollapsed?: boolean }> = ({ isCollapsed = false }) => {
  const [recentPages, setRecentPages] = useState<RecentPage[]>([]);
  const [displayLimit, setDisplayLimit] = useState<number>(DEFAULT_RECENT_PAGES_LIMIT);
  const [userHeight, setUserHeight] = useState<number>(() => {
    if (typeof window === 'undefined') return DEFAULT_HEIGHT;
    const stored = localStorage.getItem(HEIGHT_STORAGE_KEY);
    if (stored) {
      const parsed = parseInt(stored, 10);
      if (!Number.isNaN(parsed)) return clampHeight(parsed);
    }
    return DEFAULT_HEIGHT;
  });
  const [isDragging, setIsDragging] = useState(false);
  const dragStateRef = useRef<{ startY: number; startHeight: number } | null>(null);
  const location = useLocation();

  // Route to label mapping for display
  const getPageLabel = (path: string): string => {
    const routeLabels: Record<string, string> = {
      '/': 'Home',
      '/inventory': 'Inventory Dashboard',
      '/inventory/assets': 'Assets',
      '/inventory/admin': 'Admin Dashboard',
      '/inventory/transparency': 'Transparency',
      '/inventory/scan': 'Scan Items',
      '/purchasing/orders': 'Purchase Orders',
      '/assets': 'Assets',
      '/facilities/tv-dashboard': 'TV Dashboard',
      '/facilities/logistics': 'Logistics',
      '/sigs/dashboard': 'SIG Dashboard',
      '/settings/tax-receipt/lookup': 'Tax Receipt Lookup',
    };

    if (routeLabels[path]) {
      return routeLabels[path];
    }

    // Try to match dynamic routes
    const pathSegments = path.split('/').filter(Boolean);
    if (pathSegments.length > 0) {
      const basePath = `/${pathSegments[0]}`;
      if (routeLabels[basePath]) {
        return `${routeLabels[basePath]} - ${pathSegments[pathSegments.length - 1]}`;
      }
    }

    // Fallback: format path
    return pathSegments
      .map((segment) =>
        segment
          .split('-')
          .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
          .join(' ')
      )
      .join(' > ') || 'Home';
  };

  useEffect(() => {
    // Load recent pages from localStorage
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) {
      try {
        const parsed = JSON.parse(stored);
        setRecentPages(parsed);
      } catch (e) {
        console.error('Failed to parse recent pages:', e);
      }
    }

    // Load user preference for recent pages limit
    const loadPreferences = async () => {
      try {
        const response = await notificationsAPI.getPreferences();
        if (response.data.recent_pages_limit) {
          setDisplayLimit(response.data.recent_pages_limit);
        }
      } catch (err) {
        console.error('Error loading preferences:', err);
        // Use default if preference loading fails
      }
    };
    loadPreferences();

    // Listen for preference updates
    const handlePreferencesUpdate = () => {
      loadPreferences();
    };
    window.addEventListener('preferencesUpdated', handlePreferencesUpdate);

    return () => {
      window.removeEventListener('preferencesUpdated', handlePreferencesUpdate);
    };
  }, []);

  useEffect(() => {
    // Skip tracking for certain routes
    const skipRoutes = ['/tv-dashboard', '/tv-logistics', '/facilities/tv-dashboard', '/facilities/logistics'];
    if (skipRoutes.some((route) => location.pathname.startsWith(route))) {
      return;
    }

    // Skip if it's the same as the last page
    const stored = localStorage.getItem(STORAGE_KEY);
    let currentPages: RecentPage[] = [];
    if (stored) {
      try {
        currentPages = JSON.parse(stored);
        if (currentPages.length > 0 && currentPages[0].path === location.pathname) {
          return; // Already the most recent
        }
      } catch (e) {
        // Ignore parse errors
      }
    }

    // Add current page to recent pages
    const newPage: RecentPage = {
      path: location.pathname,
      label: getPageLabel(location.pathname),
      timestamp: Date.now(),
    };

    // Remove if already exists and add to front
    // Store up to 50 pages, but display will be limited by user preference
    const updatedPages = [
      newPage,
      ...currentPages.filter((page) => page.path !== location.pathname),
    ].slice(0, 50); // Store more than display limit to allow user to adjust preference

    localStorage.setItem(STORAGE_KEY, JSON.stringify(updatedPages));
    setRecentPages(updatedPages);
  }, [location.pathname]);

  const startDrag = useCallback(
    (event: React.MouseEvent<HTMLDivElement>) => {
      event.preventDefault();
      dragStateRef.current = { startY: event.clientY, startHeight: userHeight };
      setIsDragging(true);
    },
    [userHeight]
  );

  useEffect(() => {
    if (!isDragging) return;

    const handleMove = (event: MouseEvent) => {
      const state = dragStateRef.current;
      if (!state) return;
      // Handle sits above the scroll area; dragging up (delta negative) grows panel.
      const delta = event.clientY - state.startY;
      setUserHeight(clampHeight(state.startHeight - delta));
    };

    const handleUp = () => {
      setIsDragging(false);
      dragStateRef.current = null;
    };

    document.addEventListener('mousemove', handleMove);
    document.addEventListener('mouseup', handleUp);
    return () => {
      document.removeEventListener('mousemove', handleMove);
      document.removeEventListener('mouseup', handleUp);
    };
  }, [isDragging]);

  useEffect(() => {
    localStorage.setItem(HEIGHT_STORAGE_KEY, String(userHeight));
  }, [userHeight]);

  if (recentPages.length === 0) {
    return null;
  }

  // Limit displayed pages based on user preference
  const displayedPages = recentPages.slice(0, displayLimit);
  const hasMorePages = recentPages.length > displayLimit;
  const effectiveHeight = isCollapsed ? COLLAPSED_HEIGHT : userHeight;
  const showHandle = !isCollapsed;

  return (
    <div className="recent-pages">
      {showHandle && (
        <div
          role="separator"
          aria-orientation="horizontal"
          aria-label="Resize recent pages"
          className={`recent-pages-resize-handle${isDragging ? ' dragging' : ''}`}
          onMouseDown={startDrag}
        />
      )}
      {!isCollapsed && <h3 className="recent-pages-title">Recent</h3>}
      <div
        className="recent-pages-scroll-container"
        data-testid="recent-pages-scroll"
        style={{ height: effectiveHeight }}
      >
        <ul className="recent-pages-list">
          {displayedPages.map((page) => (
            <li key={`${page.path}-${page.timestamp}`} className="recent-page-item">
              <Link to={page.path} className="recent-page-link">
                {!isCollapsed && <span className="recent-page-icon">🕒</span>}
                {!isCollapsed && <span className="recent-page-label">{page.label}</span>}
                {isCollapsed && <span className="recent-page-icon" title={page.label}>🕒</span>}
              </Link>
            </li>
          ))}
        </ul>
        {hasMorePages && !isCollapsed && (
          <div className="recent-pages-more-indicator">
            {recentPages.length - displayLimit} more
          </div>
        )}
      </div>
    </div>
  );
};

export default RecentPages;

