// jest-dom adds custom jest matchers for asserting on DOM nodes.
// allows you to do things like:
// expect(element).toHaveTextContent(/react/i)
// learn more: https://github.com/testing-library/jest-dom
import '@testing-library/jest-dom';

// Mock Highlight to avoid initialization in tests. We mock both packages
// because @highlight-run/react re-exports ErrorBoundary independently of the
// core highlight.run runtime; the boundary needs to render its children
// without requiring the real H global to exist.
jest.mock('highlight.run', () => ({
  H: {
    init: jest.fn(),
    consume: jest.fn(),
    consumeError: jest.fn(),
    identify: jest.fn(),
    track: jest.fn(),
    start: jest.fn(),
    stop: jest.fn(),
  },
}));

jest.mock('@highlight-run/react', () => ({
  ErrorBoundary: ({ children }: any) => children,
}));

// Mock window.matchMedia - must be set up before any imports that use it
// This is critical for Mantine components that use use-media-query hook
const matchMediaMock = (query: string) => ({
  matches: false,
  media: query,
  onchange: null,
  addListener: jest.fn(),
  removeListener: jest.fn(),
  addEventListener: jest.fn(),
  removeEventListener: jest.fn(),
  dispatchEvent: jest.fn(),
});

// Set up matchMedia before any tests run - use both approaches
if (typeof window !== 'undefined') {
  (window as any).matchMedia = matchMediaMock;
}

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: matchMediaMock,
  configurable: true,
});

// Mock ResizeObserver for Mantine components (used by ScrollArea, etc.)
global.ResizeObserver = jest.fn().mockImplementation(() => ({
  observe: jest.fn(),
  unobserve: jest.fn(),
  disconnect: jest.fn(),
}));

// Mock ResizeObserver for Mantine components (used by ScrollArea, etc.)
// Must be a class, not just a function
class ResizeObserverMock {
  observe = jest.fn();
  unobserve = jest.fn();
  disconnect = jest.fn();
}

global.ResizeObserver = ResizeObserverMock as any;
if (typeof window !== 'undefined') {
  (window as any).ResizeObserver = ResizeObserverMock;
}

// Mock scrollIntoView for Mantine Combobox
Element.prototype.scrollIntoView = jest.fn();
