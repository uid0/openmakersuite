/**
 * Tests for SerializedForecastPanel (#819) — consumption forecast +
 * low-stock report surfaced on the inventory + purchasing overviews.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import SerializedForecastPanel from '../../components/inventory/SerializedForecastPanel';
import { reportsAPI, SerializedForecastRow } from '../../services/api';

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual('../../services/api');
  return {
    ...actual,
    reportsAPI: {
      getSerializedForecast: jest.fn(),
    },
  };
});

const mockReports = reportsAPI as jest.Mocked<typeof reportsAPI>;

const buildRow = (overrides: Partial<SerializedForecastRow> = {}): SerializedForecastRow => ({
  item_id: 'item-1',
  item_name: 'Cutting blade',
  sku: 'BLD-1',
  category_name: 'Consumables',
  serial_tracking_mode: 'consumable',
  available: 3,
  on_hand: 5,
  installed: 2,
  available_stock: 5,
  current_stock: 5,
  window_days: 90,
  units_depleted_in_window: 9,
  avg_daily_use: 0.1,
  days_until_stockout: 30,
  projected_stockout_date: '2026-08-01',
  lead_time_days: 7,
  safety_stock: 2,
  reorder_point: 3,
  needs_reorder: true,
  no_orderable_supplier: false,
  ...overrides,
});

const renderPanel = (props = {}) =>
  render(
    <MantineProvider>
      <SerializedForecastPanel {...props} />
    </MantineProvider>,
  );

describe('SerializedForecastPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('loads and renders forecast rows with a low-stock flag', async () => {
    mockReports.getSerializedForecast.mockResolvedValue({
      data: [
        buildRow({ item_id: 'a', item_name: 'Low blade', needs_reorder: true }),
        buildRow({
          item_id: 'b',
          item_name: 'Healthy filter',
          needs_reorder: false,
          available_stock: 40,
        }),
      ],
    } as never);

    renderPanel();

    expect(await screen.findByText('Low blade')).toBeInTheDocument();
    expect(screen.getByText('Healthy filter')).toBeInTheDocument();
    // One row is below reorder point → the header "N low" badge reads 1.
    expect(screen.getByText('1 low')).toBeInTheDocument();
    expect(screen.getByText('Reorder')).toBeInTheDocument();
    expect(screen.getByText('OK')).toBeInTheDocument();
  });

  it('requests the forecast with the default window on mount', async () => {
    mockReports.getSerializedForecast.mockResolvedValue({ data: [] } as never);
    renderPanel();
    await waitFor(() =>
      expect(mockReports.getSerializedForecast).toHaveBeenCalledWith({
        window_days: 90,
        low_stock_only: false,
      }),
    );
  });

  it('starts with low-stock-only when defaultLowStockOnly is set', async () => {
    mockReports.getSerializedForecast.mockResolvedValue({ data: [] } as never);
    renderPanel({ defaultLowStockOnly: true });
    await waitFor(() =>
      expect(mockReports.getSerializedForecast).toHaveBeenCalledWith({
        window_days: 90,
        low_stock_only: true,
      }),
    );
    expect(
      await screen.findByText('No serialized items are below their reorder point.'),
    ).toBeInTheDocument();
  });

  it('refetches with low_stock_only when the switch is toggled', async () => {
    mockReports.getSerializedForecast.mockResolvedValue({ data: [buildRow()] } as never);
    renderPanel();
    await screen.findByText('Cutting blade');

    fireEvent.click(screen.getByLabelText('Low stock only'));

    await waitFor(() =>
      expect(mockReports.getSerializedForecast).toHaveBeenLastCalledWith({
        window_days: 90,
        low_stock_only: true,
      }),
    );
  });

  it('shows available (reorder driver) and on-hand as distinct columns', async () => {
    // Distinct, collision-free numbers (reorder_point is 3, avg_daily_use 0.1,
    // stockout "30 d" — 7 and 9 appear only as available / on-hand).
    mockReports.getSerializedForecast.mockResolvedValue({
      data: [buildRow({ available: 7, on_hand: 9, installed: 2 })],
    } as never);
    renderPanel();

    const row = await screen.findByTestId('serialized-forecast-row-item-1');
    expect(within(row).getByText('7')).toBeInTheDocument();
    expect(within(row).getByText('9')).toBeInTheDocument();
    // A legend spells out that reorder follows available, not on-hand.
    expect(screen.getByTestId('serialized-forecast-legend')).toBeInTheDocument();
  });

  it('says "No supplier" with the remedy, not a Reorder badge, when nothing is orderable', async () => {
    // A refusal has to be actionable where the OPERATOR sees it. This row is
    // flagged despite healthy stock, so an orange "Reorder" beside "40
    // available" would contradict the numbers next to it and name no remedy.
    mockReports.getSerializedForecast.mockResolvedValue({
      data: [
        buildRow({
          available: 40,
          needs_reorder: true,
          no_orderable_supplier: true,
          lead_time_days: null,
          reorder_point: null,
        }),
      ],
    } as never);
    renderPanel();

    const row = await screen.findByTestId('serialized-forecast-row-item-1');
    expect(within(row).getByText('No supplier')).toBeInTheDocument();
    expect(within(row).queryByText('Reorder')).not.toBeInTheDocument();
    expect(within(row).getByText(/add or reactivate a supplier link/i)).toBeInTheDocument();
    // And no fabricated reorder point beside the flag to contradict it.
    expect(within(row).queryByText('0')).not.toBeInTheDocument();
  });

  it('still shows Reorder for an ordinary low-stock row', async () => {
    mockReports.getSerializedForecast.mockResolvedValue({
      data: [buildRow({ needs_reorder: true, no_orderable_supplier: false })],
    } as never);
    renderPanel();

    const row = await screen.findByTestId('serialized-forecast-row-item-1');
    expect(within(row).getByText('Reorder')).toBeInTheDocument();
    expect(within(row).queryByText('No supplier')).not.toBeInTheDocument();
  });

  it('invokes onSelectItem when a row is clicked', async () => {
    mockReports.getSerializedForecast.mockResolvedValue({ data: [buildRow()] } as never);
    const onSelectItem = vi.fn();
    renderPanel({ onSelectItem });

    fireEvent.click(await screen.findByTestId('serialized-forecast-row-item-1'));
    expect(onSelectItem).toHaveBeenCalledWith('item-1');
  });
});
