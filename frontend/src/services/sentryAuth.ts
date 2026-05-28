/**
 * Sentry user-scope helpers.
 *
 * Centralises the calls to `Sentry.setUser` / `Sentry.setTag` so the
 * three places that mutate auth state (login, register-then-login,
 * logout) and the page-load restore from localStorage all attribute
 * Sentry events consistently. Without this the SDK's "users affected"
 * counts default to anonymous and uid0 can't filter incidents by who
 * hit them.
 *
 * Safe to call when `Sentry.init` was skipped (VITE_SENTRY_DSN unset);
 * `@sentry/react` short-circuits when the SDK isn't initialised.
 */
import * as Sentry from '@sentry/react';

export type LoginResponseLike = {
  username?: string;
  is_staff?: boolean | string | null;
  is_superuser?: boolean | string | null;
};

function coerceBool(value: boolean | string | null | undefined): boolean {
  if (typeof value === 'boolean') return value;
  if (typeof value === 'string') return value === 'true';
  return false;
}

function deriveRole(isStaff: boolean, isSuperuser: boolean): string {
  if (isSuperuser) return 'admin';
  if (isStaff) return 'staff';
  return 'member';
}

export function applySentryAuth(payload: LoginResponseLike): void {
  if (!payload?.username) {
    return;
  }
  const isStaff = coerceBool(payload.is_staff);
  const isSuperuser = coerceBool(payload.is_superuser);
  Sentry.setUser({
    username: payload.username,
    ip_address: '{{auto}}',
  });
  Sentry.setTag('oms.user_role', deriveRole(isStaff, isSuperuser));
}

export function clearSentryAuth(): void {
  Sentry.setUser(null);
  Sentry.setTag('oms.user_role', 'anonymous');
}

/**
 * Restore the Sentry scope from whatever the auth localStorage keys
 * already say at page-load. Pairs with the analogous backend
 * SentryUserScopeMiddleware so frontend and backend show the same
 * `oms.user_role` tag against any given event.
 */
export function restoreSentryAuthFromStorage(): void {
  try {
    const username = localStorage.getItem('username');
    if (!username) {
      clearSentryAuth();
      return;
    }
    applySentryAuth({
      username,
      is_staff: localStorage.getItem('is_staff'),
      is_superuser: localStorage.getItem('is_superuser'),
    });
  } catch {
    // localStorage can throw in private-mode iOS / disabled-storage
    // contexts. Silent fail keeps the SDK in a safe anonymous state.
    clearSentryAuth();
  }
}
