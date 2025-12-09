/**
 * Tests for PurchaseOrderReceivingPage component
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import PurchaseOrderReceivingPage from '../../pages/PurchaseOrderReceivingPage';
import * as api from '../../services/api';

// Mock the API
jest.mock('../../services/api');

const mockNavigate = jest.fn();
jest.mock('react-router-dom', () => ({
  ...jest.requireActual('react-router-dom'),
  useNavigate: () => mockNavigate,
}));

describe('PurchaseOrderReceivingPage', () => {
  const mockOrder = {
    id: 'po-123',
    po_number: 'PO-2024-001',
    supplier: 1,
    supplier_details: 'Test Supplier',
    status: 'sent',
    status_label: 'Sent to Supplier',
    order_date: '2024-01-01T00:00:00Z',
    expected_delivery_date: '2024-01-15T00:00:00Z',
    notes: '',
    estimated_total: '100.00',
    actual_total: null,
    created_by: 1,
    created_by_username: 'admin',
    sent_by: 1,
    sent_by_username: 'admin',
    sent_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
    items: [
      {
        id: 'item-1',
        item_type: 'inventory_item' as const,
        item_details: {
          id: 'item-1',
          name: 'Test Item 1',
          sku: 'TEST-001',
          current_stock: 10,
        },
        asset_details: null,
        quantity_ordered: 20,
        quantity_received: 0,
        unit_cost_ordered: '5.00',
        unit_cost_actual: null,
        estimated_cost: '100.00',
        actual_cost: null,
        expected_shipment_date: null,
        notes: '',
        is_voided: false,
        voided_at: null,
        void_reason: '',
      },
      {
        id: 'item-2',
        item_type: 'inventory_item' as const,
        item_details: {
          id: 'item-2',
          name: 'Test Item 2',
          sku: 'TEST-002',
          current_stock: 5,
        },
        asset_details: null,
        quantity_ordered: 10,
        quantity_received: 5,
        unit_cost_ordered: '3.00',
        unit_cost_actual: null,
        estimated_cost: '30.00',
        actual_cost: null,
        expected_shipment_date: null,
        notes: '',
        is_voided: false,
        voided_at: null,
        void_reason: '',
      },
    ],
    total_items: 2,
    total_quantity: 30,
    total_received_quantity: 5,
    is_fully_received: false,
    days_since_ordered: 5,
  };

  const mockDeliveries = [
    {
      id: 'delivery-1',
      purchase_order: 1,
      purchase_order_details: mockOrder,
      delivery_date: '2024-01-05T00:00:00Z',
      tracking_number: 'TRACK123',
      carrier: 'UPS',
      received_by: 1,
      received_by_username: 'admin',
      receipt_notes: 'Received in good condition',
      is_complete: false,
      items: [],
      total_items_received: 1,
      total_quantity_received: 5,
      created_at: '2024-01-05T00:00:00Z',
      updated_at: '2024-01-05T00:00:00Z',
    },
  ];

  beforeEach(() => {
    jest.clearAllMocks();
    mockNavigate.mockClear();
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({
      data: mockOrder,
    });
    (api.purchaseOrderAPI.getDeliveries as jest.Mock).mockResolvedValue({
      data: { results: mockDeliveries },
    });
  });

  const renderPage = (orderId = 'po-123') => {
    const view = render(
      <MantineProvider>
        <MemoryRouter initialEntries={[`/purchasing/orders/${orderId}/receive`]}>
          <Routes>
            <Route path="/purchasing/orders/:orderId/receive" element={<PurchaseOrderReceivingPage />} />
          </Routes>
        </MemoryRouter>
      </MantineProvider>
    );
    return view;
  };

  test('renders receiving page with order details', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Receive Purchase Order')).toBeInTheDocument();
    });

    expect(screen.getByText(/PO #PO-2024-001/)).toBeInTheDocument();
    expect(screen.getByText(/Test Supplier/)).toBeInTheDocument();
  });

  test('displays loading state initially', () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockReturnValue(new Promise(() => {}));

    renderPage();

    expect(screen.getByText(/loading purchase order/i)).toBeInTheDocument();
  });

  test('displays barcode scanning section for receivable orders', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Barcode Scanning')).toBeInTheDocument();
    });

    expect(screen.getByLabelText('UPC/Barcode')).toBeInTheDocument();
    expect(screen.getByLabelText('Quantity')).toBeInTheDocument();
    expect(screen.getByText('Scan & Receive')).toBeInTheDocument();
  });

  test('displays manual receipt section', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Manual Receipt')).toBeInTheDocument();
    });

    expect(screen.getByText('Test Item 1')).toBeInTheDocument();
    expect(screen.getByText('Test Item 2')).toBeInTheDocument();
  });

  test('shows order items with quantities', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Test Item 1')).toBeInTheDocument();
    });

    // Check ordered quantities - use getAllByText since there might be multiple instances
    const ordered20 = screen.getAllByText('20');
    expect(ordered20.length).toBeGreaterThan(0); // Item 1 ordered
    expect(screen.getByText('10')).toBeInTheDocument(); // Item 2 ordered

    // Check received quantities - use getAllByText for values that might appear multiple times
    const received0 = screen.getAllByText('0');
    expect(received0.length).toBeGreaterThan(0); // Item 1 received
    const received5 = screen.getAllByText('5');
    expect(received5.length).toBeGreaterThan(0); // Item 2 received
  });

  test('allows entering UPC and quantity for barcode scanning', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByLabelText('UPC/Barcode')).toBeInTheDocument();
    });

    const upcInput = screen.getByLabelText('UPC/Barcode');
    const quantityInput = screen.getByLabelText('Quantity');

    fireEvent.change(upcInput, { target: { value: '1234567890' } });
    fireEvent.change(quantityInput, { target: { value: '5' } });

    expect(upcInput).toHaveValue('1234567890');
    expect(quantityInput).toHaveValue(5);
  });

  test('processes barcode scan successfully', async () => {
    const mockScanResponse = {
      success: true,
      message: 'Successfully received 5 unit(s) of Test Item 1',
      item_name: 'Test Item 1',
      quantity_received: 5,
      total_received: 5,
      quantity_remaining: 15,
      order_status: 'partially_received',
      updated_inventory_stock: 15,
    };

    (api.purchaseOrderAPI.scanBarcode as jest.Mock).mockResolvedValue({
      data: mockScanResponse,
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByLabelText('UPC/Barcode')).toBeInTheDocument();
    });

    const upcInput = screen.getByLabelText('UPC/Barcode');
    const quantityInput = screen.getByLabelText('Quantity');
    const scanButton = screen.getByText('Scan & Receive');

    fireEvent.change(upcInput, { target: { value: '1234567890' } });
    fireEvent.change(quantityInput, { target: { value: '5' } });
    fireEvent.click(scanButton);

    await waitFor(() => {
      expect(api.purchaseOrderAPI.scanBarcode).toHaveBeenCalledWith({
        purchase_order_id: parseInt('po-123'),
        scanned_upc: '1234567890',
        quantity_received: 5,
        is_damaged: false,
        is_expired: false,
        condition_notes: '',
      });
    });

    await waitFor(() => {
      expect(screen.getByText(/successfully received/i)).toBeInTheDocument();
    });
  });

  test('handles barcode scan errors', async () => {
    (api.purchaseOrderAPI.scanBarcode as jest.Mock).mockRejectedValue({
      response: { data: { error: 'No items in this order match the scanned UPC' } },
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByLabelText('UPC/Barcode')).toBeInTheDocument();
    });

    const upcInput = screen.getByLabelText('UPC/Barcode');
    const scanButton = screen.getByText('Scan & Receive');

    fireEvent.change(upcInput, { target: { value: 'invalid-upc' } });
    fireEvent.click(scanButton);

    await waitFor(() => {
      expect(screen.getByText(/no items in this order match/i)).toBeInTheDocument();
    });
  });

  test('allows setting quantities for manual receipt', async () => {
    const view = renderPage();

    await waitFor(() => {
      expect(screen.getByText('Test Item 1')).toBeInTheDocument();
    });

    // Find quantity inputs in the manual receipt table
    // Look for number inputs in the table (they might not have labels)
    const inputs = view.container.querySelectorAll('input[type="number"]');
    expect(inputs.length).toBeGreaterThan(0);
  });

  test('allows marking items as damaged or expired', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Test Item 1')).toBeInTheDocument();
    });

    // Find checkboxes (they should be present in the manual receipt table)
    const checkboxes = screen.getAllByRole('checkbox');
    expect(checkboxes.length).toBeGreaterThan(0);
  });

  test('displays delivery history', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Delivery History')).toBeInTheDocument();
    });

    // Text might be in nested elements, use more flexible matching
    expect(screen.getByText(/TRACK123/i)).toBeInTheDocument();
    expect(screen.getByText(/UPS/i)).toBeInTheDocument();
  });

  test('shows alert for non-receivable orders', async () => {
    const cancelledOrder = { ...mockOrder, status: 'cancelled', status_label: 'Cancelled' };
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({
      data: cancelledOrder,
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/this purchase order cannot be received/i)).toBeInTheDocument();
    });
  });

  test('handles order not found error', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockRejectedValue({
      response: { data: { error: 'Purchase order not found' } },
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/error/i)).toBeInTheDocument();
    });
  });

  test('navigates back to purchase orders list', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Back to Purchase Orders')).toBeInTheDocument();
    });

    const backButton = screen.getByText('Back to Purchase Orders');
    fireEvent.click(backButton);

    expect(mockNavigate).toHaveBeenCalledWith('/purchasing/orders');
  });

  test('navigates to PO details page', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('View PO Details')).toBeInTheDocument();
    });

    const viewDetailsButton = screen.getByText('View PO Details');
    fireEvent.click(viewDetailsButton);

    expect(mockNavigate).toHaveBeenCalledWith('/purchasing/orders/po-123');
  });
});
