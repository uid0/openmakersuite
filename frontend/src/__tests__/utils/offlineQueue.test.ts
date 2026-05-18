import {
  clearOfflineQueue,
  enqueueOfflineMutation,
  isQueueableError,
  listOfflineEntries,
  removeOfflineEntry,
  replayOfflineQueue,
} from '../../utils/offlineQueue';

describe('utils/offlineQueue', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  describe('enqueue + list + remove', () => {
    it('round-trips a single entry through localStorage', () => {
      const entry = enqueueOfflineMutation({
        method: 'PATCH',
        resource: 'breakers',
        resourceId: 42,
        label: 'Breaker 14 — needs attention',
        payload: { review_status: 'needs_attention' },
      });
      expect(entry.id).toBeTruthy();
      expect(entry.queuedAt).toBeGreaterThan(0);
      expect(entry.attemptCount).toBe(0);

      const fromStorage = listOfflineEntries();
      expect(fromStorage).toHaveLength(1);
      expect(fromStorage[0]).toMatchObject({
        method: 'PATCH',
        resource: 'breakers',
        resourceId: 42,
      });
    });

    it('preserves insertion order across multiple enqueues', () => {
      enqueueOfflineMutation({
        method: 'PATCH',
        resource: 'breakers',
        resourceId: 1,
        label: 'first',
        payload: {},
      });
      enqueueOfflineMutation({
        method: 'PATCH',
        resource: 'breakers',
        resourceId: 2,
        label: 'second',
        payload: {},
      });
      enqueueOfflineMutation({
        method: 'PATCH',
        resource: 'breakers',
        resourceId: 3,
        label: 'third',
        payload: {},
      });
      expect(listOfflineEntries().map((e) => e.resourceId)).toEqual([1, 2, 3]);
    });

    it('removes a single entry without affecting others', () => {
      const e1 = enqueueOfflineMutation({
        method: 'PATCH',
        resource: 'breakers',
        resourceId: 1,
        label: 'one',
        payload: {},
      });
      const e2 = enqueueOfflineMutation({
        method: 'PATCH',
        resource: 'breakers',
        resourceId: 2,
        label: 'two',
        payload: {},
      });
      removeOfflineEntry(e1.id);
      const remaining = listOfflineEntries();
      expect(remaining).toHaveLength(1);
      expect(remaining[0].id).toBe(e2.id);
    });

    it('clearOfflineQueue empties everything', () => {
      enqueueOfflineMutation({
        method: 'PATCH',
        resource: 'breakers',
        resourceId: 1,
        label: 'one',
        payload: {},
      });
      clearOfflineQueue();
      expect(listOfflineEntries()).toEqual([]);
    });
  });

  describe('replayOfflineQueue', () => {
    it('removes succeeded entries and keeps failed ones', async () => {
      enqueueOfflineMutation({
        method: 'PATCH',
        resource: 'breakers',
        resourceId: 1,
        label: 'will succeed',
        payload: {},
      });
      enqueueOfflineMutation({
        method: 'PATCH',
        resource: 'breakers',
        resourceId: 2,
        label: 'will fail',
        payload: {},
      });

      const sender = jest.fn(async (entry) => {
        if (entry.resourceId === 2) throw new Error('still offline');
      });

      const summary = await replayOfflineQueue(sender);
      expect(summary).toEqual({ attempted: 2, succeeded: 1, failed: 1 });
      const remaining = listOfflineEntries();
      expect(remaining).toHaveLength(1);
      expect(remaining[0].resourceId).toBe(2);
      expect(remaining[0].lastError).toBe('still offline');
      expect(remaining[0].attemptCount).toBe(1);
    });

    it('returns zero summary when queue is empty', async () => {
      const sender = jest.fn();
      const summary = await replayOfflineQueue(sender);
      expect(summary).toEqual({ attempted: 0, succeeded: 0, failed: 0 });
      expect(sender).not.toHaveBeenCalled();
    });
  });

  describe('isQueueableError', () => {
    it('queues axios ERR_NETWORK', () => {
      expect(isQueueableError({ code: 'ERR_NETWORK', message: 'Network Error' })).toBe(true);
    });

    it('queues 5xx responses', () => {
      expect(isQueueableError({ response: { status: 502 } })).toBe(true);
      expect(isQueueableError({ response: { status: 503 } })).toBe(true);
    });

    it('does NOT queue 4xx (client-fault, would loop forever)', () => {
      expect(isQueueableError({ response: { status: 400 } })).toBe(false);
      expect(isQueueableError({ response: { status: 404 } })).toBe(false);
      expect(isQueueableError({ response: { status: 422 } })).toBe(false);
    });

    it('queues axios "Network Error" message without a response', () => {
      expect(isQueueableError({ message: 'Network Error' })).toBe(true);
    });

    it('does not queue null / undefined / random values', () => {
      expect(isQueueableError(null)).toBe(false);
      expect(isQueueableError(undefined)).toBe(false);
      expect(isQueueableError('a string error')).toBe(false);
    });
  });
});
