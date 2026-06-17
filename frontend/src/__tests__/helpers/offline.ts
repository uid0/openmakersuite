/**
 * Offline / poor-network test helpers.
 *
 * Journey slices (R2..R7 of #457) reuse these to drive the two ways the app
 * detects connectivity loss:
 *
 *  1. The `navigator.onLine` flag plus the `online`/`offline` window events
 *     that `useOnlineStatus` (and therefore `<OfflineIndicator />`) listens to.
 *  2. An axios error shaped like a real network failure — `error.request` set,
 *     `error.response` undefined, `code === 'ERR_NETWORK'` — which is how axios
 *     reports a request that left the browser but got no reply (offline, DNS
 *     failure, timeout). Components branch on this to show retry/offline UI.
 *
 * Example:
 *
 *   import { goOffline, networkError } from '../helpers/offline';
 *
 *   it('shows the offline banner', () => {
 *     goOffline();
 *     render(<OfflineIndicator />);
 *     expect(screen.getByText(/offline/i)).toBeInTheDocument();
 *   });
 *
 *   it('surfaces a retry on network failure', async () => {
 *     vi.mocked(api.get).mockRejectedValue(networkError());
 *     // ...assert the component renders its offline/retry affordance
 *   });
 *
 * `navigator.onLine` is reset to `true` automatically after every test (see the
 * `afterEach` below), so a test that calls `goOffline()` cannot leak its state
 * into the next one.
 */
import { AxiosError } from 'axios';
import { afterEach } from 'vitest';

let patched = false;

const setNavigatorOnLine = (value: boolean): void => {
  Object.defineProperty(window.navigator, 'onLine', {
    configurable: true,
    get: () => value,
  });
  patched = true;
};

/** Mark the browser offline and fire the `offline` event. */
export const goOffline = (): void => {
  setNavigatorOnLine(false);
  window.dispatchEvent(new Event('offline'));
};

/** Mark the browser online and fire the `online` event. */
export const goOnline = (): void => {
  setNavigatorOnLine(true);
  window.dispatchEvent(new Event('online'));
};

/** Restore the default (`navigator.onLine === true`) without firing an event. */
export const resetOnlineStatus = (): void => {
  if (!patched) {
    return;
  }
  Object.defineProperty(window.navigator, 'onLine', {
    configurable: true,
    get: () => true,
  });
  patched = false;
};

/**
 * Build an axios network-failure error (offline signature: `request` set,
 * `response` undefined, `code === 'ERR_NETWORK'`, `isAxiosError === true`).
 */
export const networkError = (message = 'Network Error'): AxiosError =>
  new AxiosError(message, AxiosError.ERR_NETWORK, undefined, {});

// Auto-reset so an offline state set by one test never bleeds into the next.
afterEach(() => {
  resetOnlineStatus();
});
