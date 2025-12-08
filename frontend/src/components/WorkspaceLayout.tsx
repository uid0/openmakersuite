/**
 * Workspace Layout Component
 * Wraps pages with sidebar, breadcrumbs, and content area
 */
import React from 'react';
import { useLocation } from 'react-router-dom';
import Breadcrumbs from './Breadcrumbs';
import Sidebar from './Sidebar';
import '../styles/WorkspaceLayout.css';

interface WorkspaceLayoutProps {
  children: React.ReactNode;
}

const WorkspaceLayout: React.FC<WorkspaceLayoutProps> = ({ children }) => {
  const location = useLocation();
  
  // Hide sidebar on TV dashboards
  const shouldHideSidebar = 
    location.pathname.startsWith('/facilities/tv-dashboard') ||
    location.pathname.startsWith('/facilities/logistics') ||
    location.pathname.startsWith('/tv-dashboard') ||
    location.pathname.startsWith('/tv-logistics');

  if (shouldHideSidebar) {
    return <div className="workspace-layout no-sidebar">{children}</div>;
  }

  return (
    <div className="workspace-layout">
      <Sidebar />
      <div className="workspace-content">
        <div className="workspace-header">
          <Breadcrumbs />
        </div>
        <main className="workspace-main">{children}</main>
      </div>
    </div>
  );
};

export default WorkspaceLayout;

