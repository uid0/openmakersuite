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
  });

  const renderWithRouter = (itemId = 'test-id-123') => {
    return render(
      <MemoryRouter initialEntries={[`/scan/${itemId}`]}>
        <Routes>
          <Route path="/scan/:itemId" element={<ScanPage />} />
        </Routes>
      </MemoryRouter>
    );
  };

  test('displays loading state initially', () => {
    (api.inventoryAPI.getItem as jest.Mock).mockReturnValue(new Promise(() => {}));

    renderWithRouter();

    expect(screen.getByText(/loading item details/i)).toBeInTheDocument();
  });

  test('displays item details after loading', async () => {
    (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({
      data: mockItem,
    });

    renderWithRouter();

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
    const lowStockItem = { ...mockItem, current_stock: 5, needs_reorder: true };

    (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({
      data: lowStockItem,
    });

    renderWithRouter();

    await screen.findByText('Test Widget');
    expect(screen.getByText(/low stock alert/i)).toBeInTheDocument();
  });

  test('handles form submission', async () => {
    (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({
      data: mockItem,
    });

    (api.reorderAPI.createRequest as jest.Mock).mockResolvedValue({
      data: { id: 1, item: mockItem.id },
    });

    renderWithRouter();

    await screen.findByText('Test Widget');

    // Fill in the form
    const nameInput = screen.getByLabelText(/your name/i);
    const notesInput = screen.getByLabelText(/notes/i);

    fireEvent.change(nameInput, { target: { value: 'John Doe' } });
    fireEvent.change(notesInput, { target: { value: 'Need more stock' } });

    // Submit the form
    const submitButton = screen.getByRole('button', { name: /request 25 units/i });
    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(api.reorderAPI.createRequest).toHaveBeenCalledWith({
        item: mockItem.id,
        quantity: 25,
        requested_by: 'John Doe',
        request_notes: 'Need more stock',
        priority: 'normal',
      });
    });
  });

  test('displays success message after submission', async () => {
    (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({
      data: mockItem,
    });

    (api.reorderAPI.createRequest as jest.Mock).mockResolvedValue({
      data: { id: 1 },
    });

    renderWithRouter();

    await screen.findByText('Test Widget');

    const submitButton = screen.getByRole('button', { name: /request 25 units/i });
    fireEvent.click(submitButton);

    await screen.findByText(/reorder request submitted/i);
  });

  test('handles API error gracefully', async () => {
    (api.inventoryAPI.getItem as jest.Mock).mockRejectedValue({
      response: { data: { detail: 'Item not found' } },
    });

    renderWithRouter();

    await screen.findByText(/item not found/i);
  });
});
