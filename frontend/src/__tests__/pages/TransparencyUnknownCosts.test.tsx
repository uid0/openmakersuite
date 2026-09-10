/**
 * The public transparency page must not hide a cost it KNOWS is zero (op-9m2v),
 * and must not print a figure the order does not own.
 *
 * The zero half: the server publishes a recorded `0.00` as `0.0` — a known
 * cost, not an absence — and `null` only where no figure is on file. The page
 * guarded with truthiness, which fails twice over in JSX: a numeric `0` is
 * falsy, so the row disappeared, AND `{0 && <div/>}` evaluates to `0`, which
 * React RENDERS as a bare "0" into the card.
 *
 * The attribution half: `estimated_cost` and `cost_variance` used to sit in
 * this same block. Both were resolved from the ITEM's current supplier links at
 * request time, and the variance was printed as an over/under-BUDGET verdict on
 * an order that records no budget. They are gone from the payload; what remains
 * is `item_estimated_cost_today`, under its own heading and its own sentence.
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor, within } from '@testing-library/react';
import React from 'react';
import { MemoryRouter } from 'react-router-dom';
import TransparencyPage from '../../pages/TransparencyPage';
import { analyticsAPI } from '../../services/api';

vi.mock('../../services/api');

const choice = (name: string | null = 'Charity') => ({
  item_supplier_id: name === null ? null : 7,
  supplier_name: name,
  reason: name === null ? 'no_suppliers' : null,
  alternatives: [],
});

const order = (overrides: Record<string, unknown> = {}) => ({
  id: 1,
  item_id: 'item-1',
  item_name: 'Donated Filament',
  item_category: 'Consumables',
  quantity_ordered: 6,
  status: 'ordered',
  requested_at: '2026-01-01T00:00:00Z',
  ordered_at: '2026-01-02T00:00:00Z',
  delivered_at: null,
  actual_cost: 0,
  cost_per_unit: 0,
  order_number: 'PO-FREE-1',
  invoice_number: '',
  invoice_url: '',
  purchase_order_url: '',
  delivery_tracking_url: '',
  supplier_url: '',
  public_notes: '',
  item_supplier_choice: choice(),
  item_estimated_cost_today: 0,
  ...overrides,
});

const ledgerEntry = (overrides: Record<string, unknown> = {}) => ({
  id: 1,
  item_id: 'item-1',
  item_name: 'Ledger Filament',
  item_supplier_choice: choice(),
  quantity: 6,
  requested_at: '2026-01-01T00:00:00Z',
  ordered_at: '2026-01-02T00:00:00Z',
  delivered_at: null,
  actual_cost: 0,
  status: 'ordered',
  order_number: 'PO-FREE-1',
  invoice_number: '',
  ...overrides,
});

const renderFeed = async (
  orders: Record<string, unknown>[],
  ledger: Record<string, unknown>[] = [],
  summary: Record<string, unknown> = {}
) => {
  (analyticsAPI.getTransparencyLedger as jest.Mock).mockResolvedValue({
    data: {
      summary: {
        total_orders_with_financial_data: orders.length,
        total_amount_spent: 0,
        last_updated: '2026-01-03T00:00:00Z',
        transparency_note: 'note',
        ...summary,
      },
      orders,
      ledger,
    },
  });

  render(
    <MantineProvider>
      <MemoryRouter>
        <TransparencyPage />
      </MemoryRouter>
    </MantineProvider>
  );

  await waitFor(() => expect(screen.getByText('Donated Filament')).toBeInTheDocument());
  return screen.getByText('Donated Filament').closest('.order-card')!;
};

afterEach(() => {
  jest.clearAllMocks();
});

describe('the public transparency order card', () => {
  it('shows a donated order as costing $0.00 rather than hiding it', async () => {
    const card = await renderFeed([order()]);
    const financials = card.querySelector('.financial-info')!;

    expect(within(financials as HTMLElement).getByText('Actual Cost:')).toBeInTheDocument();
    expect(financials).toHaveTextContent('$0.00');
  });

  it('does not print a stray "0" beside the figures', async () => {
    const card = await renderFeed([order()]);
    const financials = card.querySelector('.financial-info')!;

    // `{0 && <div/>}` renders the number itself. The block is the two rows the
    // order owns and nothing else.
    expect(financials.textContent).toBe('Actual Cost:$0.00Cost per Unit:$0.00');
  });

  it('still renders nothing where the server reported no figure', async () => {
    const card = await renderFeed([order({ actual_cost: null, cost_per_unit: null })]);
    const financials = card.querySelector('.financial-info')!;

    expect(financials.textContent).toBe('');
    expect(screen.queryByText('Actual Cost:')).not.toBeInTheDocument();
    expect(screen.queryByText('Cost per Unit:')).not.toBeInTheDocument();
  });

  it('is unchanged for an ordinary priced order — the branch invariant', async () => {
    const card = await renderFeed([order({ actual_cost: 12, cost_per_unit: 2 })]);
    const financials = card.querySelector('.financial-info')!;

    expect(financials).toHaveTextContent('$12.00');
    expect(financials).toHaveTextContent('$2.00');
  });
});

/**
 * A value resolved from the ITEM must never be shown as the order's, and must
 * never be presented as something the order's actual cost can be measured
 * against.
 */
