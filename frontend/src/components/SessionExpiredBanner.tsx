/**
 * SessionExpiredBanner
 *
 * Listens for the global `oms:session-expired` event dispatched by the API
 * client when token refresh fails (see frontend/src/services/api.ts) and
 * surfaces an actionable recovery prompt. Captures the current pathname so
 * the user can be returned to the workflow they were attempting after a
 * fresh login (AC-17).
 *
 * Mounted once near the root of the layout — there is exactly one banner per
 * tab, regardless of which page raised the event.
 */
import { Button, Group, Notification, Stack, Text } from '@mantine/core';
import { IconLockOff } from '@tabler/icons-react';
import React, { useCallback, useEffect, useState } from 'react';

const STORAGE_KEY = 'oms_pending_return_to';

const isRecoverablePath = (pathname: string): boolean => {
  if (!pathname || pathname === '/') return false;
  // Avoid bouncing the user back to a public scan flow — they would just
  // re-trigger the auto-submit. Only restore staff/admin workspace routes.
  return (
    pathname.startsWith('/inventory') ||
    pathname.startsWith('/purchasing') ||
    pathname.startsWith('/assets') ||
    pathname.startsWith('/maintenance') ||
    pathname.startsWith('/facilities') ||
    pathname.startsWith('/sigs') ||
    pathname.startsWith('/reports') ||
    pathname.startsWith('/settings')
  );
};

const SessionExpiredBanner: React.FC = () => {
  const [visible, setVisible] = useState(false);
  const [returnTo, setReturnTo] = useState<string | null>(null);

  useEffect(() => {
    const handler = (event: Event) => {
      const detail = (event as CustomEvent<{ pathname?: string }>).detail;
      const pathname = detail?.pathname ?? window.location.pathname;
      if (isRecoverablePath(pathname)) {
        try {
          sessionStorage.setItem(STORAGE_KEY, pathname);
          setReturnTo(pathname);
        } catch {
          // sessionStorage is best-effort. If it fails (private mode, quota),
          // we still show the banner — we just lose the return-to.
          setReturnTo(null);
        }
      } else {
        setReturnTo(null);
      }
      setVisible(true);
    };

    window.addEventListener('oms:session-expired', handler);
    return () => {
      window.removeEventListener('oms:session-expired', handler);
    };
  }, []);

  const handleSignIn = useCallback(() => {
    setVisible(false);
    // Home page renders the AuthSection inline. The banner has already
    // persisted the attempted route into sessionStorage; pages that consume
    // SessionExpiredBanner are expected to read it after a successful login
    // (see useReturnToAfterLogin hook in this directory).
    if (window.location.pathname !== '/') {
      window.location.assign('/');
    }
  }, []);

  const handleDismiss = useCallback(() => {
    setVisible(false);
  }, []);

  if (!visible) return null;

  return (
    <Notification
      icon={<IconLockOff size={18} aria-hidden="true" />}
      color="yellow"
      title="Your session expired"
      onClose={handleDismiss}
      role="alert"
      aria-live="assertive"
      data-testid="session-expired-banner"
      style={{
        position: 'fixed',
        top: 16,
        right: 16,
        zIndex: 1100,
        maxWidth: 420,
      }}
    >
      <Stack gap="xs">
        <Text size="sm">
          You were signed out for security. Sign in again to continue
          {returnTo ? ' where you left off.' : '.'}
        </Text>
        <Group gap="xs">
          <Button size="xs" variant="filled" onClick={handleSignIn}>
            Sign in again
          </Button>
          <Button size="xs" variant="subtle" onClick={handleDismiss}>
            Dismiss
          </Button>
        </Group>
      </Stack>
    </Notification>
  );
};

/**
 * Persist an attempted route so a login surface can return the user to it
 * after a fresh sign-in. Shared by the auth guard (RequireAuth) so a
 * logged-out visitor bounced off a protected page (e.g. /dashboard) lands
 * back there once they authenticate — the same channel the session-expired
 * flow uses. Best-effort: a `/` path or unavailable sessionStorage is a no-op.
 */
export const persistPendingReturnTo = (pathname: string): void => {
  if (!pathname || pathname === '/') return;
  try {
    sessionStorage.setItem(STORAGE_KEY, pathname);
  } catch {
    // sessionStorage is best-effort (private mode, quota). Losing the
    // return-to only means the user isn't auto-forwarded after login.
  }
};

/**
 * Hook for pages that want to honor a stored return-to after a fresh login.
 * AuthSection (or any login surface) should call this on a successful login
 * to navigate the user back to where they were when their session expired.
 */
export const consumePendingReturnTo = (): string | null => {
  try {
    const value = sessionStorage.getItem(STORAGE_KEY);
    if (value) {
      sessionStorage.removeItem(STORAGE_KEY);
    }
    return value;
  } catch {
    return null;
  }
};

export default SessionExpiredBanner;
