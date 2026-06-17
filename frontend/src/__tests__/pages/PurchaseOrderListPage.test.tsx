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
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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

describe('PurchaseOrderListPage sorting', () => {
  const baseOrder = {
    id: 'po-1',
    po_number: 'PO-2026-001',
    supplier_details: 'Acme Supplies',
    status: 'sent',
    status_label: 'Sent to Supplier',
    order_date: '2026-01-15',
    expected_delivery_date: '2026-01-25',
    estimated_total: '1250.00',
    actual_total: '1000.00',
    total_items: 3,
    total_quantity: 12,
    is_fully_received: false,
  };

  // Three orders chosen so each sortable column yields a distinct ordering.
  const orders = [
    {
      ...baseOrder,
      id: 'po-1',
      po_number: 'PO-2026-001',
      order_date: '2026-01-15',
      estimated_total: '1250.00',
      actual_total: '1000.00',
    },
    {
      ...baseOrder,
      id: 'po-2',
      po_number: 'PO-2026-002',
      order_date: '2026-03-10',
      estimated_total: '300.00',
      actual_total: null,
    },
    {
      ...baseOrder,
      id: 'po-3',
      po_number: 'PO-2026-003',
      order_date: '2026-02-01',
      estimated_total: '9999.00',
      actual_total: '50.00',
    },
  ];

  beforeEach(() => {
    jest.clearAllMocks();
    vi.spyOn(console, 'error').mockImplementation(() => {});
    (api.purchaseOrderAPI.listOrders as jest.Mock).mockResolvedValue({
      data: { results: orders },
    });
  });

  // PO numbers in render order (the first body cell of each non-header row).
  const renderedPoNumbers = () =>
    screen
      .getAllByRole('row')
      .slice(1)
      .map((row) => within(row).getAllByRole('cell')[0].textContent);

  const loadPage = async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('PO-2026-001')).toBeInTheDocument();
    });
  };

  it('defaults to Order Date descending (newest first)', async () => {
    await loadPage();

    expect(renderedPoNumbers()).toEqual(['PO-2026-002', 'PO-2026-003', 'PO-2026-001']);
    expect(screen.getByRole('columnheader', { name: 'Order Date' })).toHaveAttribute(
      'aria-sort',
      'descending'
    );
    // Inactive sortable columns advertise themselves via aria-sort="none".
    expect(screen.getByRole('columnheader', { name: 'PO Number' })).toHaveAttribute(
      'aria-sort',
      'none'
    );
  });

  it('sorts by PO Number ascending on click, then toggles to descending', async () => {
    await loadPage();

    await userEvent.click(screen.getByRole('button', { name: 'PO Number' }));
    expect(renderedPoNumbers()).toEqual(['PO-2026-001', 'PO-2026-002', 'PO-2026-003']);
    expect(screen.getByRole('columnheader', { name: 'PO Number' })).toHaveAttribute(
      'aria-sort',
      'ascending'
    );

    await userEvent.click(screen.getByRole('button', { name: 'PO Number' }));
    expect(renderedPoNumbers()).toEqual(['PO-2026-003', 'PO-2026-002', 'PO-2026-001']);
    expect(screen.getByRole('columnheader', { name: 'PO Number' })).toHaveAttribute(
      'aria-sort',
      'descending'
    );
  });

  it('sorts Estimated Total numerically rather than lexically', async () => {
    await loadPage();

    await userEvent.click(screen.getByRole('button', { name: 'Estimated Total' }));
    // 300 < 1250 < 9999 — a string sort would place '1250' before '300'.
    expect(renderedPoNumbers()).toEqual(['PO-2026-002', 'PO-2026-001', 'PO-2026-003']);
  });

  it('keeps blank Actual Total rows last in both sort directions', async () => {
    await loadPage();

    await userEvent.click(screen.getByRole('button', { name: 'Actual Total' }));
    // asc: 50, 1000, then the null (po-2) sorted last.
    expect(renderedPoNumbers()).toEqual(['PO-2026-003', 'PO-2026-001', 'PO-2026-002']);

    await userEvent.click(screen.getByRole('button', { name: 'Actual Total' }));
    // desc: 1000, 50, with the null (po-2) still last.
    expect(renderedPoNumbers()).toEqual(['PO-2026-001', 'PO-2026-003', 'PO-2026-002']);
  });
});
