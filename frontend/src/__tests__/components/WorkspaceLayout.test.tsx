/**
 * WorkspaceLayout Component Tests
 */
import { MantineProvider } from '@mantine/core';
import { Notifications } from '@mantine/notifications';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { NotificationProvider } from '../../contexts/NotificationContext';
import WorkspaceLayout from '../../components/WorkspaceLayout';

// WorkspaceLayout loads the notification bell on mount (notificationsAPI.list
// via NotificationContext.loadNotifications). Nothing here awaits it, so
// leaving it unmocked fires a REAL XHR that outlives the test file: vitest
// reuses a worker across files, so the request settles later and the
// console.error in loadNotifications' catch block races that worker's teardown —
//   EnvironmentTeardownError: Closing rpc while "onUserConsoleLog" was pending
// which fails the whole run (exit 1) even though every assertion passed. It is
// timing-dependent, so the file passes in isolation and the full suite flakes
// (same class as the leaked-timer note in src/setupTests.ts).
//
// The sidebar assertions below do not care about notification contents, so
// resolve an empty list and let renderWithRouter flush it — the fetch then
// completes inside the test that started it.
vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/api')>();
  return {
    ...actual,
    notificationsAPI: {
      ...actual.notificationsAPI,
      list: vi.fn().mockResolvedValue({ data: { results: [] } }),
    },
  };
});

// Mock Sidebar and Breadcrumbs
vi.mock('../../components/Sidebar', () => ({
  default: function Sidebar() {
    return <div data-testid="sidebar">Sidebar</div>;
  },
}));

vi.mock('../../components/Breadcrumbs', () => ({
  default: function Breadcrumbs() {
    return <div data-testid="breadcrumbs">Breadcrumbs</div>;
  },
}));

// Mock useCommandPalette hook
vi.mock('../../hooks/useCommandPalette', () => ({
  useCommandPalette: () => ({
    isOpen: false,
    open: jest.fn(),
    close: jest.fn(),
    toggle: jest.fn(),
  }),
}));

const renderWithRouter = async (path: string) => {
  const result = render(
    <MantineProvider>
      <NotificationProvider>
        <Notifications />
        <MemoryRouter initialEntries={[path]}>
          <WorkspaceLayout>
            <div>Test Content</div>
          </WorkspaceLayout>
        </MemoryRouter>
      </NotificationProvider>
    </MantineProvider>
  );
  // Let the mocked fetch settle so its state update belongs to this test
  // instead of landing after teardown (see mock above). One microtask is
  // enough because the mock resolves immediately; act() is deliberately not
  // used here — React 19's async act never settles under jsdom's
  // pretendToBeVisual rAF loop and every test times out.
  await Promise.resolve();
  return result;
};

describe('WorkspaceLayout Component', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('renders sidebar, menu toggle, logo, and breadcrumbs for normal routes', async () => {
    await renderWithRouter('/inventory');
    expect(screen.getByTestId('sidebar')).toBeInTheDocument();
    expect(screen.getByTestId('breadcrumbs')).toBeInTheDocument();
    expect(screen.getByText('Test Content')).toBeInTheDocument();
    expect(screen.getByText(/dallasmakerspace/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/collapse sidebar|expand sidebar/i)).toBeInTheDocument();
  });

  it('hides sidebar for TV dashboard routes', async () => {
    await renderWithRouter('/facilities/tv-dashboard');
    expect(screen.queryByTestId('sidebar')).not.toBeInTheDocument();
    expect(screen.queryByTestId('breadcrumbs')).not.toBeInTheDocument();
    expect(screen.getByText('Test Content')).toBeInTheDocument();
  });

  it('hides sidebar for logistics dashboard routes', async () => {
    await renderWithRouter('/facilities/logistics');
    expect(screen.queryByTestId('sidebar')).not.toBeInTheDocument();
    expect(screen.queryByTestId('breadcrumbs')).not.toBeInTheDocument();
    expect(screen.getByText('Test Content')).toBeInTheDocument();
  });

  it('hides sidebar for legacy TV dashboard routes', async () => {
    await renderWithRouter('/tv-dashboard');
    expect(screen.queryByTestId('sidebar')).not.toBeInTheDocument();
  });

  it('hides sidebar for legacy logistics routes', async () => {
    await renderWithRouter('/tv-logistics');
    expect(screen.queryByTestId('sidebar')).not.toBeInTheDocument();
  });
});

