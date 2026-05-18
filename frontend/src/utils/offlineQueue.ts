/**
 * LocalStorage-backed FIFO queue for electrical-topology mutations that
 * fail because the operator is walking around a building with spotty
 * wifi.
 *
 * Scope: PATCH/PUT/DELETE on power-topology resources (PowerPanel,
 * PowerBreaker, PowerCircuit, PowerOutlet). Creates are intentionally
 * out of scope for v1 — they need server-assigned IDs to chain follow-up
 * operations, which complicates the queue model.
 *
 * Design contract:
 * - Each entry carries a stable `id` (uuid) so callers can correlate
 *   the queued operation with their optimistic UI.
 * - Entries are durable across page reloads via localStorage.
 * - Operations are idempotent: the queued payload is the full PATCH
 *   body, so replaying twice produces the same end state.
 * - No conflict resolution: last write wins. Operators editing the same
 *   resource from two devices will overwrite each other; out of scope
 *   for this iteration.
 */

const STORAGE_KEY = 'oms.electrical.offline-queue.v1';

export type OfflineResource = 'panels' | 'breakers' | 'circuits' | 'outlets';
export type OfflineMethod = 'PATCH' | 'PUT' | 'DELETE';

export interface OfflineQueueEntry {
  id: string;
  method: OfflineMethod;
  resource: OfflineResource;
  /**
   * Resource ID being mutated. For DELETE this is required; for PATCH
   * this is the existing row id.
   */
  resourceId: number | string;
  /** Human label so the queue UI can show what's pending without a network round-trip. */
  label: string;
  /** The PATCH payload (or empty for DELETE). */
  payload: Record<string, unknown>;
  queuedAt: number;
  lastAttemptAt: number | null;
  lastError: string | null;
  attemptCount: number;
}

function readQueue(): OfflineQueueEntry[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    // Corrupt storage — fall back to empty rather than crashing the
    // whole app for one bad JSON blob. The user will lose pending
    // edits in this rare case, which is preferable to never loading.
    return [];
  }
}

function writeQueue(entries: OfflineQueueEntry[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(entries));
    // Hand-fire a storage event for same-tab subscribers (the browser
    // only fires `storage` for OTHER tabs). Custom event keeps the
    // useOfflineQueue hook in sync within the same window.
    window.dispatchEvent(new CustomEvent('oms:offline-queue-changed'));
  } catch (err) {
    // Quota exceeded or localStorage disabled — log and continue.
    // The mutation will fail loudly upstream rather than silently drop.
    console.error('Failed to persist offline queue', err);
  }
}

function genId(): string {
  // crypto.randomUUID() is available in all browsers we target; the
  // fallback covers jsdom/test environments that haven't shimmed it.
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `oq-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export function listOfflineEntries(): OfflineQueueEntry[] {
  return readQueue();
}

export function enqueueOfflineMutation(
  args: Omit<OfflineQueueEntry, 'id' | 'queuedAt' | 'lastAttemptAt' | 'lastError' | 'attemptCount'>,
): OfflineQueueEntry {
  const entry: OfflineQueueEntry = {
    ...args,
    id: genId(),
    queuedAt: Date.now(),
    lastAttemptAt: null,
    lastError: null,
    attemptCount: 0,
  };
  const queue = readQueue();
  queue.push(entry);
  writeQueue(queue);
  return entry;
}

export function removeOfflineEntry(entryId: string): void {
  const queue = readQueue().filter((e) => e.id !== entryId);
  writeQueue(queue);
}

export function clearOfflineQueue(): void {
  writeQueue([]);
}

function markAttempt(entryId: string, error: string | null): void {
  const queue = readQueue();
  const idx = queue.findIndex((e) => e.id === entryId);
  if (idx === -1) return;
  queue[idx] = {
    ...queue[idx],
    lastAttemptAt: Date.now(),
    lastError: error,
    attemptCount: queue[idx].attemptCount + 1,
  };
  writeQueue(queue);
}

/**
 * Replay every queued entry. Caller supplies a `sender` that knows how
 * to convert an entry back into an HTTP call (typically the axios
 * client). Successful entries are removed; failed ones stay queued
 * with their lastError updated.
 *
 * Returns a summary so the UI can flash "Synced N edits" / "M failed".
 */
export interface ReplaySummary {
  attempted: number;
  succeeded: number;
  failed: number;
}

export async function replayOfflineQueue(
  sender: (entry: OfflineQueueEntry) => Promise<void>,
): Promise<ReplaySummary> {
  const entries = readQueue();
  let succeeded = 0;
  let failed = 0;

  for (const entry of entries) {
    try {
      await sender(entry);
      removeOfflineEntry(entry.id);
      succeeded += 1;
    } catch (err) {
      const message =
        err instanceof Error ? err.message : typeof err === 'string' ? err : 'unknown error';
      markAttempt(entry.id, message);
      failed += 1;
    }
  }

  return { attempted: entries.length, succeeded, failed };
}

/**
 * Heuristic for "should this error queue?". We only queue on
 * network-level failures (offline, DNS, timeout) — server-side 4xx
 * means the mutation is malformed and would fail forever in the
 * queue. 5xx is debatable; for v1 we treat 5xx as queue-worthy too
 * since it usually clears on retry.
 */
export function isQueueableError(err: unknown): boolean {
  if (!err || typeof err !== 'object') return false;
  // axios shape
  const ax = err as { code?: string; response?: { status?: number }; message?: string };
  if (ax.code === 'ERR_NETWORK' || ax.code === 'ECONNABORTED') return true;
  if (ax.response && typeof ax.response.status === 'number') {
    const s = ax.response.status;
    return s >= 500 && s < 600;
  }
  // No response object usually means the request never reached the server.
  if (!ax.response && ax.message && /Network Error|timeout/i.test(ax.message)) return true;
  return false;
}
