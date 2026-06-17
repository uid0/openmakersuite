// jest-dom adds custom matchers for asserting on DOM nodes.
import '@testing-library/jest-dom/vitest';
import { toHaveNoViolations } from 'jest-axe';
import { afterEach, expect, vi } from 'vitest';

// Register the jest-axe accessibility matcher globally so `toHaveNoViolations`
// (and the `expectNoA11yViolations` helper that wraps it) is available in every
// test without per-file setup. See src/__tests__/helpers/axe.ts.
expect.extend(toHaveNoViolations);

// Shim: existing test files reference `jest.fn` / `jest.mock` etc.
// Vitest's `vi` is API-compatible for the operations used here, so
// alias `jest` to `vi` globally to avoid touching ~400 test sites.
(globalThis as typeof globalThis & { jest: typeof vi }).jest = vi;

// Node 24's experimental built-in localStorage shadows jsdom's when
// `window === globalThis`, leaving `localStorage` undefined unless the
// process was started with `--localstorage-file`. Install a deterministic
// in-memory backing store so tests don't depend on Node CLI flags.
const installMemoryStorage = (target: string) => {
  const store = new Map<string, string>();
  const storage: Storage = {
    get length() {
      return store.size;
    },
    clear: () => store.clear(),
    getItem: (key) => (store.has(key) ? store.get(key)! : null),
    setItem: (key, value) => {
      store.set(key, String(value));
    },
    removeItem: (key) => {
      store.delete(key);
    },
    key: (index) => Array.from(store.keys())[index] ?? null,
  };
  Object.defineProperty(globalThis, target, {
    configurable: true,
    writable: true,
    value: storage,
  });
  if (typeof window !== 'undefined') {
    Object.defineProperty(window, target, {
      configurable: true,
      writable: true,
      value: storage,
    });
  }
};
installMemoryStorage('localStorage');
installMemoryStorage('sessionStorage');

const matchMediaMock = (query: string) => ({
  matches: false,
  media: query,
  onchange: null,
  addListener: vi.fn(),
  removeListener: vi.fn(),
  addEventListener: vi.fn(),
  removeEventListener: vi.fn(),
  dispatchEvent: vi.fn(),
});

if (typeof window !== 'undefined') {
  (window as unknown as { matchMedia: typeof matchMediaMock }).matchMedia = matchMediaMock;
}

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: matchMediaMock,
  configurable: true,
});

class ResizeObserverMock {
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
}

(globalThis as unknown as { ResizeObserver: typeof ResizeObserverMock }).ResizeObserver = ResizeObserverMock;
if (typeof window !== 'undefined') {
  (window as unknown as { ResizeObserver: typeof ResizeObserverMock }).ResizeObserver = ResizeObserverMock;
}

// jsdom has no IntersectionObserver; provide an inert default so components
// using it for infinite scroll render without throwing. Individual tests may
// override this to capture the callback and simulate intersections.
class IntersectionObserverMock {
  constructor(_callback: IntersectionObserverCallback) {}
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
  takeRecords = vi.fn(() => []);
}

(globalThis as unknown as { IntersectionObserver: typeof IntersectionObserverMock }).IntersectionObserver =
  IntersectionObserverMock;
if (typeof window !== 'undefined') {
  (window as unknown as { IntersectionObserver: typeof IntersectionObserverMock }).IntersectionObserver =
    IntersectionObserverMock;
}

Element.prototype.scrollIntoView = vi.fn();

// jsdom has no FontFaceSet — Mantine v9's Textarea/Autosize listens
// for "loadingdone" on document.fonts so the textarea resizes once
// custom fonts swap in. Provide an inert event-target shim so the
// hook can subscribe without crashing.
if (typeof document !== 'undefined' && !(document as Document & { fonts?: unknown }).fonts) {
  Object.defineProperty(document, 'fonts', {
    configurable: true,
    value: {
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
      ready: Promise.resolve(),
    },
  });
}

// ---------------------------------------------------------------------------
// Clear leaked long-lived timers after every test (e.g. @mantine/notifications
// auto-close timers).
//
// Mantine v9's NotificationContainer schedules its auto-close
// `window.setTimeout(handleHide, …)` from two separate effects that share a
// single `autoCloseTimeout` ref, so the first timer's id is overwritten and
// orphaned. On unmount Mantine cancels only the tracked (second) timer; the
// orphaned one survives, then fires a few seconds later — after vitest has torn
// down this file's jsdom environment — and runs `window.clearTimeout(...)`,
// throwing "window is not defined". Under vitest v4's stricter unhandled-error
// handling that reddens the whole run. It is flaky because it depends on
// teardown-vs-timer timing: a file passes in isolation but fails in the full
// suite (observed first on AdminDashboard.test.tsx, gh-660).
//
// Fix (test-only, no production impact): track timers with a delay at or above
// the threshold and clear any still pending after each test, so no notification
// auto-close timer (autoClose is 3–5s in this app) can outlive the test that
// scheduled it. The threshold leaves sub-second timers (Testing Library
// polling, Mantine transitions, React scheduling) untouched to keep the blast
// radius minimal.
// ---------------------------------------------------------------------------
const LEAKED_TIMER_THRESHOLD_MS = 1000;
const longLivedTimers = new Set<unknown>();
const nativeSetTimeout = window.setTimeout.bind(window);
const nativeClearTimeout = window.clearTimeout.bind(window);

window.setTimeout = ((handler: TimerHandler, timeout?: number, ...args: unknown[]) => {
  const id = nativeSetTimeout(handler as never, timeout as never, ...(args as never[]));
  if (typeof timeout === 'number' && timeout >= LEAKED_TIMER_THRESHOLD_MS) {
    longLivedTimers.add(id);
  }
  return id;
}) as typeof window.setTimeout;

window.clearTimeout = ((id?: unknown) => {
  longLivedTimers.delete(id);
  return nativeClearTimeout(id as never);
}) as typeof window.clearTimeout;

afterEach(() => {
  longLivedTimers.forEach((id) => nativeClearTimeout(id as never));
  longLivedTimers.clear();
});
