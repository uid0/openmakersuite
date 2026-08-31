/**
 * Tests for PurchaseOrderPage:
 *  - oms-aq2: editable metadata + file attachments behaviors.
 *  - oms-74q: freeform PO line items render their description, not 'Unknown Item'.
 *  - gh-453: mark-delivered patches the page from the response without
 *    flipping back into the initial "Loading purchase order…" placeholder.
 */
import { MantineProvider } from '@mantine/core';
import { act, render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import SessionExpiredBanner, {
  consumePendingReturnTo,
} from '../../components/SessionExpiredBanner';
import PurchaseOrderPage from '../../pages/PurchaseOrderPage';
import * as api from '../../services/api';
import { confirmAction, showError, showSuccess } from '../../utils/dialogs';

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

// `can_receive` is served by the API from its own RECEIVABLE_STATUSES rather
// than re-derived in the client, so fixtures have to carry it like any other
// server-computed field.
const baseOrder = {
  id: 'po-1',
  po_number: 'PO-2026-0001',
  supplier_details: 'Acme Supplies',
  status: 'sent',
  status_label: 'Sent',
  can_receive: true,
  is_settled: false,
  has_receipt_variance: false,
  outstanding_line_count: 0,
  variance_line_count: 0,
  order_date: '2026-04-01T00:00:00Z',
  expected_delivery_date: '2026-05-15',
  supplier_order_number: 'SUP-9',
  sales_order_number: 'SO-7',
  estimated_total: '100.00',
  voided_at: null,
  voided_by_username: null,
  void_reason: '',
  items: [],
  attachments: [
    {
      id: 1,
      file: '/media/purchase_orders/attachments/2026/04/sales-order.pdf',
      file_url: 'http://testserver/media/purchase_orders/attachments/2026/04/sales-order.pdf',
      file_name: 'sales-order.pdf',
      description: 'Sales order from supplier',
      uploaded_by: 1,
      uploaded_by_name: 'Jane',
      uploaded_at: '2026-04-27T00:00:00Z',
    },
  ],
};

describe('PurchaseOrderPage attachments + metadata', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('token', 'test-token');
    localStorage.setItem('is_staff', 'true');
  });

  test('renders metadata fields and existing attachment', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({
      data: baseOrder,
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText('PO-2026-0001', { exact: false })).toBeInTheDocument();
    });

    expect(screen.getByText('SUP-9')).toBeInTheDocument();
    expect(screen.getByText('SO-7')).toBeInTheDocument();
    expect(screen.getByText('sales-order.pdf')).toBeInTheDocument();
    expect(
      screen.getByText('— Sales order from supplier', { exact: false }),
    ).toBeInTheDocument();
  });

  test('uploading an attachment calls the API and reloads', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({
      data: { ...baseOrder, attachments: [] },
    });
    (api.purchaseOrderAPI.uploadAttachment as jest.Mock).mockResolvedValue({
      data: { id: 2 },
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/Upload Attachment/i)).toBeInTheDocument();
    });

    const fileInput = screen.getByLabelText(/^File$/i) as HTMLInputElement;
    const file = new File(['hello'], 'doc.pdf', { type: 'application/pdf' });
    fireEvent.change(fileInput, { target: { files: [file] } });

    fireEvent.change(screen.getByLabelText(/Description/i), {
      target: { value: 'Confirmation email' },
    });

    fireEvent.click(screen.getByRole('button', { name: /^Upload$/i }));

    await waitFor(() => {
      expect(api.purchaseOrderAPI.uploadAttachment).toHaveBeenCalledWith(
        'po-1',
        file,
        'Confirmation email',
      );
    });
  });
});

describe('PurchaseOrderPage mark-delivered reactive contract (gh-453)', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('token', 'test-token');
    localStorage.setItem('is_staff', 'true');
  });

  test('patches the page from the mark-delivered response without a follow-up GET', async () => {
    const sentOrder = {
      ...baseOrder,
      status: 'sent',
      status_label: 'Sent',
      items: [],
      attachments: [],
    };
    const deliveredOrder = {
      ...sentOrder,
      status: 'received',
      status_label: 'Received',
    };

    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: sentOrder });
    (api.purchaseOrderAPI.markDelivered as jest.Mock).mockResolvedValue({
      data: deliveredOrder,
    });

    renderPage();

    // Initial render: the "Mark as delivered" affordance is shown for sent POs.
    const openBtn = await screen.findByRole('button', { name: /^mark as delivered$/i });
    fireEvent.click(openBtn);

    fireEvent.change(screen.getByLabelText(/delivery date/i), {
      target: { value: '2026-05-10' },
    });

    fireEvent.click(screen.getByRole('button', { name: /confirm delivery/i }));

    await waitFor(() => {
      expect(api.purchaseOrderAPI.markDelivered).toHaveBeenCalledWith('po-1', {
        delivery_date: '2026-05-10',
        tracking_number: undefined,
        carrier: undefined,
      });
    });

    // Status flips to "Received" from the response — no follow-up GET. Match the
    // exact status label so we don't collide with the "Quantity Received" column
    // header (a loose /Received/ regex matches both and throws on multiple nodes).
    await waitFor(() => {
      expect(screen.getByText('Received')).toBeInTheDocument();
    });
    expect(api.purchaseOrderAPI.getOrder).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/loading purchase order/i)).not.toBeInTheDocument();
  });

  test('disables submit while pending and re-enables on failure with context preserved', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({
      data: { ...baseOrder, status: 'sent', items: [], attachments: [] },
    });

    let rejectMark: (err: any) => void = () => undefined;
    (api.purchaseOrderAPI.markDelivered as jest.Mock).mockImplementation(
      () =>
        new Promise((_resolve, reject) => {
          rejectMark = reject;
        }),
    );

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /^mark as delivered$/i }));

    const dateInput = screen.getByLabelText(/delivery date/i) as HTMLInputElement;
    fireEvent.change(dateInput, { target: { value: '2026-05-10' } });
    const trackingInput = screen.getByLabelText(/tracking number/i) as HTMLInputElement;
    fireEvent.change(trackingInput, { target: { value: 'TRK-99' } });

    const confirm = screen.getByRole('button', { name: /confirm delivery/i });
    fireEvent.click(confirm);

    // Submit button reflects pending state.
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /saving/i })).toBeDisabled();
    });
    expect(screen.queryByText(/loading purchase order/i)).not.toBeInTheDocument();

    // Reject — the panel stays mounted with the user's typed values intact
    // so they can retry without re-entering anything.
    rejectMark(new Error('boom'));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /confirm delivery/i })).not.toBeDisabled();
    });
    expect((screen.getByLabelText(/delivery date/i) as HTMLInputElement).value).toBe('2026-05-10');
    expect((screen.getByLabelText(/tracking number/i) as HTMLInputElement).value).toBe('TRK-99');
  });

  test('surfaces the error and keeps the panel when the mark-delivered save fails (#457 R3)', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({
      data: { ...baseOrder, status: 'sent', items: [], attachments: [] },
    });
    // No detail on the error → the page falls back to its generic save message.
    (api.purchaseOrderAPI.markDelivered as jest.Mock).mockRejectedValue({
      response: { status: 500, data: {} },
    });

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /^mark as delivered$/i }));
    fireEvent.change(screen.getByLabelText(/delivery date/i), { target: { value: '2026-05-10' } });
    fireEvent.click(screen.getByRole('button', { name: /confirm delivery/i }));

    await waitFor(() => {
      expect(showError).toHaveBeenCalledWith('Failed to mark purchase order as delivered');
    });
    // The panel stays mounted with the typed date so the user can retry.
    expect(screen.getByRole('button', { name: /confirm delivery/i })).toBeInTheDocument();
    expect((screen.getByLabelText(/delivery date/i) as HTMLInputElement).value).toBe('2026-05-10');
  });
});

