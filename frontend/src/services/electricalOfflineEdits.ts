/**
 * Queueable wrappers around the electrical-topology mutation endpoints.
 *
 * Callers (form pages) import these instead of calling
 * `electricalTopologyAPI.update*` directly when they want the edit to
 * be durable across an intermittent network. On network failure the
 * payload is enqueued to localStorage and re-applied automatically
 * when connectivity returns (see hooks/useOfflineQueue.ts).
 *
 * Scope: PATCH only for v1. CREATE / DELETE require server-assigned IDs
 * to chain further work and aren't yet supported.
 */
import {
  enqueueOfflineMutation,
  isQueueableError,
  OfflineQueueEntry,
  OfflineResource,
  ReplaySummary,
} from '../utils/offlineQueue';

import {
  electricalTopologyAPI,
  PowerBreakerWritable,
  PowerCircuitWritable,
  PowerOutletWritable,
  PowerPanelWritable,
} from './api';

export interface QueueableUpdateResult<T> {
  /** True when the server accepted the mutation; false when it was queued. */
  applied: boolean;
  /** Server response when applied=true; null when queued. */
  data: T | null;
  /** Queue entry id when applied=false; null otherwise. */
  queuedId: string | null;
}

async function attempt<T>(
  resource: OfflineResource,
  resourceId: number | string,
  label: string,
  payload: Record<string, unknown>,
  callApi: () => Promise<{ data: T }>,
): Promise<QueueableUpdateResult<T>> {
  try {
    const response = await callApi();
    return { applied: true, data: response.data, queuedId: null };
  } catch (err) {
    if (!isQueueableError(err)) {
      throw err;
    }
    const entry = enqueueOfflineMutation({
      method: 'PATCH',
      resource,
      resourceId,
      label,
      payload,
    });
    return { applied: false, data: null, queuedId: entry.id };
  }
}

export function queueablePatchBreaker(
  id: number | string,
  payload: Partial<PowerBreakerWritable>,
  label: string,
): Promise<QueueableUpdateResult<unknown>> {
  return attempt('breakers', id, label, payload as Record<string, unknown>, () =>
    electricalTopologyAPI.updateBreaker(id, payload),
  );
}

export function queueablePatchPanel(
  id: number | string,
  payload: Partial<PowerPanelWritable>,
  label: string,
): Promise<QueueableUpdateResult<unknown>> {
  return attempt('panels', id, label, payload as Record<string, unknown>, () =>
    electricalTopologyAPI.updatePanel(id, payload),
  );
}

export function queueablePatchCircuit(
  id: number | string,
  payload: Partial<PowerCircuitWritable>,
  label: string,
): Promise<QueueableUpdateResult<unknown>> {
  return attempt('circuits', id, label, payload as Record<string, unknown>, () =>
    electricalTopologyAPI.updateCircuit(id, payload),
  );
}

export function queueablePatchOutlet(
  id: number | string,
  payload: Partial<PowerOutletWritable>,
  label: string,
): Promise<QueueableUpdateResult<unknown>> {
  return attempt('outlets', id, label, payload as Record<string, unknown>, () =>
    electricalTopologyAPI.updateOutlet(id, payload),
  );
}

/**
 * Sender used by useOfflineQueue to replay a queued entry. Maps
 * (resource, method, resourceId, payload) back onto the API client.
 * Rejects on failure so the entry stays in the queue.
 */
export function buildOfflineSender(): (entry: OfflineQueueEntry) => Promise<void> {
  return async (entry) => {
    if (entry.method !== 'PATCH') {
      throw new Error(`Unsupported queued method: ${entry.method}`);
    }
    switch (entry.resource) {
      case 'breakers':
        await electricalTopologyAPI.updateBreaker(entry.resourceId, entry.payload as Partial<PowerBreakerWritable>);
        return;
      case 'panels':
        await electricalTopologyAPI.updatePanel(entry.resourceId, entry.payload as Partial<PowerPanelWritable>);
        return;
      case 'circuits':
        await electricalTopologyAPI.updateCircuit(entry.resourceId, entry.payload as Partial<PowerCircuitWritable>);
        return;
      case 'outlets':
        await electricalTopologyAPI.updateOutlet(entry.resourceId, entry.payload as Partial<PowerOutletWritable>);
        return;
      default: {
        const _exhaustive: never = entry.resource;
        throw new Error(`Unknown resource in queue: ${_exhaustive as string}`);
      }
    }
  };
}

export type { ReplaySummary };
