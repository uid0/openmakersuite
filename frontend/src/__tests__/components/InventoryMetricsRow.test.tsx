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
  quantity_in_transit: 3,
  reorder_point: 5,
  lead_time_days: 14,
  unit_cost: '5.00',
  cost_trend: 'up',
  last_po_unit_cost: '4.0000',
  is_case_based: false,
  case_size: null,
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
});
