/**
 * Work-order / committee association on a purchase order (op-shb9).
 *
 * Create form: order-level pickers offer the unfinished jobs and the viewer's
 * committees, and the choices ride along in the create payload.
 *
 * Detail page: the order's associations are displayed and editable in the
 * order-details block, and every line carries its own pair — settable and
 * clearable through `update_item`, because which job (or committee) the parts
 * were for is often identified after the order goes out.
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import PurchaseOrderFormPage from '../../pages/PurchaseOrderFormPage';
import PurchaseOrderPage from '../../pages/PurchaseOrderPage';
import * as api from '../../services/api';

vi.mock('../../services/api');

vi.mock('../../utils/dialogs', () => ({
  showError: jest.fn(),
  showSuccess: jest.fn(),
  confirmAction: jest.fn((_title: string, _msg: string, onConfirm: () => void) => {
    void onConfirm();
  }),
  promptInput: jest.fn(),
}));

const mockNavigate = jest.fn();
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useNavigate: () => mockNavigate,
}));

const mockItem: api.ReorderDataItem = {
  item_supplier_id: 1,
  item_id: 'item-1',
  item_name: 'Test Item',
  item_sku: 'TEST-001',
  current_stock: 5,
  minimum_stock: 10,
  reorder_quantity: 20,
  suggested_quantity: 10,
  unit_cost: '2.50',
  package_cost: '2.50',
  quantity_per_package: 1,
  lead_time_days: 7,
  supplier_sku: 'SUP-001',
  supplier_url: 'https://example.com/item',
  is_primary: true,
  line_total: '25.00',
};

const supplier = () => ({
  id: 1,
  name: 'Acme Supplies',
  supplier_type: 'online',
  website: '',
  total_items: 1,
  items: [mockItem],
  assets: [],
  estimated_total: '25.00',
  avg_lead_time: 5,
});

const workOrder = (id: string, shortId: string, title: string, status = 'open') => ({
  id,
  short_id: shortId,
  display_title: title,
  maintenance_item_title: title,
  asset_name: 'Lathe',
  status,
});

const sig = (id: number, name: string) => ({ id, name });

/** Every option list the association pickers pull, resolved. */
const mockOptions = () => {
  (api.workOrderAPI.listWorkOrders as jest.Mock).mockImplementation(
    ({ status }: { status: string }) =>
      Promise.resolve({
        data: {
          results:
            status === 'open'
              ? [workOrder('wo-1', 'WO-AAAA1111', 'Replace belt')]
              : [workOrder('wo-2', 'WO-BBBB2222', 'Rewire panel', 'in_progress')],
        },
      })
  );
  (api.sigAPI.listMySIGs as jest.Mock).mockResolvedValue({
    data: { results: [sig(3, 'Woodshop'), sig(4, 'Metal Shop')] },
  });
};

// ───────────────────────────────────────────────────────────────────────────
// Create form — order-level association
// ───────────────────────────────────────────────────────────────────────────
const renderForm = () =>
  render(
    <MemoryRouter>
      <PurchaseOrderFormPage />
    </MemoryRouter>
  );

const selectSupplier = async () => {
  await waitFor(() => {
    expect(screen.getByText('Acme Supplies')).toBeInTheDocument();
  });
  fireEvent.click(screen.getByText('Acme Supplies').closest('button')!);
};

