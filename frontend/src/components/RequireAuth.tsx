/**
 * RequireAuth — route guard for authenticated-only pages.
 *
 * A logged-out visitor who hits a guarded route is redirected to the login
 * surface *before* the page mounts, so its on-mount data fetches never fire
 * and the user never sees an API error (e.g. the dashboard's "Failed to load
 * dashboard widgets" panel from a 401). See op-3er.
 *
 * There is no dedicated `/login` route in this app: `/` (HomePage) renders the
 * inline AuthSection login form, and that is where the session-expired flow
 * already sends people (SessionExpiredBanner). We redirect there for
 * consistency, and stash the attempted path on the same `oms_pending_return_to`
 * channel AuthSection consumes on a successful login, so the user is forwarded
 * back to the page they wanted once they sign in.
 */
import React from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { persistPendingReturnTo } from './SessionExpiredBanner';

/**
 * The JWT access token in localStorage is the app's auth signal — the API
 * client attaches it as a Bearer header on every request (services/api.ts).
 * Its presence is what distinguishes a logged-in visitor here.
 */
export const isAuthenticated = (): boolean => {
  try {
    return Boolean(localStorage.getItem('token'));
  } catch {
    // localStorage can throw in locked-down/private contexts. Treat an
    // unreadable store as "not authenticated" and let the redirect happen.
    return false;
  }
};

interface RequireAuthProps {
  children: React.ReactNode;
}

const RequireAuth: React.FC<RequireAuthProps> = ({ children }) => {
  const location = useLocation();

  if (!isAuthenticated()) {
    // Remember where they were headed so login can forward them back.
    persistPendingReturnTo(location.pathname);
    return <Navigate to="/" replace state={{ from: location }} />;
  }

  return <>{children}</>;
};

export default RequireAuth;
