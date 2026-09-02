/**
 * Tests for InventoryMetricsRow (issue-5) — the prominent
 * SKU · QOH · QOO · QA · QC · QIT · RP · Lead · Cost strip on the item detail
 * page.
 */
import { MantineProvider } from '@mantine/core';
import { render, screen } from '@testing-library/react';

import InventoryMetricsRow from '../../components/inventory/InventoryMetricsRow';
import { InventoryItemMetrics } from '../../types';

const buildMetrics = (overrides: Partial<InventoryItemMetrics> = {}): InventoryItemMetrics => ({
  current_stock: 10,
  quantity_on_order: 7,
  quantity_available: 6,
  quantity_committed: 4,
  committed_breakdown: [],
  quantity_in_transit: 3,
  reorder_point: 5,
  lead_time_days: 14,
  unit_cost: '5.00',
  cost_trend: 'up',
  last_po_unit_cost: '4.0000',
  is_case_based: false,
  case_size: null,
  supplier_scored_without_price: false,
  supplier_scored_without_history: false,
  ...overrides,
});

const renderRow = (metrics: InventoryItemMetrics, sku = 'SKU-123') =>
  render(
    <MantineProvider>
      <InventoryMetricsRow sku={sku} metrics={metrics} />
    </MantineProvider>,
  );

describe('InventoryMetricsRow', () => {
  it('renders every metric cell with its computed value', () => {
    renderRow(buildMetrics());

    expect(screen.getByTestId('inventory-metrics-row')).toBeInTheDocument();
    expect(screen.getByTestId('metric-sku')).toHaveTextContent('SKU-123');
    expect(screen.getByTestId('metric-qoh')).toHaveTextContent('10');
    expect(screen.getByTestId('metric-qoo')).toHaveTextContent('7');
    expect(screen.getByTestId('metric-qa')).toHaveTextContent('6');
    expect(screen.getByTestId('metric-qc')).toHaveTextContent('4');
    expect(screen.getByTestId('metric-qit')).toHaveTextContent('3');
    expect(screen.getByTestId('metric-rp')).toHaveTextContent('5');
    expect(screen.getByTestId('metric-lead')).toHaveTextContent('14d');
  });

  it('shows the per-unit cost with an up-trend arrow', () => {
    renderRow(buildMetrics({ unit_cost: '5.00', cost_trend: 'up' }));

    const cost = screen.getByTestId('metric-cost');
    expect(cost).toHaveTextContent('Cost');
    expect(cost).toHaveTextContent('$5.00');
    expect(cost).toHaveTextContent('↑');
    expect(screen.getByTestId('cost-trend-up')).toBeInTheDocument();
  });

  it('shows a down-trend arrow when the cost has fallen', () => {
    renderRow(buildMetrics({ cost_trend: 'down' }));

    expect(screen.getByTestId('metric-cost')).toHaveTextContent('↓');
    expect(screen.getByTestId('cost-trend-down')).toBeInTheDocument();
  });

  it('labels the cost per-case and shows the case size for case-based items', () => {
    renderRow(buildMetrics({ is_case_based: true, case_size: 12, unit_cost: '24.00' }));

    const cost = screen.getByTestId('metric-cost');
    expect(cost).toHaveTextContent('Cost/case');
    expect(cost).toHaveTextContent('$24.00');
  });

  it('renders em-dashes for missing lead time and cost, and no arrow without history', () => {
    renderRow(
      buildMetrics({ lead_time_days: null, unit_cost: null, cost_trend: 'no_history' }),
    );

    expect(screen.getByTestId('metric-lead')).toHaveTextContent('—');
    expect(screen.getByTestId('metric-cost')).toHaveTextContent('—');
    expect(screen.queryByTestId('cost-trend-no_history')).not.toBeInTheDocument();
  });

  it('formats fractional committed/available quantities to two decimals', () => {
    renderRow(buildMetrics({ quantity_committed: 2.5, quantity_available: 7.5 }));

    expect(screen.getByTestId('metric-qc')).toHaveTextContent('2.50');
    expect(screen.getByTestId('metric-qa')).toHaveTextContent('7.50');
  });

  // The supplier scoring neither punishes nor pays for a missing price (op-2rsp),
  // so a supplier carrying one can win. A blank Cost cell alone is ambiguous —
  // "no supplier" and "a supplier nobody has priced" send an operator to
  // different screens — so the row says which. The delivery-history gap is NOT
  // rendered: it stays on the wire for API consumers, but it is true of nearly
  // every link, so a note carrying it would say nothing.
  it('says when the chosen supplier has no price on file', () => {
    renderRow(buildMetrics({ unit_cost: null, supplier_scored_without_price: true }));

    expect(screen.getByTestId('metric-cost')).toHaveTextContent('—');
    expect(screen.getByTestId('metric-supplier-gaps')).toHaveTextContent(
      'Chosen supplier has no price on file',
    );
  });

  it('says nothing when the only gap is an empty delivery history', () => {
    renderRow(buildMetrics({ supplier_scored_without_history: true }));

    expect(screen.queryByTestId('metric-supplier-gaps')).not.toBeInTheDocument();
  });

  it('names only the price gap when the choice was made without either', () => {
    renderRow(
      buildMetrics({
        unit_cost: null,
        supplier_scored_without_price: true,
        supplier_scored_without_history: true,
      }),
    );

    const note = screen.getByTestId('metric-supplier-gaps');
    expect(note).toHaveTextContent('Chosen supplier has no price on file');
    expect(note).not.toHaveTextContent('delivery history');
  });

  it('says nothing when the choice knew the price', () => {
    renderRow(buildMetrics());

    expect(screen.queryByTestId('metric-supplier-gaps')).not.toBeInTheDocument();
  });
});
