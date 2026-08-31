/**
 * A report must not render a price it does not have as $0.00 (op-9m2v).
 *
 * The report screens are where the money falsy guard was hardest to see: the
 * purchasing price-trend table's three cost columns were `float(x or 0)` on the
 * server, so "no price recorded", "this vendor is free" and "no price history
 * at all" all arrived as `0` and printed as `$0.00`. They arrive as `null` now,
 * and a `$0.00` in that column means the supplier charges nothing.
 *
 * The inventory value tables are the other shape: their totals are
 * `SUM(stock * COALESCE(unit_cost, 0))`, so the NUMBER is a lower bound and
 * always was. It is deliberately unchanged — moving it would be inventing
 * money — and the new "Unpriced Items" column is what makes the claim honest.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { buildPriceTrendChartData } from '../../components/PriceTrendChart';
import InventoryReportPage from '../../pages/InventoryReportPage';
import PurchasingReportPage from '../../pages/PurchasingReportPage';
import { reportsAPI } from '../../services/api';

vi.mock('../../services/api');

beforeEach(() => {
  jest.clearAllMocks();
});

const priceTrendRow = (overrides: Record<string, unknown> = {}) => ({
  item_id: 'item-1',
  item_name: 'Mystery Widget',
  supplier_name: 'Acme',
  price_changes: 2,
  min_unit_cost: null,
  max_unit_cost: null,
  latest_unit_cost: null,
  price_change_percentage: null,
  ...overrides,
});

const renderPurchasingPriceTrends = async (rows: Record<string, unknown>[]) => {
  (reportsAPI.getPurchasingSpendBySupplier as jest.Mock).mockResolvedValue({ data: [] });
  (reportsAPI.getPurchasingSpendByCategory as jest.Mock).mockResolvedValue({ data: [] });
  (reportsAPI.getPurchasingLeadTimeAnalysis as jest.Mock).mockResolvedValue({ data: [] });
  (reportsAPI.getPurchasingPriceTrends as jest.Mock).mockResolvedValue({ data: rows });

  render(
    <MantineProvider>
      <MemoryRouter>
        <PurchasingReportPage />
      </MemoryRouter>
    </MantineProvider>
  );
  // Price trends is not the default tab.
  await waitFor(() => expect(screen.getByText(/price trends/i)).toBeInTheDocument());
  fireEvent.click(screen.getByText(/price trends/i));
  await waitFor(() => expect(screen.getByText('Mystery Widget')).toBeInTheDocument());
  return screen.getByText('Mystery Widget').closest('tr')!;
};

describe('the purchasing price-trend table', () => {
  test('renders a dash where the server recorded no price', async () => {
    const row = await renderPurchasingPriceTrends([priceTrendRow()]);

    expect(row).not.toHaveTextContent('$0.00');
    expect(row.textContent).toContain('—');
  });

  test('still renders a real $0.00 for a supplier that charges nothing', async () => {
    const row = await renderPurchasingPriceTrends([
      priceTrendRow({ min_unit_cost: 0, max_unit_cost: 0, latest_unit_cost: 0 }),
    ]);

    expect(row).toHaveTextContent('$0.00');
  });

  test('is unchanged for ordinary prices — the branch invariant', async () => {
    const row = await renderPurchasingPriceTrends([
      priceTrendRow({ min_unit_cost: 4, max_unit_cost: 5, latest_unit_cost: 5 }),
    ]);

    expect(row).toHaveTextContent('$4.00');
    expect(row).toHaveTextContent('$5.00');
  });
});

const renderInventoryStockByCategory = async (rows: Record<string, unknown>[]) => {
  (reportsAPI.getInventoryStockByCategory as jest.Mock).mockResolvedValue({ data: rows });
  (reportsAPI.getInventoryReorderFrequency as jest.Mock).mockResolvedValue({ data: [] });
  (reportsAPI.getInventoryValueByLocation as jest.Mock).mockResolvedValue({ data: [] });

  render(
    <MantineProvider>
      <MemoryRouter>
        <InventoryReportPage />
      </MemoryRouter>
    </MantineProvider>
  );
  await waitFor(() => expect(screen.getByText('Consumables')).toBeInTheDocument());
  return screen.getByText('Consumables').closest('tr')!;
};

describe('the inventory stock-value table', () => {
  const category = (overrides: Record<string, unknown> = {}) => ({
    category_id: 1,
    category_name: 'Consumables',
    total_items: 3,
    total_stock: 30,
    total_value: 20,
    items_without_price: 0,
    low_stock_count: 1,
    ...overrides,
  });

  test('marks a total it could only compute part of', async () => {
    const row = await renderInventoryStockByCategory([category({ items_without_price: 1 })]);

    // The number is unchanged; the "+" and the count are what is new.
    expect(row).toHaveTextContent('$20.00 +');
    expect(row).toHaveTextContent('1');
  });

  test('claims nothing extra when it priced everything — the branch invariant', async () => {
    const row = await renderInventoryStockByCategory([category()]);

    expect(row).toHaveTextContent('$20.00');
    expect(row).not.toHaveTextContent('$20.00 +');
    expect(row).toHaveTextContent('—');
  });
});


describe('sorting the purchasing price-trend table', () => {
  const priceTrendTable = () => screen.getByText('Min Unit Cost').closest('table')!;

  const rowsInOrder = () =>
    within(priceTrendTable())
      .getAllByRole('row')
      .slice(1)
      .map((tr) => tr.querySelector('td')!.textContent);

  const renderSortable = () =>
    renderPurchasingPriceTrends([
      priceTrendRow({ item_id: 'a', item_name: 'Mystery Widget', min_unit_cost: null }),
      priceTrendRow({ item_id: 'b', item_name: 'Free Widget', min_unit_cost: 0 }),
      priceTrendRow({ item_id: 'c', item_name: 'Priced Widget', min_unit_cost: 4 }),
    ]);

  const sortBy = (header: string) =>
    fireEvent.click(within(priceTrendTable()).getByText(header));

  /**
   * `null` coerces to `0` in a JS relational comparison, so a price nobody
   * recorded used to sort in among the cheapest, indistinguishable from a
   * supplier that genuinely charges nothing. An unknown price must not be
   * COMPARED as a real number (op-9m2v).
   */
  it('sorts a price nobody recorded last, not as if it were free', async () => {
    await renderSortable();
    sortBy('Min Unit Cost');

    await waitFor(() =>
      expect(rowsInOrder()).toEqual(['Free Widget', 'Priced Widget', 'Mystery Widget'])
    );
  });

  it('keeps the unknowns last when the direction is reversed', async () => {
    await renderSortable();
    sortBy('Min Unit Cost');
    sortBy('Min Unit Cost');

    await waitFor(() =>
      expect(rowsInOrder()).toEqual(['Priced Widget', 'Free Widget', 'Mystery Widget'])
    );
  });

  it('still sorts by name — the invariant, on a column that did not change', async () => {
    await renderSortable();
    sortBy('Item Name');

    await waitFor(() =>
      expect(rowsInOrder()).toEqual(['Free Widget', 'Mystery Widget', 'Priced Widget'])
    );
  });
});


