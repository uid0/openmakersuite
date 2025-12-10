/**
 * Workspace Layout Component
 * Wraps pages with sidebar, breadcrumbs, and content area
 */
import { ActionIcon } from '@mantine/core';
import { IconBell } from '@tabler/icons-react';
import React, { useEffect, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useCommandPalette } from '../hooks/useCommandPalette';
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

  useEffect(() => {
    localStorage.setItem('sidebarCollapsed', JSON.stringify(isCollapsed));
  }, [isCollapsed]);

  // Hide sidebar on TV dashboards
  const shouldHideSidebar = 
    location.pathname.startsWith('/facilities/tv-dashboard') ||
    location.pathname.startsWith('/facilities/logistics') ||
    location.pathname.startsWith('/tv-dashboard') ||
    location.pathname.startsWith('/tv-logistics');

  const toggleSidebar = () => {
    setIsCollapsed(!isCollapsed);
    if (!hasInteracted) {
      setHasInteracted(true);
      localStorage.setItem('menuInteracted', 'true');
    }
  };

  // Load notifications on mount
  useEffect(() => {
    notifications.loadNotifications();
  }, [notifications]);

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
      <Sidebar isCollapsed={isCollapsed} />
      <div className="workspace-content">
        <div className="workspace-header">
          <button
            className={`menu-toggle ${!hasInteracted ? 'shimmer' : ''}`}
            onClick={toggleSidebar}
            aria-label={isCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
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

