/**
 * Tests for PurchaseOrderFormPage component
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import PurchaseOrderFormPage from '../../pages/PurchaseOrderFormPage';
import * as api from '../../services/api';

// Mock the API
jest.mock('../../services/api');

const mockNavigate = jest.fn();
jest.mock('react-router-dom', () => ({
  ...jest.requireActual('react-router-dom'),
  useNavigate: () => mockNavigate,
}));

describe('PurchaseOrderFormPage', () => {
  const mockSupplier = {
    id: 1,
    name: 'Test Supplier',
    supplier_type: 'online',
    total_items: 2,
    assets: [],
    estimated_total: '100.00',
    avg_lead_time: 5,
  };

  const mockItem: api.ReorderDataItem = {
    item_supplier_id: 1,
    item_id: 'item-1',
    item_name: 'Test Item',
    item_sku: 'TEST-001',
    current_stock: 5,
    minimum_stock: 10,
    reorder_quantity: 20,
    suggested_quantity: 25,
    unit_cost: '2.50',
    package_cost: '30.00',
    quantity_per_package: 12,
    lead_time_days: 7,
    supplier_sku: 'SUP-001',
    supplier_url: 'https://example.com/item',
    is_primary: true,
    line_total: '62.50',
  };

  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('token', 'test-token');
  });

  test('displays loading state initially', async () => {
    (api.purchaseOrderAPI.getReorderData as jest.Mock).mockReturnValue(
      new Promise(() => {})
    );

    render(
      <MemoryRouter>
        <PurchaseOrderFormPage />
      </MemoryRouter>
    );

    expect(screen.getByText(/loading reorder data/i)).toBeInTheDocument();
  });

  test('displays suppliers after loading', async () => {
    (api.purchaseOrderAPI.getReorderData as jest.Mock).mockResolvedValue({
      data: {
        suppliers: [mockSupplier],
      },
    });

    render(
      <MemoryRouter>
        <PurchaseOrderFormPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('Test Supplier')).toBeInTheDocument();
    });
  });

  test('creates purchase order successfully without 500 error', async () => {
    const mockReorderData = {
      suppliers: [
        {
          ...mockSupplier,
          items: [mockItem],
        },
      ],
    };

    const mockCreatedOrder = {
      id: 1,
      po_number: 'PO-2024-0001',
      supplier: 1,
      status: 'draft',
    };

    (api.purchaseOrderAPI.getReorderData as jest.Mock).mockResolvedValue({
      data: mockReorderData,
    });

    (api.purchaseOrderAPI.createOrder as jest.Mock).mockResolvedValue({
      data: mockCreatedOrder,
    });

    render(
      <MemoryRouter>
        <PurchaseOrderFormPage />
      </MemoryRouter>
    );

    // Wait for suppliers to load
    await waitFor(() => {
      expect(screen.getByText('Test Supplier')).toBeInTheDocument();
    });

    // Select supplier
    const supplierCard = screen.getByText('Test Supplier').closest('button');
    if (supplierCard) {
      fireEvent.click(supplierCard);
    }

    // Wait for items to populate
    await waitFor(() => {
      expect(screen.getByText('Test Item')).toBeInTheDocument();
    });

    // Submit the form
    const submitButton = screen.getByRole('button', {
      name: /create purchase order/i,
    });
    fireEvent.click(submitButton);

    // Wait for API call
    await waitFor(() => {
      expect(api.purchaseOrderAPI.createOrder).toHaveBeenCalled();
    });

    // Verify the API was called with correct data
    const createOrderCall = (api.purchaseOrderAPI.createOrder as jest.Mock).mock
      .calls[0][0];
    expect(createOrderCall.supplier).toBe(1);
    expect(createOrderCall.items).toHaveLength(1);
    expect(createOrderCall.items[0].item_supplier_id).toBe(1);
    expect(createOrderCall.items[0].quantity).toBe(25);

    // Verify navigation was called (indicating success, not a 500 error)
    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/purchasing/orders/1');
    });
  });

  test('handles API error gracefully', async () => {
    const mockReorderData = {
      suppliers: [
        {
          ...mockSupplier,
          items: [mockItem],
        },
      ],
    };

    (api.purchaseOrderAPI.getReorderData as jest.Mock).mockResolvedValue({
      data: mockReorderData,
    });

    // Mock a 500 error response
    const mockError = {
      response: {
        status: 500,
        data: {
          detail: 'Internal server error',
        },
      },
    };

    (api.purchaseOrderAPI.createOrder as jest.Mock).mockRejectedValue(mockError);

    render(
      <MemoryRouter>
        <PurchaseOrderFormPage />
      </MemoryRouter>
    );

    // Wait for suppliers to load
    await waitFor(() => {
      expect(screen.getByText('Test Supplier')).toBeInTheDocument();
    });

    // Select supplier
    const supplierCard = screen.getByText('Test Supplier').closest('button');
    if (supplierCard) {
      fireEvent.click(supplierCard);
    }

    // Wait for items to populate
    await waitFor(() => {
      expect(screen.getByText('Test Item')).toBeInTheDocument();
    });

    // Submit the form
    const submitButton = screen.getByRole('button', {
      name: /create purchase order/i,
    });
    fireEvent.click(submitButton);

    // Wait for error message
    await waitFor(() => {
      expect(screen.getByText(/internal server error/i)).toBeInTheDocument();
    });

    // Verify navigation was NOT called (error occurred)
    expect(mockNavigate).not.toHaveBeenCalled();
  });
});

