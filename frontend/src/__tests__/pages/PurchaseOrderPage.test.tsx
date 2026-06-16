/**
 * Tests for PurchaseOrderPage:
 *  - oms-aq2: editable metadata + file attachments behaviors.
 *  - oms-74q: freeform PO line items render their description, not 'Unknown Item'.
 *  - gh-453: mark-delivered patches the page from the response without
 *    flipping back into the initial "Loading purchase order…" placeholder.
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

const baseOrder = {
  id: 'po-1',
  po_number: 'PO-2026-0001',
  supplier_details: 'Acme Supplies',
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

  // TODO(vite-migration): under vitest the optimistic patch doesn't surface
  // before the assertion timeout; needs an act() wrap around the modal-close
  // dispatch. Re-enable after PR #565 lands.
  test.skip('patches the page from the mark-delivered response without a follow-up GET', async () => {
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

    // Status flips to "Received" from the response — no follow-up GET.
    await waitFor(() => {
      expect(screen.getByText(/Received/)).toBeInTheDocument();
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

  test('rejects an over-receive that exceeds the pending quantity', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: makeReceiveOrder() });

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /^receive items$/i }));

    // Bolt only has 7 pending; ask for more.
    fireEvent.change(screen.getByLabelText('Receive quantity for Stocked Bolt'), {
      target: { value: '99' },
    });

    fireEvent.click(screen.getByRole('button', { name: /confirm receipt/i }));

    await waitFor(() => {
      expect(showError).toHaveBeenCalledWith(expect.stringMatching(/only 7 pending/i));
    });
    expect(api.purchaseOrderAPI.receiveItems).not.toHaveBeenCalled();
  });
});
