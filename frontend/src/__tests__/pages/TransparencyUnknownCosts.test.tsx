/**
 * The public transparency page must not hide a cost it KNOWS is zero (op-9m2v).
 *
 * The server publishes `estimated_cost: 0.0` for a donated order — a known
 * $0.00, not an absence — and `null` only where no price is on file. The page
 * guarded both with truthiness, which fails twice over in JSX: a numeric `0` is
 * falsy, so the "Estimated Cost" row disappeared, AND `{0 && <div/>}` evaluates
 * to `0`, which React RENDERS as a bare "0" into the card. The same shape sat
 * on `cost_variance`, which the server newly computes against a known `0.00`
 * estimate.
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor, within } from '@testing-library/react';
import React from 'react';
import { MemoryRouter } from 'react-router-dom';
import TransparencyPage from '../../pages/TransparencyPage';
import { analyticsAPI } from '../../services/api';

vi.mock('../../services/api');

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
  estimated_cost: 0,
  actual_cost: null,
  cost_per_unit: null,
  cost_variance: null,
  order_number: 'PO-FREE-1',
  invoice_number: '',
  invoice_url: '',
  purchase_order_url: '',
  delivery_tracking_url: '',
  supplier_url: '',
  public_notes: '',
  supplier_name: 'Charity',
  ...overrides,
});

const ledgerEntry = (overrides: Record<string, unknown> = {}) => ({
  id: 1,
  item_id: 'item-1',
  item_name: 'Ledger Filament',
  supplier_name: 'Charity',
  quantity: 6,
  requested_at: '2026-01-01T00:00:00Z',
  ordered_at: '2026-01-02T00:00:00Z',
  delivered_at: null,
  actual_cost: null,
  estimated_cost: 0,
  status: 'ordered',
  order_number: 'PO-FREE-1',
  invoice_number: '',
  ...overrides,
});

const renderFeed = async (
  orders: Record<string, unknown>[],
  ledger: Record<string, unknown>[] = []
) => {
  (analyticsAPI.getTransparencyLedger as jest.Mock).mockResolvedValue({
    data: {
      summary: {
        total_orders_with_financial_data: orders.length,
        total_amount_spent: 0,
        last_updated: '2026-01-03T00:00:00Z',
        transparency_note: 'note',
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

    expect(within(financials as HTMLElement).getByText('Estimated Cost:')).toBeInTheDocument();
    expect(financials).toHaveTextContent('$0.00');
  });

  it('does not print a stray "0" beside the figures', async () => {
    const card = await renderFeed([order()]);
    const financials = card.querySelector('.financial-info')!;

    // `{0 && <div/>}` renders the number itself. The row text is "Estimated
    // Cost:" + "$0.00" and nothing else.
    expect(financials.textContent).toBe('Estimated Cost:$0.00');
  });

  it('shows a genuine 0.00 variance, and calls it ON budget not UNDER', async () => {
    const card = await renderFeed([
      order({ estimated_cost: 10, actual_cost: 10, cost_variance: 0 }),
    ]);
    const financials = card.querySelector('.financial-info')!;

    expect(within(financials as HTMLElement).getByText('Cost Variance:')).toBeInTheDocument();
    expect(financials.textContent).toContain('Cost Variance:$0.00 on budget');
    // Landing exactly on estimate is a THIRD state, not the favourable one.
    expect(financials.querySelector('.on-budget')).not.toBeNull();
    expect(financials.querySelector('.under-budget')).toBeNull();
    expect(financials.querySelector('.over-budget')).toBeNull();
  });

  it('still calls a real overrun over budget', async () => {
    const card = await renderFeed([order({ cost_variance: 2 })]);

    expect(card.querySelector('.financial-info')!.textContent).toContain('+$2.00 over budget');
    expect(card.querySelector('.over-budget')).not.toBeNull();
    expect(card.querySelector('.on-budget')).toBeNull();
  });

  it('still calls a real saving under budget', async () => {
    const card = await renderFeed([order({ cost_variance: -2 })]);

    expect(card.querySelector('.financial-info')!.textContent).toContain('-$2.00 under budget');
    expect(card.querySelector('.under-budget')).not.toBeNull();
    expect(card.querySelector('.on-budget')).toBeNull();
  });

  it('still renders nothing where the server reported no figure', async () => {
    const card = await renderFeed([
      order({ estimated_cost: null, cost_variance: null }),
    ]);
    const financials = card.querySelector('.financial-info')!;

    expect(financials.textContent).toBe('');
    expect(screen.queryByText('Estimated Cost:')).not.toBeInTheDocument();
    expect(screen.queryByText('Cost Variance:')).not.toBeInTheDocument();
  });

  it('is unchanged for an ordinary priced order — the branch invariant', async () => {
    const card = await renderFeed([
      order({ estimated_cost: 10, actual_cost: 12, cost_per_unit: 2, cost_variance: 2 }),
    ]);
    const financials = card.querySelector('.financial-info')!;

    expect(financials).toHaveTextContent('$10.00');
    expect(financials).toHaveTextContent('$12.00');
    expect(financials).toHaveTextContent('+$2.00');
  });
});


/**
 * The ledger table's Cost column reads `actual_cost ?? estimated_cost`, so it
 * moved when the feed started publishing `estimated_cost: 0.0` for a donated
 * order: base sent `null` for both and the cell read "N/A".
 */
describe('the public transparency ledger table', () => {
  const costCell = () =>
    screen.getByRole('table').querySelectorAll('tbody tr td')[6];

  it('shows a donated purchase as $0.00 rather than N/A', async () => {
    await renderFeed([order()], [ledgerEntry()]);

    expect(costCell().textContent).toBe('$0.00');
  });

  it('still shows N/A where neither cost is known', async () => {
    await renderFeed([order()], [ledgerEntry({ estimated_cost: null })]);

    expect(costCell().textContent).toBe('N/A');
  });

  it('still prefers a real actual cost — the branch invariant', async () => {
    await renderFeed([order()], [ledgerEntry({ actual_cost: 12, estimated_cost: 10 })]);

    expect(costCell().textContent).toBe('$12.00');
  });

  it('shows a comped purchase as $0.00, not as its estimate', async () => {
    // `??`, never `||`: a recorded actual cost of 0 is what the purchase
    // ACTUALLY cost, and falling through to the estimate would publish a
    // number nobody paid (op-9m2v).
    await renderFeed([order()], [ledgerEntry({ actual_cost: 0, estimated_cost: 10 })]);

    expect(costCell().textContent).toBe('$0.00');
  });
});
