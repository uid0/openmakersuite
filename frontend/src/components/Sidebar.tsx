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
  requiresSuperuser?: boolean;
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
  isMobileOpen?: boolean;
  onClose?: () => void;
}

const Sidebar: React.FC<SidebarProps> = ({ isCollapsed = false, isMobileOpen = false, onClose }) => {
  const [touchStart, setTouchStart] = useState<number | null>(null);
  const [touchEnd, setTouchEnd] = useState<number | null>(null);
  const [isLoggedIn, setIsLoggedIn] = useState<boolean>(false);
  const [isStaff, setIsStaff] = useState<boolean>(false);
  const [isSuperuser, setIsSuperuser] = useState<boolean>(false);
  const [expandedSections, setExpandedSections] = useState<Set<string>>(new Set());
  const location = useLocation();

  useEffect(() => {
    // Check authentication status
    const checkAuth = () => {
      const token = localStorage.getItem('token');
      const staffStatus = localStorage.getItem('is_staff');
      const superuserStatus = localStorage.getItem('is_superuser');
      
      setIsLoggedIn(!!token);
      setIsStaff(staffStatus === 'true');
      setIsSuperuser(superuserStatus === 'true');
    };

    checkAuth();

    const handleStorageChange = (e: StorageEvent) => {
      if (e.key === 'token' || e.key === 'is_staff' || e.key === 'is_superuser') {
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
        { path: '/dashboard', label: 'Dashboard', icon: '📊', requiresAuth: true },
        { path: '/inventory', label: 'Inventory Home', icon: '🏠' },
        { path: '/inventory/assets', label: 'Assets', icon: '📋' },
        { path: '/inventory/locations', label: 'Locations', icon: '📍', requiresStaff: true },
        { path: '/inventory/categories', label: 'Categories', icon: '📁', requiresStaff: true },
        { path: '/inventory/admin', label: 'Admin Dashboard', icon: '⚙️', requiresAuth: true },
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
        { path: '/facilities/forgekey-dashboard', label: 'ForgeKey Dashboard', icon: '📊', requiresStaff: true },
        { path: '/facilities/forgekey-devices', label: 'ForgeKey Devices', icon: '📡', requiresStaff: true },
        { path: '/facilities/forgekey-device-types', label: 'Device Types', icon: '🏷️', requiresStaff: true },
        { path: '/facilities/forgekey-epaper', label: 'e-Paper Panels', icon: '🖼️', requiresStaff: true },
        { path: '/facilities/forgekey-rollouts', label: 'Firmware Rollouts', icon: '🚀', requiresStaff: true },
        { path: '/facilities/forgekey-certificates', label: 'Certificates (PKI)', icon: '🔐', requiresStaff: true },
        { path: '/facilities/lockers', label: 'Lockers', icon: '🔒', requiresStaff: true },
        { path: '/facilities/maker-boxes', label: 'Maker Boxes', icon: '🗃️', requiresStaff: true },
        { path: '/facilities/maker-boxes/pre-conversion', label: 'Maker Box pre-conversion', icon: '📥', requiresStaff: true },
        { path: '/facilities/maker-boxes/scan', label: 'Maker Box scan', icon: '🔍', requiresStaff: true },
        { path: '/facilities/electrical', label: 'Electrical & Network', icon: '⚡', requiresStaff: true },
        { path: '/facilities/project-storage/queue', label: 'Project Storage', icon: '📦', requiresStaff: true },
        { path: '/facilities/storage-vision', label: 'Storage Vision', icon: '📸', requiresStaff: true },
        { path: '/facilities/storage-vision/capture', label: 'Vision capture', icon: '📷', requiresStaff: true },
        { path: '/facilities/storage-vision/review', label: 'Vision review', icon: '✅', requiresStaff: true },
      ],
    },
    {
      id: 'maintenance',
      label: 'Maintenance',
      icon: '🔧',
      items: [
        { path: '/maintenance', label: 'Overview', icon: '🧭', requiresAuth: true },
        { path: '/maintenance/dashboard', label: 'PM Dashboard', icon: '📋', requiresAuth: true },
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
        { path: '/analytics', label: 'Analytics Pulse', icon: '📈', requiresAuth: true, requiresStaff: true },
        { path: '/reports/inventory', label: 'Inventory Report', icon: '📦', requiresAuth: true },
        { path: '/reports/purchasing', label: 'Purchasing Report', icon: '🛒', requiresAuth: true },
        { path: '/reports/assets', label: 'Asset Report', icon: '🏢', requiresAuth: true },
        { path: '/reports/cost-recovery', label: 'Cost Recovery', icon: '💰', requiresAuth: true },
      ],
    },
    {
      id: 'settings',
      label: 'Settings',
      icon: '⚙️',
      items: [
        { path: '/settings/profile', label: 'Profile', icon: '👤', requiresAuth: true },
        { path: '/settings/site', label: 'Site Settings', icon: '🎨', requiresSuperuser: true },
        { path: '/settings/webhooks', label: 'Webhooks', icon: '🔗', requiresAuth: true },
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
    if (item.requiresSuperuser && !isSuperuser) return false;
    if (item.requiresStaff && !isStaff) return false;
    if (item.requiresAuth && !isLoggedIn) return false;
    return true;
  };

  // Swipe to close handlers
  const minSwipeDistance = 50;

  const onTouchStart = (e: React.TouchEvent) => {
    setTouchEnd(null);
    setTouchStart(e.targetTouches[0].clientX);
  };

  const onTouchMove = (e: React.TouchEvent) => {
    setTouchEnd(e.targetTouches[0].clientX);
  };

  const onTouchEnd = () => {
    if (!touchStart || !touchEnd) return;
    const distance = touchStart - touchEnd;
    const isLeftSwipe = distance > minSwipeDistance;
    if (isLeftSwipe && isMobileOpen && onClose) {
      onClose();
    }
  };

  // Close sidebar when clicking a nav item on mobile
  const handleNavClick = () => {
    if (isMobileOpen && onClose) {
      onClose();
    }
  };

  return (
    <aside
      className={`sidebar ${isCollapsed ? 'collapsed' : ''} ${isMobileOpen ? 'mobile-open' : ''}`}
      onTouchStart={onTouchStart}
      onTouchMove={onTouchMove}
      onTouchEnd={onTouchEnd}
    >
      {/* Mobile close button */}
      {isMobileOpen && (
        <button
          className="sidebar-close-button"
          onClick={onClose}
          aria-label="Close sidebar"
        >
          ✕
        </button>
      )}
      <div className="sidebar-brand" data-testid="sidebar-brand">
        <span className="sidebar-brand-chip" aria-hidden="true">
          OM
        </span>
        <div className="sidebar-brand-text">
          <span className="sidebar-brand-eyebrow">OpenMakerSuite</span>
          <span className="sidebar-brand-title">Workspace</span>
        </div>
      </div>
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
                          onClick={handleNavClick}
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
                        onClick={handleNavClick}
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

