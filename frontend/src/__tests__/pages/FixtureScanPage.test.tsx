/**
 * Tests for FixtureScanPage — closes the documented coverage gap for the
 * public fixture-refill scan workflow (gh-457, AC-15 duplicate guard /
 * AC-19 consistent loading/empty/error states).
 *
 * The page has two distinct flows:
 *   - Anonymous user: auto-submits a refill request on load and shows a
 *     final "Refill Request Submitted" confirmation state. The submit must
 *     happen exactly once even though the underlying effect can re-fire.
 *   - Authenticated staff/admin: sees the fixture detail and can submit a
 *     refill (staff) or resolve all pending requests (admin).
 *
 * These tests pin all three lifecycle states (loading, missing fixture,
 * inactive fixture), the anonymous duplicate-submit guard, and the
 * authenticated submission path.
 */
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import FixtureScanPage from '../../pages/FixtureScanPage';
import { NotificationProvider } from '../../contexts/NotificationContext';
import { MantineProvider } from '@mantine/core';
import { Notifications } from '@mantine/notifications';
import * as api from '../../services/api';

vi.mock('../../services/api');

const mockNavigate = jest.fn();
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useNavigate: () => mockNavigate,
}));

const fixture = (overrides: Partial<api.Fixture> = {}): api.Fixture =>
  ({
    id: 'fix-1',
    name: 'Hand Soap Dispenser',
    asset_tag: 'AT-1',
    description: 'Bathroom soap',
    location: 'loc-1',
    location_name: 'Main Bathroom',
    refill_item: 'item-1',
    refill_item_name: 'Hand Soap',
    refill_item_sku: 'SOAP-1',
    is_active: true,
    pending_requests_count: 0,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
    ...overrides,
  } as unknown as api.Fixture);

const renderPage = () =>
  render(
    <MantineProvider>
      <NotificationProvider>
        <Notifications />
        <MemoryRouter initialEntries={['/inventory/scan/fixture/fix-1']}>
          <Routes>
            <Route
              path="/inventory/scan/fixture/:fixtureId"
              element={<FixtureScanPage />}
            />
          </Routes>
        </MemoryRouter>
      </NotificationProvider>
    </MantineProvider>,
  );

describe('FixtureScanPage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
    (api.fixturesAPI.listRequests as jest.Mock).mockResolvedValue({
      data: { results: [] },
    });
  });

  test('shows the loading state while the fixture request is in flight', () => {
    (api.fixturesAPI.getFixture as jest.Mock).mockReturnValue(
      new Promise(() => {}),
    );

    renderPage();

    expect(screen.getByText(/loading fixture details/i)).toBeInTheDocument();
  });

  test('shows an actionable not-found state when the fixture lookup fails', async () => {
    (api.fixturesAPI.getFixture as jest.Mock).mockRejectedValue({
      response: { data: { detail: 'No Fixture matches the given query.' } },
    });

    renderPage();

    await screen.findByText(/no fixture matches the given query/i);
    expect(screen.getByRole('button', { name: /go home/i })).toBeInTheDocument();
  });

  test('refuses inactive fixtures with a dedicated state instead of auto-submitting', async () => {
    (api.fixturesAPI.getFixture as jest.Mock).mockResolvedValue({
      data: fixture({ is_active: false }),
    });

    renderPage();

    await screen.findByRole('heading', { name: /fixture inactive/i });
    expect(api.fixturesAPI.scanFixture).not.toHaveBeenCalled();
  });

  test('anonymous user: auto-submits exactly once and lands on the confirmation surface', async () => {
    let resolveScan!: (value: { data: unknown }) => void;
    (api.fixturesAPI.getFixture as jest.Mock).mockResolvedValue({
      data: fixture(),
    });
    (api.fixturesAPI.scanFixture as jest.Mock).mockReturnValue(
      new Promise((resolve) => {
        resolveScan = resolve as (value: { data: unknown }) => void;
      }),
    );

    renderPage();

    await waitFor(() => {
      expect(api.fixturesAPI.scanFixture).toHaveBeenCalledTimes(1);
    });

    resolveScan({ data: { id: 'req-1' } });

    await screen.findByRole('heading', { name: /refill request submitted/i });
    expect(api.fixturesAPI.scanFixture).toHaveBeenCalledTimes(1);
    expect(api.fixturesAPI.scanFixture).toHaveBeenCalledWith('fix-1');
  });

  test('authenticated staff: shows the refill form and submits with notes', async () => {
    localStorage.setItem('token', 'fake-token');
    (api.fixturesAPI.getFixture as jest.Mock).mockResolvedValue({
      data: fixture(),
    });
    (api.fixturesAPI.scanFixture as jest.Mock).mockResolvedValue({
      data: { id: 'req-1' },
    });

    renderPage();

    await screen.findByRole('heading', { name: /submit refill request/i });

    const notes = screen.getByLabelText(/additional information/i);
    fireEvent.change(notes, { target: { value: 'Almost empty' } });

    fireEvent.click(screen.getByRole('button', { name: /submit refill request/i }));

    await waitFor(() => {
      expect(api.fixturesAPI.scanFixture).toHaveBeenCalledWith('fix-1', 'Almost empty');
    });
  });

  test('authenticated admin: sees the Resolve-All form instead of plain submit', async () => {
    localStorage.setItem('token', 'fake-token');
    localStorage.setItem('is_staff', 'true');
    (api.fixturesAPI.getFixture as jest.Mock).mockResolvedValue({
      data: fixture({ pending_requests_count: 2 }),
    });
    (api.fixturesAPI.listRequests as jest.Mock).mockResolvedValue({
      data: {
        results: [
          {
            id: 'req-1',
            requested_at: '2024-01-01T00:00:00Z',
            requested_by: 'alice',
            notes: 'still empty',
          },
        ],
      },
    });

    renderPage();

    await screen.findByRole('heading', { name: /resolve all pending requests/i });
    expect(
      screen.getByRole('heading', { name: /pending refill requests/i }),
    ).toBeInTheDocument();
    expect(screen.getByText(/still empty/i)).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /resolve all requests/i }),
    ).toBeInTheDocument();
  });
});
