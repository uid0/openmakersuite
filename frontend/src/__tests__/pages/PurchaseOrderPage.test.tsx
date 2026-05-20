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

jest.mock('../../services/api');

jest.mock('../../utils/dialogs', () => ({
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