describe('PurchaseOrderPage line item rendering', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('token', 'test-token');
    localStorage.setItem('is_staff', 'true');
  });

  test('renders freeform line item description, not "Unknown Item"', async () => {
    const order = {
      id: 'po-1',
      po_number: 'PO-2024-0001',
      supplier_details: 'Test Supplier',
      status: 'submitted',
      status_label: 'Submitted',
      order_date: '2026-04-27',
      expected_delivery_date: null,
      estimated_total: '52.50',
      voided_at: null,
      voided_by_username: null,
      void_reason: '',
      attachments: [],
      items: [
        {
          id: 'item-1',
          item_type: 'freeform',
          description: 'Custom widget XL',
          item_details: null,
          asset_details: null,
          quantity_ordered: 3,
          quantity_received: 0,
          unit_cost_ordered: '17.50',
          unit_cost_actual: null,
          estimated_cost: '52.50',
          actual_cost: null,
          expected_shipment_date: null,
          notes: '',
          is_voided: false,
          voided_at: null,
          void_reason: '',
        },
      ],
    };

    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: order });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Custom widget XL')).toBeInTheDocument();
    });
    expect(screen.queryByText('Unknown Item')).not.toBeInTheDocument();
  });

  test('renders inventory and asset items via their respective details', async () => {
    const order = {
      id: 'po-1',
      po_number: 'PO-2024-0002',
      supplier_details: 'Test Supplier',
      status: 'submitted',
      status_label: 'Submitted',
      order_date: '2026-04-27',
      expected_delivery_date: null,
      estimated_total: '0.00',
      voided_at: null,
      voided_by_username: null,
      void_reason: '',
      attachments: [],
      items: [
        {
          id: 'item-inv',
          item_type: 'inventory_item',
          description: null,
          item_details: { name: 'Stocked Bolt', sku: 'BOLT-1' },
          asset_details: null,
          quantity_ordered: 10,
          quantity_received: 0,
          unit_cost_ordered: '1.00',
          unit_cost_actual: null,
          estimated_cost: '10.00',
          actual_cost: null,
          expected_shipment_date: null,
          notes: '',
          is_voided: false,
          voided_at: null,
          void_reason: '',
        },
        {
          id: 'item-asset',
          item_type: 'asset',
          description: null,
          item_details: null,
          asset_details: {
            id: 'a-1',
            name: 'Forklift 7',
            asset_tag: 'FL-7',
            location_name: null,
          },
          quantity_ordered: 1,
          quantity_received: 0,
          unit_cost_ordered: '5000.00',
          unit_cost_actual: null,
          estimated_cost: '5000.00',
          actual_cost: null,
          expected_shipment_date: null,
          notes: '',
          is_voided: false,
          voided_at: null,
          void_reason: '',
        },
      ],
    };

    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: order });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Stocked Bolt')).toBeInTheDocument();
    });
    expect(screen.getByText('Forklift 7')).toBeInTheDocument();
    expect(screen.queryByText('Unknown Item')).not.toBeInTheDocument();
    expect(screen.queryByText('Unknown Asset')).not.toBeInTheDocument();
  });
});

describe('PurchaseOrderPage line target-type label (op-49th)', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('token', 'test-token');
    localStorage.setItem('is_staff', 'true');
  });

  test('labels every line with its target type, falling back to — for an unknown type', async () => {
    const line = (id: string, item_type: string | null) => ({
      id,
      item_type,
      description: 'Custom Panel',
      item_details: { id: 'i1', name: `Item ${id}`, sku: `SKU-${id}` },
      asset_details: { id: 'a1', name: `Asset ${id}`, asset_tag: `AT-${id}`, location_name: null },
      quantity_ordered: 1,
      quantity_received: 0,
      quantity_pending: 1,
      is_fully_received: false,
      unit_cost_ordered: '1.00',
      unit_cost_actual: null,
      estimated_cost: '1.00',
      actual_cost: null,
      expected_shipment_date: null,
      notes: '',
      is_voided: false,
      voided_at: null,
      void_reason: '',
    });

    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({
      data: {
        ...baseOrder,
        attachments: [],
        items: [
          line('inv', 'inventory_item'),
          line('ast', 'asset'),
          line('free', 'freeform'),
          line('none', null),
        ],
      },
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getAllByTestId('line-item-type')).toHaveLength(4);
    });
    expect(
      screen.getAllByTestId('line-item-type').map((node) => node.textContent),
    ).toEqual(['Inventory item', 'Asset', 'Freeform', '—']);
  });
});

