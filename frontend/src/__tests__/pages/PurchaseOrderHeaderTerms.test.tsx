/**
 * Purchase-order header terms in the web UI (op-uc0o, consuming op-bwo9).
 *
 * Detail page: the order date is editable — an order is often entered after it
 * was placed — alongside priority and the payment/freight terms, and the
 * payment those terms imply is read back from the API.
 *
 * Create form: the same four fields are set before the order exists, and the
 * payment schedule is mirrored client-side so it tracks the cart as lines are
 * added rather than waiting for a round trip.
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
  confirmAction: jest.fn(),
  promptInput: jest.fn(),
}));

const mockNavigate = jest.fn();
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useNavigate: () => mockNavigate,
}));

// ───────────────────────────────────────────────────────────────────────────
// Create form — header terms and the live payment schedule
// ───────────────────────────────────────────────────────────────────────────
const mockItem: api.ReorderDataItem = {
  item_supplier_id: 1,
  item_id: 'item-1',
  item_name: 'Test Item',
  item_sku: 'TEST-001',
  current_stock: 5,
  minimum_stock: 10,
  // 10 × $2.50 = $25.00 in the cart the moment the supplier is picked.
  suggested_quantity: 10,
  reorder_quantity: 20,
  unit_cost: '2.50',
  package_cost: '2.50',
  quantity_per_package: 1,
  lead_time_days: 7,
  supplier_sku: 'SUP-001',
  supplier_url: 'https://example.com/item',
  is_primary: true,
  line_total: '25.00',
};

const mockSupplier = {
  id: 1,
  name: 'Acme Supplies',
  supplier_type: 'online',
  website: '',
  total_items: 1,
  items: [mockItem],
  assets: [],
  estimated_total: '25.00',
  avg_lead_time: 5,
};

const liveSchedule = () => screen.getByTestId('live-payment-schedule').textContent;

const renderFormWithSupplier = async () => {
  render(
    <MemoryRouter>
      <PurchaseOrderFormPage />
    </MemoryRouter>
  );
  await waitFor(() => {
    expect(screen.getByText('Acme Supplies')).toBeInTheDocument();
  });
  fireEvent.click(screen.getByText('Acme Supplies').closest('button')!);
  await screen.findByText('Test Item');
};

describe('PurchaseOrderFormPage — header terms', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('token', 'test-token');
    (api.purchaseOrderAPI.getReorderData as jest.Mock).mockResolvedValue({
      data: { suppliers: [mockSupplier] },
    });
    (api.supplierAgreementAPI.listBySupplier as jest.Mock).mockResolvedValue({
      data: { results: [] },
    });
    (api.workOrderAPI.listWorkOrders as jest.Mock).mockResolvedValue({ data: { results: [] } });
    (api.sigAPI.listMySIGs as jest.Mock).mockResolvedValue({ data: { results: [] } });
    (api.purchaseOrderAPI.createOrder as jest.Mock).mockResolvedValue({
      data: { id: 'po-42', po_number: 'PO-2026-0042' },
    });
  });

  test('a new order reads as a draft with no payment due until terms are set', async () => {
    await renderFormWithSupplier();

    expect(screen.getByTestId('live-status')).toHaveTextContent('Draft');
    expect(liveSchedule()).toBe('$25.00 — no due date (No payment terms set)');
  });

  test('net terms fall due that many days after the order date', async () => {
    await renderFormWithSupplier();

    fireEvent.change(screen.getByLabelText('Date Ordered'), {
      target: { value: '2026-04-01' },
    });
    fireEvent.change(screen.getByLabelText('Payment Terms'), { target: { value: 'net_30' } });

    expect(liveSchedule()).toBe('$25.00 — due May 1, 2026 (Net 30 from order date)');
  });

  test('the payment tracks the cart as line quantities change', async () => {
    await renderFormWithSupplier();

    fireEvent.change(screen.getByLabelText('Date Ordered'), {
      target: { value: '2026-04-01' },
    });
    fireEvent.change(screen.getByLabelText('Payment Terms'), { target: { value: 'net_15' } });
    expect(liveSchedule()).toBe('$25.00 — due Apr 16, 2026 (Net 15 from order date)');

    // Doubling the line doubles the payment — same due date, no round trip.
    fireEvent.change(screen.getByLabelText('Quantity for Test Item'), {
      target: { value: '20' },
    });
    expect(liveSchedule()).toBe('$50.00 — due Apr 16, 2026 (Net 15 from order date)');
  });

  test('delivery-anchored terms wait for a promised date', async () => {
    await renderFormWithSupplier();

    fireEvent.change(screen.getByLabelText('Payment Terms'), { target: { value: 'cod' } });
    expect(liveSchedule()).toBe('$25.00 — no due date (On delivery)');

    fireEvent.change(screen.getByLabelText('Date Promised (expected delivery)'), {
      target: { value: '2026-04-20' },
    });
    expect(liveSchedule()).toBe('$25.00 — due Apr 20, 2026 (On delivery)');
  });

  test('sends the header terms with the created order', async () => {
    await renderFormWithSupplier();

    fireEvent.change(screen.getByLabelText('Date Ordered'), {
      target: { value: '2026-04-01' },
    });
    fireEvent.change(screen.getByLabelText('Priority'), { target: { value: 'urgent' } });
    fireEvent.change(screen.getByLabelText('Payment Terms'), { target: { value: 'net_30' } });
    fireEvent.change(screen.getByLabelText('Freight Terms'), { target: { value: 'fob_origin' } });
    fireEvent.click(screen.getByRole('button', { name: /create purchase order/i }));

    await waitFor(() => {
      expect(api.purchaseOrderAPI.createOrder).toHaveBeenCalled();
    });
    expect((api.purchaseOrderAPI.createOrder as jest.Mock).mock.calls[0][0]).toMatchObject({
      // A day picked in the UI is a day the server keeps: midday UTC survives
      // the datetime round trip in either direction.
      order_date: '2026-04-01T12:00:00Z',
      priority: 'urgent',
      payment_terms: 'net_30',
      freight_terms: 'fob_origin',
    });
  });

  test('an order placed now keeps the server’s own date and no agreed terms', async () => {
    await renderFormWithSupplier();

    fireEvent.click(screen.getByRole('button', { name: /create purchase order/i }));

    await waitFor(() => {
      expect(api.purchaseOrderAPI.createOrder).toHaveBeenCalled();
    });
    const payload = (api.purchaseOrderAPI.createOrder as jest.Mock).mock.calls[0][0];
    expect(payload).not.toHaveProperty('order_date');
    expect(payload).not.toHaveProperty('payment_terms');
    expect(payload).not.toHaveProperty('freight_terms');
    expect(payload.priority).toBe('normal');
  });
});

// ───────────────────────────────────────────────────────────────────────────
// Detail page — displaying and editing the header
// ───────────────────────────────────────────────────────────────────────────
const line = () => ({
  id: 'line-1',
  item_type: 'inventory_item',
  description: null,
  item_details: { id: 'item-1', name: 'Test Item', sku: 'TEST-001' },
  asset_details: null,
  quantity_ordered: 4,
  quantity_received: 0,
  quantity_pending: 4,
  is_fully_received: false,
  unit_cost_ordered: '25.00',
  unit_cost_actual: null,
  estimated_cost: '100.00',
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
});

const baseOrder = {
  id: 'po-1',
  po_number: 'PO-2026-0001',
  supplier_details: 'Acme Supplies',
  supplier_ordering_adapter: null,
  supplier_agreement: null,
  supplier_agreement_details: null,
  work_order: null,
  work_order_details: null,
  owning_group: null,
  owning_group_details: null,
  status: 'sent',
  status_label: 'Sent',
  // Midnight UTC — west of UTC this renders as the previous day unless the
  // page reads it as the business date the backend derives payments from.
  order_date: '2026-04-01T00:00:00Z',
  priority: 'high',
  payment_terms: 'net_30',
  freight_terms: 'fob_destination',
  payment_schedule: {
    due_date: '2026-05-01',
    amount: '100.00',
    basis: 'Net 30 from order date',
  },
  expected_delivery_date: '2026-04-20',
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

const infoValue = (label: string) =>
  screen.getByText(label).closest('.info-item')!.querySelector('.info-value')!.textContent;

describe('PurchaseOrderPage — header terms', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('token', 'test-token');
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: baseOrder });
    (api.purchaseOrderAPI.updateOrder as jest.Mock).mockResolvedValue({ data: baseOrder });
    (api.workOrderAPI.listWorkOrders as jest.Mock).mockResolvedValue({ data: { results: [] } });
    (api.sigAPI.listMySIGs as jest.Mock).mockResolvedValue({ data: { results: [] } });
  });

  test('shows the terms and the payment they imply', async () => {
    renderDetail();

    await waitFor(() => {
      expect(screen.getByText('Priority:')).toBeInTheDocument();
    });
    expect(infoValue('Date Ordered:')).toBe('Apr 1, 2026');
    expect(infoValue('Date Promised:')).toBe('Apr 20, 2026');
    expect(infoValue('Priority:')).toBe('High');
    expect(infoValue('Payment Terms:')).toBe('Net 30');
    expect(infoValue('Freight:')).toBe('FOB Destination');
    expect(infoValue('Payment schedule:')).toBe(
      '$100.00 — due May 1, 2026 (Net 30 from order date)'
    );
  });

  test('reads an order with no terms agreed as an em dash, not a blank', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({
      data: {
        ...baseOrder,
        priority: 'normal',
        payment_terms: '',
        freight_terms: '',
        payment_schedule: {
          due_date: null,
          amount: '100.00',
          basis: 'No payment terms set',
        },
      },
    });

    renderDetail();

    await waitFor(() => {
      expect(screen.getByText('Priority:')).toBeInTheDocument();
    });
    expect(infoValue('Payment Terms:')).toBe('—');
    expect(infoValue('Freight:')).toBe('—');
    expect(infoValue('Payment schedule:')).toBe(
      '$100.00 — no due date (No payment terms set)'
    );
  });

  test('the editor opens on the order’s current terms', async () => {
    renderDetail();

    fireEvent.click(await screen.findByRole('button', { name: /edit details/i }));

    expect((screen.getByLabelText('Date Ordered') as HTMLInputElement).value).toBe('2026-04-01');
    expect((screen.getByLabelText('Priority') as HTMLSelectElement).value).toBe('high');
    expect((screen.getByLabelText('Payment Terms') as HTMLSelectElement).value).toBe('net_30');
    expect((screen.getByLabelText('Freight Terms') as HTMLSelectElement).value).toBe(
      'fob_destination'
    );
    const promised = screen.getByLabelText('Date Promised (expected delivery)');
    expect((promised as HTMLInputElement).value).toBe('2026-04-20');
  });

  test('saves a backdated order date and the three terms', async () => {
    renderDetail();

    fireEvent.click(await screen.findByRole('button', { name: /edit details/i }));
    fireEvent.change(screen.getByLabelText('Date Ordered'), { target: { value: '2026-03-20' } });
    fireEvent.change(screen.getByLabelText('Priority'), { target: { value: 'urgent' } });
    fireEvent.change(screen.getByLabelText('Payment Terms'), { target: { value: 'net_60' } });
    fireEvent.change(screen.getByLabelText('Freight Terms'), { target: { value: 'collect' } });
    fireEvent.click(screen.getByRole('button', { name: /save details/i }));

    await waitFor(() => {
      expect(api.purchaseOrderAPI.updateOrder).toHaveBeenCalled();
    });
    const [orderId, payload] = (api.purchaseOrderAPI.updateOrder as jest.Mock).mock.calls[0];
    expect(orderId).toBe('po-1');
    expect(payload).toMatchObject({
      order_date: '2026-03-20T12:00:00Z',
      priority: 'urgent',
      payment_terms: 'net_60',
      freight_terms: 'collect',
    });
  });

  test('clearing a term sends the blank rather than dropping the key', async () => {
    renderDetail();

    fireEvent.click(await screen.findByRole('button', { name: /edit details/i }));
    fireEvent.change(screen.getByLabelText('Payment Terms'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('Freight Terms'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: /save details/i }));

    await waitFor(() => {
      expect(api.purchaseOrderAPI.updateOrder).toHaveBeenCalled();
    });
    expect((api.purchaseOrderAPI.updateOrder as jest.Mock).mock.calls[0][1]).toMatchObject({
      payment_terms: '',
      freight_terms: '',
    });
  });

  test('offers every priority the backend accepts', async () => {
    renderDetail();

    fireEvent.click(await screen.findByRole('button', { name: /edit details/i }));
    const picker = screen.getByLabelText('Priority');
    expect(within(picker).getAllByRole('option').map((o) => o.textContent)).toEqual([
      'Low',
      'Normal',
      'High',
      'Urgent',
    ]);
  });

  test('a signed-out viewer sees the terms but gets no editor', async () => {
    localStorage.clear();

    renderDetail();

    await waitFor(() => {
      expect(screen.getByText('Priority:')).toBeInTheDocument();
    });
    expect(screen.queryByRole('button', { name: /edit details/i })).not.toBeInTheDocument();
  });
});
