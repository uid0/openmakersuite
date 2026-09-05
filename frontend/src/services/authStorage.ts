/**
 * The locally stored auth state, and the one route that discards it.
 *
 * Everything the SPA calls "signed in" lives in these localStorage keys — the
 * API client attaches `token` as a Bearer header on every request, and
 * AuthSection greets anyone holding `token` + `username` with "Welcome back".
 * That is a claim about this browser, not about the server, and the two can
 * disagree: a session cookie expires at SESSION_COOKIE_AGE while the refresh
 * token lives four times as long.
 */
import { clearSentryAuth } from './sentryAuth';

/**
 * Where a server-side refusal sends someone whose local auth state is stale.
 *
 * NOT `/`. A refused `/media/` download (config/protected_media.py) is reached
 * by exactly the visitor whose session cookie has lapsed with a live refresh
 * token still in localStorage — `/` renders "Welcome back" and a Logout button
 * for them, so a remedy pointing there is a dead end. This path clears the
 * stale state first, which is what makes the sign-in form appear.
 */
export const REAUTH_PATH = '/reauth';

/**
 * Forget everything this browser holds about who is signed in.
 *
 * The same keys `services/api.ts` clears when a token refresh fails, and the
 * same ones AuthSection clears on an explicit logout — no server call, because
 * the credential this drops is already worthless to the server.
 */
export const clearStoredAuth = (): void => {
  try {
    localStorage.removeItem('token');
    localStorage.removeItem('refresh_token');
    localStorage.removeItem('username');
    localStorage.removeItem('is_staff');
    localStorage.removeItem('is_superuser');
  } catch {
    // localStorage can throw in locked-down/private contexts. Nothing was
    // readable there either, so the sign-in form renders regardless.
  }
  clearSentryAuth();
};