describe('PurchaseOrderFormPage — order-level associations', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('token', 'test-token');
    (api.purchaseOrderAPI.getReorderData as jest.Mock).mockResolvedValue({
      data: { suppliers: [supplier()] },
    });
    (api.supplierAgreementAPI.listBySupplier as jest.Mock).mockResolvedValue({
      data: { results: [] },
    });
    (api.purchaseOrderAPI.createOrder as jest.Mock).mockResolvedValue({
      data: { id: 42, po_number: 'PO-2026-0042' },
    });
    mockOptions();
  });

  test('offers unfinished work orders and the viewer’s committees', async () => {
    renderForm();
    await selectSupplier();

    const workOrderPicker = await screen.findByLabelText('Work Order');
    // Both open and in-progress jobs are offerable — parts get bought mid-job.
    expect(
      within(workOrderPicker).getByRole('option', { name: 'WO-AAAA1111 — Replace belt' })
    ).toBeInTheDocument();
    expect(
      within(workOrderPicker).getByRole('option', { name: 'WO-BBBB2222 — Rewire panel' })
    ).toBeInTheDocument();
    expect((workOrderPicker as HTMLSelectElement).value).toBe('');

    const committeePicker = screen.getByLabelText('Committee');
    expect(within(committeePicker).getByRole('option', { name: 'Woodshop' })).toBeInTheDocument();
    expect((committeePicker as HTMLSelectElement).value).toBe('');
  });

  test('sends the chosen work order and committee with the created order', async () => {
    renderForm();
    await selectSupplier();

    fireEvent.change(await screen.findByLabelText('Work Order'), { target: { value: 'wo-1' } });
    fireEvent.change(screen.getByLabelText('Committee'), { target: { value: '3' } });
    fireEvent.click(screen.getByRole('button', { name: /create purchase order/i }));

    await waitFor(() => {
      expect(api.purchaseOrderAPI.createOrder).toHaveBeenCalled();
    });
    expect((api.purchaseOrderAPI.createOrder as jest.Mock).mock.calls[0][0]).toMatchObject({
      supplier: 1,
      work_order: 'wo-1',
      owning_group: 3,
    });
  });

  test('omits both associations when neither is picked', async () => {
    renderForm();
    await selectSupplier();
    await screen.findByLabelText('Work Order');

    fireEvent.click(screen.getByRole('button', { name: /create purchase order/i }));

    await waitFor(() => {
      expect(api.purchaseOrderAPI.createOrder).toHaveBeenCalled();
    });
    const payload = (api.purchaseOrderAPI.createOrder as jest.Mock).mock.calls[0][0];
    expect(payload).not.toHaveProperty('work_order');
    expect(payload).not.toHaveProperty('owning_group');
  });

  test('a failing options lookup never blocks creating the order', async () => {
    (api.workOrderAPI.listWorkOrders as jest.Mock).mockRejectedValue(new Error('boom'));
    (api.sigAPI.listMySIGs as jest.Mock).mockRejectedValue(new Error('boom'));

    renderForm();
    await selectSupplier();

    const workOrderPicker = await screen.findByLabelText('Work Order');
    expect(within(workOrderPicker).getAllByRole('option')).toHaveLength(1);

    fireEvent.click(screen.getByRole('button', { name: /create purchase order/i }));
    await waitFor(() => {
      expect(api.purchaseOrderAPI.createOrder).toHaveBeenCalled();
    });
  });
});

