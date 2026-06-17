/**
 * Ambient type declarations for `jest-axe` (v10 ships no types of its own).
 *
 * Only the surface this codebase consumes is declared. The typed entry point
 * tests should use is the helper in `src/__tests__/helpers/axe.ts`; this
 * declaration exists so `tsc` resolves the bare `jest-axe` import without
 * pulling Jest's global type definitions (this project runs on Vitest).
 */
declare module 'jest-axe' {
  import type AxeCore from 'axe-core';

  export type JestAxeConfigureOptions = AxeCore.RunOptions & {
    globalOptions?: AxeCore.Spec;
  };

  export function axe(
    html: AxeCore.ElementContext,
    options?: JestAxeConfigureOptions
  ): Promise<AxeCore.AxeResults>;

  export function configureAxe(
    options?: JestAxeConfigureOptions
  ): typeof axe;

  /** Matcher object passed to `expect.extend(...)`. */
  export const toHaveNoViolations: Record<
    string,
    (results: AxeCore.AxeResults) => { pass: boolean; message(): string }
  >;
}
