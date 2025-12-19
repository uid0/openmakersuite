/**
 * Workspace Layout Component
 * Wraps pages with sidebar, breadcrumbs, and content area
 */
import { ActionIcon } from '@mantine/core';
import { IconBell } from '@tabler/icons-react';
import React, { useEffect, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useCommandPalette } from '../hooks/useCommandPalette';
import { useKeyboardShortcuts } from '../hooks/useKeyboardShortcuts';
import { useNotifications } from '../hooks/useNotifications';
import '../styles/WorkspaceLayout.css';
import Breadcrumbs from './Breadcrumbs';
import { CommandPalette } from './CommandPalette';
import NotificationBadge from './NotificationBadge';
import NotificationBanner from './NotificationBanner';
import NotificationCenter from './NotificationCenter';
import Sidebar from './Sidebar';

interface WorkspaceLayoutProps {
  children: React.ReactNode;
}

const WorkspaceLayout: React.FC<WorkspaceLayoutProps> = ({ children }) => {
  const location = useLocation();
  const commandPalette = useCommandPalette();
  const notifications = useNotifications();
  const [isNotificationCenterOpen, setIsNotificationCenterOpen] = useState(false);
  const [isCollapsed, setIsCollapsed] = useState<boolean>(() => {
    const saved = localStorage.getItem('sidebarCollapsed');
    return saved ? JSON.parse(saved) : false;
  });
  const [hasInteracted, setHasInteracted] = useState<boolean>(() => {
    return localStorage.getItem('menuInteracted') === 'true';
  });
  const [isMobileOpen, setIsMobileOpen] = useState(false);
  const [isMobile, setIsMobile] = useState(false);

  // Detect mobile viewport
  useEffect(() => {
    const checkMobile = () => {
      setIsMobile(window.innerWidth < 768);
    };
    checkMobile();
    window.addEventListener('resize', checkMobile);
    return () => window.removeEventListener('resize', checkMobile);
  }, []);

  useEffect(() => {
    localStorage.setItem('sidebarCollapsed', JSON.stringify(isCollapsed));
  }, [isCollapsed]);

  // Close mobile sidebar on route change
  useEffect(() => {
    if (isMobile) {
      setIsMobileOpen(false);
    }
  }, [location.pathname, isMobile]);

  // Prevent body scroll when mobile sidebar is open
  useEffect(() => {
    if (isMobile && isMobileOpen) {
      document.body.classList.add('sidebar-open');
    } else {
      document.body.classList.remove('sidebar-open');
    }
    return () => {
      document.body.classList.remove('sidebar-open');
    };
  }, [isMobile, isMobileOpen]);

  // Hide sidebar on TV dashboards
  const shouldHideSidebar = 
    location.pathname.startsWith('/facilities/tv-dashboard') ||
    location.pathname.startsWith('/facilities/logistics') ||
    location.pathname.startsWith('/tv-dashboard') ||
    location.pathname.startsWith('/tv-logistics');

  const toggleSidebar = () => {
    if (isMobile) {
      setIsMobileOpen(!isMobileOpen);
    } else {
      setIsCollapsed(!isCollapsed);
      if (!hasInteracted) {
        setHasInteracted(true);
        localStorage.setItem('menuInteracted', 'true');
      }
    }
  };

  const closeMobileSidebar = () => {
    setIsMobileOpen(false);
  };

  // Load notifications on mount
  useEffect(() => {
    notifications.loadNotifications();
  }, [notifications]);

  // Setup keyboard shortcuts
  useKeyboardShortcuts(commandPalette.isOpen, commandPalette.toggle);

  if (shouldHideSidebar) {
    return (
      <div className="workspace-layout no-sidebar">
        {/* Banners */}
        {notifications.banners.length > 0 && (
          <div style={{ padding: '16px' }}>
            {notifications.banners.map((banner) => (
              <NotificationBanner
                key={banner.id}
                banner={banner}
                onDismiss={notifications.dismissBanner}
              />
            ))}
          </div>
        )}
        {children}
        <CommandPalette isOpen={commandPalette.isOpen} onClose={commandPalette.close} />
        <NotificationCenter
          isOpen={isNotificationCenterOpen}
          onClose={() => setIsNotificationCenterOpen(false)}
        />
      </div>
    );
  }

  return (
    <div className="workspace-layout">
      {/* Mobile backdrop */}
      {isMobile && isMobileOpen && (
        <div
          className="sidebar-backdrop"
          onClick={closeMobileSidebar}
          aria-hidden="true"
        />
      )}
      <Sidebar
        isCollapsed={isCollapsed}
        isMobileOpen={isMobileOpen}
        onClose={closeMobileSidebar}
      />
      <div className="workspace-content">
        <div className="workspace-header">
          <button
            className={`menu-toggle ${!hasInteracted ? 'shimmer' : ''}`}
            onClick={toggleSidebar}
            aria-label={isMobile ? (isMobileOpen ? 'Close sidebar' : 'Open sidebar') : (isCollapsed ? 'Expand sidebar' : 'Collapse sidebar')}
          >
            ☰
          </button>
          <Link to="/" className="header-logo">
            <span className="logo-icon">📦</span>
            <span className="logo-text">DallasMakerspace</span>
          </Link>
          <Breadcrumbs />
          <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '12px' }}>
            <ActionIcon
              variant="subtle"
              size="lg"
              onClick={() => setIsNotificationCenterOpen(true)}
              aria-label="Open notifications"
              style={{ position: 'relative' }}
            >
              <IconBell size={20} />
              <NotificationBadge count={notifications.unreadCount} />
            </ActionIcon>
          </div>
        </div>
        {/* Banners */}
        {notifications.banners.length > 0 && (
          <div style={{ padding: '16px 20px 0' }}>
            {notifications.banners.map((banner) => (
              <NotificationBanner
                key={banner.id}
                banner={banner}
                onDismiss={notifications.dismissBanner}
              />
            ))}
          </div>
        )}
        <main className="workspace-main">{children}</main>
      </div>
      <CommandPalette isOpen={commandPalette.isOpen} onClose={commandPalette.close} />
      <NotificationCenter
        isOpen={isNotificationCenterOpen}
        onClose={() => setIsNotificationCenterOpen(false)}
      />
    </div>
  );
};

export default WorkspaceLayout;

