/**
 * Adding a freeform ("custom") line to a draft purchase order.
 *
 * Salvaged from PR #1019 and fitted onto the add-a-line endpoint that landed in
 * #1020: same route, same draft-only rule, a different line shape. A purchase
 * order has always been able to carry a line the catalogue does not know about
 * — a one-off freight charge, a part bought once — but only at create time, so
 * remembering one late meant deleting the order and retyping it.
 *
 * The control sits behind a disclosure deliberately: the scan-and-Enter field
 * above is the workflow, and this is the exception.
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import PurchaseOrderPage from '../../pages/PurchaseOrderPage';
import * as api from '../../services/api';

vi.mock('../../services/api');

vi.mock('../../utils/dialogs', () => ({
  showError: jest.fn(),
  showSuccess: jest.fn(),
  confirmAction: jest.fn(),
  promptInput: jest.fn(),
}));

const freeformLine = (overrides: Record<string, unknown> = {}) => ({
  id: 'line-9',
  item_type: 'freeform',
  description: 'Pallet freight surcharge',
  item_details: null,
  asset_details: null,
  quantity_ordered: 1,
  quantity_received: 0,
  quantity_pending: 1,
  is_fully_received: false,
  unit_cost_ordered: '75.0000',
  unit_cost_actual: null,
  estimated_cost: '75.00',
  actual_cost: null,
  expected_shipment_date: null,
  notes: '',
  is_voided: false,
  voided_at: null,
  void_reason: '',
  work_order: null,
  work_order_details: null,
  owning_group: null,
  owning_group_details: null,
  ...overrides,
});

const order = (overrides: Record<string, unknown> = {}) => ({
  id: 'po-1',
  po_number: 'PO-2026-0007',
  supplier_details: 'Acme Fasteners',
  supplier_agreement: null,
  supplier_agreement_details: null,
  work_order: null,
  work_order_details: null,
  owning_group: null,
  owning_group_details: null,
  status: 'draft',
  status_label: 'Draft',
  order_date: '2026-04-01T00:00:00Z',
  expected_delivery_date: null,
  supplier_order_number: '',
  sales_order_number: '',
  estimated_total: '0.00',
  voided_at: null,
  voided_by_username: null,
  void_reason: '',
  items: [],
  attachments: [],
  ...overrides,
});

const apiError = (status: number, data: Record<string, unknown>) =>
  Object.assign(new Error('request failed'), { response: { status, data } });

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

const openCustomLine = async () => {
  renderPage();
  const toggle = await screen.findByRole('button', { name: /add a custom line/i });
  fireEvent.click(toggle);
  return toggle;
};

describe('PurchaseOrderPage — adding a custom (freeform) line', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('token', 'test-token');
    (api.workOrderAPI.listWorkOrders as jest.Mock).mockResolvedValue({ data: { results: [] } });
    (api.sigAPI.listMySIGs as jest.Mock).mockResolvedValue({ data: { results: [] } });
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: order() });
  });

  test('posts the freeform shape to the one add-a-line endpoint', async () => {
    (api.purchaseOrderAPI.addLineItem as jest.Mock).mockResolvedValue({
      data: {
        created: true,
        line_item: freeformLine(),
        match: null,
        purchase_order: order({ items: [freeformLine()], estimated_total: '75.00' }),
      },
    });

    await openCustomLine();

    fireEvent.change(screen.getByLabelText(/what is being bought/i), {
      target: { value: 'Pallet freight surcharge' },
    });
    fireEvent.change(screen.getByLabelText(/unit cost/i), { target: { value: '75.00' } });
    fireEvent.click(screen.getByRole('button', { name: /add custom line/i }));

    await waitFor(() => {
      expect(api.purchaseOrderAPI.addLineItem).toHaveBeenCalledWith('po-1', {
        description: 'Pallet freight surcharge',
        unit_cost: '75.00',
        quantity: 1,
      });
    });

    // The line's description is the only name it will ever have, so the
    // confirmation has to fall back to it rather than saying "Item".
    expect(await screen.findByRole('status')).toHaveTextContent(
      /Added Pallet freight surcharge × 1/,
    );
  });

  test('an explicit quantity rides along', async () => {
    (api.purchaseOrderAPI.addLineItem as jest.Mock).mockResolvedValue({
      data: {
        created: true,
        line_item: freeformLine({ quantity_ordered: 4 }),
        match: null,
        purchase_order: order(),
      },
    });

    await openCustomLine();

    fireEvent.change(screen.getByLabelText(/what is being bought/i), {
      target: { value: 'Crating' },
    });
    fireEvent.change(screen.getByLabelText(/unit cost/i), { target: { value: '12' } });
    fireEvent.change(screen.getByLabelText(/quantity/i), { target: { value: '4' } });
    fireEvent.click(screen.getByRole('button', { name: /add custom line/i }));

    await waitFor(() => {
      expect(api.purchaseOrderAPI.addLineItem).toHaveBeenCalledWith('po-1', {
        description: 'Crating',
        unit_cost: '12',
        quantity: 4,
      });
    });
  });

  test('a custom line with no price is refused before it reaches the server', async () => {
    await openCustomLine();

    fireEvent.change(screen.getByLabelText(/what is being bought/i), {
      target: { value: 'Mystery charge' },
    });
    fireEvent.click(screen.getByRole('button', { name: /add custom line/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/needs a unit cost/i);
    expect(api.purchaseOrderAPI.addLineItem).not.toHaveBeenCalled();
  });

  test('a server refusal lands under the custom-line form, not the scan field', async () => {
    (api.purchaseOrderAPI.addLineItem as jest.Mock).mockRejectedValue(
      apiError(400, {
        error: 'Line items can only be added while a purchase order is a draft.',
        code: 'not_draft',
      }),
    );

    await openCustomLine();

    fireEvent.change(screen.getByLabelText(/what is being bought/i), {
      target: { value: 'Crating' },
    });
    fireEvent.change(screen.getByLabelText(/unit cost/i), { target: { value: '9' } });
    fireEvent.click(screen.getByRole('button', { name: /add custom line/i }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/only be added while a purchase order is a draft/);
    expect(alert.closest('form')).toBe(
      screen.getByRole('button', { name: /add custom line/i }).closest('form'),
    );
  });

  test('the control is absent once the order is no longer a draft', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({
      data: order({ status: 'sent', status_label: 'Sent to Supplier' }),
    });

    renderPage();
    await screen.findByText(/PO-2026-0007/, { exact: false });

    expect(screen.queryByRole('button', { name: /add a custom line/i })).not.toBeInTheDocument();
  });
});
