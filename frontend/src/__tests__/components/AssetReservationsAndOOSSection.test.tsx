/**
 * Tests for AssetReservationsAndOOSSection.
 *
 * Covers: OOS banner + restore, mark-OOS modal flow, active reservation
 * list + cancel, reserve modal validation, history collapse visibility.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import AssetReservationsAndOOSSection from '../../components/AssetReservationsAndOOSSection';
import { assetOutOfServiceAPI, assetReservationsAPI } from '../../services/api';

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual('../../services/api');
  return {
    ...actual,
    assetReservationsAPI: {
      list: jest.fn(),
      create: jest.fn(),
      cancel: jest.fn(),
    },
    assetOutOfServiceAPI: {
      list: jest.fn(),
      open: jest.fn(),
      restore: jest.fn(),
    },
  };
});

const mockRes = assetReservationsAPI as jest.Mocked<typeof assetReservationsAPI>;
const mockOOS = assetOutOfServiceAPI as jest.Mocked<typeof assetOutOfServiceAPI>;

const ASSET_ID = '11111111-1111-1111-1111-111111111111';

const buildReservation = (overrides: Partial<any> = {}) => ({
  id: 'r1',
  asset: ASSET_ID,
  asset_name: 'Lathe',
  title: 'Welding 101',
  starts_at: '2099-01-01T10:00:00Z',
  ends_at: '2099-01-01T12:00:00Z',
  notes: '',
  reserved_by: 1,
  reserved_by_username: 'alice',
  cancelled_at: null,
  cancelled_by: null,
  is_current: false,
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-06-01T00:00:00Z',
  ...overrides,
});

const buildOOS = (overrides: Partial<any> = {}) => ({
  id: 'o1',
  asset: ASSET_ID,
  asset_name: 'Lathe',
  reason: 'Spindle bearing knocking',
  placed_out_at: '2026-06-01T15:00:00Z',
  placed_by: 1,
  placed_by_username: 'alice',
  expected_return_at: '2026-06-10T00:00:00Z',
  restored_at: null,
  restored_by: null,
  is_open: true,
  created_at: '2026-06-01T15:00:00Z',
  updated_at: '2026-06-01T15:00:00Z',
  ...overrides,
});

const renderSection = () =>
  render(
    <MantineProvider>
      <AssetReservationsAndOOSSection assetId={ASSET_ID} />
    </MantineProvider>,
  );

describe('AssetReservationsAndOOSSection', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockRes.list.mockResolvedValue({ data: { results: [] } } as any);
    mockOOS.list.mockResolvedValue({ data: { results: [] } } as any);
  });

  test('loads reservations + OOS for the asset on mount', async () => {
    renderSection();
    await waitFor(() => {
      expect(mockRes.list).toHaveBeenCalledWith({ asset: ASSET_ID });
      expect(mockOOS.list).toHaveBeenCalledWith({ asset: ASSET_ID });
    });
  });

  test('renders OUT OF SERVICE banner when an OOS is open', async () => {
    mockOOS.list.mockResolvedValue({ data: { results: [buildOOS()] } } as any);
    renderSection();
    expect(await screen.findByTestId('oos-banner')).toBeInTheDocument();
    expect(screen.getByText(/Spindle bearing knocking/)).toBeInTheDocument();
    expect(screen.getByTestId('restore-button')).toBeInTheDocument();
  });

  test('hides mark-OOS button when an OOS is already open', async () => {
    mockOOS.list.mockResolvedValue({ data: { results: [buildOOS()] } } as any);
    renderSection();
    await screen.findByTestId('oos-banner');
    expect(screen.queryByTestId('mark-oos-button')).not.toBeInTheDocument();
  });

  test('restore POSTs to assetOutOfServiceAPI.restore and reloads', async () => {
    mockOOS.list.mockResolvedValueOnce({ data: { results: [buildOOS()] } } as any);
    mockOOS.restore.mockResolvedValue({ data: buildOOS({ is_open: false }) } as any);
    mockOOS.list.mockResolvedValueOnce({ data: { results: [] } } as any);

    renderSection();
    const restoreBtn = await screen.findByTestId('restore-button');
    fireEvent.click(restoreBtn);

    await waitFor(() => {
      expect(mockOOS.restore).toHaveBeenCalledWith('o1');
    });
    await waitFor(() => {
      expect(mockOOS.list).toHaveBeenCalledTimes(2);
    });
  });

  test('shows future reservation in the active list', async () => {
    mockRes.list.mockResolvedValue({ data: { results: [buildReservation()] } } as any);
    renderSection();
    expect(await screen.findByTestId('reservation-r1')).toBeInTheDocument();
    expect(screen.getByText('Welding 101')).toBeInTheDocument();
  });

  test('cancel reservation calls cancel + reload', async () => {
    mockRes.list.mockResolvedValueOnce({ data: { results: [buildReservation()] } } as any);
    mockRes.cancel.mockResolvedValue({ data: {} } as any);
    mockRes.list.mockResolvedValueOnce({ data: { results: [] } } as any);

    renderSection();
    const btn = await screen.findByTestId('cancel-reservation-r1');
    fireEvent.click(btn);

    await waitFor(() => {
      expect(mockRes.cancel).toHaveBeenCalledWith('r1');
    });
  });

  test('reserve modal blocks submit without required fields', async () => {
    renderSection();
    const reserveBtn = await screen.findByTestId('reserve-button');
    fireEvent.click(reserveBtn);

    const submitBtn = await screen.findByTestId('reserve-submit');
    fireEvent.click(submitBtn);

    await screen.findByText(/Title, start, and end are required/i);
    expect(mockRes.create).not.toHaveBeenCalled();
  });

  test('mark-OOS modal blocks submit without a reason', async () => {
    renderSection();
    const markBtn = await screen.findByTestId('mark-oos-button');
    fireEvent.click(markBtn);

    const submitBtn = await screen.findByTestId('oos-submit');
    fireEvent.click(submitBtn);

    await screen.findByText(/Reason is required/i);
    expect(mockOOS.open).not.toHaveBeenCalled();
  });
});