describe("the order card's item-scoped block", () => {
  it("names the supplier as the item's, and says so beside the price", async () => {
    const card = await renderFeed([
      order({ item_supplier_choice: choice('Acme Filament'), item_estimated_cost_today: 30 }),
    ]);

    expect(card).toHaveTextContent('Item supplier today:');
    expect(card).toHaveTextContent('Acme Filament');
    expect(card).toHaveTextContent("Same quantity at today's price:");
    expect(card).toHaveTextContent('$30.00');
    expect(card.querySelector('.item-scope-note')!.textContent).toBe(
      "The item's supplier and price as of now — not this order's."
    );
  });

  it("never labels anything on the card as this order's supplier or estimate", async () => {
    // FED THE KEYS THE OLD SERVER SENT, deliberately. A fixture carrying only
    // the new shape cannot tell a page that dropped these labels apart from one
    // that simply had nothing to fill them with, and would pass on the very
    // code this pins.
    const card = await renderFeed([
      order({
        supplier_name: 'Ghost Vendor Co.',
        estimated_cost: 99,
        item_supplier_choice: choice('Acme Filament'),
        item_estimated_cost_today: 30,
      }),
    ]);

    expect(card.textContent).not.toContain('Supplier:');
    expect(card.textContent).not.toContain('Estimated Cost:');
    expect(card.textContent).not.toContain('Ghost Vendor Co.');
    expect(card.textContent).not.toContain('$99.00');
  });

  it('prints no budget verdict, for any figures at all', async () => {
    // ``cost_variance`` is on the fixture for the same reason: the removed row
    // rendered off THIS key, so a fixture without it proves nothing.
    const card = await renderFeed([
      order({
        actual_cost: 12,
        cost_per_unit: 2,
        cost_variance: 20,
        item_estimated_cost_today: 10,
      }),
    ]);

    // The order records no estimate, so "over"/"under"/"on budget" was a
    // verdict against a live re-quote — it flipped sign when somebody edited a
    // supplier link, with nothing about the order having changed.
    expect(card.textContent).not.toContain('budget');
    expect(card.textContent).not.toContain('Cost Variance');
    expect(card.querySelector('.over-budget')).toBeNull();
    expect(card.querySelector('.under-budget')).toBeNull();
    expect(card.querySelector('.on-budget')).toBeNull();
  });

  it('says how many other suppliers the item has, rather than implying one', async () => {
    const card = await renderFeed([
      order({
        item_supplier_choice: {
          ...choice('Acme Filament'),
          alternatives: [
            { id: 2, supplier_name: 'Beta Parts' },
            { id: 3, supplier_name: 'Gamma Wholesale' },
          ],
        },
      }),
    ]);

    expect(card).toHaveTextContent('Acme Filament, or 2 others');
  });

  it('renders no supplier row at all where the item has none', async () => {
    // A legacy ``supplier_name`` is on the fixture so that "no row" means the
    // page declined to name one, not that it had nothing to hand.
    const card = await renderFeed([
      order({ item_supplier_choice: choice(null), supplier_name: 'Ghost Vendor Co.' }),
    ]);

    expect(card.textContent).not.toContain('Item supplier today:');
    expect(card.textContent).not.toContain('Ghost Vendor Co.');
  });
});

/**
 * The ledger table's cost column publishes what was PAID. It used to read
 * `actual_cost ?? estimated_cost`, so a delivered order with no recorded actual
 * showed a live re-quote under a heading that said we had paid it.
 */
describe('the public transparency ledger table', () => {
  const cell = (index: number) =>
    screen.getByRole('table').querySelectorAll('tbody tr td')[index];
  const supplierCell = () => cell(5);
  const paidCell = () => cell(6);

  it('shows a donated purchase as $0.00 rather than N/A', async () => {
    await renderFeed([order()], [ledgerEntry()]);

    expect(paidCell().textContent).toBe('$0.00');
  });

  it('shows N/A where nothing was recorded, and never a substitute figure', async () => {
    // ``estimated_cost`` is on the fixture because the cell used to fall
    // through to it. Without it this asserts nothing about the fallback.
    await renderFeed([order()], [ledgerEntry({ actual_cost: null, estimated_cost: 10 })]);

    expect(paidCell().textContent).toBe('N/A');
  });

  it('shows a real actual cost — the branch invariant', async () => {
    await renderFeed([order()], [ledgerEntry({ actual_cost: 12, estimated_cost: 10 })]);

    expect(paidCell().textContent).toBe('$12.00');
  });

  it("heads the supplier column as the item's, and fills it from the item's choice", async () => {
    await renderFeed(
      [order()],
      [ledgerEntry({ item_supplier_choice: choice('Acme Filament') })]
    );

    const headers = Array.from(
      screen.getByRole('table').querySelectorAll('thead th')
    ).map((th) => th.textContent);
    expect(headers).toContain('Item supplier today');
    expect(headers).not.toContain('Supplier');
    expect(supplierCell().textContent).toBe('Acme Filament');
  });
});


/**
 * The summary counts every qualifying order; the table below it is a page of
 * one hundred. The server used to compute the summary by walking that page, so
 * both numbers agreed and both were wrong. Now that the total is right, the
 * table has to say which slice of it a reader is looking at.
 */
describe('the ledger window note', () => {
  it('says how many of how many, when the table is a page of a longer ledger', async () => {
    await renderFeed([order()], [ledgerEntry()], {
      total_orders_with_financial_data: 250,
    });

    expect(screen.getByTestId('ledger-window')).toHaveTextContent(
      'Showing the 1 most recent of 250.'
    );
  });

  it('says nothing at all when the table IS the whole ledger', async () => {
    // A truncation note where nothing was truncated is its own false claim.
    await renderFeed([order()], [ledgerEntry()], {
      total_orders_with_financial_data: 1,
    });

    expect(screen.queryByTestId('ledger-window')).not.toBeInTheDocument();
  });
});
