/**
 * Adding line items to a draft purchase order, and explaining why receiving is
 * unavailable (op-4kq).
 *
 * Lines could only ever be supplied when the order was created, so a forgotten
 * item meant deleting the PO and retyping it. The detail page now stages lines
 * locally and posts them as one batch, draft-only.
 *
 * The companion change is the notice: the receive affordances used to simply
 * vanish on an order that could not receive, which reads as a broken button
 * rather than a draft that has not been sent yet.
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import PurchaseOrderPage from '../../pages/PurchaseOrderPage';
import * as api from '../../services/api';
import { showError } from '../../utils/dialogs';

vi.mock('../../services/api');

vi.mock('../../utils/dialogs', () => ({
  showError: jest.fn(),
  showSuccess: jest.fn(),
  confirmAction: jest.fn((_title, _msg, onConfirm) => {
    void onConfirm();
  }),
  promptInput: jest.fn(),
}));

const renderPage = () =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={['/purchase-orders/po-1']}>
        <Routes>
          <Route path="/purchase-orders/:orderId" element={<PurchaseOrderPage />} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>
  );

const draftOrder = {
  id: 'po-1',
  po_number: 'PO-2026-0014',
  supplier: 3,
  supplier_details: 'HD Supply',
  status: 'draft',
  status_label: 'Draft',
  order_date: '2026-07-25T00:00:00Z',
  expected_delivery_date: null,
  supplier_order_number: '',
  sales_order_number: '',
  estimated_total: '0.00',
  voided_at: null,
  voided_by_username: null,
  void_reason: '',
  items: [],
  attachments: [],
};

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
  localStorage.setItem('token', 'test-token');
  localStorage.setItem('is_staff', 'true');
});

const loadOrder = async (order: Record<string, unknown>) => {
  (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: order });
  renderPage();
  await waitFor(() => {
    expect(screen.getByText(String(order.po_number), { exact: false })).toBeInTheDocument();
  });
};

describe('explaining why receiving is unavailable', () => {
  test('a draft order says to send it first', async () => {
    await loadOrder(draftOrder);

    expect(screen.getByTestId('receive-unavailable-notice')).toHaveTextContent(
      /still a draft.*[Ss]end it to the supplier/,
    );
    expect(screen.queryByRole('button', { name: /Receive items/i })).not.toBeInTheDocument();
  });

  test('a fully received order says so', async () => {
    await loadOrder({ ...draftOrder, status: 'received', status_label: 'Fully Received' });

    expect(screen.getByTestId('receive-unavailable-notice')).toHaveTextContent(
      /already been received in full/,
    );
  });

  test('a voided order says so', async () => {
    await loadOrder({ ...draftOrder, status: 'voided', status_label: 'Voided' });

    expect(screen.getByTestId('receive-unavailable-notice')).toHaveTextContent(/voided/);
  });

  test('no notice on an order that CAN receive', async () => {
    await loadOrder({ ...draftOrder, status: 'sent', status_label: 'Sent to Supplier' });

    expect(screen.queryByTestId('receive-unavailable-notice')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Receive items/i })).toBeInTheDocument();
  });
});

describe('adding line items to a draft order', () => {
  test('the Add items button only appears on a draft', async () => {
    await loadOrder(draftOrder);
    expect(screen.getByRole('button', { name: /Add items/i })).toBeInTheDocument();
  });

  test('the Add items button is absent once the order is sent', async () => {
    await loadOrder({ ...draftOrder, status: 'sent', status_label: 'Sent to Supplier' });
    expect(screen.queryByRole('button', { name: /Add items/i })).not.toBeInTheDocument();
  });

  test('stages a supplier item and posts it as a batch', async () => {
    (api.inventoryAPI.listItems as jest.Mock).mockResolvedValue({
      data: { results: [{ id: 'item-1', name: 'Shop Towels', sku: 'ST-1' }] },
    });
    (api.inventoryAPI.getItemSuppliers as jest.Mock).mockResolvedValue({
      data: {
        results: [
          { id: 55, supplier: 3, is_active: true, is_discontinued: false, unit_cost: '4.25' },
        ],
      },
    });
    (api.purchaseOrderAPI.addLineItems as jest.Mock).mockResolvedValue({
      data: { ...draftOrder, total_items: 1 },
    });

    await loadOrder(draftOrder);
    fireEvent.click(screen.getByRole('button', { name: /Add items/i }));

    fireEvent.change(screen.getByLabelText(/Item name or SKU/i), {
      target: { value: 'Shop Towels' },
    });
    fireEvent.change(screen.getByLabelText(/^Quantity$/i), { target: { value: '12' } });
    fireEvent.click(screen.getByRole('button', { name: /Stage inventory item/i }));

    await waitFor(() => {
      expect(screen.getByText('Shop Towels (ST-1)')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /Add to order/i }));

    await waitFor(() => {
      expect(api.purchaseOrderAPI.addLineItems).toHaveBeenCalledWith('po-1', [
        { item_supplier_id: 55, quantity: 12, unit_cost: 4.25 },
      ]);
    });
  });

  test('refuses an item the supplier does not carry', async () => {
    (api.inventoryAPI.listItems as jest.Mock).mockResolvedValue({
      data: { results: [{ id: 'item-9', name: 'Widget', sku: 'W-9' }] },
    });
    (api.inventoryAPI.getItemSuppliers as jest.Mock).mockResolvedValue({
      // Belongs to a different supplier than this order's (3).
      data: {
        results: [
          { id: 77, supplier: 99, is_active: true, is_discontinued: false, unit_cost: '1.00' },
        ],
      },
    });

    await loadOrder(draftOrder);
    fireEvent.click(screen.getByRole('button', { name: /Add items/i }));
    fireEvent.change(screen.getByLabelText(/Item name or SKU/i), {
      target: { value: 'Widget' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Stage inventory item/i }));

    await waitFor(() => {
      expect(showError).toHaveBeenCalledWith(
        expect.stringContaining('not available from this supplier'),
      );
    });
    expect(api.purchaseOrderAPI.addLineItems).not.toHaveBeenCalled();
  });

  test('stages a freeform line', async () => {
    (api.purchaseOrderAPI.addLineItems as jest.Mock).mockResolvedValue({ data: draftOrder });

    await loadOrder(draftOrder);
    fireEvent.click(screen.getByRole('button', { name: /Add items/i }));

    fireEvent.change(screen.getByLabelText(/Freeform description/i), {
      target: { value: 'Pallet surcharge' },
    });
    fireEvent.change(screen.getByLabelText(/Unit cost/i), { target: { value: '9.00' } });
    fireEvent.click(screen.getByRole('button', { name: /Stage freeform line/i }));

    await waitFor(() => {
      expect(screen.getByText('Pallet surcharge')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /Add to order/i }));

    await waitFor(() => {
      expect(api.purchaseOrderAPI.addLineItems).toHaveBeenCalledWith('po-1', [
        { description: 'Pallet surcharge', quantity: 1, unit_cost: 9 },
      ]);
    });
  });

  test('a freeform line without a unit cost is refused before posting', async () => {
    await loadOrder(draftOrder);
    fireEvent.click(screen.getByRole('button', { name: /Add items/i }));

    fireEvent.change(screen.getByLabelText(/Freeform description/i), {
      target: { value: 'Mystery fee' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Stage freeform line/i }));

    await waitFor(() => {
      expect(showError).toHaveBeenCalledWith(expect.stringContaining('unit cost'));
    });
    expect(api.purchaseOrderAPI.addLineItems).not.toHaveBeenCalled();
  });

  test('repaints from the response instead of refetching', async () => {
    (api.inventoryAPI.listItems as jest.Mock).mockResolvedValue({
      data: { results: [{ id: 'item-1', name: 'Shop Towels', sku: 'ST-1' }] },
    });
    (api.inventoryAPI.getItemSuppliers as jest.Mock).mockResolvedValue({
      data: {
        results: [
          { id: 55, supplier: 3, is_active: true, is_discontinued: false, unit_cost: '4.25' },
        ],
      },
    });
    (api.purchaseOrderAPI.addLineItems as jest.Mock).mockResolvedValue({
      data: { ...draftOrder, po_number: 'PO-2026-0014', estimated_total: '51.00' },
    });

    await loadOrder(draftOrder);
    const getCallsBefore = (api.purchaseOrderAPI.getOrder as jest.Mock).mock.calls.length;

    fireEvent.click(screen.getByRole('button', { name: /Add items/i }));
    fireEvent.change(screen.getByLabelText(/Item name or SKU/i), {
      target: { value: 'Shop Towels' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Stage inventory item/i }));
    await waitFor(() => {
      expect(screen.getByText('Shop Towels (ST-1)')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: /Add to order/i }));

    await waitFor(() => {
      expect(api.purchaseOrderAPI.addLineItems).toHaveBeenCalled();
    });
    expect((api.purchaseOrderAPI.getOrder as jest.Mock).mock.calls.length).toBe(getCallsBefore);
  });
});