describe('PurchaseOrderPage line-items running total (op-r1sg)', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('token', 'test-token');
    localStorage.setItem('is_staff', 'true');
  });

  const costLine = (
    id: string,
    overrides: Partial<{ estimated_cost: string; actual_cost: string | null; is_voided: boolean }>,
  ) => ({
    id,
    item_type: 'inventory_item',
    description: null,
    item_details: { id: `i-${id}`, name: `Item ${id}`, sku: `SKU-${id}` },
    asset_details: null,
    quantity_ordered: 1,
    quantity_received: 0,
    quantity_pending: 1,
    is_fully_received: false,
    unit_cost_ordered: '1.00',
    unit_cost_actual: null,
    estimated_cost: '0.00',
    actual_cost: null,
    expected_shipment_date: null,
    notes: '',
    is_voided: false,
    voided_at: null,
    void_reason: '',
    ...overrides,
  });

  test('sums the active lines and excludes voided ones', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({
      data: {
        ...baseOrder,
        attachments: [],
        items: [
          costLine('a', { estimated_cost: '10.00' }),
          costLine('b', { estimated_cost: '48.00' }),
          costLine('c', { estimated_cost: '12.00', is_voided: true }),
        ],
      },
    });

    renderPage();

    const total = await screen.findByTestId('po-items-total');
    expect(total).toHaveTextContent('$58.00');
    // Nothing to reconcile while every line is still at its ordered cost.
    expect(total).not.toHaveTextContent(/estimated/i);
  });

  test('follows the displayed line costs and spells out the estimated sum when they diverge', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({
      data: {
        ...baseOrder,
        attachments: [],
        items: [
          // Received at a higher unit cost than ordered — the Line Cost column
          // shows the actual, so the footer must too.
          costLine('a', { estimated_cost: '10.00', actual_cost: '12.00' }),
          costLine('b', { estimated_cost: '48.00' }),
        ],
      },
    });

    renderPage();

    const total = await screen.findByTestId('po-items-total');
    expect(total).toHaveTextContent('$60.00');
    expect(total).toHaveTextContent('(estimated: $58.00)');
  });

  test('omits the total row entirely when the order has no lines', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({
      data: { ...baseOrder, attachments: [], items: [] },
    });

    renderPage();

    await screen.findByText('No line items found');
    expect(screen.queryByTestId('po-items-total')).not.toBeInTheDocument();
  });
});

