/**
 * Resilience tests for PurchaseOrderListPage (#457 R3).
 *
 * Covers the four states a transparency-facing list must survive:
 *  - loading (request in flight),
 *  - empty (no orders match the filter),
 *  - forbidden (403 — surfaced in the error banner, page does not crash),
 *  - error (server failure — falls back to the generic message),
 *  - plus an a11y audit of the populated table (AC-20).
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import PurchaseOrderListPage from '../../pages/PurchaseOrderListPage';
import * as api from '../../services/api';
import { expectNoA11yViolations } from '../helpers/axe';

vi.mock('../../services/api');

const renderPage = () =>
  render(
    <MantineProvider>
      <MemoryRouter>
        <PurchaseOrderListPage />
      </MemoryRouter>
    </MantineProvider>
  );

describe('PurchaseOrderListPage', () => {
  const mockOrder = {
    id: 'po-1',
    po_number: 'PO-2026-001',
    supplier_details: 'Acme Supplies',
    status: 'sent',
    status_label: 'Sent to Supplier',
    order_date: '2026-01-15',
    expected_delivery_date: '2026-01-25',
    estimated_total: '1250.00',
    actual_total: null,
    total_items: 3,
    total_quantity: 12,
    is_fully_received: false,
  };

  beforeEach(() => {
    jest.clearAllMocks();
    // The page logs failures to console.error; keep the test output clean.
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('shows the loading state while orders are in flight', () => {
    // A promise that never settles keeps the page in its loading state.
    (api.purchaseOrderAPI.listOrders as jest.Mock).mockReturnValue(new Promise(() => {}));

    renderPage();

    expect(screen.getByText(/Loading purchase orders/)).toBeInTheDocument();
  });

  it('shows the empty state when no orders match', async () => {
    (api.purchaseOrderAPI.listOrders as jest.Mock).mockResolvedValue({
      data: { results: [] },
    });

    renderPage();

    await waitFor(() => {
      expect(screen.queryByText(/Loading purchase orders/)).not.toBeInTheDocument();
    });
    expect(screen.getByText(/No purchase orders found/)).toBeInTheDocument();
  });

  it('renders the orders table once loaded', async () => {
    (api.purchaseOrderAPI.listOrders as jest.Mock).mockResolvedValue({
      data: { results: [mockOrder] },
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText('PO-2026-001')).toBeInTheDocument();
    });
    expect(screen.getByText('Acme Supplies')).toBeInTheDocument();
    // 'Sent to Supplier' is also a filter <option>; scope to the status badge.
    expect(screen.getByText('Sent to Supplier', { selector: '.status-badge' })).toBeInTheDocument();
    expect(screen.getByText('View Details →')).toBeInTheDocument();
  });

  it('surfaces a forbidden (403) response in the error banner', async () => {
    (api.purchaseOrderAPI.listOrders as jest.Mock).mockRejectedValue({
      response: {
        status: 403,
        data: { detail: 'You do not have permission to view purchase orders.' },
      },
    });

    renderPage();

    await waitFor(() => {
      expect(
        screen.getByText('You do not have permission to view purchase orders.')
      ).toBeInTheDocument();
    });
    // The page degrades gracefully rather than rendering a table.
    expect(screen.getByText(/No purchase orders found/)).toBeInTheDocument();
  });

  it('falls back to a generic message on a server error', async () => {
    (api.purchaseOrderAPI.listOrders as jest.Mock).mockRejectedValue({
      response: { status: 500, data: {} },
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Failed to load purchase orders')).toBeInTheDocument();
    });
  });

  it('has no accessibility violations with orders loaded', async () => {
    (api.purchaseOrderAPI.listOrders as jest.Mock).mockResolvedValue({
      data: { results: [mockOrder] },
    });

    const { container } = renderPage();

    await waitFor(() => {
      expect(screen.getByText('PO-2026-001')).toBeInTheDocument();
    });
    await expectNoA11yViolations(container);
  });
});
