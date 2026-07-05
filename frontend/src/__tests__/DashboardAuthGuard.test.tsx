/**
 * Auth guard for the operations dashboard (op-3er).
 *
 * Regression: visiting /dashboard while logged out used to mount the page and
 * fire its widget/notification fetches, which 401 for an anonymous visitor and
 * surfaced an error panel ("Failed to load dashboard widgets"). Expected: a
 * logged-out visitor is cleanly redirected to the login surface (/) before the
 * dashboard renders, and a logged-in visitor sees it unchanged.
 *
 * We render the real <App /> so this exercises the actual route table + guard
 * wiring in App.tsx, mocking only the leaf page/layout modules to keep the test
 * focused on routing (and free of Mantine/data-fetch machinery).
 */
import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, test } from 'vitest';
import App from '../App';

vi.mock('../pages/HomePage', () => ({ default: () => <div>Home Page</div> }));
vi.mock('../pages/DashboardPage', () => ({ default: () => <div>Dashboard Page</div> }));
// WorkspaceLayout pulls in notifications polling + Mantine chrome; a passthrough
// keeps this test about the guard, not the layout.
vi.mock('../components/WorkspaceLayout', () => ({
  default: ({ children }: { children: React.ReactNode }) => <div data-testid="workspace-layout">{children}</div>,
}));

const RETURN_TO_KEY = 'oms_pending_return_to';

const renderAppAt = (path: string) => {
  window.history.pushState({}, '', path);
  return render(<App />);
};

describe('Dashboard auth guard', () => {
  afterEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    window.history.pushState({}, '', '/');
  });

  test('redirects a logged-out visitor from /dashboard to the login home without rendering the dashboard', () => {
    renderAppAt('/dashboard');

    // Redirected to the login surface, dashboard never mounted (so it never
    // fetched and never errored).
    expect(screen.getByText('Home Page')).toBeInTheDocument();
    expect(screen.queryByText('Dashboard Page')).not.toBeInTheDocument();
  });

  test('stashes the attempted route so login can forward the visitor back', () => {
    renderAppAt('/dashboard');

    expect(sessionStorage.getItem(RETURN_TO_KEY)).toBe('/dashboard');
  });

  test('renders the dashboard for an authenticated visitor', () => {
    localStorage.setItem('token', 'jwt-access-token');

    renderAppAt('/dashboard');

    expect(screen.getByText('Dashboard Page')).toBeInTheDocument();
    expect(screen.queryByText('Home Page')).not.toBeInTheDocument();
  });
});
