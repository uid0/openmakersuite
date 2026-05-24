/**
 * Tests for SupplierFormPage component
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import SupplierFormPage from '../../pages/SupplierFormPage';
import * as api from '../../services/api';

// Mock the API
vi.mock('../../services/api');

const mockNavigate = jest.fn();
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useNavigate: () => mockNavigate,
}));

describe('SupplierFormPage - Create Mode', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('renders create form', () => {
    render(
      <MantineProvider>
        <MemoryRouter initialEntries={['/inventory/suppliers/new']}>
          <Routes>
            <Route path="/inventory/suppliers/new" element={<SupplierFormPage />} />
          </Routes>
        </MemoryRouter>
      </MantineProvider>
    );

    expect(screen.getByTestId('page-hero-title')).toHaveTextContent(/new supplier/i);
    // Check for name input by placeholder
    expect(screen.getByPlaceholderText('Supplier name')).toBeInTheDocument();
  });

  it('submits new supplier', async () => {
    (api.inventoryAPI.createSupplier as jest.Mock).mockResolvedValue({
      data: { id: 1, name: 'New Supplier' },
    });

    render(
      <MantineProvider>
        <MemoryRouter initialEntries={['/inventory/suppliers/new']}>
          <Routes>
            <Route path="/inventory/suppliers/new" element={<SupplierFormPage />} />
          </Routes>
        </MemoryRouter>
      </MantineProvider>
    );

    const nameInput = await screen.findByPlaceholderText('Supplier name');
    fireEvent.change(nameInput, { target: { value: 'New Supplier' } });

    const submitButton = screen.getByText('Create Supplier');
    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(api.inventoryAPI.createSupplier).toHaveBeenCalled();
      expect(mockNavigate).toHaveBeenCalledWith('/inventory/suppliers');
    });
  });

  it('validates required fields', async () => {
    render(
      <MantineProvider>
        <MemoryRouter>
          <SupplierFormPage />
        </MemoryRouter>
      </MantineProvider>
    );

    const submitButton = screen.getByText('Create Supplier');
    fireEvent.click(submitButton);

    await waitFor(() => {
      // Form validation should prevent submission
      expect(api.inventoryAPI.createSupplier).not.toHaveBeenCalled();
    });
  });

  it('cancels and navigates back', () => {
    render(
      <MantineProvider>
        <MemoryRouter>
          <SupplierFormPage />
        </MemoryRouter>
      </MantineProvider>
    );

    const cancelButton = screen.getByText('Cancel');
    fireEvent.click(cancelButton);

    expect(mockNavigate).toHaveBeenCalledWith(-1);
  });
});

describe('SupplierFormPage - Edit Mode', () => {
  const mockSupplier = {
    id: 1,
    name: 'Existing Supplier',
    supplier_type: 'local' as const,
    website: 'https://example.com',
    account_number: 'ACC-123',
    tax_free_paperwork_filed: true,
    notes: 'Test notes',
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
  };

  beforeEach(() => {
    jest.clearAllMocks();
    (api.inventoryAPI.getSupplier as jest.Mock).mockResolvedValue({
      data: mockSupplier,
    });
  });

  it('loads supplier data in edit mode', async () => {
    render(
      <MantineProvider>
        <MemoryRouter initialEntries={['/inventory/suppliers/1/edit']}>
          <Routes>
            <Route path="/inventory/suppliers/:id/edit" element={<SupplierFormPage />} />
          </Routes>
        </MemoryRouter>
      </MantineProvider>
    );

    await waitFor(() => {
      expect(api.inventoryAPI.getSupplier).toHaveBeenCalledWith('1');
      expect(screen.getByDisplayValue('Existing Supplier')).toBeInTheDocument();
    });
  });

  it('updates supplier', async () => {
    (api.inventoryAPI.updateSupplier as jest.Mock).mockResolvedValue({
      data: { ...mockSupplier, name: 'Updated Supplier' },
    });

    render(
      <MantineProvider>
        <MemoryRouter initialEntries={['/inventory/suppliers/1/edit']}>
          <Routes>
            <Route path="/inventory/suppliers/:id/edit" element={<SupplierFormPage />} />
          </Routes>
        </MemoryRouter>
      </MantineProvider>
    );

    await waitFor(() => {
      expect(screen.getByDisplayValue('Existing Supplier')).toBeInTheDocument();
    });

    const nameInput = screen.getByDisplayValue('Existing Supplier');
    fireEvent.change(nameInput, { target: { value: 'Updated Supplier' } });

    const submitButton = screen.getByText('Save Changes');
    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(api.inventoryAPI.updateSupplier).toHaveBeenCalledWith('1', expect.objectContaining({
        name: 'Updated Supplier',
      }));
      expect(mockNavigate).toHaveBeenCalledWith('/inventory/suppliers');
    });
  });
});
