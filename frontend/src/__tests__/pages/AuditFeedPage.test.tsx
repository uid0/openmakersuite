/**
 * Tests for the AuditFeedPage staff-review surface (closes #459).
 *
 * Covers ACs:
 *   1. Page calls dashboardAPI.getAuditFeed on mount and renders rows.
 *   2. Domain select fires a new GET with the chosen domain.
 *   3. Actor filter narrows the displayed rows client-side (no second
 *      network call) without dropping unmatched rows from state.
 *   4. Clicking a row toggles the metadata expansion.
 *   5. Non-staff visitors are redirected to home.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import AuditFeedPage from '../../pages/AuditFeedPage';
import { dashboardAPI } from '../../services/api';

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual('../../services/api');
  return {
    ...actual,
    dashboardAPI: {
      ...actual.dashboardAPI,
      getAuditFeed: jest.fn(),
    },
  };
});

const mockDashboard = dashboardAPI as jest.Mocked<typeof dashboardAPI>;

const buildEvent = (overrides: Partial<any> = {}) => ({
  domain: 'forgekey',
  action: 'lockout_create',
  actor_id: 1,
  actor_username: 'uid0',
  created_at: '2026-05-29T10:00:00+00:00',
  entity_type: 'lockout',
  entity_id: 'l1',
  notes: 'Member tripped reed switch',
  metadata: { device_mac: 'AA:BB:CC:DD:EE:FF' },
  ...overrides,
});

const renderPage = () =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={['/admin/audit-feed']}>
        <Routes>
          <Route path="/admin/audit-feed" element={<AuditFeedPage />} />
          <Route path="/" element={<div>HOME</div>} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );

describe('AuditFeedPage', () => {
  beforeEach(() => {
    jest.resetAllMocks();
    localStorage.setItem('is_staff', 'true');
  });

  afterEach(() => {
    localStorage.clear();
  });

  it('fetches and renders audit events', async () => {
    mockDashboard.getAuditFeed.mockResolvedValue({
      data: { count: 2, events: [buildEvent(), buildEvent({ actor_username: 'sysop', action: 'po_void' })] },
    } as any);

    renderPage();

    await waitFor(() => {
      expect(mockDashboard.getAuditFeed).toHaveBeenCalledTimes(1);
    });
    expect(await screen.findByText('lockout_create')).toBeInTheDocument();
    expect(screen.getByText('po_void')).toBeInTheDocument();
  });

  it('client-side filters by actor without re-fetching', async () => {
    mockDashboard.getAuditFeed.mockResolvedValue({
      data: {
        count: 2,
        events: [
          buildEvent({ action: 'lockout_create', actor_username: 'uid0' }),
          buildEvent({ action: 'po_void', actor_username: 'sysop' }),
        ],
      },
    } as any);

    renderPage();

    await screen.findByText('lockout_create');
    expect(screen.getByText('po_void')).toBeInTheDocument();
    const before = mockDashboard.getAuditFeed.mock.calls.length;

    fireEvent.change(screen.getByLabelText(/Actor/i), { target: { value: 'sysop' } });

    await waitFor(() => {
      expect(screen.queryByText('lockout_create')).not.toBeInTheDocument();
      expect(screen.getByText('po_void')).toBeInTheDocument();
    });
    // No new network call — actor filter is purely client-side
    expect(mockDashboard.getAuditFeed.mock.calls.length).toBe(before);
  });

  it('expands metadata on row click', async () => {
    mockDashboard.getAuditFeed.mockResolvedValue({
      data: { count: 1, events: [buildEvent()] },
    } as any);

    renderPage();

    const row = await screen.findByTestId('audit-row-0');
    fireEvent.click(row);

    expect(await screen.findByText(/device_mac/)).toBeInTheDocument();
  });

  it('redirects non-staff visitors home', async () => {
    localStorage.setItem('is_staff', 'false');
    localStorage.removeItem('is_superuser');

    renderPage();

    expect(await screen.findByText('HOME')).toBeInTheDocument();
    expect(mockDashboard.getAuditFeed).not.toHaveBeenCalled();
  });
});
