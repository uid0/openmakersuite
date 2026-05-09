/**
 * Breadcrumbs Component
 *
 * Renders the navigation path from current route. Labels and click-target
 * routability come from `frontend/src/navigation/routeMap.ts` so a single
 * place defines both. Segments that are not navigable (no route handler)
 * render as plain text instead of links — see `clickable: false` entries
 * in the route map.
 */
import React from 'react';
import { Link, useLocation } from 'react-router-dom';
import '../styles/Breadcrumbs.css';

import { getRouteEntry, isClickable, labelFor } from '../navigation/routeMap';

interface BreadcrumbSegment {
  path: string;
  label: string;
  clickable: boolean;
}

// Bare resource identifiers (UUIDs or all-digit ids). Rendering one between
// the parent listing and an action label like "Edit" is noise — the user
// already navigated through the parent to get here.
const ID_LIKE = /^(\d+|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i;

const Breadcrumbs: React.FC = () => {
  const location = useLocation();

  const breadcrumbs = React.useMemo<BreadcrumbSegment[]>(() => {
    const pathSegments = location.pathname.split('/').filter(Boolean);
    const out: BreadcrumbSegment[] = [{ path: '/', label: 'Home', clickable: true }];

    let currentPath = '';
    pathSegments.forEach((segment, index) => {
      currentPath += `/${segment}`;
      const pathKey = pathSegments.slice(0, index + 1).join('/');
      // Hide ID-like segments unless the route map explicitly registers
      // them (e.g. a code that's worth surfacing to the user).
      if (ID_LIKE.test(segment) && !getRouteEntry(pathKey)) {
        return;
      }
      out.push({
        path: currentPath,
        label: labelFor(pathKey, segment),
        clickable: isClickable(pathKey),
      });
    });

    return out;
  }, [location.pathname]);

  if (location.pathname === '/') {
    return null;
  }

  return (
    <nav className="breadcrumbs" aria-label="Breadcrumb">
      <ol className="breadcrumb-list">
        {breadcrumbs.map((crumb, index) => {
          const isLast = index === breadcrumbs.length - 1;
          const renderAsLink = !isLast && crumb.clickable;

          return (
            <li key={crumb.path} className="breadcrumb-item">
              {renderAsLink ? (
                <Link to={crumb.path} className="breadcrumb-link">
                  {crumb.label}
                </Link>
              ) : (
                <span
                  className={isLast ? 'breadcrumb-current' : 'breadcrumb-static'}
                  aria-current={isLast ? 'page' : undefined}
                >
                  {crumb.label}
                </span>
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
