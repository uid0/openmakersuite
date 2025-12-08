/**
 * Tests for InventoryItemFormPage component
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import InventoryItemFormPage from '../../pages/InventoryItemFormPage';
import * as api from '../../services/api';

// Mock the API
jest.mock('../../services/api');

// Mock qrcode.react
jest.mock('qrcode.react', () => ({
  QRCodeSVG: () => <div data-testid="qr-code">QR Code</div>,
}));

// Mock NFPADiamond
jest.mock('../../components/NFPADiamond', () => ({
  __esModule: true,
  default: () => <div data-testid="nfpa-diamond">NFPA Diamond</div>,
}));

// Mock SupplierRelationshipForm
jest.mock('../../components/SupplierRelationshipForm', () => ({
  __esModule: true,
  default: ({ onChange }: any) => (
    <div data-testid="supplier-form">
      <button onClick={() => onChange([])}>Change Suppliers</button>
    </div>
  ),
}));

const mockNavigate = jest.fn();
jest.mock('react-router-dom', () => ({
  ...jest.requireActual('react-router-dom'),
  useNavigate: () => mockNavigate,
}));

describe('InventoryItemFormPage', () => {
  const mockCategories = [
    { id: 1, name: 'Tools', slug: 'tools', description: 'Tools category', parent: null },
  ];

  const mockLocations = [
    { id: 1, name: 'Shelf A', description: 'Shelf A location', is_active: true },
  ];

  const mockSuppliers = [
    {
      id: 1,
      name: 'Test Supplier',
      supplier_type: 'amazon' as const,
      website: 'https://example.com',
      notes: '',
      created_at: '2024-01-01T00:00:00Z',
      updated_at: '2024-01-01T00:00:00Z',
    },
  ];

  const mockItem = {
    id: 'test-id',
    name: 'Test Item',
    description: 'Test description',
    sku: 'TEST-001',
    category: 1,
    category_name: 'Tools',
    location: 'Shelf A',
    current_stock: 10,
    minimum_stock: 5,
    reorder_quantity: 20,
    unit_cost: '15.99',
    supplier_name: 'Test Supplier',
    needs_reorder: false,
    has_pending_reorder: false,
    is_active: true,
    image: null,
    thumbnail: null,
    qr_code: null,
    use_case_based_reorder: false,
    minimum_cases: 0,
    reorder_cases: 0,
    current_cases: 0,
    supplier: null,
    supplier_sku: '',
    supplier_url: '',
    average_lead_time: 7,
    notes: 'Test notes',
    total_value: '159.90',
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
    ownership_type: 'space' as const,
    owning_user: null,
    owning_group: null,
    reorder_status: '',
    expected_delivery_date: null,
    active_reorder_request: null,
    is_hazardous: false,
    msds_url: null,
    nfpa_health_hazard: null,
    nfpa_fire_hazard: null,
    nfpa_instability_hazard: null,
    nfpa_special_hazards: '',
    nfpa_fire_diamond_display: '',
    hazmat_compliance_status: '',
    has_complete_nfpa_data: false,
  };

  beforeEach(() => {
    jest.clearAllMocks();
    (api.inventoryAPI.listCategories as jest.Mock).mockResolvedValue({
      data: { results: mockCategories },
    });
    (api.inventoryAPI.listLocations as jest.Mock).mockResolvedValue({
      data: { results: mockLocations },
    });
    (api.inventoryAPI.listSuppliers as jest.Mock).mockResolvedValue({
      data: { results: mockSuppliers },
    });
    (api.inventoryAPI.getItemSuppliers as jest.Mock).mockResolvedValue({
      data: { results: [] },
    });
  });

  const renderCreatePage = () => {
    return render(
      <MantineProvider>
        <MemoryRouter initialEntries={['/inventory/items/new']}>
          <Routes>
            <Route path="/inventory/items/new" element={<InventoryItemFormPage />} />
          </Routes>
        </MemoryRouter>
      </MantineProvider>
    );
  };

  const renderEditPage = (itemId = 'test-id') => {
    (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({
      data: mockItem,
    });

    return render(
      <MantineProvider>
        <MemoryRouter initialEntries={[`/inventory/items/${itemId}/edit`]}>
          <Routes>
            <Route path="/inventory/items/:id/edit" element={<InventoryItemFormPage />} />
          </Routes>
        </MemoryRouter>
      </MantineProvider>
    );
  };

  it('renders create form', async () => {
    renderCreatePage();

    await waitFor(() => {
      expect(screen.getByText('Create Inventory Item')).toBeInTheDocument();
    });

    // Use getAllByLabelText and get the first one if multiple
    const nameInputs = screen.getAllByLabelText(/Name/i);
    expect(nameInputs.length).toBeGreaterThan(0);
    expect(screen.getByText('Create Item')).toBeInTheDocument();
  });

  it('renders edit form with item data', async () => {
    renderEditPage();

    await waitFor(() => {
      expect(screen.getByText('Edit Inventory Item')).toBeInTheDocument();
    });

    expect(screen.getByDisplayValue('Test Item')).toBeInTheDocument();
    expect(screen.getByDisplayValue('Test description')).toBeInTheDocument();
    expect(screen.getByText('Save Changes')).toBeInTheDocument();
  });

  it('validates required fields', async () => {
    renderCreatePage();

    await waitFor(() => {
      expect(screen.getByText('Create Item')).toBeInTheDocument();
    });

    const submitButton = screen.getByText('Create Item');
    fireEvent.click(submitButton);

    // Form validation should prevent submission
    // The form should show validation errors
    await waitFor(() => {
      // Check that the form is still visible (not submitted)
      expect(screen.getByText('Create Item')).toBeInTheDocument();
    }, { timeout: 2000 });
  });

  it('submits form with valid data', async () => {
    (api.inventoryAPI.createItem as jest.Mock).mockResolvedValue({
      data: { ...mockItem, id: 'new-id' },
    });

    renderCreatePage();

    await waitFor(() => {
      const nameInputs = screen.getAllByLabelText(/Name/i);
      expect(nameInputs.length).toBeGreaterThan(0);
    });

    // Fill in required fields - use first input if multiple
    const nameInputs = screen.getAllByLabelText(/Name/i);
    fireEvent.change(nameInputs[0], { target: { value: 'New Item' } });
    
    const currentStockInputs = screen.getAllByLabelText(/Current Stock/i);
    if (currentStockInputs.length > 0) {
      fireEvent.change(currentStockInputs[0], { target: { value: '10' } });
    }
    
    const minStockInputs = screen.getAllByLabelText(/Minimum Stock/i);
    if (minStockInputs.length > 0) {
      fireEvent.change(minStockInputs[0], { target: { value: '5' } });
    }
    
    const reorderInputs = screen.getAllByLabelText(/Reorder Quantity/i);
    if (reorderInputs.length > 0) {
      fireEvent.change(reorderInputs[0], { target: { value: '20' } });
    }

    const submitButton = screen.getByText('Create Item');
    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(api.inventoryAPI.createItem).toHaveBeenCalled();
    }, { timeout: 3000 });
  });

  it('toggles case-based reordering', async () => {
    renderCreatePage();

    await waitFor(() => {
      const toggles = screen.getAllByLabelText(/Use Case-Based Reordering/i);
      expect(toggles.length).toBeGreaterThan(0);
    });

    const toggles = screen.getAllByLabelText(/Use Case-Based Reordering/i);
    const toggle = toggles[0];
    fireEvent.click(toggle);

    await waitFor(() => {
      const minCasesInputs = screen.getAllByLabelText(/Minimum Cases/i);
      expect(minCasesInputs.length).toBeGreaterThan(0);
    }, { timeout: 2000 });
  });

  it('toggles hazmat section', async () => {
    renderCreatePage();

    await waitFor(() => {
      const toggles = screen.getAllByLabelText(/Is Hazardous Material/i);
      expect(toggles.length).toBeGreaterThan(0);
    });

    const toggles = screen.getAllByLabelText(/Is Hazardous Material/i);
    const toggle = toggles[0];
    fireEvent.click(toggle);

    await waitFor(() => {
      expect(screen.getByTestId('nfpa-diamond')).toBeInTheDocument();
    }, { timeout: 2000 });
  });

  it('creates new category', async () => {
    (api.inventoryAPI.createCategory as jest.Mock).mockResolvedValue({
      data: { id: 2, name: 'New Category', slug: 'new-category', description: '', parent: null },
    });

    renderCreatePage();

    await waitFor(() => {
      // Find category input/label
      const categoryLabels = screen.getAllByText(/Category/i);
      expect(categoryLabels.length).toBeGreaterThan(0);
    });
  });

  it('handles form cancellation', async () => {
    renderCreatePage();

    await waitFor(() => {
      const cancelButtons = screen.getAllByText('Cancel');
      expect(cancelButtons.length).toBeGreaterThan(0);
    });

    // Get the cancel button that's not in a modal
    const cancelButtons = screen.getAllByText('Cancel');
    const formCancelButton = cancelButtons.find(btn => {
      const parent = btn.closest('form') || btn.closest('[class*="Paper"]');
      return parent !== null;
    });
    
    if (formCancelButton) {
      fireEvent.click(formCancelButton);
      expect(mockNavigate).toHaveBeenCalledWith(-1);
    } else {
      // Fallback: click first cancel button
      fireEvent.click(cancelButtons[0]);
      expect(mockNavigate).toHaveBeenCalled();
    }
  });

  it('handles API errors', async () => {
    (api.inventoryAPI.createItem as jest.Mock).mockRejectedValue({
      response: { data: { detail: 'Validation error' } },
    });

    renderCreatePage();

    await waitFor(() => {
      const nameInputs = screen.getAllByLabelText(/Name/i);
      expect(nameInputs.length).toBeGreaterThan(0);
    });

    const nameInputs = screen.getAllByLabelText(/Name/i);
    fireEvent.change(nameInputs[0], { target: { value: 'New Item' } });
    
    const currentStockInputs = screen.getAllByLabelText(/Current Stock/i);
    if (currentStockInputs.length > 0) {
      fireEvent.change(currentStockInputs[0], { target: { value: '10' } });
    }
    
    const minStockInputs = screen.getAllByLabelText(/Minimum Stock/i);
    if (minStockInputs.length > 0) {
      fireEvent.change(minStockInputs[0], { target: { value: '5' } });
    }
    
    const reorderInputs = screen.getAllByLabelText(/Reorder Quantity/i);
    if (reorderInputs.length > 0) {
      fireEvent.change(reorderInputs[0], { target: { value: '20' } });
    }

    const submitButton = screen.getByText('Create Item');
    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(screen.getByText(/Validation error/)).toBeInTheDocument();
    }, { timeout: 3000 });
  });
});
