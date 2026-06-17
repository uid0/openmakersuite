/**
 * Accessibility (a11y) test helper.
 *
 * Wraps `jest-axe` so journey slices can assert a rendered subtree has no
 * accessibility violations with a single call:
 *
 *   const { container } = render(<Foo />);
 *   await expectNoA11yViolations(container);
 *
 * The `toHaveNoViolations` matcher is registered globally in
 * `src/setupTests.ts`; this helper centralises the `axe(...)` call plus the
 * jsdom-friendly default configuration so every slice runs the same audit.
 */
import { axe } from 'jest-axe';
import { expect } from 'vitest';

// Augment Vitest's matcher interface with the jest-axe matcher registered in
// setupTests.ts so `expect(results).toHaveNoViolations()` type-checks.
declare module 'vitest' {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  interface Assertion<T = any> {
    toHaveNoViolations(): T;
  }
  interface AsymmetricMatchersContaining {
    toHaveNoViolations(): unknown;
  }
}

type AxeOptions = Parameters<typeof axe>[1];

/**
 * Default axe configuration tuned for component-level testing in jsdom:
 *
 *  - Restrict to WCAG 2.0/2.1 A & AA rules. Best-practice rules (landmark /
 *    region / heading-order / dialog-name) assume a full page and produce false
 *    positives when auditing an isolated component fragment.
 *  - Disable `color-contrast`: jsdom performs no layout, so axe cannot compute a
 *    contrast ratio and the rule can only ever return an inconclusive result.
 */
const DEFAULT_OPTIONS: AxeOptions = {
  runOnly: {
    type: 'tag',
    values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'],
  },
  rules: {
    'color-contrast': { enabled: false },
  },
};

/**
 * Assert that `container` has no WCAG A/AA accessibility violations.
 *
 * @param container  A rendered element (e.g. `container` or `baseElement` from
 *                   `@testing-library/react`'s `render`). Use `baseElement`
 *                   (the default `document.body`) for components that portal
 *                   their content, such as Mantine `Modal`/`Drawer`.
 * @param options    Optional axe overrides merged over the defaults.
 */
export async function expectNoA11yViolations(
  container: Element,
  options: AxeOptions = {}
): Promise<void> {
  const results = await axe(container, { ...DEFAULT_OPTIONS, ...options });
  expect(results).toHaveNoViolations();
}