describe('PurchaseOrderPage receive items (oms-s0hj4)', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('token', 'test-token');
    localStorage.setItem('is_staff', 'true');
  });

  // A partially-received PO mixing receivable, fully-received and voided lines.
  const makeReceiveOrder = () => ({
    id: 'po-1',
    po_number: 'PO-2026-0042',
    supplier_details: 'Acme Supplies',
    status: 'partially_received',
    status_label: 'Partially Received',
    can_receive: true,
    is_settled: false,
    has_receipt_variance: false,
    outstanding_line_count: 1,
    variance_line_count: 0,
    order_date: '2026-04-01T00:00:00Z',
    expected_delivery_date: '2026-05-15',
    supplier_order_number: '',
    sales_order_number: '',
    estimated_total: '100.00',
    voided_at: null,
    voided_by_username: null,
    void_reason: '',
    attachments: [],
    items: [
      {
        id: 101,
        item_type: 'inventory_item',
        description: null,
        item_details: { name: 'Stocked Bolt', sku: 'BOLT-1' },
        asset_details: null,
        quantity_ordered: 10,
        quantity_received: 3,
        quantity_pending: 7,
        is_fully_received: false,
        unit_cost_ordered: '1.00',
        unit_cost_actual: null,
        estimated_cost: '10.00',
        actual_cost: null,
        expected_shipment_date: null,
        notes: '',
        is_voided: false,
        voided_at: null,
        void_reason: '',
      },
      {
        id: 102,
        item_type: 'inventory_item',
        description: null,
        item_details: { name: 'Done Widget', sku: 'DONE-1' },
        asset_details: null,
        quantity_ordered: 5,
        quantity_received: 5,
        quantity_pending: 0,
        is_fully_received: true,
        is_settled: true,
        unit_cost_ordered: '2.00',
        unit_cost_actual: null,
        estimated_cost: '10.00',
        actual_cost: null,
        expected_shipment_date: null,
        notes: '',
        is_voided: false,
        voided_at: null,
        void_reason: '',
      },
      {
        id: 103,
        item_type: 'inventory_item',
        description: null,
        item_details: { name: 'Voided Thing', sku: 'VOID-1' },
        asset_details: null,
        quantity_ordered: 4,
        quantity_received: 0,
        quantity_pending: 4,
        is_fully_received: false,
        unit_cost_ordered: '3.00',
        unit_cost_actual: null,
        estimated_cost: '12.00',
        actual_cost: null,
        expected_shipment_date: null,
        notes: '',
        is_voided: true,
        voided_at: '2026-04-10T00:00:00Z',
        void_reason: 'discontinued',
      },
      {
        id: 104,
        item_type: 'freeform',
        description: 'Custom Panel',
        item_details: null,
        asset_details: null,
        quantity_ordered: 6,
        quantity_received: 0,
        quantity_pending: 6,
        is_fully_received: false,
        unit_cost_ordered: '8.00',
        unit_cost_actual: null,
        estimated_cost: '48.00',
        actual_cost: null,
        expected_shipment_date: null,
        notes: '',
        is_voided: false,
        voided_at: null,
        void_reason: '',
      },
    ],
  });

  test('opens a panel with qty inputs only for non-voided, not-fully-received items, defaulted to pending', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: makeReceiveOrder() });

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /^receive items$/i }));

    // Receivable lines get an input pre-filled with their pending quantity.
    expect((screen.getByLabelText('Receive quantity for Stocked Bolt') as HTMLInputElement).value).toBe('7');
    expect((screen.getByLabelText('Receive quantity for Custom Panel') as HTMLInputElement).value).toBe('6');

    // Fully-received and voided lines are excluded from the receive panel.
    expect(screen.queryByLabelText('Receive quantity for Done Widget')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Receive quantity for Voided Thing')).not.toBeInTheDocument();
  });

  test('submits entered quantities to receiveItems and patches the page from the response', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: makeReceiveOrder() });
    const receivedOrder = {
      ...makeReceiveOrder(),
      status: 'received',
      status_label: 'Received',
    };
    (api.purchaseOrderAPI.receiveItems as jest.Mock).mockResolvedValue({ data: receivedOrder });

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /^receive items$/i }));

    // Override the default delivery date so the assertion is deterministic.
    fireEvent.change(screen.getByLabelText(/delivery date/i), { target: { value: '2026-06-10' } });
    // Receive a partial quantity for the bolt; leave the panel at its default for the freeform line.
    fireEvent.change(screen.getByLabelText('Receive quantity for Stocked Bolt'), {
      target: { value: '2' },
    });

    fireEvent.click(screen.getByRole('button', { name: /confirm receipt/i }));

    await waitFor(() => {
      expect(api.purchaseOrderAPI.receiveItems).toHaveBeenCalledWith('po-1', {
        items: [
          { purchase_order_item: 101, quantity_received: 2 },
          { purchase_order_item: 104, quantity_received: 6 },
        ],
        delivery_date: '2026-06-10',
        receipt_notes: undefined,
      });
    });

    // The page is patched from the response (status flips, panel closes) — no follow-up GET.
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /confirm receipt/i })).not.toBeInTheDocument();
    });
    expect(screen.getByText('Received')).toBeInTheDocument();
    expect(api.purchaseOrderAPI.getOrder).toHaveBeenCalledTimes(1);
    // The patch never flips the page back into the initial loading placeholder.
    expect(screen.queryByText(/loading purchase order/i)).not.toBeInTheDocument();
  });

  test('disables Confirm Receipt while in-flight and ignores duplicate submits', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: makeReceiveOrder() });
    const receivedOrder = {
      ...makeReceiveOrder(),
      status: 'received',
      status_label: 'Received',
    };

    // Hold the receive request in-flight so we can observe the pending state.
    let resolveReceive: (value: any) => void = () => undefined;
    (api.purchaseOrderAPI.receiveItems as jest.Mock).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveReceive = resolve;
        }),
    );

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /^receive items$/i }));
    fireEvent.change(screen.getByLabelText('Receive quantity for Stocked Bolt'), {
      target: { value: '2' },
    });

    fireEvent.click(screen.getByRole('button', { name: /confirm receipt/i }));

    // While the request is pending the submit button reflects the saving state
    // and is disabled — and the page does not fall back to the loading placeholder.
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /saving/i })).toBeDisabled();
    });
    expect(screen.queryByText(/loading purchase order/i)).not.toBeInTheDocument();

    // A second click while pending must not dispatch another receiveItems call.
    fireEvent.click(screen.getByRole('button', { name: /saving/i }));
    expect(api.purchaseOrderAPI.receiveItems).toHaveBeenCalledTimes(1);

    // Let the in-flight request settle so the success path patches and closes.
    resolveReceive({ data: receivedOrder });
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /confirm receipt/i })).not.toBeInTheDocument();
    });
  });

  test('keeps the panel and entered quantities on failure, re-enabling Confirm Receipt', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: makeReceiveOrder() });

    let rejectReceive: (err: any) => void = () => undefined;
    (api.purchaseOrderAPI.receiveItems as jest.Mock).mockImplementation(
      () =>
        new Promise((_resolve, reject) => {
          rejectReceive = reject;
        }),
    );

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /^receive items$/i }));
    fireEvent.change(screen.getByLabelText('Receive quantity for Stocked Bolt'), {
      target: { value: '2' },
    });

    fireEvent.click(screen.getByRole('button', { name: /confirm receipt/i }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /saving/i })).toBeDisabled();
    });

    // Reject — the panel stays mounted with the entered quantity intact so the
    // user can retry without re-typing, and Confirm Receipt re-enables.
    rejectReceive(new Error('boom'));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /confirm receipt/i })).not.toBeDisabled();
    });
    expect(
      (screen.getByLabelText('Receive quantity for Stocked Bolt') as HTMLInputElement).value,
    ).toBe('2');
    expect(screen.queryByText(/loading purchase order/i)).not.toBeInTheDocument();
  });

  test('errors and does not call the API when no quantities are entered', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: makeReceiveOrder() });

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /^receive items$/i }));

    // Zero out both receivable lines.
    fireEvent.change(screen.getByLabelText('Receive quantity for Stocked Bolt'), {
      target: { value: '0' },
    });
    fireEvent.change(screen.getByLabelText('Receive quantity for Custom Panel'), {
      target: { value: '0' },
    });

    fireEvent.click(screen.getByRole('button', { name: /confirm receipt/i }));

    await waitFor(() => {
      expect(showError).toHaveBeenCalledWith(expect.stringMatching(/at least one item/i));
    });
    expect(api.purchaseOrderAPI.receiveItems).not.toHaveBeenCalled();
  });

  test('records an over-receive after saying so, rather than refusing it', async () => {
    // What the captain asked for: the record says what actually turned up, and
    // the difference is flagged. Refusing the receipt would lose real stock and
    // hide a vendor's mistake; rounding it down silently would be worse.
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: makeReceiveOrder() });
    (api.purchaseOrderAPI.receiveItems as jest.Mock).mockResolvedValue({
      data: makeReceiveOrder(),
    });

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /^receive items$/i }));

    // Bolt only has 7 outstanding; 9 turned up.
    fireEvent.change(screen.getByLabelText('Receive quantity for Stocked Bolt'), {
      target: { value: '9' },
    });

    // The over-receipt is announced inline, before anything is sent.
    expect(await screen.findByTestId('receive-over-note-101')).toHaveTextContent(/\+2 over/i);

    fireEvent.click(screen.getByRole('button', { name: /confirm receipt/i }));

    await waitFor(() => {
      expect(api.purchaseOrderAPI.receiveItems).toHaveBeenCalled();
    });
    // Confirmed out loud first — never silently.
    expect(confirmAction).toHaveBeenCalledWith(
      expect.any(String),
      expect.stringMatching(/over-receipt/i),
      expect.any(Function),
      expect.anything(),
    );
    const [, body] = (api.purchaseOrderAPI.receiveItems as jest.Mock).mock.calls[0];
    const bolt = body.items.find((line: any) => line.purchase_order_item === 101);
    // Sent as entered — not clamped to the 7 that were outstanding.
    expect(bolt.quantity_received).toBe(9);
  });

  test('an over-receipt the operator declines is not sent at all', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: makeReceiveOrder() });
    // The operator backs out of the confirm rather than accepting it.
    (confirmAction as jest.Mock).mockImplementationOnce(() => {});

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /^receive items$/i }));
    fireEvent.change(screen.getByLabelText('Receive quantity for Stocked Bolt'), {
      target: { value: '99' },
    });
    fireEvent.click(screen.getByRole('button', { name: /confirm receipt/i }));

    await waitFor(() => {
      expect(confirmAction).toHaveBeenCalled();
    });
    expect(api.purchaseOrderAPI.receiveItems).not.toHaveBeenCalled();
    // The typed quantity survives so the operator can correct it.
    expect(screen.getByLabelText('Receive quantity for Stocked Bolt')).toHaveValue(99);
  });

  test('surfaces a permission error and keeps the panel when receiving is forbidden (403) (#457 R3)', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: makeReceiveOrder() });
    // The receive permission is enforced by the backend (403); the page has no
    // client-side gate, so it must surface the message and let the user recover.
    (api.purchaseOrderAPI.receiveItems as jest.Mock).mockRejectedValue({
      response: {
        status: 403,
        data: { detail: 'You do not have permission to receive items.' },
      },
    });

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /^receive items$/i }));
    fireEvent.change(screen.getByLabelText('Receive quantity for Stocked Bolt'), {
      target: { value: '2' },
    });
    fireEvent.click(screen.getByRole('button', { name: /confirm receipt/i }));

    await waitFor(() => {
      expect(showError).toHaveBeenCalledWith('You do not have permission to receive items.');
    });
    // The panel stays open with the entered quantity so a permitted user can retry.
    expect(
      (screen.getByLabelText('Receive quantity for Stocked Bolt') as HTMLInputElement).value,
    ).toBe('2');
    expect(screen.getByRole('button', { name: /confirm receipt/i })).toBeInTheDocument();
  });
});

