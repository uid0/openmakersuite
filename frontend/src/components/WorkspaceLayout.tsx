/**
 * Workspace Layout Component
 * Wraps pages with sidebar, breadcrumbs, and content area
 */
import React, { useEffect, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import '../styles/WorkspaceLayout.css';
import Breadcrumbs from './Breadcrumbs';
import Sidebar from './Sidebar';

interface WorkspaceLayoutProps {
  children: React.ReactNode;
}

const WorkspaceLayout: React.FC<WorkspaceLayoutProps> = ({ children }) => {
  const location = useLocation();
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

  if (shouldHideSidebar) {
    return <div className="workspace-layout no-sidebar">{children}</div>;
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
        </div>
        <main className="workspace-main">{children}</main>
      </div>
    </div>
  );
};

export default WorkspaceLayout;

