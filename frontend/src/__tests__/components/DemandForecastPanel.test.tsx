/**
 * Tests for DemandForecastPanel — the restock-cadence forecast + predictive
 * reorder alerts surfaced on the inventory + purchasing overviews.
 *
 * The v2 (restock-interval) model drives every column here: cadence, last
 * restock, next due and days-until-due. The retired v1 usage-rate keys are
 * still on the wire (written 0/null) and are deliberately not rendered — one
 * test pins that so they cannot creep back in as columns of zeroes.
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

/**
 * A populated v2 row. The v1 quantity fields are pinned at the 0/null the
 * engine actually writes, so any test asserting on them would be asserting on
 * dead data — exactly what the panel must not render.
 */
const buildRow = (overrides: Partial<DemandForecastRow> = {}): DemandForecastRow => ({
  id: 1,
  item: 'item-1',
  item_name: 'Toilet paper',
  sku: 'TP-1',
  category_name: 'Supplies',
  generated_at: '2026-07-19T04:00:00Z',
  // Restock-interval signal.
  avg_interval_days: 47.6,
  interval_samples: 4,
  last_restock_date: '2026-07-01',
  predicted_next_reorder_date: '2026-08-18',
  days_until_due: 5,
  // Retired v1 projection — 0/null on every current row.
  horizon_days: 0,
  predicted_daily_demand: 0,
  horizon_demand: 0,
  horizon_demand_upper: 0,
  days_until_stockout: null,
  projected_stockout_date: null,
  predictive_reorder_point: 0,
  safety_stock: 0,
  available_at_generation: 4,
  needs_reorder: true,
  lead_time_days: 7,
  method: 'restock_interval',
  model_version: 'interval-1',
  ...overrides,
});