describe('PurchaseOrderPage session-expiry return-to (#457 R3)', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('token', 'test-token');
    localStorage.setItem('is_staff', 'true');
    sessionStorage.clear();
  });

  // Mounts the global re-login banner (normally in WorkspaceLayout) next to the page.
  const renderWithBanner = () =>
    render(
      <MantineProvider>
        <MemoryRouter initialEntries={['/purchase-orders/po-1']}>
          <SessionExpiredBanner />
          <Routes>
            <Route path="/purchase-orders/:orderId" element={<PurchaseOrderPage />} />
          </Routes>
        </MemoryRouter>
      </MantineProvider>,
    );

  test('shows the re-login banner with a return path and keeps the order on screen', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: baseOrder });

    renderWithBanner();
    await waitFor(() => {
      expect(screen.getByText('PO-2026-0001', { exact: false })).toBeInTheDocument();
    });

    // The API client raises this on a 401; in production the detail page lives
    // under the recoverable /purchasing/orders/:id route.
    act(() => {
      window.dispatchEvent(
        new CustomEvent('oms:session-expired', {
          detail: { pathname: '/purchasing/orders/po-1' },
        }),
      );
    });

    expect(screen.getByTestId('session-expired-banner')).toBeInTheDocument();
    expect(screen.getByText(/where you left off/i)).toBeInTheDocument();
    expect(consumePendingReturnTo()).toBe('/purchasing/orders/po-1');
    // The page itself is untouched — the order is still rendered behind the banner.
    expect(screen.getByText('PO-2026-0001', { exact: false })).toBeInTheDocument();
  });
});

describe('PurchaseOrderPage receive-with-serial (op-y45)', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('token', 'test-token');
    localStorage.setItem('is_staff', 'true');
  });

  // A PO with one serialized inventory line (2 pending) plus a plain line.
  const makeSerialOrder = () => ({
    id: 'po-1',
    po_number: 'PO-2026-0099',
    supplier_details: 'Acme Supplies',
    status: 'sent',
    status_label: 'Sent',
    can_receive: true,
    is_settled: false,
    has_receipt_variance: false,
    outstanding_line_count: 1,
    variance_line_count: 0,
    order_date: '2026-04-01T00:00:00Z',
    expected_delivery_date: '2026-05-15',
    supplier_order_number: '',
    sales_order_number: '',
    estimated_total: '50.00',
    voided_at: null,
    voided_by_username: null,
    void_reason: '',
    attachments: [],
    items: [
      {
        id: 201,
        item_type: 'inventory_item',
        description: null,
        item_details: {
          id: 'inv-1',
          name: 'Serial Blade',
          sku: 'SB-1',
          is_serialized: true,
          serial_tracking_mode: 'consumable',
        },
        asset_details: null,
        quantity_ordered: 2,
        quantity_received: 0,
        quantity_pending: 2,
        is_fully_received: false,
        is_settled: false,
        has_receipt_variance: false,
        quantity_variance: -2,
        receipt_state: 'not_received',
        // The identities a receipt on this line may serialize, served by the
        // API. Read INSTEAD of `item_details.is_serialized`, which on a kit
        // line describes a SKU that is never stocked.
        serial_targets: [
          {
            item: 'inv-1',
            item_name: 'Serial Blade',
            item_sku: 'SB-1',
            serial_tracking_mode: 'consumable',
            quantity: 2,
          },
        ],
        serials_recorded: 0,
        unit_cost_ordered: '5.00',
        unit_cost_actual: null,
        estimated_cost: '10.00',
        actual_cost: null,
        expected_shipment_date: null,
        notes: '',
        is_voided: false,
        voided_at: null,
        void_reason: '',
      },
    ],
  });

  test('sends serials inside the receipt itself, one call, with lot and expiry', async () => {
    // Serials used to be created by N separate follow-up calls AFTER the
    // receipt had already committed, so a failure there left stock credited
    // and the operator's serials lost. They now ride inside the receipt's own
    // transaction.
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: makeSerialOrder() });
    (api.purchaseOrderAPI.receiveItems as jest.Mock).mockResolvedValue({
      data: { ...makeSerialOrder(), status: 'received', status_label: 'Received' },
    });

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /^receive items$/i }));

    fireEvent.change(screen.getByLabelText('Serial numbers for Serial Blade'), {
      target: { value: 'SN-1\nSN-2' },
    });
    fireEvent.change(screen.getByLabelText(/^Lot \(optional\)$/i), {
      target: { value: 'LOT-42' },
    });
    fireEvent.change(screen.getByLabelText(/^Expiry \(optional\)$/i), {
      target: { value: '2027-01-31' },
    });

    fireEvent.click(screen.getByRole('button', { name: /confirm receipt/i }));

    await waitFor(() => {
      expect(api.purchaseOrderAPI.receiveItems).toHaveBeenCalled();
    });
    const [, body] = (api.purchaseOrderAPI.receiveItems as jest.Mock).mock.calls[0];
    expect(body.items).toEqual([
      {
        purchase_order_item: 201,
        quantity_received: 2,
        serials: [
          {
            serial_number: 'SN-1',
            item: 'inv-1',
            lot: 'LOT-42',
            expiration_date: '2027-01-31',
          },
          {
            serial_number: 'SN-2',
            item: 'inv-1',
            lot: 'LOT-42',
            expiration_date: '2027-01-31',
          },
        ],
      },
    ]);
    // No separate follow-up writes — that path is gone.
    expect(api.serializedComponentsAPI.create).not.toHaveBeenCalled();
  });

  test('accepts fewer serials than units and says the rest are outstanding', async () => {
    // Refusing the receipt because only one of two labels has been scanned
    // would leave the operator holding stock they cannot enter. The gap is
    // named instead, then confirmed.
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: makeSerialOrder() });
    (api.purchaseOrderAPI.receiveItems as jest.Mock).mockResolvedValue({
      data: makeSerialOrder(),
    });

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /^receive items$/i }));
    fireEvent.change(screen.getByLabelText('Serial numbers for Serial Blade'), {
      target: { value: 'SN-1' },
    });

    fireEvent.click(screen.getByRole('button', { name: /confirm receipt/i }));

    await waitFor(() => {
      expect(api.purchaseOrderAPI.receiveItems).toHaveBeenCalled();
    });
    expect(confirmAction).toHaveBeenCalledWith(
      expect.any(String),
      expect.stringMatching(/1 of 2 serials captured/i),
      expect.any(Function),
      expect.anything(),
    );
    const [, body] = (api.purchaseOrderAPI.receiveItems as jest.Mock).mock.calls[0];
    expect(body.items[0].serials).toHaveLength(1);
  });

  test('refuses more serials than the receipt credits, dropping none of them', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: makeSerialOrder() });

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /^receive items$/i }));
    // Three labels for a quantity of two.
    fireEvent.change(screen.getByLabelText('Serial numbers for Serial Blade'), {
      target: { value: 'SN-1\nSN-2\nSN-3' },
    });

    fireEvent.click(screen.getByRole('button', { name: /confirm receipt/i }));

    await waitFor(() => {
      expect(showError).toHaveBeenCalledWith(
        expect.stringContaining('Enter at most 2 serial numbers for Serial Blade'),
      );
    });
    // Nothing sent, so nothing to silently truncate.
    expect(api.purchaseOrderAPI.receiveItems).not.toHaveBeenCalled();
  });
});

