/// <reference types="vite/client" />
/// <reference types="vite-plugin-pwa/client" />
/// <reference types="vitest/globals" />

import type { Mock, MockInstance, Mocked, MockedClass, MockedFunction, MockedObject, vi } from 'vitest';

declare global {
  // Setup shim: `jest` is aliased to `vi` at runtime in setupTests.ts so
  // existing test sites that call `jest.fn()`, `jest.mock()`, `jest.spyOn()`,
  // etc. keep compiling without per-file conversion. We also alias the
  // common type-namespace members (`jest.Mock`, `jest.Mocked<T>`, …) so
  // type assertions resolve to vitest's equivalents.
  const jest: typeof vi;
  // eslint-disable-next-line @typescript-eslint/no-namespace
  namespace jest {
    type Mock<TArgs extends any[] = any[], TReturn = any> = import('vitest').Mock<TArgs, TReturn>;
    type MockInstance<TReturn = any, TArgs extends any[] = any[]> = import('vitest').MockInstance<TArgs, TReturn>;
    type Mocked<T> = import('vitest').Mocked<T>;
    type MockedClass<T extends abstract new (...args: any[]) => any> = import('vitest').MockedClass<T>;
    type MockedFunction<T extends (...args: any[]) => any> = import('vitest').MockedFunction<T>;
    type MockedObject<T> = import('vitest').MockedObject<T>;
  }
}

interface ImportMetaEnv {
  readonly VITE_API_URL?: string;
  readonly VITE_GIT_HASH?: string;
  readonly VITE_GITHUB_URL?: string;
  readonly VITE_SENTRY_DSN?: string;
  readonly VITE_DASHBOARD_TITLE?: string;
  readonly VITE_DASHBOARD_SUBTITLE?: string;
  readonly VITE_DASHBOARD_LOGO?: string;
  readonly VITE_SHOW_LOGO?: string;
  readonly VITE_SHOW_TRANSPARENCY?: string;
  readonly VITE_FOOTER_MESSAGES?: string;
  readonly VITE_MESSAGE_ROTATION_SECONDS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
