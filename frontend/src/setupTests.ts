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
// Cancel leaked timers after every test (Mantine transition + @mantine/
// notifications auto-close timers).
//
// Mantine drives notifications, modals, tooltips, menus, etc. through
// @mantine/core's <Transition> (components/Transition/use-transition.ts), whose
// animation ends in a `window.setTimeout(…, transitionDuration)`;
// @mantine/notifications then adds a multi-second auto-close `window.setTimeout`
// on top. When one of those fires AFTER Testing Library has unmounted the tree —
// or after vitest tears down this file's jsdom environment — it calls
// `dispatchSetState` on a torn-down React 19 root and throws inside
// `resolveUpdatePriority`. Vitest v4 reports that as an unhandled error and
// reddens the whole run even though every assertion passed. It is flaky because
// it depends on teardown-vs-timer timing: a file passes in isolation but the
// full suite fails.
//
// First seen on the notification auto-close timer (gh-660), which a >=1000ms
// threshold fixed. It resurfaced on the one-click mark-ordered notification
// (op-28u): there the SUB-second transition timer, not the auto-close one, won
// the teardown race, so the threshold let it through. So track every window
// timer and clear whatever is still pending after each test.
//
// Fix (test-only, no production impact): afterEach runs after Testing Library's
// own unmount (registered later, so it runs first), so a timer still pending
// here is leaked async work the finished test no longer needs. Clearing it
// cannot affect an assertion that already ran, and it guarantees no Mantine
// transition or notification callback can fire against a torn-down root. Only
// setTimeout is wrapped (the leaking callback is a setTimeout, per the
// use-transition.mjs stack); requestAnimationFrame is deliberately left alone so
// jsdom's pretendToBeVisual frame loop — which Mantine/floating-ui popovers rely
// on — keeps running normally between tests.
// ---------------------------------------------------------------------------
const pendingTimers = new Set<unknown>();
const nativeSetTimeout = window.setTimeout.bind(window);
const nativeClearTimeout = window.clearTimeout.bind(window);

window.setTimeout = ((handler: TimerHandler, timeout?: number, ...args: unknown[]) => {
  const id = nativeSetTimeout(handler as never, timeout as never, ...(args as never[]));
  pendingTimers.add(id);
  return id;
}) as typeof window.setTimeout;

window.clearTimeout = ((id?: unknown) => {
  pendingTimers.delete(id);
  return nativeClearTimeout(id as never);
}) as typeof window.clearTimeout;

afterEach(() => {
  pendingTimers.forEach((id) => nativeClearTimeout(id as never));
  pendingTimers.clear();
});
