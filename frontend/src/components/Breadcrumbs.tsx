/**
 * Breadcrumbs Component
 * Displays full navigation path from current route
 */
import React from 'react';
import { Link, useLocation } from 'react-router-dom';
import '../styles/Breadcrumbs.css';

interface BreadcrumbSegment {
  path: string;
  label: string;
}

const Breadcrumbs: React.FC = () => {
  const location = useLocation();

  const getBreadcrumbs = (): BreadcrumbSegment[] => {
    const pathSegments = location.pathname.split('/').filter(Boolean);
    const breadcrumbs: BreadcrumbSegment[] = [{ path: '/', label: 'Home' }];

    // Route to label mapping
    const routeLabels: Record<string, string> = {
      inventory: 'Inventory',
      'inventory/assets': 'Assets',
      'inventory/admin': 'Admin Dashboard',
      'inventory/code-entry': 'Code Entry',
      'inventory/transparency': 'Transparency',
      'inventory/scan': 'Scan Items',
      purchasing: 'Purchasing',
      'purchasing/orders': 'Purchase Orders',
      assets: 'Assets',
      facilities: 'Facilities',
      'facilities/tv-dashboard': 'TV Dashboard',
      'facilities/logistics': 'Logistics',
      'facilities/checklist': 'Checklist',
      sigs: 'SIGs',
      'sigs/dashboard': 'SIG Dashboard',
      settings: 'Settings',
      'settings/tax-receipt': 'Tax Receipt',
      'settings/tax-receipt/lookup': 'Tax Receipt Lookup',
    };

    let currentPath = '';
    pathSegments.forEach((segment, index) => {
      currentPath += `/${segment}`;
      const pathKey = pathSegments.slice(0, index + 1).join('/');
      
      // Use mapped label or format segment
      const label = routeLabels[pathKey] || 
                   segment.split('-').map(word => 
                     word.charAt(0).toUpperCase() + word.slice(1)
                   ).join(' ');
      
      breadcrumbs.push({ path: currentPath, label });
    });

    return breadcrumbs;
  };

  const breadcrumbs = getBreadcrumbs();

  // Don't show breadcrumbs on home page
  if (location.pathname === '/') {
    return null;
  }

  return (
    <nav className="breadcrumbs" aria-label="Breadcrumb">
      <ol className="breadcrumb-list">
        {breadcrumbs.map((crumb, index) => {
          const isLast = index === breadcrumbs.length - 1;
          
          return (
            <li key={crumb.path} className="breadcrumb-item">
              {isLast ? (
                <span className="breadcrumb-current" aria-current="page">
                  {crumb.label}
                </span>
              ) : (
                <Link to={crumb.path} className="breadcrumb-link">
                  {crumb.label}
                </Link>
              )}
              {!isLast && <span className="breadcrumb-separator">›</span>}
            </li>
          );
        })}
      </ol>
    </nav>
  );
};

export default Breadcrumbs;

