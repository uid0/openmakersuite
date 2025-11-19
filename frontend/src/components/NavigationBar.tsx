/**
 * Navigation Bar Component
 * Top menu bar with links to all public and private endpoints
 */
import React, { useEffect, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import '../styles/NavigationBar.css';

const NavigationBar: React.FC = () => {
  const [isLoggedIn, setIsLoggedIn] = useState<boolean>(false);
  const [isStaff, setIsStaff] = useState<boolean>(false);
  const location = useLocation();

  useEffect(() => {
    // Check authentication status
    const checkAuth = () => {
      const token = localStorage.getItem('token');
      const staffStatus = localStorage.getItem('is_staff');
      
      setIsLoggedIn(!!token);
      setIsStaff(staffStatus === 'true');
    };

    // Check on mount and route change
    checkAuth();

    // Listen for storage changes (when user logs in/out in another tab or component)
    const handleStorageChange = (e: StorageEvent) => {
      if (e.key === 'token' || e.key === 'is_staff') {
        checkAuth();
      }
    };

    window.addEventListener('storage', handleStorageChange);

    // Also listen for custom events (for same-tab auth changes)
    const handleAuthChange = () => {
      checkAuth();
    };

    window.addEventListener('authChange', handleAuthChange);

    return () => {
      window.removeEventListener('storage', handleStorageChange);
      window.removeEventListener('authChange', handleAuthChange);
    };
  }, [location]); // Re-check on route change

  const isActive = (path: string) => {
    return location.pathname === path || location.pathname.startsWith(path + '/');
  };

  return (
    <nav className="navigation-bar">
      <div className="nav-container">
        <Link to="/" className="nav-logo">
          <span className="logo-icon">📦</span>
          <span className="logo-text">Makerspace</span>
        </Link>

        <div className="nav-links">
          {/* Public Links */}
          <Link 
            to="/" 
            className={`nav-link ${isActive('/') && location.pathname === '/' ? 'active' : ''}`}
          >
            Home
          </Link>
          <Link 
            to="/assets" 
            className={`nav-link ${isActive('/assets') ? 'active' : ''}`}
          >
            Assets
          </Link>
          <Link 
            to="/tv-dashboard" 
            className={`nav-link ${isActive('/tv-dashboard') ? 'active' : ''}`}
          >
            TV Dashboard
          </Link>
          <Link 
            to="/tv-logistics" 
            className={`nav-link ${isActive('/tv-logistics') ? 'active' : ''}`}
          >
            Logistics
          </Link>
          <Link 
            to="/transparency" 
            className={`nav-link ${isActive('/transparency') ? 'active' : ''}`}
          >
            Transparency
          </Link>

          {/* Private Links (when logged in) */}
          {isLoggedIn && (
            <>
              <Link 
                to="/orderadmin" 
                className={`nav-link ${isActive('/orderadmin') ? 'active' : ''}`}
              >
                Admin Dashboard
              </Link>
              <Link 
                to="/purchase-order" 
                className={`nav-link ${isActive('/purchase-order') ? 'active' : ''}`}
              >
                Purchase Orders
              </Link>
              <Link 
                to="/code-entry" 
                className={`nav-link ${isActive('/code-entry') ? 'active' : ''}`}
              >
                Code Entry
              </Link>
            </>
          )}

          {/* Staff Links */}
          {isStaff && (
            <a 
              href="/admin" 
              className="nav-link nav-link-admin"
              target="_blank"
              rel="noopener noreferrer"
            >
              Django Admin
            </a>
          )}
        </div>
      </div>
    </nav>
  );
};

export default NavigationBar;