describe('PurchaseOrderPage send-to-supplier + confirm (op-alh)', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('token', 'test-token');
    localStorage.setItem('is_staff', 'true');
  });

  const makeOrder = (status: string, status_label: string) => ({
    ...baseOrder,
    status,
    status_label,
    // Mirrors what the API computes for this status.
    can_receive: ['sent', 'confirmed', 'partially_received'].includes(status),
    items: [],
    attachments: [],
  });

  test('a draft PO shows only the Send to Supplier lifecycle action', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({
      data: makeOrder('draft', 'Draft'),
    });

    renderPage();

    expect(
      await screen.findByRole('button', { name: /^send to supplier$/i }),
    ).toBeInTheDocument();
    // Confirm and the receive/mark affordances are gated out on a draft PO.
    expect(screen.queryByRole('button', { name: /^confirm$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^receive items$/i })).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /^mark as delivered$/i }),
    ).not.toBeInTheDocument();
  });

  test('a sent PO shows Confirm alongside receive/mark, but not Send', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({
      data: makeOrder('sent', 'Sent'),
    });

    renderPage();

    expect(await screen.findByRole('button', { name: /^confirm$/i })).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /^send to supplier$/i }),
    ).not.toBeInTheDocument();
    // The existing receive/mark affordances remain available on a sent PO.
    expect(screen.getByRole('button', { name: /^receive items$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^mark as delivered$/i })).toBeInTheDocument();
  });

  test('a received PO shows neither Send nor Confirm', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({
      data: makeOrder('received', 'Received'),
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText('PO-2026-0001', { exact: false })).toBeInTheDocument();
    });
    expect(
      screen.queryByRole('button', { name: /^send to supplier$/i }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^confirm$/i })).not.toBeInTheDocument();
  });

  test('clicking Send to Supplier calls the API and reloads; Confirm then appears', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock)
      .mockResolvedValueOnce({ data: makeOrder('draft', 'Draft') })
      .mockResolvedValueOnce({ data: makeOrder('sent', 'Sent') });
    (api.purchaseOrderAPI.sendToSupplier as jest.Mock).mockResolvedValue({
      data: makeOrder('sent', 'Sent'),
    });

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /^send to supplier$/i }));

    await waitFor(() => {
      expect(api.purchaseOrderAPI.sendToSupplier).toHaveBeenCalledWith('po-1');
    });
    // Refetch flips the PO to sent, so the Confirm action replaces Send.
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /^confirm$/i })).toBeInTheDocument();
    });
    expect(screen.queryByRole('button', { name: /^send to supplier$/i })).not.toBeInTheDocument();
    expect(api.purchaseOrderAPI.getOrder).toHaveBeenCalledTimes(2);
    expect(showSuccess).toHaveBeenCalledWith('Purchase order sent to supplier');
  });

  test('clicking Confirm calls the API with no body and reloads', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock)
      .mockResolvedValueOnce({ data: makeOrder('sent', 'Sent') })
      .mockResolvedValueOnce({ data: makeOrder('confirmed', 'Confirmed') });
    (api.purchaseOrderAPI.confirmOrder as jest.Mock).mockResolvedValue({
      data: makeOrder('confirmed', 'Confirmed'),
    });

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /^confirm$/i }));

    await waitFor(() => {
      expect(api.purchaseOrderAPI.confirmOrder).toHaveBeenCalledWith('po-1');
    });
    // Confirmed POs drop the Confirm action (they can still be received).
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /^confirm$/i })).not.toBeInTheDocument();
    });
    expect(api.purchaseOrderAPI.getOrder).toHaveBeenCalledTimes(2);
    expect(showSuccess).toHaveBeenCalledWith('Purchase order confirmed');
  });

  test('disables Send while in flight and ignores duplicate clicks', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({
      data: makeOrder('draft', 'Draft'),
    });
    let resolveSend: (value: any) => void = () => undefined;
    (api.purchaseOrderAPI.sendToSupplier as jest.Mock).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveSend = resolve;
        }),
    );

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /^send to supplier$/i }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /^send to supplier$/i })).toBeDisabled();
    });
    // A second click while pending must not dispatch another transition call.
    fireEvent.click(screen.getByRole('button', { name: /^send to supplier$/i }));
    expect(api.purchaseOrderAPI.sendToSupplier).toHaveBeenCalledTimes(1);

    resolveSend({ data: makeOrder('sent', 'Sent') });
  });

  test('surfaces the backend error when Send fails and keeps the action enabled', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({
      data: makeOrder('draft', 'Draft'),
    });
    (api.purchaseOrderAPI.sendToSupplier as jest.Mock).mockRejectedValue({
      response: { status: 400, data: { error: 'Only draft orders can be sent to suppliers' } },
    });

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /^send to supplier$/i }));

    await waitFor(() => {
      expect(showError).toHaveBeenCalledWith('Only draft orders can be sent to suppliers');
    });
    // A failed transition does not reload the PO; the action re-enables for retry.
    expect(api.purchaseOrderAPI.getOrder).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: /^send to supplier$/i })).not.toBeDisabled();
  });

  test('surfaces the backend error when Confirm fails and keeps the action enabled', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({
      data: makeOrder('sent', 'Sent'),
    });
    (api.purchaseOrderAPI.confirmOrder as jest.Mock).mockRejectedValue({
      response: { status: 400, data: { error: 'Only sent orders can be confirmed' } },
    });

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /^confirm$/i }));

    await waitFor(() => {
      expect(showError).toHaveBeenCalledWith('Only sent orders can be confirmed');
    });
    expect(api.purchaseOrderAPI.getOrder).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: /^confirm$/i })).not.toBeDisabled();
  });
});

