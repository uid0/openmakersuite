/**
 * useServiceStatus Hook
 * Convenience hook for reading which external services are currently degraded.
 *
 * Backed by one shared poll (contexts/ServiceStatusContext) — call it from as
 * many components as you like without adding a request. Outside the provider
 * it reports everything healthy, so it never gates a control on ignorance.
 */
import { useServiceStatusContext } from '../contexts/ServiceStatusContext';

export const useServiceStatus = () => {
  return useServiceStatusContext();
};

export default useServiceStatus;
