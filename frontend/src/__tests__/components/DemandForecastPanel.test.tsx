/**
 * Tests for DemandForecastPanel (op-3) — the ML demand forecast + predictive
 * reorder alerts surfaced on the inventory + purchasing overviews.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import DemandForecastPanel from '../../components/inventory/DemandForecastPanel';
import { DemandForecastRow, reportsAPI } from '../../services/api';

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual('../../services/api');
  return {
    ...actual,
    reportsAPI: {
      getDemandForecast: jest.fn(),
      getReorderAlerts: jest.fn(),
    },
  };
});

const mockReports = reportsAPI as jest.Mocked<typeof reportsAPI>;

const buildRow = (overrides: Partial<DemandForecastRow> = {}): DemandForecastRow => ({
  id: 1,
  item: 'item-1',
  item_name: 'Toilet paper',
  sku: 'TP-1',
  category_name: 'Supplies',
  generated_at: '2026-07-19T04:00:00Z',
  horizon_days: 14,
  predicted_daily_demand: 0.5,
  horizon_demand: 7,
  horizon_demand_upper: 9,
  available_at_generation: 4,
  days_until_stockout: 8,
  projected_stockout_date: '2026-07-27',
  predictive_reorder_point: 9,
  needs_reorder: true,
  lead_time_days: 7,
  safety_stock: 2,
  method: 'holtwinters',
  model_version: 'holtwinters-1',
  ...overrides,
});

const renderPanel = (props = {}) =>
  render(
    <MantineProvider env="test">
      <DemandForecastPanel {...props} />
    </MantineProvider>,
  );

describe('DemandForecastPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockReports.getReorderAlerts.mockResolvedValue({ data: [] } as never);
  });

  it('requests the forecast and the alert set on mount', async () => {
    mockReports.getDemandForecast.mockResolvedValue({ data: [] } as never);

    renderPanel();

    await waitFor(() =>
      expect(mockReports.getDemandForecast).toHaveBeenCalledWith({
        low_stock_only: false,
      }),
    );
    expect(mockReports.getReorderAlerts).toHaveBeenCalled();
  });

  it('shows an engine-has-not-run empty state when there are no rows', async () => {
    mockReports.getDemandForecast.mockResolvedValue({ data: [] } as never);

    renderPanel();

    expect(await screen.findByTestId('demand-forecast-empty')).toHaveTextContent(
      /generated nightly/i,
    );
    // No forecast rows → no legend either.
    expect(screen.queryByTestId('demand-forecast-legend')).not.toBeInTheDocument();
  });

  it('renders forecast rows with method badges and a due count', async () => {
    mockReports.getDemandForecast.mockResolvedValue({
      data: [
        buildRow({ id: 1, item: 'a', item_name: 'Toilet paper', needs_reorder: true }),
        buildRow({
          id: 2,
          item: 'b',
          item_name: 'Trash bags',
          method: 'fallback',
          model_version: '',
          needs_reorder: false,
          available_at_generation: 50,
        }),
      ],
    } as never);

    renderPanel();

    expect(await screen.findByText('Toilet paper')).toBeInTheDocument();
    expect(screen.getByText('Trash bags')).toBeInTheDocument();
    // The seasonal model and the run-rate fallback are told apart at a glance.
    expect(screen.getByText('Holt-Winters')).toBeInTheDocument();
    expect(screen.getByText('Fallback')).toBeInTheDocument();
    // One of the two rows is flagged → header badge reads "1 due".
    expect(screen.getByText('1 due')).toBeInTheDocument();
    expect(screen.getByText('Reorder')).toBeInTheDocument();
    expect(screen.getByText('OK')).toBeInTheDocument();
    expect(screen.getByTestId('demand-forecast-legend')).toBeInTheDocument();
  });

  it('shows the snapshotted stock, reorder point and stockout for a row', async () => {
    mockReports.getDemandForecast.mockResolvedValue({
      data: [
        buildRow({
          predicted_daily_demand: 0.5,
          days_until_stockout: 8,
          predictive_reorder_point: 9,
          available_at_generation: 4,
        }),
      ],
    } as never);

    renderPanel();

    const row = await screen.findByTestId('demand-forecast-row-item-1');
    expect(within(row).getByText('0.50')).toBeInTheDocument();
    expect(within(row).getByText('8 d')).toBeInTheDocument();
    expect(within(row).getByText('9')).toBeInTheDocument();
    expect(within(row).getByText('4')).toBeInTheDocument();
  });

  it('renders an em dash when demand is flat and there is no stockout date', async () => {
    mockReports.getDemandForecast.mockResolvedValue({
      data: [buildRow({ days_until_stockout: null, predicted_daily_demand: 0 })],
    } as never);

    renderPanel();

    const row = await screen.findByTestId('demand-forecast-row-item-1');
    expect(within(row).getByText('—')).toBeInTheDocument();
  });

  it('refetches with low_stock_only when the switch is toggled', async () => {
    mockReports.getDemandForecast.mockResolvedValue({ data: [buildRow()] } as never);

    renderPanel();
    await screen.findByText('Toilet paper');

    fireEvent.click(screen.getByLabelText('Due to reorder only'));

    await waitFor(() =>
      expect(mockReports.getDemandForecast).toHaveBeenLastCalledWith({
        low_stock_only: true,
      }),
    );
  });

  it('starts filtered when defaultLowStockOnly is set', async () => {
    mockReports.getDemandForecast.mockResolvedValue({ data: [] } as never);

    renderPanel({ defaultLowStockOnly: true });

    await waitFor(() =>
      expect(mockReports.getDemandForecast).toHaveBeenCalledWith({
        low_stock_only: true,
      }),
    );
    expect(await screen.findByTestId('demand-forecast-empty')).toHaveTextContent(
      'No forecasted items are due to reorder.',
    );
  });

  it('surfaces the reorder alerts — the watched, due items — above the table', async () => {
    mockReports.getDemandForecast.mockResolvedValue({ data: [buildRow()] } as never);
    mockReports.getReorderAlerts.mockResolvedValue({
      data: [
        buildRow({
          id: 7,
          item: 'watched-1',
          item_name: 'Paper towels',
          available_at_generation: 2,
          predictive_reorder_point: 11,
          days_until_stockout: 3,
        }),
      ],
    } as never);

    renderPanel();

    const alerts = await screen.findByTestId('demand-forecast-alerts');
    expect(within(alerts).getByText('Reorder alerts')).toBeInTheDocument();
    // Count badge next to the heading.
    expect(within(alerts).getByText('1')).toBeInTheDocument();
    expect(screen.getByTestId('demand-forecast-alert-watched-1')).toHaveTextContent(
      'Paper towels — 2 left, reorder at 11 (out in 3 d)',
    );
  });

  it('hides the alert banner when nothing is both watched and due', async () => {
    mockReports.getDemandForecast.mockResolvedValue({ data: [buildRow()] } as never);
    mockReports.getReorderAlerts.mockResolvedValue({ data: [] } as never);

    renderPanel();

    await screen.findByText('Toilet paper');
    expect(screen.queryByTestId('demand-forecast-alerts')).not.toBeInTheDocument();
  });

  it('invokes onSelectItem when a row is clicked', async () => {
    mockReports.getDemandForecast.mockResolvedValue({ data: [buildRow()] } as never);
    const onSelectItem = vi.fn();

    renderPanel({ onSelectItem });

    fireEvent.click(await screen.findByTestId('demand-forecast-row-item-1'));
    expect(onSelectItem).toHaveBeenCalledWith('item-1');
  });

  it('shows an error message when the report fails to load', async () => {
    mockReports.getDemandForecast.mockRejectedValue({
      response: { data: { detail: 'Nope.' } },
    } as never);

    renderPanel();

    expect(await screen.findByText('Nope.')).toBeInTheDocument();
  });
});
