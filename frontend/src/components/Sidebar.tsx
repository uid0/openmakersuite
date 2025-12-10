/**
 * Sidebar Component
 * Collapsible workspace-based sidebar navigation
 */
import React, { useEffect, useMemo, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import '../styles/Sidebar.css';
import RecentPages from './RecentPages';

interface NavItem {
  path: string;
  label: string;
  icon?: string;
  requiresAuth?: boolean;
  requiresStaff?: boolean;
  external?: boolean;
}

interface WorkspaceSection {
  id: string;
  label: string;
  icon?: string;
  items: NavItem[];
  requiresAuth?: boolean;
  requiresStaff?: boolean;
}

interface SidebarProps {
  isCollapsed?: boolean;
}

const Sidebar: React.FC<SidebarProps> = ({ isCollapsed = false }) => {
  const [isLoggedIn, setIsLoggedIn] = useState<boolean>(false);
  const [isStaff, setIsStaff] = useState<boolean>(false);
  const [expandedSections, setExpandedSections] = useState<Set<string>>(new Set());
  const location = useLocation();

  useEffect(() => {
    // Check authentication status
    const checkAuth = () => {
      const token = localStorage.getItem('token');
      const staffStatus = localStorage.getItem('is_staff');
      
      setIsLoggedIn(!!token);
      setIsStaff(staffStatus === 'true');
    };

    checkAuth();

    const handleStorageChange = (e: StorageEvent) => {
      if (e.key === 'token' || e.key === 'is_staff') {
        checkAuth();
      }
    };

    window.addEventListener('storage', handleStorageChange);
    const handleAuthChange = () => {
      checkAuth();
    };

    window.addEventListener('authChange', handleAuthChange);

    return () => {
      window.removeEventListener('storage', handleStorageChange);
      window.removeEventListener('authChange', handleAuthChange);
    };
  }, []);

  const workspaceSections: WorkspaceSection[] = useMemo(() => [
    {
      id: 'inventory',
      label: 'Inventory',
      icon: '📦',
      items: [
        { path: '/inventory', label: 'Dashboard', icon: '🏠' },
        { path: '/inventory/assets', label: 'Assets', icon: '📋' },
        { path: '/inventory/locations', label: 'Locations', icon: '📍', requiresStaff: true },
        { path: '/inventory/categories', label: 'Categories', icon: '📁', requiresStaff: true },
        { path: '/inventory/admin', label: 'Admin Dashboard', icon: '⚙️', requiresAuth: true },
        { path: '/inventory/code-entry', label: 'Code Entry', icon: '🔢', requiresAuth: true },
        { path: '/inventory/transparency', label: 'Transparency', icon: '🔍' },
        { path: '/inventory/scan', label: 'Scan Items', icon: '📱' },
      ],
    },
    {
      id: 'purchasing',
      label: 'Purchasing',
      icon: '🛒',
      items: [
        { path: '/purchasing/orders', label: 'Orders', icon: '📝', requiresAuth: true },
      ],
    },
    {
      id: 'assets',
      label: 'Assets',
      icon: '🏢',
      items: [
        { path: '/assets', label: 'Assets', icon: '📋' },
      ],
    },
    {
      id: 'facilities',
      label: 'Facilities',
      icon: '🏭',
      items: [
        { path: '/facilities/tv-dashboard', label: 'TV Dashboard', icon: '📺' },
        { path: '/facilities/logistics', label: 'Logistics', icon: '🚛' },
      ],
    },
    {
      id: 'sigs',
      label: 'SIGs',
      icon: '👥',
      items: [
        { path: '/sigs/dashboard', label: 'SIG Dashboard', icon: '📊' },
      ],
    },
    {
      id: 'reports',
      label: 'Reports',
      icon: '📊',
      items: [
        { path: '/reports/inventory', label: 'Inventory Report', icon: '📦', requiresAuth: true },
        { path: '/reports/purchasing', label: 'Purchasing Report', icon: '🛒', requiresAuth: true },
        { path: '/reports/assets', label: 'Asset Report', icon: '🏢', requiresAuth: true },
      ],
    },
    {
      id: 'settings',
      label: 'Settings',
      icon: '⚙️',
      items: [
        { path: '/settings/tax-receipt/lookup', label: 'Tax Receipt Lookup', icon: '🧾' },
        { path: '/admin', label: 'Django Admin', icon: '🔧', requiresStaff: true, external: true },
      ],
    },
  ], []);

  // Auto-expand section if current route is within it
  useEffect(() => {
    const currentPath = location.pathname;
    workspaceSections.forEach((section) => {
      const hasActiveItem = section.items.some((item) => {
        if (item.external) return false;
        return currentPath === item.path || currentPath.startsWith(item.path + '/');
      });
      if (hasActiveItem) {
        setExpandedSections((prev) => new Set(prev).add(section.id));
      }
    });
  }, [location.pathname, workspaceSections]);

  const toggleCollapse = () => {
    setIsCollapsed(!isCollapsed);
  };

  const toggleSection = (sectionId: string) => {
    setExpandedSections((prev) => {
      const newSet = new Set(prev);
      if (newSet.has(sectionId)) {
        newSet.delete(sectionId);
      } else {
        newSet.add(sectionId);
      }
      return newSet;
    });
  };

  const isActive = (path: string) => {
    return location.pathname === path || location.pathname.startsWith(path + '/');
  };

  const shouldShowSection = (section: WorkspaceSection): boolean => {
    if (section.requiresStaff && !isStaff) return false;
    if (section.requiresAuth && !isLoggedIn) return false;
    return true;
  };

  const shouldShowItem = (item: NavItem): boolean => {
    if (item.requiresStaff && !isStaff) return false;
    if (item.requiresAuth && !isLoggedIn) return false;
    return true;
  };

  return (
    <aside className={`sidebar ${isCollapsed ? 'collapsed' : ''}`}>
      <nav className="sidebar-nav">
        {workspaceSections.map((section) => {
          if (!shouldShowSection(section)) return null;

          // Check if any items in this section are visible
          const hasVisibleItems = section.items.some((item) => shouldShowItem(item));
          if (!hasVisibleItems) return null;

          const isExpanded = expandedSections.has(section.id);
          const hasActiveItem = section.items.some(
            (item) => shouldShowItem(item) && isActive(item.path)
          );

          return (
            <div key={section.id} className="workspace-section">
              <button
                className={`section-header ${hasActiveItem ? 'active' : ''}`}
                onClick={() => toggleSection(section.id)}
                aria-expanded={isExpanded}
              >
                <span className="section-icon">{section.icon}</span>
                {!isCollapsed && (
                  <>
                    <span className="section-label">{section.label}</span>
                    <span className="section-arrow">{isExpanded ? '▼' : '▶'}</span>
                  </>
                )}
              </button>
              {(!isCollapsed || isExpanded) && (
                <div className={`section-items ${isExpanded ? 'expanded' : ''}`}>
                  {section.items.map((item) => {
                    if (!shouldShowItem(item)) return null;

                    const itemIsActive = isActive(item.path);

                    if (item.external) {
                      return (
                        <a
                          key={item.path}
                          href={item.path}
                          target="_blank"
                          rel="noopener noreferrer"
                          className={`nav-item ${itemIsActive ? 'active' : ''}`}
                        >
                          {item.icon && <span className="item-icon">{item.icon}</span>}
                          {!isCollapsed && <span className="item-label">{item.label}</span>}
                        </a>
                      );
                    }

                    return (
                      <Link
                        key={item.path}
                        to={item.path}
                        className={`nav-item ${itemIsActive ? 'active' : ''}`}
                      >
                        {item.icon && <span className="item-icon">{item.icon}</span>}
                        {!isCollapsed && <span className="item-label">{item.label}</span>}
                      </Link>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </nav>
      <RecentPages isCollapsed={isCollapsed} />
    </aside>
  );
};

export default Sidebar;

