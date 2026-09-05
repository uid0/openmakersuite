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
    // The gate's marker rides on the SUMMARY as well as on each row. The page
    // reads it here, because the two arrays are built in one server-side loop
    // and so are empty together — see the empty-feed case below.
    vendor_data_withheld: true,
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

/** The same feed as a caller WITH a session gets it: no marker, vendor block intact. */
const SIGNED_IN_NOTE =
  'Dallas Makerspace operates with full financial transparency. All purchase ' +
  'information is publicly available.';

const signedInPayload = {
  summary: {
    ...withheldPayload.summary,
    transparency_note: SIGNED_IN_NOTE,
    vendor_data_withheld: undefined,
  },
  orders: [
    { ...withheldPayload.orders[0], supplier_name: 'Belt Vendor Co.', actual_cost: 200.0 },
  ],
  ledger: [
    { ...withheldPayload.ledger[0], supplier_name: 'Belt Vendor Co.', actual_cost: 200.0 },
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

  it('CONTROL: gives a signed-in reader the Supplier and Cost columns back', async () => {
    (analyticsAPI.getTransparencyLedger as jest.Mock).mockResolvedValue({
      data: signedInPayload,
    });
    renderPage();

    await waitFor(() => {
      expect(screen.getAllByText('Laser Cutter Belt').length).toBeGreaterThan(0);
    });
    expect(screen.getByRole('columnheader', { name: 'Supplier' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Cost' })).toBeInTheDocument();
    expect(screen.queryByTestId('ledger-vendor-withheld')).not.toBeInTheDocument();
  });

  describe('an EMPTY ledger', () => {
    /**
     * REGRESSION. `vendorWithheld` was derived from `orders[0] || ledger[0]`,
     * and the server builds both arrays in one loop — so they empty together
     * and the marker read `false` for a feed with nothing in it. The page then
     * told an anonymous reader that "All financial information is made
     * available", which is the exact claim this branch reworded because the
     * payload stopped honouring it.
     */
    const emptyWithheldPayload = {
      summary: {
        total_orders_with_financial_data: 0,
        total_amount_spent: 0,
        last_updated: '2026-01-15T12:00:00Z',
        transparency_note: withheldPayload.summary.transparency_note,
        vendor_data_withheld: true,
      },
      orders: [],
      ledger: [],
      purchase_orders: [],
    };

    it('does not claim ALL financial information is published', async () => {
      (analyticsAPI.getTransparencyLedger as jest.Mock).mockResolvedValue({
        data: emptyWithheldPayload,
      });
      const { container } = renderPage();

      await waitFor(() => {
        expect(
          screen.getByText(/No logistics purchases with transparency data/i),
        ).toBeInTheDocument();
      });
      expect(container.textContent).not.toContain('All financial information is made available');
      expect(container.textContent).toContain(
        'supplier names and per-order costs are shown to signed-in members',
      );
    });

    it('CONTROL: a signed-in reader of the same empty feed keeps the original claim', async () => {
      (analyticsAPI.getTransparencyLedger as jest.Mock).mockResolvedValue({
        data: {
          ...emptyWithheldPayload,
          summary: {
            ...emptyWithheldPayload.summary,
            transparency_note: SIGNED_IN_NOTE,
            vendor_data_withheld: undefined,
          },
        },
      });
      const { container } = renderPage();

      await waitFor(() => {
        expect(
          screen.getByText(/No logistics purchases with transparency data/i),
        ).toBeInTheDocument();
      });
      expect(container.textContent).toContain('All financial information is made available');
    });
  });
});
