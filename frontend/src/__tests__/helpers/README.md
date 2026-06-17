# Frontend test helpers

Reusable test harnesses for the `#457` frontend journey-resilience program.
Slice **R1** added these so the journey slices (**R2..R7**) share one a11y audit
and one offline-simulation pattern instead of re-rolling them per test.

## `axe.ts` — accessibility assertions

Wraps [`jest-axe`](https://github.com/nickcolley/jest-axe). The
`toHaveNoViolations` matcher is registered globally in `src/setupTests.ts`, so
you only import the helper:

```ts
import { render } from '@testing-library/react';
import { expectNoA11yViolations } from '../helpers/axe';

it('has no accessibility violations', async () => {
  const { container } = render(<MyComponent />);
  await expectNoA11yViolations(container);
});
```

For components that **portal** their content (Mantine `Modal`, `Drawer`,
`Menu`, tooltips), audit `baseElement` (defaults to `document.body`) instead of
`container`, otherwise the portaled DOM is not included:

```ts
const { baseElement } = render(<CommandPalette isOpen onClose={vi.fn()} />);
await expectNoA11yViolations(baseElement);
```

Defaults are tuned for component-level testing in jsdom: only **WCAG 2.0/2.1 A
& AA** rules run (page-level best-practice rules like `region`/`landmark` would
false-positive on a fragment), and `color-contrast` is disabled (jsdom does no
layout, so the ratio is never computable). Pass an axe options object as the
second argument to override.

## `offline.ts` — offline / poor-network simulation

The app detects connectivity loss two ways; the helper covers both.

**1. `navigator.onLine` + window events** (drives `useOnlineStatus`,
`<OfflineIndicator />`):

```ts
import { goOffline, goOnline } from '../helpers/offline';

it('shows the offline indicator', () => {
  goOffline();                 // set before render to seed the initial state
  render(<OfflineIndicator />);
  expect(screen.getByText(/offline/i)).toBeInTheDocument();
});

// Toggle live (wrap in act() when it triggers React state updates):
act(() => goOnline());
```

`navigator.onLine` is reset to `true` automatically in an `afterEach`, so
offline state never leaks between tests. Call `resetOnlineStatus()` manually if
you need to reset mid-test.

**2. axios network-failure error** (drives retry/offline branches in data
fetching):

```ts
import { networkError } from '../helpers/offline';

vi.mocked(api.get).mockRejectedValue(networkError());
// error.request is set, error.response is undefined, code === 'ERR_NETWORK',
// and axios.isAxiosError(error) === true — the real offline signature.
```
