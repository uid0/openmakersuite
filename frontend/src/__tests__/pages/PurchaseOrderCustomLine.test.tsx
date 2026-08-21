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

const candidate = (overrides: Record<string, unknown> = {}) => ({
  item_supplier: 12,
  match_kind: 'partial_item_name',
  match_label: 'item name (partial)',
  matched_value: 'M3 hex',
  is_exact: false,
  item: { id: 'item-1', name: 'M3 hex bolt', sku: 'OMS-M3-HEX', is_kit: false },
  supplier_sku: 'ACME-M3-100',
  package_upc: '012345678905',
  unit_upc: '998877665544',
  quantity_per_package: 5,
  suggested_quantity: 10,
  suggested_unit_cost: '2.50',
  already_on_order: null,
  ...overrides,
});

const inventoryLine = (overrides: Record<string, unknown> = {}) => ({
  ...freeformLine({
    id: 'line-1',
    item_type: 'inventory_item',
    description: null,
    item_details: { id: 'item-1', name: 'M3 hex bolt', sku: 'OMS-M3-HEX' },
    quantity_ordered: 10,
  }),
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
  /**
   * The two controls are independent. Adding from one must not silently throw
   * away work sitting in the other — the operator gets no warning and no way
   * back, which is the exact failure this change set exists to close.
   */
  describe('the scan field and the custom-line form do not clobber each other', () => {
    test('a successful identifier add leaves a half-typed custom line alone', async () => {
      (api.purchaseOrderAPI.addLineItem as jest.Mock).mockResolvedValue({
        data: {
          created: true,
          line_item: inventoryLine(),
          match: null,
          purchase_order: order({ items: [inventoryLine()] }),
        },
      });

      await openCustomLine();

      fireEvent.change(screen.getByLabelText(/what is being bought/i), {
        target: { value: 'Pallet freight surcharge' },
      });
      fireEvent.change(screen.getByLabelText(/unit cost/i), { target: { value: '75.00' } });

      // ...and only then remembers the catalogued part that should go on first.
      const scanField = screen.getByLabelText(/add an item/i);
      fireEvent.change(scanField, { target: { value: 'OMS-M3-HEX' } });
      fireEvent.submit(scanField.closest('form')!);

      await screen.findByRole('status');

      expect(screen.getByLabelText(/what is being bought/i)).toHaveValue(
        'Pallet freight surcharge',
      );
      expect(screen.getByLabelText(/unit cost/i)).toHaveValue(75);
      // The control the add came from does clear itself, ready for the next scan.
      expect(screen.getByLabelText(/add an item/i)).toHaveValue('');
    });

    test('a successful custom-line add leaves a pending ambiguity choice-set standing', async () => {
      (api.purchaseOrderAPI.addLineItem as jest.Mock).mockRejectedValueOnce(
        apiError(409, {
          code: 'ambiguous',
          error: '"M3 hex" matches 2 items Acme Fasteners supplies. Choose which one to add.',
          candidates: [
            candidate(),
            candidate({
              item_supplier: 13,
              item: { id: 'item-2', name: 'M3 hex nut', sku: 'OMS-M3-NUT', is_kit: false },
            }),
          ],
        }),
      );

      renderPage();
      const scanField = await screen.findByLabelText(/add an item/i);
      fireEvent.change(scanField, { target: { value: 'M3 hex' } });
      fireEvent.submit(scanField.closest('form')!);

      await screen.findByRole('button', { name: /add m3 hex nut/i });

      (api.purchaseOrderAPI.addLineItem as jest.Mock).mockResolvedValueOnce({
        data: {
          created: true,
          line_item: freeformLine(),
          match: null,
          purchase_order: order({ items: [freeformLine()] }),
        },
      });

      fireEvent.click(screen.getByRole('button', { name: /add a custom line/i }));
      fireEvent.change(screen.getByLabelText(/what is being bought/i), {
        target: { value: 'Pallet freight surcharge' },
      });
      fireEvent.change(screen.getByLabelText(/unit cost/i), { target: { value: '75.00' } });
      fireEvent.click(screen.getByRole('button', { name: /add custom line/i }));

      await waitFor(() => {
        expect(api.purchaseOrderAPI.addLineItem).toHaveBeenLastCalledWith('po-1', {
          description: 'Pallet freight surcharge',
          unit_cost: '75.00',
          quantity: 1,
        });
      });

      // The choice the operator still owes an answer to is still on screen,
      // and so is what they scanned to raise it.
      expect(screen.getByRole('button', { name: /add m3 hex bolt/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /add m3 hex nut/i })).toBeInTheDocument();
      expect(screen.getByLabelText(/add an item/i)).toHaveValue('M3 hex');
      // Its own fields are the ones that reset.
      expect(screen.getByLabelText(/what is being bought/i)).toHaveValue('');
    });
  });
});
