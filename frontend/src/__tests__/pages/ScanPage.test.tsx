/**
 * Tests for ScanPage component
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import ScanPage from '../../pages/ScanPage';
import * as api from '../../services/api';

// Mock the API
jest.mock('../../services/api');

const mockNavigate = jest.fn();
jest.mock('react-router-dom', () => ({
  ...jest.requireActual('react-router-dom'),
  useNavigate: () => mockNavigate,
}));

describe('ScanPage', () => {
  const mockItem = {
    id: 'test-id-123',
    name: 'Test Widget',
    description: 'A test item description',
    sku: 'TEST-001',
    location: 'Shelf A',
    reorder_quantity: 25,
    current_stock: 50,
    minimum_stock: 10,
    average_lead_time: 7,
    supplier_name: 'Test Supplier',
    unit_cost: '15.99',
    needs_reorder: false,
    category_name: 'Tools',
    image: null,
    thumbnail: null,
    qr_code: null,
    is_active: true,
    notes: '',
    total_value: '799.50',
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
    category: null,
    supplier: null,
    supplier_sku: '',
    supplier_url: '',
  };

  beforeEach(() => {
    jest.clearAllMocks();
    // Clear localStorage to ensure clean state
    localStorage.clear();
  });

  const renderWithRouter = async (itemId = 'test-id-123') => {
    const view = render(
      <MemoryRouter initialEntries={[`/scan/${itemId}`]}>
        <Routes>
          <Route path="/scan/:itemId" element={<ScanPage />} />
        </Routes>
      </MemoryRouter>
    );
    return view;
  };

  test('displays loading state initially', async () => {
    (api.inventoryAPI.getItem as jest.Mock).mockReturnValue(new Promise(() => {}));

    await renderWithRouter();

    expect(screen.getByText(/loading item details/i)).toBeInTheDocument();
  });

  test('displays item details after loading (logged in user)', async () => {
    // Set logged in state
    localStorage.setItem('token', 'test-token');
    
    (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({
      data: mockItem,
    });

    (api.inventoryAPI.getItemSuppliers as jest.Mock).mockResolvedValue({
      data: { results: [] },
    });

    await renderWithRouter();

    await screen.findByText('Test Widget');

    expect(screen.getByText(/a test item description/i)).toBeInTheDocument();
    expect(screen.getByText(/sku: TEST-001/i)).toBeInTheDocument();
    expect(screen.getByText(/location:/i)).toBeInTheDocument();
    expect(screen.getByText(/shelf a/i)).toBeInTheDocument();
    expect(screen.getByText(/current stock:/i)).toBeInTheDocument();
    expect(screen.getByText(/reorder quantity:/i)).toBeInTheDocument();
    expect(screen.getByText(/average lead time:/i)).toBeInTheDocument();
  });

  test('displays low stock warning when needed', async () => {
    // Set logged in state to avoid auto-submit
    localStorage.setItem('token', 'test-token');
    
    const lowStockItem = { ...mockItem, current_stock: 5, needs_reorder: true };

    (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({
      data: lowStockItem,
    });

    (api.inventoryAPI.getItemSuppliers as jest.Mock).mockResolvedValue({
      data: { results: [] },
    });

    await renderWithRouter();

    await screen.findByText('Test Widget');
    expect(screen.getByText(/low stock alert/i)).toBeInTheDocument();
  });

  test('handles form submission for logged in user', async () => {
    // Set logged in state to get the manual form
    localStorage.setItem('token', 'test-token');
    
    const mockSupplier = {
      id: 1,
      supplier_name: 'Test Supplier',
      unit_cost: '15.99',
      quantity_per_package: 1,
      is_active: true,
      average_lead_time: 7,
    };
    
    (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({
      data: mockItem,
    });

    (api.inventoryAPI.getItemSuppliers as jest.Mock).mockResolvedValue({
      data: { results: [mockSupplier] },
    });

    (api.reorderAPI.createRequest as jest.Mock).mockResolvedValue({
      data: { id: 1, item: mockItem.id },
    });

    await renderWithRouter();

    await screen.findByText('Test Widget');

    // Fill in the form
    const nameInput = screen.getByLabelText(/your name/i);
    const notesInput = screen.getByLabelText(/notes/i);

    fireEvent.change(nameInput, { target: { value: 'John Doe' } });
    fireEvent.change(notesInput, { target: { value: 'Need more stock' } });

    // Submit the form (should have supplier and proper quantities now)
    const submitButton = screen.getByRole('button', { name: /request \d+ units/i });
    
    fireEvent.click(submitButton);

    // Wait for the async operations to complete
    await waitFor(() => {
      expect(api.reorderAPI.createRequest).toHaveBeenCalledWith(
        expect.objectContaining({
          item: mockItem.id,
          requested_by: 'John Doe',
          request_notes: 'Need more stock',
          priority: 'normal',
        })
      );
    });
  });

  test('auto-submits reorder for non-logged users', async () => {
    // Don't set token to simulate non-logged user
    const itemWithoutPending = { ...mockItem, has_pending_reorder: false };

    (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({
      data: itemWithoutPending,
    });

    (api.reorderAPI.createRequest as jest.Mock).mockResolvedValue({
      data: { id: 1 },
    });

    await renderWithRouter();

    // Should show auto-submit processing message
    await screen.findByText(/submitting reorder request/i);

    // Wait for auto-submit to complete
    await waitFor(() => {
      expect(api.reorderAPI.createRequest).toHaveBeenCalledWith({
        item: itemWithoutPending.id,
        quantity: itemWithoutPending.reorder_quantity,
        requested_by: 'Anonymous',
        request_notes: 'Auto-submitted via QR scan',
        priority: 'normal',
      });
    });
  });

  test('handles API error gracefully', async () => {
    (api.inventoryAPI.getItem as jest.Mock).mockRejectedValue({
      response: { data: { detail: 'Item not found' } },
    });

    await renderWithRouter();

    await screen.findByText(/item not found/i);
  });
});
