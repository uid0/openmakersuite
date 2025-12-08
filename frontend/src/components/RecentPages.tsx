/**
 * Recent Pages Component
 * Tracks and displays recently visited pages
 */
import React, { useEffect, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import '../styles/RecentPages.css';

interface RecentPage {
  path: string;
  label: string;
  timestamp: number;
}

const MAX_RECENT_PAGES = 8;
const STORAGE_KEY = 'recentPages';

const RecentPages: React.FC<{ isCollapsed?: boolean }> = ({ isCollapsed = false }) => {
  const [recentPages, setRecentPages] = useState<RecentPage[]>([]);
  const location = useLocation();

  // Route to label mapping for display
  const getPageLabel = (path: string): string => {
    const routeLabels: Record<string, string> = {
      '/': 'Home',
      '/inventory': 'Inventory Dashboard',
      '/inventory/assets': 'Assets',
      '/inventory/admin': 'Admin Dashboard',
      '/inventory/code-entry': 'Code Entry',
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
    const updatedPages = [
      newPage,
      ...currentPages.filter((page) => page.path !== location.pathname),
    ].slice(0, MAX_RECENT_PAGES);

    localStorage.setItem(STORAGE_KEY, JSON.stringify(updatedPages));
    setRecentPages(updatedPages);
  }, [location.pathname]);

  if (recentPages.length === 0) {
    return null;
  }

  return (
    <div className="recent-pages">
      {!isCollapsed && <h3 className="recent-pages-title">Recent</h3>}
      <ul className="recent-pages-list">
        {recentPages.map((page) => (
          <li key={`${page.path}-${page.timestamp}`} className="recent-page-item">
            <Link to={page.path} className="recent-page-link">
              {!isCollapsed && <span className="recent-page-icon">🕒</span>}
              {!isCollapsed && <span className="recent-page-label">{page.label}</span>}
              {isCollapsed && <span className="recent-page-icon" title={page.label}>🕒</span>}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
};

export default RecentPages;

