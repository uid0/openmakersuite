/**
 * Tests for DonationItemScanPage — closes the documented coverage gap for
 * the donation-item disposition workflow (gh-457, AC-18 protected-action
 * recovery / AC-19 consistent loading/empty/error states).
 *
 * The page renders three distinct surfaces depending on auth and load
 * state: loading, missing/error, anonymous "please log in" state, and the
 * authenticated staff disposition form. These tests pin each surface and
 * the disposition-create happy path that staff actually use on the floor.
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import DonationItemScanPage from '../../pages/DonationItemScanPage';
import * as api from '../../services/api';
import { networkError } from '../helpers/offline';

vi.mock('../../services/api');

vi.mock('../../utils/dialogs', async () => ({
  showError: jest.fn(),
}));

const mockNavigate = jest.fn();
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useNavigate: () => mockNavigate,
}));

const donationItem = (overrides = {}) =>
  ({
    id: 'don-1',
    name: 'Donated drill',
    description: 'Cordless drill',
    condition: 'good' as const,
    quantity: 5,
    remaining_quantity: 5,
    status: 'in_inventory',
    donation_number: 'DN-100',
    donor_name: 'Alice',
    ...overrides,
  });

const renderPage = (itemId = 'don-1') =>
  render(
    <MemoryRouter initialEntries={[`/inventory/scan/donation-item/${itemId}`]}>
      <Routes>
        <Route
          path="/inventory/scan/donation-item/:itemId"
          element={<DonationItemScanPage />}
        />
      </Routes>
    </MemoryRouter>,
  );

describe('DonationItemScanPage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
    (api.donationsAPI.getDispositions as jest.Mock).mockResolvedValue({
      data: { results: [] },
    });
  });

  test('shows the loading state while the donation item is fetched', () => {
    (api.donationsAPI.getDonationItem as jest.Mock).mockReturnValue(
      new Promise(() => {}),
    );

    renderPage();

    expect(screen.getByText(/loading donation item/i)).toBeInTheDocument();
  });

  test('shows an error state with a recovery action when the item lookup fails', async () => {
    (api.donationsAPI.getDonationItem as jest.Mock).mockRejectedValue({
      response: { data: { detail: 'Donation item not found' } },
    });

    renderPage();

    await screen.findByText(/donation item not found/i);
    expect(screen.getByRole('button', { name: /go home/i })).toBeInTheDocument();
  });

  test('anonymous user: sees the login-required state instead of the disposition form', async () => {
    (api.donationsAPI.getDonationItem as jest.Mock).mockResolvedValue({
      data: donationItem(),
    });

    renderPage();

    await screen.findByText(/please log in to identify and process/i);
    expect(
      screen.queryByRole('heading', { name: /record initial disposition/i }),
    ).not.toBeInTheDocument();
  });

  test('authenticated staff: renders the disposition form and item header', async () => {
    localStorage.setItem('token', 'fake-token');
    (api.donationsAPI.getDonationItem as jest.Mock).mockResolvedValue({
      data: donationItem(),
    });

    renderPage();

    await screen.findByRole('heading', { name: /identify item/i });
    expect(
      screen.getByRole('heading', { name: /record initial disposition/i }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(/disposition type/i)).toBeInTheDocument();
  });

  test('authenticated staff: records a "kept" disposition with required destination', async () => {
    localStorage.setItem('token', 'fake-token');
    (api.donationsAPI.getDonationItem as jest.Mock).mockResolvedValue({
      data: donationItem(),
    });
    (api.donationsAPI.createDisposition as jest.Mock).mockResolvedValue({
      data: { id: 'disp-1' },
    });

    renderPage();

    await screen.findByRole('heading', { name: /record initial disposition/i });

    fireEvent.change(screen.getByLabelText(/disposition type/i), {
      target: { value: 'kept' },
    });
    fireEvent.change(await screen.findByLabelText(/kept for/i), {
      target: { value: 'makerspace' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^record disposition$/i }));

    await waitFor(() => {
      expect(api.donationsAPI.createDisposition).toHaveBeenCalledWith(
        expect.objectContaining({
          donation_item: 'don-1',
          disposition_type: 'kept',
          kept_destination: 'makerspace',
          quantity: 5,
        }),
      );
    });
    await screen.findByText(/disposition recorded successfully/i);
  });

  // R2 resilience (oms-sr1l4): AC-16 — a true network failure (offline) must
  // degrade to the same actionable error surface, not a blank screen.
  test('AC-16: an offline item load shows an actionable error with a recovery action', async () => {
    (api.donationsAPI.getDonationItem as jest.Mock).mockRejectedValue(networkError());

    renderPage();

    await screen.findByText(/failed to load donation item/i);
    expect(screen.getByRole('button', { name: /go home/i })).toBeInTheDocument();
  });
});
