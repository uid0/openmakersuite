/**
 * Tests for SupplierDetailPage component
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import SupplierDetailPage from '../../pages/SupplierDetailPage';
import * as api from '../../services/api';

// Mock the API
vi.mock('../../services/api');

// Mock chart components
vi.mock('../../components/LeadTimeChart', async () => ({
  __esModule: true,
  default: ({ analytics }: any) => <div>LeadTimeChart: {analytics?.total_orders || 0} orders</div>,
}));

vi.mock('../../components/PriceTrendChart', async () => ({
  __esModule: true,
  default: ({ priceTrends }: any) => (
    <div>PriceTrendChart: {priceTrends?.summary?.price_changes_count || 0} changes</div>
  ),
}));

const mockNavigate = jest.fn();
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useNavigate: () => mockNavigate,
  useParams: () => ({ id: '1' }),
}));

describe('SupplierDetailPage', () => {
  const mockSupplier = {
    id: 1,
    name: 'Test Supplier',
    supplier_type: 'local' as const,
    website: 'https://test.com',
    account_number: 'ACC-123',
    tax_free_paperwork_filed: true,
    notes: 'Test notes',
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
    item_count: 5,
    purchase_order_count: 3,
    total_spent: '1500.00',
    items: [
      {
        id: 1,
        item: '1',
        item_name: 'Test Item 1',
        supplier: 1,
        supplier_name: 'Test Supplier',
        supplier_sku: 'SKU-1',
        supplier_url: 'https://test.com/item1',
        unit_cost: '10.00',
        package_cost: '100.00',
        average_lead_time: 7,
        is_primary: true,
        is_active: true,
      },
    ],
    purchase_orders: [
      {
        id: 1,
        po_number: 'PO-001',
        order_date: '2024-01-01T00:00:00Z',
        status: 'received',
        items: [],
        estimated_total: '500.00',
        actual_total: '500.00',
      },
    ],
    lead_time_analytics: {
      average_lead_time: 7.5,
      min_lead_time: 5,
      max_lead_time: 10,
      avg_variance_vs_quoted_lead_time_days: 0.5,
      total_orders: 10,
      within_quoted_lead_time_pct: 80,
    },
    price_trends: {
      trends: [],
      summary: {
        average_unit_cost: 10.0,
        min_unit_cost: 8.0,
        max_unit_cost: 12.0,
        price_changes_count: 5,
      },
    },
  };

  beforeEach(() => {
    jest.clearAllMocks();
    (api.inventoryAPI.getSupplier as jest.Mock).mockResolvedValue({
      data: mockSupplier,
    });
  });

  it('renders loading state initially', async () => {
    (api.inventoryAPI.getSupplier as jest.Mock).mockReturnValue(new Promise(() => {}));
    render(
      <MantineProvider>
        <MemoryRouter>
          <SupplierDetailPage />
        </MemoryRouter>
      </MantineProvider>
    );

    expect(screen.getByText(/Loading supplier/i)).toBeInTheDocument();
  });

  it('displays supplier information', async () => {
    render(
      <MantineProvider>
        <MemoryRouter>
          <SupplierDetailPage />
        </MemoryRouter>
      </MantineProvider>
    );

    await waitFor(() => {
      const supplierNames = screen.getAllByText('Test Supplier');
      expect(supplierNames.length).toBeGreaterThan(0);
      expect(screen.getByText(/Local supplier/i)).toBeInTheDocument();
    });
  });

  it('displays overview tab content', async () => {
    render(
      <MantineProvider>
        <MemoryRouter>
          <SupplierDetailPage />
        </MemoryRouter>
      </MantineProvider>
    );

    await waitFor(() => {
      expect(screen.getByText('Basic Information')).toBeInTheDocument();
      expect(screen.getByText('Statistics')).toBeInTheDocument();
      expect(screen.getByText('5')).toBeInTheDocument(); // item_count
      expect(screen.getByText('3')).toBeInTheDocument(); // purchase_order_count
      expect(screen.getByText('$1500.00')).toBeInTheDocument(); // total_spent
    });
  });

  it('displays items tab content', async () => {
    render(
      <MantineProvider>
        <MemoryRouter>
          <SupplierDetailPage />
        </MemoryRouter>
      </MantineProvider>
    );

    await waitFor(() => {
      expect(screen.getByText('Items Supplied (1)')).toBeInTheDocument();
    });

    // Click on Items tab
    const itemsTab = screen.getByRole('tab', { name: /items/i });
    fireEvent.click(itemsTab);

    await waitFor(() => {
      expect(screen.getByText('Test Item 1')).toBeInTheDocument();
      expect(screen.getByText('SKU-1')).toBeInTheDocument();
    });
  });

  it('displays lead time analytics tab', async () => {
    render(
      <MantineProvider>
        <MemoryRouter>
          <SupplierDetailPage />
        </MemoryRouter>
      </MantineProvider>
    );

    await waitFor(() => {
      expect(screen.getAllByText('Test Supplier').length).toBeGreaterThan(0);
    });

    const leadTimeTab = screen.getByRole('tab', { name: /lead time/i });
    fireEvent.click(leadTimeTab);

    await waitFor(() => {
      expect(screen.getByText(/LeadTimeChart/i)).toBeInTheDocument();
    });
  });

  it('displays order history tab', async () => {
    render(
      <MantineProvider>
        <MemoryRouter>
          <SupplierDetailPage />
        </MemoryRouter>
      </MantineProvider>
    );

    await waitFor(() => {
      expect(screen.getAllByText('Test Supplier').length).toBeGreaterThan(0);
    });

    const ordersTab = screen.getByRole('tab', { name: /order history/i });
    fireEvent.click(ordersTab);

    await waitFor(() => {
      // "Purchase Orders" appears in both Statistics and Order History tab
      const purchaseOrderTexts = screen.getAllByText(/Purchase Orders/i);
      expect(purchaseOrderTexts.length).toBeGreaterThan(0);
      expect(screen.getByText('PO-001')).toBeInTheDocument();
    }, { timeout: 3000 });
  });

  it('displays price trends tab', async () => {
    render(
      <MantineProvider>
        <MemoryRouter>
          <SupplierDetailPage />
        </MemoryRouter>
      </MantineProvider>
    );

    await waitFor(() => {
      expect(screen.getAllByText('Test Supplier').length).toBeGreaterThan(0);
    });

    const priceTrendsTab = screen.getByRole('tab', { name: /price trends/i });
    fireEvent.click(priceTrendsTab);

    await waitFor(() => {
      expect(screen.getByText(/PriceTrendChart/i)).toBeInTheDocument();
    });
  });

  it('navigates to edit page', async () => {
    render(
      <MantineProvider>
        <MemoryRouter>
          <SupplierDetailPage />
        </MemoryRouter>
      </MantineProvider>
    );

    await waitFor(() => {
      expect(screen.getByText('Edit')).toBeInTheDocument();
    });

    const editButton = screen.getByText('Edit');
    editButton.click();

    expect(mockNavigate).toHaveBeenCalledWith('/inventory/suppliers/1/edit');
  });

  it('displays tax-free badge', async () => {
    render(
      <MantineProvider>
        <MemoryRouter>
          <SupplierDetailPage />
        </MemoryRouter>
      </MantineProvider>
    );

    await waitFor(() => {
      expect(screen.getByText('Tax-Free')).toBeInTheDocument();
    });
  });

  it('handles missing supplier', async () => {
    (api.inventoryAPI.getSupplier as jest.Mock).mockResolvedValue({
      data: null,
    });

    render(
      <MantineProvider>
        <MemoryRouter>
          <SupplierDetailPage />
        </MemoryRouter>
      </MantineProvider>
    );

    await waitFor(() => {
      expect(screen.getByText(/Supplier not found/i)).toBeInTheDocument();
    });
  });
});
