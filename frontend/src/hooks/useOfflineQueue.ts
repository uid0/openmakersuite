/**
 * React-side surface for the electrical offline-edit queue.
 *
 * Wires up:
 * - Live subscription to queue changes (same-tab via custom event,
 *   cross-tab via the storage event).
 * - Auto-sync on transitions to online + on a periodic 60s heartbeat,
 *   so a user who left their browser open across a network blip gets
 *   their queue replayed without manual intervention.
 * - A manual `syncNow()` for the "Sync now" button.
 *
 * The replay strategy is supplied by the caller (the axios client lives
 * outside this hook so the util layer doesn't pull in `services/api`
 * and create a circular module). Pass a sender that maps an entry back
 * to the actual HTTP request.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import {
  listOfflineEntries,
  OfflineQueueEntry,
  removeOfflineEntry,
  replayOfflineQueue,
  ReplaySummary,
} from '../utils/offlineQueue';

import { useOnlineStatus } from './useOnlineStatus';

const PERIODIC_RETRY_MS = 60 * 1000;

export interface UseOfflineQueueArgs {
  /** Caller-provided HTTP sender. Should reject on failure so the
   * queue keeps the entry; resolve on success so it's removed. */
  sender: (entry: OfflineQueueEntry) => Promise<void>;
}

export interface UseOfflineQueueReturn {
  entries: OfflineQueueEntry[];
  pendingCount: number;
  lastSyncAt: number | null;
  lastSyncSummary: ReplaySummary | null;
  isSyncing: boolean;
  isOnline: boolean;
  syncNow: () => Promise<ReplaySummary | null>;
  removeEntry: (id: string) => void;
}

export function useOfflineQueue(args: UseOfflineQueueArgs): UseOfflineQueueReturn {
  const { sender } = args;
  const [entries, setEntries] = useState<OfflineQueueEntry[]>(() => listOfflineEntries());
  const [isSyncing, setIsSyncing] = useState(false);
  const [lastSyncAt, setLastSyncAt] = useState<number | null>(null);
  const [lastSyncSummary, setLastSyncSummary] = useState<ReplaySummary | null>(null);
  const { isOnline } = useOnlineStatus();
  // Hold sender in a ref so the auto-sync effect doesn't rebind every
  // render — callers often pass freshly-built closures.
  const senderRef = useRef(sender);
  senderRef.current = sender;

  const refresh = useCallback(() => {
    setEntries(listOfflineEntries());
  }, []);

  const syncNow = useCallback(async (): Promise<ReplaySummary | null> => {
    if (isSyncing) return null;
    if (listOfflineEntries().length === 0) return null;
    setIsSyncing(true);
    try {
      const summary = await replayOfflineQueue(senderRef.current);
      setLastSyncAt(Date.now());
      setLastSyncSummary(summary);
      refresh();
      return summary;
    } finally {
      setIsSyncing(false);
    }
  }, [isSyncing, refresh]);

  // Subscribe to queue changes (own tab + other tabs).
  useEffect(() => {
    const handler = () => refresh();
    window.addEventListener('oms:offline-queue-changed', handler);
    window.addEventListener('storage', handler);
    return () => {
      window.removeEventListener('oms:offline-queue-changed', handler);
      window.removeEventListener('storage', handler);
    };
  }, [refresh]);

  // Auto-sync on online transitions.
  useEffect(() => {
    if (!isOnline) return;
    // Fire-and-forget. Errors are absorbed into entry.lastError by replay.
    syncNow();
  }, [isOnline, syncNow]);

  // Periodic heartbeat — catches the case where navigator.onLine never
  // flipped but the network actually came back (some captive portals
  // and corporate proxies don't dispatch the event reliably).
  useEffect(() => {
    const id = window.setInterval(() => {
      if (listOfflineEntries().length === 0) return;
      syncNow();
    }, PERIODIC_RETRY_MS);
    return () => window.clearInterval(id);
  }, [syncNow]);

  return {
    entries,
    pendingCount: entries.length,
    lastSyncAt,
    lastSyncSummary,
    isSyncing,
    isOnline,
    syncNow,
    removeEntry: (id) => {
      removeOfflineEntry(id);
      refresh();
    },
  };
}