// ───────────────────────────────────────────────────────────────────────────
// Detail page — order- and line-level association
// ───────────────────────────────────────────────────────────────────────────
const line = (overrides: Record<string, unknown> = {}) => ({
  id: 'line-1',
  item_type: 'inventory_item',
  description: null,
  item_details: { id: 'item-1', name: 'Test Item', sku: 'TEST-001' },
  asset_details: null,
  quantity_ordered: 4,
  quantity_received: 0,
  quantity_pending: 4,
  is_fully_received: false,
  unit_cost_ordered: '2.50',
  unit_cost_actual: null,
  estimated_cost: '10.00',
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

const baseOrder = {
  id: 'po-1',
  po_number: 'PO-2026-0001',
  supplier_details: 'Acme Supplies',
  supplier_agreement: null,
  supplier_agreement_details: null,
  work_order: null,
  work_order_details: null,
  owning_group: null,
  owning_group_details: null,
  status: 'sent',
  status_label: 'Sent',
  order_date: '2026-04-01T00:00:00Z',
  expected_delivery_date: '2026-05-15',
  supplier_order_number: 'SUP-9',
  sales_order_number: 'SO-7',
  estimated_total: '100.00',
  voided_at: null,
  voided_by_username: null,
  void_reason: '',
  items: [line()],
  attachments: [],
};

const renderDetail = () =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={['/purchase-orders/po-1']}>
        <Routes>
          <Route path="/purchase-orders/:orderId" element={<PurchaseOrderPage />} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>
  );

describe('PurchaseOrderPage — order-level associations', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('token', 'test-token');
    (api.purchaseOrderAPI.updateOrder as jest.Mock).mockResolvedValue({ data: baseOrder });
    mockOptions();
  });

  test('shows the work order and committee the order was placed for', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({
      data: {
        ...baseOrder,
        work_order: 'wo-1',
        work_order_details: {
          id: 'wo-1',
          short_id: 'WO-AAAA1111',
          display_title: 'Replace belt',
          status: 'open',
        },
        owning_group: 3,
        owning_group_details: { id: 3, name: 'Woodshop' },
      },
    });

    renderDetail();

    await waitFor(() => {
      expect(screen.getByText('Work Order:')).toBeInTheDocument();
    });
    const workOrderRow = screen.getByText('Work Order:').closest('.info-item')!;
    expect(workOrderRow.querySelector('.info-value')!.textContent).toBe(
      'WO-AAAA1111 — Replace belt'
    );
    const committeeRow = screen.getByText('Committee:').closest('.info-item')!;
    expect(committeeRow.querySelector('.info-value')!.textContent).toBe('Woodshop');
  });

  test('shows an em dash for each association the order does not have', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: baseOrder });

    renderDetail();

    await waitFor(() => {
      expect(screen.getByText('Work Order:')).toBeInTheDocument();
    });
    expect(
      screen.getByText('Work Order:').closest('.info-item')!.querySelector('.info-value')!
        .textContent
    ).toBe('—');
    expect(
      screen.getByText('Committee:').closest('.info-item')!.querySelector('.info-value')!
        .textContent
    ).toBe('—');
  });

  test('saves both associations from the order-details editor', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: baseOrder });

    renderDetail();

    fireEvent.click(await screen.findByRole('button', { name: /edit details/i }));
    fireEvent.change(await screen.findByLabelText('Work Order'), { target: { value: 'wo-2' } });
    fireEvent.change(screen.getByLabelText('Committee'), { target: { value: '4' } });
    fireEvent.click(screen.getByRole('button', { name: /save details/i }));

    await waitFor(() => {
      expect(api.purchaseOrderAPI.updateOrder).toHaveBeenCalled();
    });
    expect((api.purchaseOrderAPI.updateOrder as jest.Mock).mock.calls[0][1]).toMatchObject({
      work_order: 'wo-2',
      owning_group: 4,
    });
  });

  test('clearing an association sends null rather than dropping the key', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({
      data: {
        ...baseOrder,
        work_order: 'wo-1',
        work_order_details: {
          id: 'wo-1',
          short_id: 'WO-AAAA1111',
          display_title: 'Replace belt',
          status: 'open',
        },
        owning_group: 3,
        owning_group_details: { id: 3, name: 'Woodshop' },
      },
    });

    renderDetail();

    fireEvent.click(await screen.findByRole('button', { name: /edit details/i }));
    const picker = (await screen.findByLabelText('Work Order')) as HTMLSelectElement;
    expect(picker.value).toBe('wo-1');
    fireEvent.change(picker, { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('Committee'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: /save details/i }));

    await waitFor(() => {
      expect(api.purchaseOrderAPI.updateOrder).toHaveBeenCalled();
    });
    expect((api.purchaseOrderAPI.updateOrder as jest.Mock).mock.calls[0][1]).toMatchObject({
      work_order: null,
      owning_group: null,
    });
  });

  test('an order tagged with a finished job keeps it selectable', async () => {
    // The picker only lists open/in-progress jobs, so a completed one has to be
    // grafted on — otherwise editing an unrelated field would silently drop it.
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({
      data: {
        ...baseOrder,
        work_order: 'wo-old',
        work_order_details: {
          id: 'wo-old',
          short_id: 'WO-CCCC3333',
          display_title: 'Last winter’s rebuild',
          status: 'completed',
        },
      },
    });

    renderDetail();

    fireEvent.click(await screen.findByRole('button', { name: /edit details/i }));
    const picker = (await screen.findByLabelText('Work Order')) as HTMLSelectElement;
    expect(picker.value).toBe('wo-old');
    expect(
      within(picker).getByRole('option', { name: 'WO-CCCC3333 — Last winter’s rebuild' })
    ).toBeInTheDocument();
  });
});

