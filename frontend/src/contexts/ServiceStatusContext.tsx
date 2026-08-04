/**
 * Service Status Context
 *
 * One poll of `GET /api/resilience/status/` per tab, shared by everything that
 * needs to know whether an external dependency is down: the global banner
 * (components/ServiceStatusBanner) and every control that is gated inline
 * (components/ServiceUnavailableNotice).
 *
 * Three rules this file exists to enforce:
 *
 * 1. **Poll only when it can pay off.** The endpoint is IsAuthenticated, so a
 *    signed-out visitor never requests it; a backgrounded tab doesn't either.
 *    A wall display left open overnight should not spend a request a minute.
 *
 * 2. **A failed status fetch is not an outage.** If the status endpoint itself
 *    is unreachable we fall back to "unknown", which shows nothing and gates
 *    nothing. The monitoring must never become the thing that breaks the app.
 *
 * 3. **Unknown is healthy.** `isDegraded` is false unless the backend
 *    positively reports the service as open/half-open. That is what makes it
 *    safe to call from a component rendered outside this provider (a page test,
 *    a public kiosk route) — the default context value below gates nothing.
 *    This deliberately differs from NotificationContext, which throws when its
 *    provider is missing: here, failing open is the whole point.
 */
import React, {
  createContext,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react';

import { isAuthenticated } from '../components/RequireAuth';
import { resilienceAPI } from '../services/api';
import { ResilienceStatus, ServiceKey, ServiceStatus } from '../types';

/** How often the snapshot is refreshed while signed in with a visible tab. */
export const SERVICE_STATUS_POLL_MS = 60_000;

/** States that count as degraded. Half-open is on trial — calls may still fail. */
const DEGRADED_STATES = ['open', 'half_open'];

export interface ServiceStatusContextType {
  /** Latest snapshot, or null while unknown (never fetched / failed / signed out). */
  status: ResilienceStatus | null;
  /** True only when the backend positively reports something degraded. */
  degraded: boolean;
  /** Every service the backend knows about; empty while unknown. */
  services: ServiceStatus[];
  /** The named service's row, or null when unknown. */
  getService: (key: ServiceKey) => ServiceStatus | null;
  /** True only when the named service is positively open/half-open. */
  isDegraded: (key: ServiceKey) => boolean;
  /** Fetch a fresh snapshot now (used after a control fails, and by tests). */
  refresh: () => Promise<void>;
}

const UNKNOWN: ServiceStatusContextType = {
  status: null,
  degraded: false,
  services: [],
  getService: () => null,
  isDegraded: () => false,
  refresh: async () => {},
};

const ServiceStatusContext = createContext<ServiceStatusContextType>(UNKNOWN);

/**
 * Read the shared service status. Safe outside the provider: returns the
 * unknown-but-healthy default rather than throwing, so no component can be
 * broken merely by being rendered somewhere the provider isn't mounted.
 */
export const useServiceStatusContext = (): ServiceStatusContextType =>
  useContext(ServiceStatusContext);

interface ServiceStatusProviderProps {
  children: ReactNode;
}

export const ServiceStatusProvider: React.FC<ServiceStatusProviderProps> = ({ children }) => {
  const [status, setStatus] = useState<ResilienceStatus | null>(null);
  const [authed, setAuthed] = useState<boolean>(() => isAuthenticated());

  // Login and logout both flip polling on/off without a reload. `authChange` is
  // the app's own signal (AuthSection dispatches it, Sidebar listens); `storage`
  // covers a sign-out performed in another tab.
  useEffect(() => {
    const sync = () => setAuthed(isAuthenticated());
    window.addEventListener('authChange', sync);
    window.addEventListener('storage', sync);
    return () => {
      window.removeEventListener('authChange', sync);
      window.removeEventListener('storage', sync);
    };
  }, []);

  const fetchStatus = useCallback(async () => {
    try {
      const response = await resilienceAPI.getStatus();
      const data = response?.data;
      // Defensive: a mocked/absent payload is "unknown", not a crash.
      setStatus(data && Array.isArray(data.services) ? data : null);
    } catch {
      // Deliberately silent, and deliberately back to unknown: a status
      // endpoint we cannot reach tells us nothing, and stale "degraded" would
      // keep gating controls on evidence we no longer have.
      setStatus(null);
    }
  }, []);

  useEffect(() => {
    if (!authed) {
      setStatus(null);
      return undefined;
    }

    let cancelled = false;
    const poll = async () => {
      // A backgrounded tab learns nothing it can show anyone; skip the request
      // and catch up on the visibilitychange below.
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;
      if (cancelled) return;
      await fetchStatus();
    };

    poll();
    const interval = window.setInterval(poll, SERVICE_STATUS_POLL_MS);
    const onVisibilityChange = () => {
      if (document.visibilityState === 'visible') poll();
    };
    document.addEventListener('visibilitychange', onVisibilityChange);

    return () => {
      cancelled = true;
      window.clearInterval(interval);
      document.removeEventListener('visibilitychange', onVisibilityChange);
    };
    // `fetchStatus` is stable (useCallback with no deps); `authed` is the real
    // trigger — sign in starts the loop, sign out tears it down.
  }, [authed, fetchStatus]);

  const services = useMemo(() => status?.services ?? [], [status]);

  const byKey = useMemo(() => {
    const map = new Map<string, ServiceStatus>();
    services.forEach((service) => map.set(service.key, service));
    return map;
  }, [services]);

  const getService = useCallback(
    (key: ServiceKey) => byKey.get(key) ?? null,
    [byKey],
  );

  const isDegraded = useCallback(
    (key: ServiceKey) => {
      const service = byKey.get(key);
      return Boolean(service) && DEGRADED_STATES.includes(service!.state);
    },
    [byKey],
  );

  const value = useMemo<ServiceStatusContextType>(
    () => ({
      status,
      degraded: Boolean(status?.degraded),
      services,
      getService,
      isDegraded,
      refresh: fetchStatus,
    }),
    [status, services, getService, isDegraded, fetchStatus],
  );

  return (
    <ServiceStatusContext.Provider value={value}>{children}</ServiceStatusContext.Provider>
  );
};

export default ServiceStatusContext;