/**
 * The supplier-detail price chart is fed by `SupplierDetailSerializer`, which
 * now emits `unit_cost: 0.0` for a snapshot recording a price of zero where it
 * used to emit `null`. `ph.unit_cost || ph.package_cost || null` discarded that
 * — so the drop to free, the most notable move the chart can show, appeared as
 * a gap (op-9m2v). recharts renders nothing measurable under jsdom, so this
 * exercises the exported series builder the chart consumes.
 */
describe('the supplier price-trend chart series', () => {
  const trends = (
    history: Array<{ unit_cost: number | null; package_cost: number | null }>
  ) => ({
    trends: [
      {
        item_id: 'item-1',
        item_name: 'Widget',
        price_history: history.map((h, idx) => ({
          recorded_at: `2026-01-0${idx + 1}T00:00:00Z`,
          change_type: 'updated',
          price_change_percentage: null,
          ...h,
        })),
      },
    ],
    summary: {
      average_unit_cost: null,
      min_unit_cost: null,
      max_unit_cost: null,
      price_changes_count: history.length,
    },
  });

  it('plots a recorded price of zero as a $0.00 point, not a gap', () => {
    const data = buildPriceTrendChartData(
      trends([
        { unit_cost: 4, package_cost: null },
        { unit_cost: 0, package_cost: 0 },
      ])
    );

    expect(data.map((point) => point['item_item-1'])).toEqual([4, 0]);
  });

  it('still leaves a gap where no price was recorded at all', () => {
    const data = buildPriceTrendChartData(
      trends([
        { unit_cost: 4, package_cost: null },
        { unit_cost: null, package_cost: null },
      ])
    );

    expect(data.map((point) => point['item_item-1'])).toEqual([4, null]);
  });

  it('falls back to a package cost of zero rather than to a gap', () => {
    const data = buildPriceTrendChartData(
      trends([{ unit_cost: null, package_cost: 0 }])
    );

    expect(data.map((point) => point['item_item-1'])).toEqual([0]);
  });
});