describe('PurchaseOrderPage order-pad export (op-ls52)', () => {
  const orderWithLines = {
    ...baseOrder,
    status: 'sent',
    status_label: 'Sent',
    attachments: [],
    items: [
      {
        id: 'line-1',
        item_type: 'inventory_item',
        description: null,
        item_details: { id: 'i1', name: 'Widget', sku: 'SKU-1' },
        asset_details: null,
        quantity_ordered: 2,
        quantity_received: 0,
        quantity_pending: 2,
        is_fully_received: false,
        unit_cost_ordered: '1.00',
        unit_cost_actual: null,
        estimated_cost: '2.00',
        actual_cost: null,
        expected_shipment_date: null,
        notes: '',
        is_voided: false,
        voided_at: null,
        void_reason: '',
      },
    ],
  };

  const exportPayload = {
    adapter: 'generic_csv',
    csv: 'part#,qty\nW-1,2\n',
    text: 'W-1\t2',
    filename: 'PO-2026-0001-order.csv',
    supplier: 'Acme Supplies',
    line_count: 1,
    missing_sku: [] as string[],
    invalid_sku: [] as string[],
  };

  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('token', 'test-token');
    localStorage.setItem('is_staff', 'true');
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: orderWithLines });
  });

  test('Download order pad calls the API and triggers a CSV file download', async () => {
    (api.purchaseOrderAPI.exportOrder as jest.Mock).mockResolvedValue({ data: exportPayload });

    const createObjectURL = vi.fn(() => 'blob:mock');
    const revokeObjectURL = vi.fn();
    Object.defineProperty(window.URL, 'createObjectURL', {
      value: createObjectURL,
      configurable: true,
    });
    Object.defineProperty(window.URL, 'revokeObjectURL', {
      value: revokeObjectURL,
      configurable: true,
    });
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => undefined);

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /download order pad/i }));

    await waitFor(() => {
      expect(api.purchaseOrderAPI.exportOrder).toHaveBeenCalledWith('po-1');
    });
    // A Blob is created from the CSV and the anchor is clicked to save it.
    await waitFor(() => {
      expect(clickSpy).toHaveBeenCalled();
    });
    expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob));

    clickSpy.mockRestore();
  });

  test('Copy order pad calls the API and writes the copy block to the clipboard', async () => {
    (api.purchaseOrderAPI.exportOrder as jest.Mock).mockResolvedValue({ data: exportPayload });
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    });

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /copy order pad/i }));

    await waitFor(() => {
      expect(api.purchaseOrderAPI.exportOrder).toHaveBeenCalledWith('po-1');
    });
    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith('W-1\t2');
    });
    expect(showSuccess).toHaveBeenCalledWith('Order pad copied to clipboard');
  });

  test('warns when some lines have no supplier part number', async () => {
    (api.purchaseOrderAPI.exportOrder as jest.Mock).mockResolvedValue({
      data: {
        ...exportPayload,
        csv: 'part#,qty\n',
        text: '',
        line_count: 0,
        missing_sku: ['Widget', 'Gadget'],
      },
    });
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    });

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /copy order pad/i }));

    const warning = await screen.findByTestId('order-pad-missing-sku-warning');
    expect(warning).toHaveTextContent(/2 lines have no supplier part number/i);
  });

  test('hides the order-pad buttons for anonymous (logged-out) viewers', async () => {
    localStorage.clear(); // no token → the page treats the viewer as anonymous

    renderPage();

    // Page has loaded once the PO number is on screen.
    await screen.findByText('PO-2026-0001', { exact: false });
    expect(
      screen.queryByRole('button', { name: /download order pad/i }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /copy order pad/i })).not.toBeInTheDocument();
  });
});

