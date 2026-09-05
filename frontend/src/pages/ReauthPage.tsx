/**
 * ReauthPage — the landing spot for a server refusal the SPA cannot see.
 *
 * A gated `/media/` download is an ordinary browser navigation, so when nginx
 * (or `config.protected_media.serve_media`) refuses it, no interceptor runs and
 * no `oms:session-expired` event fires. The refusal page links here.
 *
 * The visitor who arrives is, to this browser, signed in: their session cookie
 * lapsed but the refresh token in localStorage is still good, so every API call
 * works and `/` would greet them with "Welcome back". Dropping the stored auth
 * state is the whole job — HomePage then renders the sign-in form, and
 * AuthSection forwards them to `oms_pending_return_to`, which the refusal page
 * set to the document they were after.
 */
import React, { useEffect, useState } from 'react';
import { Navigate } from 'react-router-dom';
import { clearStoredAuth } from '../services/authStorage';

const ReauthPage: React.FC = () => {
  const [cleared, setCleared] = useState(false);

  useEffect(() => {
    clearStoredAuth();
    // NavigationBar and the rest of the shell listen for this rather than
    // polling localStorage.
    window.dispatchEvent(new Event('authChange'));
    setCleared(true);
  }, []);

  // Redirecting before the effect has run would land on HomePage while the
  // token was still there, which is the "Welcome back" dead end this exists to
  // avoid.
  if (!cleared) return null;

  return <Navigate to="/" replace />;
};

export default ReauthPage;
