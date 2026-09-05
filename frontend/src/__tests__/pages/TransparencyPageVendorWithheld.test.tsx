/**
 * The public transparency page for a visitor with no session
 * (op-anonymous-read-posture).
 *
 * The feed STAYS public — publishing what the makerspace spends is the point of
 * the page, and the captain did not close it. What a caller with no session
 * loses is the vendor half of each row: who we bought from and what that order
 * cost. The aggregate totals, items, quantities, dates and statuses remain.
 *
 * Two failure modes this pins, both of which are the page saying something
 * FALSE about an order rather than something true about the reader:
 *
 *  - `entry.supplier_name || 'N/A'` renders "N/A" in the Supplier column of
 *    every row, which reads as "no supplier on file";
 *  - `formatCurrency` guarded on `=== null` only, so the withheld cost reached
 *    `Intl.NumberFormat().format(undefined)` and rendered "$NaN".
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import TransparencyPage from '../../pages/TransparencyPage';
import { analyticsAPI } from '../../services/api';

vi.mock('../../services/api', () => ({
  analyticsAPI: { getTransparencyLedger: jest.fn() },
}));

/** Exactly what the server sends a caller with no session. */
const withheldPayload = {
  summary: {
    total_orders_with_financial_data: 1,
    // The aggregate is deliberately KEPT: it names no vendor and quotes no
    // vendor's price, and it is the accountability the page exists to provide.
    total_amount_spent: 200.0,
    last_updated: '2026-01-15T12:00:00Z',
    transparency_note:
      'Dallas Makerspace publishes what it spends. Totals, items, quantities and ' +
      'dates are public; supplier names and per-order costs are shown to signed-in members.',
  },
  orders: [
    {
      id: 1,
      item_id: 'item-1',
      item_name: 'Laser Cutter Belt',
      item_category: 'Machinery',
      quantity_ordered: 2,
      status: 'received',
      requested_at: '2026-01-10T10:00:00Z',
      ordered_at: '2026-01-11T10:00:00Z',
      delivered_at: '2026-01-14T10:00:00Z',
      public_notes: 'Installed in the laser shop.',
      vendor_data_withheld: true,
    },
  ],
  ledger: [
    {
      id: 1,
      item_id: 'item-1',
      item_name: 'Laser Cutter Belt',
      quantity: 2,
      requested_at: '2026-01-10T10:00:00Z',
      ordered_at: '2026-01-11T10:00:00Z',
      delivered_at: '2026-01-14T10:00:00Z',
      status: 'received',
      vendor_data_withheld: true,
    },
  ],
  purchase_orders: [],
};

const renderPage = () =>
  render(
    <MantineProvider>
      <MemoryRouter>
        <TransparencyPage />
      </MemoryRouter>
    </MantineProvider>,
  );

describe('TransparencyPage — vendor data withheld', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    (analyticsAPI.getTransparencyLedger as jest.Mock).mockResolvedValue({
      data: withheldPayload,
    });
  });

  it('still publishes the total the page exists to publish', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('$200.00')).toBeInTheDocument();
    });
    expect(screen.getAllByText('Laser Cutter Belt').length).toBeGreaterThan(0);
  });

  it('renders no "$NaN" where a withheld cost used to be formatted', async () => {
    const { container } = renderPage();

    await waitFor(() => {
      expect(screen.getAllByText('Laser Cutter Belt').length).toBeGreaterThan(0);
    });
    expect(container.textContent).not.toContain('NaN');
  });

  it('drops the Supplier and Cost columns instead of filling them with "N/A"', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getAllByText('Laser Cutter Belt').length).toBeGreaterThan(0);
    });
    expect(screen.queryByRole('columnheader', { name: 'Supplier' })).not.toBeInTheDocument();
    expect(screen.queryByRole('columnheader', { name: 'Cost' })).not.toBeInTheDocument();
    // ...and says so once, above the table, rather than down a column.
    expect(screen.getByTestId('ledger-vendor-withheld')).toBeInTheDocument();
  });
});