/** An item the model has no cadence for: under two purchase events. */
const buildNoHistoryRow = (
  overrides: Partial<DemandForecastRow> = {},
): DemandForecastRow =>
  buildRow({
    id: 99,
    item: 'new-item',
    item_name: 'Freshly added widget',
    avg_interval_days: null,
    interval_samples: 0,
    last_restock_date: null,
    predicted_next_reorder_date: null,
    days_until_due: null,
    needs_reorder: false,
    method: 'insufficient_history',
    model_version: 'interval-1',
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
      /generated nightly from how often items are purchased/i,
    );
    // No forecast rows → no legend either.
    expect(screen.queryByTestId('demand-forecast-legend')).not.toBeInTheDocument();
  });

  it('maps an interval row onto the cadence / restock / due columns', async () => {
    mockReports.getDemandForecast.mockResolvedValue({
      data: [
        buildRow({
          avg_interval_days: 47.6,
          last_restock_date: '2026-07-01',
          predicted_next_reorder_date: '2026-08-18',
          days_until_due: 5,
        }),
      ],
    } as never);

    renderPanel();

    const row = await screen.findByTestId('demand-forecast-row-item-1');
    expect(within(row).getByText('Restock-interval')).toBeInTheDocument();
    // Mean gap between purchases, rounded to whole days.
    expect(within(row).getByText('~48d')).toBeInTheDocument();
    expect(within(row).getByText('Jul 1, 2026')).toBeInTheDocument();
    expect(within(row).getByText('Aug 18, 2026')).toBeInTheDocument();
    expect(within(row).getByText('due in 5d')).toBeInTheDocument();
    expect(within(row).getByText('Reorder')).toBeInTheDocument();
    expect(screen.getByTestId('demand-forecast-legend')).toBeInTheDocument();
  });

  it('renders the interval column headers and none of the retired v1 ones', async () => {
    mockReports.getDemandForecast.mockResolvedValue({
      data: [buildRow()],
    } as never);

    renderPanel();

    await screen.findByText('Toilet paper');
    ['Cadence', 'Last restock', 'Next due', 'Days until due', 'Status'].forEach(
      (header) => expect(screen.getByText(header)).toBeInTheDocument(),
    );
    // The usage-rate columns are gone — the backend writes those fields 0/null,
    // so rendering them would show a column of zeroes.
    expect(screen.queryByText('Avg/day')).not.toBeInTheDocument();
    expect(screen.queryByText('Stockout')).not.toBeInTheDocument();
    expect(screen.queryByText('Reorder pt')).not.toBeInTheDocument();
    expect(screen.queryByText('Available')).not.toBeInTheDocument();
  });

  it('phrases days_until_due as due-in / today / overdue', async () => {
    mockReports.getDemandForecast.mockResolvedValue({
      data: [
        buildRow({ id: 1, item: 'soon', days_until_due: 5 }),
        buildRow({ id: 2, item: 'today', days_until_due: 0 }),
        buildRow({ id: 3, item: 'late', days_until_due: -3 }),
      ],
    } as never);

    renderPanel();

    expect(
      within(await screen.findByTestId('demand-forecast-row-soon')).getByText('due in 5d'),
    ).toBeInTheDocument();
    expect(
      within(screen.getByTestId('demand-forecast-row-today')).getByText('due today'),
    ).toBeInTheDocument();
    expect(
      within(screen.getByTestId('demand-forecast-row-late')).getByText('overdue 3d'),
    ).toBeInTheDocument();
  });

  it('marks an item with too little purchase history instead of clearing it', async () => {
    mockReports.getDemandForecast.mockResolvedValue({
      data: [buildNoHistoryRow()],
    } as never);

    renderPanel();

    const row = await screen.findByTestId('demand-forecast-row-new-item');
    expect(within(row).getByText('Insufficient history')).toBeInTheDocument();
    expect(within(row).getByText('Not enough purchase history yet')).toBeInTheDocument();
    // Neither a false all-clear nor an invented cadence.
    expect(within(row).getByText('Unknown')).toBeInTheDocument();
    expect(within(row).queryByText('OK')).not.toBeInTheDocument();
    expect(within(row).queryByText('~0d')).not.toBeInTheDocument();
    // It is not counted as due either.
    expect(screen.getByText('0 due')).toBeInTheDocument();
  });

  it('counts only flagged rows as due and preserves the backend ordering', async () => {
    mockReports.getDemandForecast.mockResolvedValue({
      data: [
        buildRow({ id: 1, item: 'overdue', days_until_due: -3, needs_reorder: true }),
        buildRow({ id: 2, item: 'due-soon', days_until_due: 2, needs_reorder: true }),
        buildRow({ id: 3, item: 'later', days_until_due: 30, needs_reorder: false }),
        buildNoHistoryRow({ id: 4, item: 'no-history' }),
      ],
    } as never);

    renderPanel();

    await screen.findByTestId('demand-forecast-row-overdue');
    // The API sorts (flagged first, then soonest due, nulls last); the panel
    // renders that order as-is rather than re-sorting client-side.
    expect(
      screen
        .getAllByTestId(/^demand-forecast-row-/)
        .map((row) => row.getAttribute('data-testid')),
    ).toEqual([
      'demand-forecast-row-overdue',
      'demand-forecast-row-due-soon',
      'demand-forecast-row-later',
      'demand-forecast-row-no-history',
    ]);
    expect(screen.getByText('2 due')).toBeInTheDocument();
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
          days_until_due: -3,
          predicted_next_reorder_date: '2026-07-16',
        }),
      ],
    } as never);

    renderPanel();

    const alerts = await screen.findByTestId('demand-forecast-alerts');
    expect(within(alerts).getByText('Reorder alerts')).toBeInTheDocument();
    // Count badge next to the heading.
    expect(within(alerts).getByText('1')).toBeInTheDocument();
    // Due-based wording, not the retired "N left, reorder at M".
    expect(alerts).toHaveTextContent(
      '1 watched item is due to reorder, based on how often they are normally bought.',
    );
    expect(screen.getByTestId('demand-forecast-alert-watched-1')).toHaveTextContent(
      'Paper towels — overdue 3d (due Jul 16, 2026)',
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
