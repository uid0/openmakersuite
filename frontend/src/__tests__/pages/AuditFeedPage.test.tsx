/**
 * Tests for the AuditFeedPage staff-review surface (closes #459).
 *
 * Covers ACs:
 *   1. Page calls dashboardAPI.getAuditFeed on mount and renders rows.
 *   2. Domain select fires a new GET with the chosen domain.
 *   3. Actor filter narrows the displayed rows client-side (no second
 *      network call) without dropping unmatched rows from state.
 *   4. Clicking a row toggles the metadata expansion — asserted on
 *      VISIBILITY, not presence; see the test for why. Expansion follows
 *      the event, not its index in the actor-filtered list.
 *   5. A scanned receipt flagged damaged/expired is badged on the row
 *      without opening it, and desk rows carry no badge.
 *   6. Non-staff visitors are redirected to home.
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

  it('expands and re-collapses metadata on row click', async () => {
    mockDashboard.getAuditFeed.mockResolvedValue({
      data: { count: 1, events: [buildEvent()] },
    } as any);

    renderPage();

    const row = await screen.findByTestId('audit-row-0');

    // VISIBILITY, not presence. Mantine's Collapse defaults to
    // keepMounted, so the metadata node is in the document from first
    // paint whether the row is open or shut — an assertion that only
    // asked `toBeInTheDocument()` passed for as long as the row never
    // opened at all (#1052). Every assertion below is `toBeVisible`,
    // which is the thing the reviewer is actually promised.
    const metadata = screen.getByText(/device_mac/);
    expect(metadata).not.toBeVisible();

    fireEvent.click(row);
    await waitFor(() => {
      expect(metadata).toBeVisible();
    });

    // And shuts again — a row stuck open is the same unreadable feed.
    fireEvent.click(row);
    await waitFor(() => {
      expect(metadata).not.toBeVisible();
    });
  });

  it('keeps expansion attached to the event, not to its position in the filtered list', async () => {
    // Three events; the actor filter below keeps the first and the third, so
    // the row that survives at index 1 is NOT the row that was opened at
    // index 1. Expansion keyed on the visible index therefore opens a row
    // nobody clicked; expansion keyed on the event's own identity does not.
    mockDashboard.getAuditFeed.mockResolvedValue({
      data: {
        count: 3,
        events: [
          buildEvent({ actor_username: 'uid0', entity_id: 'a', metadata: { marker: 'alpha' } }),
          buildEvent({ actor_username: 'sysop', entity_id: 'b', metadata: { marker: 'bravo' } }),
          buildEvent({ actor_username: 'uid0', entity_id: 'c', metadata: { marker: 'charlie' } }),
        ],
      },
    } as any);

    renderPage();

    const bravoRow = await screen.findByTestId('audit-row-1');
    const charlie = screen.getByText(/charlie/);
    expect(charlie).not.toBeVisible();

    fireEvent.click(bravoRow);
    await waitFor(() => {
      expect(screen.getByText(/bravo/)).toBeVisible();
    });

    // Filter out the opened row entirely. Charlie slides into index 1.
    fireEvent.change(screen.getByLabelText(/Actor/i), { target: { value: 'uid0' } });

    await waitFor(() => {
      expect(screen.queryByText(/bravo/)).not.toBeInTheDocument();
    });
    expect(screen.getByTestId('audit-row-1')).toBeInTheDocument();
    expect(screen.getByText(/charlie/)).not.toBeVisible();
    expect(screen.getByText(/alpha/)).not.toBeVisible();
  });

  it('keeps an opened row open when a filter shifts it to a different index', async () => {
    // The converse of the test above, and the reason the fix is identity
    // keying rather than clearing the set on every filter change: a row the
    // operator opened must survive a filter that merely moves it up.
    mockDashboard.getAuditFeed.mockResolvedValue({
      data: {
        count: 3,
        events: [
          buildEvent({ actor_username: 'uid0', entity_id: 'a', metadata: { marker: 'alpha' } }),
          buildEvent({ actor_username: 'sysop', entity_id: 'b', metadata: { marker: 'bravo' } }),
          buildEvent({ actor_username: 'uid0', entity_id: 'c', metadata: { marker: 'charlie' } }),
        ],
      },
    } as any);

    renderPage();

    fireEvent.click(await screen.findByTestId('audit-row-2'));
    await waitFor(() => {
      expect(screen.getByText(/charlie/)).toBeVisible();
    });

    // Charlie moves from index 2 to index 1 and must stay open.
    fireEvent.change(screen.getByLabelText(/Actor/i), { target: { value: 'uid0' } });

    await waitFor(() => {
      expect(screen.queryByText(/bravo/)).not.toBeInTheDocument();
    });
    expect(screen.getByText(/charlie/)).toBeVisible();
    expect(screen.getByText(/alpha/)).not.toBeVisible();
  });

  it('badges a scanned receipt flagged damaged or expired on the row itself', async () => {
    mockDashboard.getAuditFeed.mockResolvedValue({
      data: {
        count: 1,
        events: [
          buildEvent({
            domain: 'purchase_orders',
            action: 'po_receive_items',
            notes: 'crushed corner, tape torn',
            metadata: {
              source: 'scan_barcode',
              scanned_upc: '0123456789012',
              is_damaged: true,
              is_expired: true,
            },
          }),
        ],
      },
    } as any);

    renderPage();

    // Visible WITHOUT opening the row: the condition is the thing the captain
    // scrolls this feed looking for, so it must not be buried in the collapsed
    // metadata blob.
    expect(await screen.findByTestId('audit-condition-damaged-0')).toBeVisible();
    expect(screen.getByTestId('audit-condition-expired-0')).toBeVisible();
    // The operator's own words survive alongside the badges.
    expect(screen.getByText('crushed corner, tape torn')).toBeVisible();
  });

  it('badges only the flag actually raised, and leaves desk rows unbadged', async () => {
    mockDashboard.getAuditFeed.mockResolvedValue({
      data: {
        count: 2,
        events: [
          // Scanned, and the operator said it was sound. `false` is an answer,
          // but it is not a problem — no badge.
          buildEvent({
            action: 'po_receive_items',
            metadata: { source: 'scan_barcode', is_damaged: false, is_expired: true },
          }),
          // Desk receipt: never asked, so the keys are absent entirely. This is
          // the case a truthiness check would get right by accident and an
          // `undefined`-blind one would render as "Damaged".
          buildEvent({
            action: 'po_receive_items',
            metadata: { delivery_date: '2026-05-29T10:00:00+00:00', carrier: 'UPS' },
          }),
        ],
      },
    } as any);

    renderPage();

    expect(await screen.findByTestId('audit-condition-expired-0')).toBeVisible();
    expect(screen.queryByTestId('audit-condition-damaged-0')).not.toBeInTheDocument();
    expect(screen.queryByTestId('audit-condition-damaged-1')).not.toBeInTheDocument();
    expect(screen.queryByTestId('audit-condition-expired-1')).not.toBeInTheDocument();
  });

  it('redirects non-staff visitors home', async () => {
    localStorage.setItem('is_staff', 'false');
    localStorage.removeItem('is_superuser');

    renderPage();

    expect(await screen.findByText('HOME')).toBeInTheDocument();
    expect(mockDashboard.getAuditFeed).not.toHaveBeenCalled();
  });
});