describe('PurchaseOrderPage adapter-aware export (op-svpq)', () => {
  const lineItem = {
    id: 'line-1',
    item_type: 'inventory_item',
    description: null,
    item_details: { id: 'i1', name: 'Widget', sku: 'SKU-1' },
    asset_details: null,
    quantity_ordered: 2,
    quantity_received: 0,
    quantity_pending: 2,
    is_fully_received: false,
    unit_cost_ordered: '1.00',
    unit_cost_actual: null,
    estimated_cost: '2.00',
    actual_cost: null,
    expected_shipment_date: null,
    notes: '',
    is_voided: false,
    voided_at: null,
    void_reason: '',
  };

  const amazonOrder = {
    ...baseOrder,
    supplier_ordering_adapter: 'amazon',
    attachments: [],
    items: [lineItem],
  };

  const hdsupplyOrder = {
    ...baseOrder,
    supplier_ordering_adapter: 'hdsupply',
    attachments: [],
    items: [lineItem],
  };

  const cartUrl1 =
    'https://www.amazon.com/gp/aws/cart/add.html?ASIN.1=B07X1234YZ&Quantity.1=2';
  const cartUrl2 =
    'https://www.amazon.com/gp/aws/cart/add.html?ASIN.1=B08ABCDE12&Quantity.1=1';

  const amazonPayload = {
    adapter: 'amazon',
    supplier: 'Acme Supplies',
    line_count: 1,
    missing_sku: [] as string[],
    invalid_sku: [] as string[],
    cart_urls: [cartUrl1],
  };

  const hdsupplyPayload = {
    adapter: 'hdsupply',
    csv: 'Part Number,Quantity\n12345,2\n',
    text: '12345\t2',
    filename: 'PO-2026-0001-order.csv',
    supplier: 'Acme Supplies',
    line_count: 1,
    missing_sku: [] as string[],
    invalid_sku: [] as string[],
  };

  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('token', 'test-token');
    localStorage.setItem('is_staff', 'true');
  });

  test('amazon supplier shows an "Open Amazon cart" button and opens the cart URL in a new tab', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: amazonOrder });
    (api.purchaseOrderAPI.exportOrder as jest.Mock).mockResolvedValue({ data: amazonPayload });
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);

    renderPage();

    // The Amazon adapter replaces the CSV download/copy affordances with a cart link.
    const cartButton = await screen.findByRole('button', { name: /open amazon cart/i });
    expect(
      screen.queryByRole('button', { name: /download order pad/i }),
    ).not.toBeInTheDocument();

    fireEvent.click(cartButton);

    await waitFor(() => {
      expect(api.purchaseOrderAPI.exportOrder).toHaveBeenCalledWith('po-1');
    });
    await waitFor(() => {
      expect(openSpy).toHaveBeenCalledWith(cartUrl1, '_blank', 'noopener,noreferrer');
    });

    openSpy.mockRestore();
  });

  test('amazon multi-chunk PO renders an "Open cart i/N" button per cart and opens each', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: amazonOrder });
    (api.purchaseOrderAPI.exportOrder as jest.Mock).mockResolvedValue({
      data: { ...amazonPayload, cart_urls: [cartUrl1, cartUrl2] },
    });
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /open amazon cart/i }));

    // First cart opens immediately; the per-chunk panel then lets the operator open the rest.
    await waitFor(() => {
      expect(openSpy).toHaveBeenCalledWith(cartUrl1, '_blank', 'noopener,noreferrer');
    });
    const secondCart = await screen.findByRole('button', { name: /open cart 2\/2/i });
    fireEvent.click(secondCart);
    expect(openSpy).toHaveBeenCalledWith(cartUrl2, '_blank', 'noopener,noreferrer');

    openSpy.mockRestore();
  });

  test('amazon adapter warns about lines with an invalid ASIN', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: amazonOrder });
    (api.purchaseOrderAPI.exportOrder as jest.Mock).mockResolvedValue({
      data: {
        ...amazonPayload,
        cart_urls: [] as string[],
        line_count: 0,
        invalid_sku: ['Widget', 'Gadget'],
      },
    });
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /open amazon cart/i }));

    const warning = await screen.findByTestId('order-pad-invalid-sku-warning');
    expect(warning).toHaveTextContent(/2 items have an invalid ASIN/i);

    openSpy.mockRestore();
  });

  test('hdsupply supplier shows a "Download for HD Supply" button that saves the CSV', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: hdsupplyOrder });
    (api.purchaseOrderAPI.exportOrder as jest.Mock).mockResolvedValue({ data: hdsupplyPayload });

    const createObjectURL = vi.fn(() => 'blob:mock');
    const revokeObjectURL = vi.fn();
    Object.defineProperty(window.URL, 'createObjectURL', {
      value: createObjectURL,
      configurable: true,
    });
    Object.defineProperty(window.URL, 'revokeObjectURL', {
      value: revokeObjectURL,
      configurable: true,
    });
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => undefined);

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /download for hd supply/i }));

    await waitFor(() => {
      expect(api.purchaseOrderAPI.exportOrder).toHaveBeenCalledWith('po-1');
    });
    await waitFor(() => {
      expect(clickSpy).toHaveBeenCalled();
    });
    expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob));

    clickSpy.mockRestore();
  });

  test('hdsupply "Copy order pad" writes the Part#,Qty paste block to the clipboard', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: hdsupplyOrder });
    (api.purchaseOrderAPI.exportOrder as jest.Mock).mockResolvedValue({ data: hdsupplyPayload });
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    });

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /copy order pad/i }));

    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith('12345\t2');
    });
    expect(showSuccess).toHaveBeenCalledWith('Order pad copied to clipboard');
  });

  test('hdsupply adapter warns about lines with a non-numeric part number', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: hdsupplyOrder });
    (api.purchaseOrderAPI.exportOrder as jest.Mock).mockResolvedValue({
      data: { ...hdsupplyPayload, invalid_sku: ['Widget'] },
    });

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /download for hd supply/i }));

    const warning = await screen.findByTestId('order-pad-invalid-sku-warning');
    expect(warning).toHaveTextContent(/1 item has an invalid part number/i);
  });
});

/**
 * CONTROL for the money falsy-guard class (op-9m2v): this page was ALREADY right.
 *
 * `unit_cost_actual` is nullable — `null` means nobody recorded what was
 * charged, `"0.00"` means the vendor comped the line (a warranty replacement, a
 * goodwill part). The cell guards it with truthiness, which is the shape this
 * branch removed everywhere else; here it is SAFE, because DRF serialises the
 * DecimalField as a STRING and `"0.00"` is truthy in JavaScript. The BACKEND
 * twin of the same expression — `po_item.unit_cost_actual or
 * po_item.unit_cost_ordered` in `receiving.py`, where the value is a Decimal
 * and `Decimal("0.00")` is falsy — WAS a real defect and is fixed with
 * `test_a_comped_line_charges_the_committee_nothing`.
 *
 * These tests changed no behaviour. They exist because that safety rests
 * entirely on the field crossing the wire as a string: parse it to a number
 * anywhere on the way in and a comped line silently starts displaying the
 * price it was ordered at. Nothing pinned that before.
 */
describe('PurchaseOrderPage comped line cost (op-9m2v)', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('token', 'test-token');
    localStorage.setItem('is_staff', 'true');
  });

  const lineWithActual = (actual: string | null) => ({
    id: 'line-1',
    item_type: 'inventory_item',
    description: null,
    item_details: { id: 'i1', name: 'Comped Widget', sku: 'CW-1' },
    asset_details: null,
    quantity_ordered: 2,
    quantity_received: 2,
    quantity_pending: 0,
    is_fully_received: true,
    unit_cost_ordered: '9.00',
    unit_cost_actual: actual,
    estimated_cost: '18.00',
    actual_cost: null,
    expected_shipment_date: null,
    notes: '',
    is_voided: false,
    voided_at: null,
    void_reason: '',
  });

  const renderWithActual = async (actual: string | null) => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({
      data: { ...baseOrder, attachments: [], items: [lineWithActual(actual)] },
    });
    renderPage();
    await waitFor(() => expect(screen.getByText('Comped Widget')).toBeInTheDocument());
    return screen.getByText('Comped Widget').closest('tr')!;
  };

  test('a comped line shows $0.00, not the price it was ordered at', async () => {
    const row = await renderWithActual('0.00');

    expect(row).toHaveTextContent('$0.00');
    // The ordered price is still shown, but as the note it is — not as the
    // figure standing in for what was charged. This is the assertion that
    // fails the moment "0.00" stops being a string.
    expect(row).toHaveTextContent('(ordered: $9.00)');
  });

  test('an unrecorded actual cost still falls back to the ordered price', async () => {
    const row = await renderWithActual(null);

    expect(row).toHaveTextContent('$9.00');
    expect(row).not.toHaveTextContent('(ordered:');
  });

  test('an ordinary actual cost is unchanged — the branch invariant', async () => {
    const row = await renderWithActual('7.50');

    expect(row).toHaveTextContent('$7.50');
    expect(row).toHaveTextContent('(ordered: $9.00)');
  });
});
