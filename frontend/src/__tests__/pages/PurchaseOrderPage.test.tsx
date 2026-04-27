/**
 * Tests for PurchaseOrderPage:
 *  - oms-aq2: editable metadata + file attachments behaviors.
 *  - oms-74q: freeform PO line items render their description, not 'Unknown Item'.
 */
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
    <MemoryRouter initialEntries={['/purchase-orders/po-1']}>
      <Routes>
        <Route path="/purchase-orders/:orderId" element={<PurchaseOrderPage />} />
      </Routes>
    </MemoryRouter>
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
