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

const renderFeed = async (orders: Record<string, unknown>[]) => {
  (analyticsAPI.getTransparencyLedger as jest.Mock).mockResolvedValue({
    data: {
      summary: {
        total_orders_with_financial_data: orders.length,
        total_amount_spent: 0,
        last_updated: '2026-01-03T00:00:00Z',
        transparency_note: 'note',
      },
      orders,
      ledger: [],
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

  it('shows a genuine 0.00 variance rather than dropping it', async () => {
    const card = await renderFeed([
      order({ estimated_cost: 10, actual_cost: 10, cost_variance: 0 }),
    ]);
    const financials = card.querySelector('.financial-info')!;

    expect(within(financials as HTMLElement).getByText('Cost Variance:')).toBeInTheDocument();
    expect(financials.textContent).toContain('Cost Variance:$0.00');
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