describe('PurchaseOrderPage — line-level associations', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('token', 'test-token');
    (api.purchaseOrderAPI.updateLineItem as jest.Mock).mockResolvedValue({ data: line() });
    mockOptions();
  });

  test('shows the work order and committee a line was ordered for', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({
      data: {
        ...baseOrder,
        items: [
          line({
            work_order: 'wo-1',
            work_order_details: {
              id: 'wo-1',
              short_id: 'WO-AAAA1111',
              display_title: 'Replace belt',
              status: 'open',
            },
            owning_group: 3,
            owning_group_details: { id: 3, name: 'Woodshop' },
          }),
        ],
      },
    });

    renderDetail();

    await waitFor(() => {
      expect(screen.getByText('Ordered For')).toBeInTheDocument();
    });
    expect(screen.getByText('WO-AAAA1111 — Replace belt')).toBeInTheDocument();
    expect(screen.getByText('Woodshop')).toBeInTheDocument();
  });

  test('tags a line with a work order and a committee', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: baseOrder });

    renderDetail();

    fireEvent.click(
      await screen.findByRole('button', { name: /edit work order and committee/i })
    );
    fireEvent.change(await screen.findByLabelText('Work Order'), { target: { value: 'wo-1' } });
    fireEvent.change(screen.getByLabelText('Committee'), { target: { value: '3' } });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => {
      expect(api.purchaseOrderAPI.updateLineItem).toHaveBeenCalled();
    });
    const [orderId, itemId, payload] = (api.purchaseOrderAPI.updateLineItem as jest.Mock).mock
      .calls[0];
    expect(orderId).toBe('po-1');
    expect(itemId).toBe('line-1');
    expect(payload).toEqual({ work_order: 'wo-1', owning_group: 3 });
  });

  test('clears a line’s associations with nulls', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({
      data: {
        ...baseOrder,
        items: [
          line({
            work_order: 'wo-1',
            work_order_details: {
              id: 'wo-1',
              short_id: 'WO-AAAA1111',
              display_title: 'Replace belt',
              status: 'open',
            },
            owning_group: 3,
            owning_group_details: { id: 3, name: 'Woodshop' },
          }),
        ],
      },
    });

    renderDetail();

    fireEvent.click(
      await screen.findByRole('button', { name: /edit work order and committee/i })
    );
    fireEvent.change(await screen.findByLabelText('Work Order'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('Committee'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => {
      expect(api.purchaseOrderAPI.updateLineItem).toHaveBeenCalled();
    });
    expect((api.purchaseOrderAPI.updateLineItem as jest.Mock).mock.calls[0][2]).toEqual({
      work_order: null,
      owning_group: null,
    });
  });

  test('a signed-out viewer gets no line association editor', async () => {
    localStorage.clear();
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: baseOrder });

    renderDetail();

    await waitFor(() => {
      expect(screen.getByText('Ordered For')).toBeInTheDocument();
    });
    expect(
      screen.queryByRole('button', { name: /edit work order and committee/i })
    ).not.toBeInTheDocument();
  });
});
