/**
 * Tests for CommittedBreakdown (op-u9ap) — the "Committed to" strip that
 * attributes an item's committed quantity (QC) to the open work orders and
 * assets holding it, from the metrics payload's `committed_breakdown`
 * (op-l4i0).
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import CommittedBreakdown from '../../components/inventory/CommittedBreakdown';
import { CommittedBreakdownEntry } from '../../types';

const renderBreakdown = (entries: CommittedBreakdownEntry[], totalCommitted: number) =>
  render(
    <MantineProvider>
      <MemoryRouter>
        <CommittedBreakdown entries={entries} totalCommitted={totalCommitted} />
      </MemoryRouter>
    </MantineProvider>,
  );

const entries: CommittedBreakdownEntry[] = [
  {
    work_order_id: 'wo-1',
    work_order_short_id: 'WO-1A2B3C4D',
    asset_id: 'asset-1',
    asset_name: 'Laser Cutter',
    quantity: 3,
  },
  {
    work_order_id: 'wo-2',
    work_order_short_id: 'WO-9F8E7D6C',
    asset_id: null,
    asset_name: null,
    quantity: 1,
  },
];

describe('CommittedBreakdown', () => {
  it('lists each work order with its asset and quantity, summing to QC', () => {
    renderBreakdown(entries, 4);

    const panel = screen.getByTestId('committed-breakdown');
    expect(panel).toHaveTextContent('Committed to');
    expect(panel).toHaveTextContent('4 committed across 2 open work orders');

    const first = screen.getByTestId('committed-entry-wo-1');
    expect(within(first).getByText('WO-1A2B3C4D')).toBeInTheDocument();
    expect(within(first).getByText('Laser Cutter')).toBeInTheDocument();
    expect(within(first).getByText('3')).toBeInTheDocument();
  });

  it('links each entry to its work order', () => {
    renderBreakdown(entries, 4);

    expect(screen.getByText('WO-1A2B3C4D').closest('a')).toHaveAttribute(
      'href',
      '/maintenance/work-orders/wo-1',
    );
  });

  it('labels an asset-less work order rather than dropping the row', () => {
    renderBreakdown(entries, 4);

    const assetless = screen.getByTestId('committed-entry-wo-2');
    expect(within(assetless).getByText('No asset')).toBeInTheDocument();
    expect(within(assetless).getByText('1')).toBeInTheDocument();
  });

  it('formats a fractional committed quantity to two decimals', () => {
    renderBreakdown(
      [{ ...entries[0], quantity: 2.5 }],
      2.5,
    );

    const panel = screen.getByTestId('committed-breakdown');
    expect(panel).toHaveTextContent('2.50 committed across 1 open work order');
    expect(within(screen.getByTestId('committed-entry-wo-1')).getByText('2.50')).toBeInTheDocument();
  });

  it('explains the empty case instead of showing a bare zero', () => {
    renderBreakdown([], 0);

    expect(screen.getByTestId('committed-breakdown-empty')).toHaveTextContent(
      'Nothing is committed to an open work order.',
    );
  });
});
