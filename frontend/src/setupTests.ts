// jest-dom adds custom matchers for asserting on DOM nodes.
import '@testing-library/jest-dom/vitest';
import { vi } from 'vitest';

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
