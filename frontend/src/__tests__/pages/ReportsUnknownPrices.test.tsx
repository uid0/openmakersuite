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
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
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
