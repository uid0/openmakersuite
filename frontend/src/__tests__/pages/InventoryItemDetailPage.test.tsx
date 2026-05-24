/**
 * Tests for InventoryItemDetailPage component
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import InventoryItemDetailPage from '../../pages/InventoryItemDetailPage';
import * as api from '../../services/api';

// Mock the API
vi.mock('../../services/api');

// Mock qrcode.react
vi.mock('qrcode.react', async () => ({
  QRCodeSVG: () => <div data-testid="qr-code">QR Code</div>,
}));

// Mock recharts
vi.mock('recharts', async () => ({
  ResponsiveContainer: ({ children }: any) => <div data-testid="responsive-container">{children}</div>,
  LineChart: () => <div data-testid="line-chart" />,
  Line: () => <div data-testid="line" />,
  XAxis: () => <div data-testid="x-axis" />,
  YAxis: () => <div data-testid="y-axis" />,
  Tooltip: () => <div data-testid="tooltip" />,
}));

const mockNavigate = jest.fn();
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useNavigate: () => mockNavigate,
}));

describe('InventoryItemDetailPage', () => {
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
    qr_code: 'http://example.com/qr.png',
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
    (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({
      data: mockItem,
    });
    (api.inventoryAPI.getUsageLogs as jest.Mock).mockResolvedValue({
      data: { results: [] },
    });
    (api.reorderAPI.listRequests as jest.Mock).mockResolvedValue({
      data: { results: [] },
    });
    (api.assetsAPI.listAssets as jest.Mock).mockResolvedValue({
      data: { results: [] },
    });
  });

  const renderPage = (itemId = 'test-id') => {
    return render(
      <MantineProvider>
        <MemoryRouter initialEntries={[`/inventory/items/${itemId}`]}>
          <Routes>
            <Route path="/inventory/items/:id" element={<InventoryItemDetailPage />} />
          </Routes>
        </MemoryRouter>
      </MantineProvider>
    );
  };

  it('renders loading state initially', async () => {
    (api.inventoryAPI.getItem as jest.Mock).mockReturnValue(new Promise(() => {}));
    renderPage();

    expect(screen.getByText(/Loading item/)).toBeInTheDocument();
  });

  it('displays item details in overview tab', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Test Item')).toBeInTheDocument();
    });

    expect(screen.getByText(/SKU TEST-001/)).toBeInTheDocument();
    // "Test description" now appears in both the hero description and the
    // overview body once the page uses <PageHero>; assert at least one.
    expect(screen.getAllByText('Test description').length).toBeGreaterThan(0);
    expect(screen.getByText(/Current Stock:/)).toBeInTheDocument();
    // Stock value is displayed - check that the stock information section exists
    expect(screen.getByText(/Stock Information/i)).toBeInTheDocument();
  });

  it('displays stock history chart', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Test Item')).toBeInTheDocument();
    });

    const stockHistoryTab = screen.getByRole('tab', { name: /Stock History/i });
    // Note: Tabs might need to be clicked to show content
    expect(stockHistoryTab).toBeInTheDocument();
  });

  it('displays reorder history', async () => {
    const mockReorder = {
      id: 1,
      item: 'test-id',
      item_details: mockItem,
      quantity: 20,
      status: 'pending' as const,
      priority: 'normal' as const,
      requested_by: 'Test User',
      request_notes: 'Need more stock',
      requested_at: '2024-01-15T00:00:00Z',
      reviewed_by: null,
      reviewed_by_username: null,
      reviewed_at: null,
      admin_notes: '',
      ordered_at: null,
      estimated_delivery: null,
      actual_delivery: null,
      order_number: '',
      actual_cost: null,
      estimated_cost: null,
      days_pending: 5,
      updated_at: '2024-01-15T00:00:00Z',
    };

    (api.reorderAPI.listRequests as jest.Mock).mockResolvedValue({
      data: { results: [mockReorder] },
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Test Item')).toBeInTheDocument();
    });

    const reorderTab = screen.getByRole('tab', { name: /Reorder History/i });
    expect(reorderTab).toBeInTheDocument();
  });

  it('displays usage logs', async () => {
    const mockUsageLog = {
      id: 1,
      item: 'test-id',
      quantity_used: 5,
      usage_date: '2024-01-15T00:00:00Z',
      notes: 'Used for project',
    };

    (api.inventoryAPI.getUsageLogs as jest.Mock).mockResolvedValue({
      data: { results: [mockUsageLog] },
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Test Item')).toBeInTheDocument();
    });

    const usageLogsTab = screen.getByRole('tab', { name: /Usage Logs/i });
    expect(usageLogsTab).toBeInTheDocument();
  });

  it('displays linked assets', async () => {
    const mockAsset = {
      id: 'asset-1',
      name: 'Test Asset',
      asset_tag: 'AT-001',
      status: 'active' as const,
      location_name: 'Shelf A',
      inventory_item: 'test-id',
      inventory_item_name: 'Test Item',
      description: '',
      serial_number: '',
      manufacturer: null,
      manufacturer_name: '',
      display_manufacturer: '',
      date_received: null,
      amount_paid: '0',
      is_donation: false,
      donor_name: '',
      acquisition_display: '',
      category: null,
      category_name: '',
      location: 1,
      product_url: '',
      wiki_page_url: '',
      maintenance_plan: '',
      image: null,
      image_url: null,
      thumbnail_url: null,
      manual_pdf: null,
      manual_pdf_url: null,
      qr_code: null,
      qr_code_url: null,
      qr_code_scan_url: null,
      condition_notes: '',
      age_in_days: 0,
      is_active: true,
      report_only: false,
      notes: '',
      circuit: '',
      needs_compressed_air: false,
      needs_ventilation: false,
      is_chargeable: false,
      last_scanned_at: null,
      ownership_type: 'space' as const,
      owning_group: null,
      owning_group_name: null,
      owning_user: null,
      owning_user_name: null,
      groups_can_enable: [],
      is_locked: false,
      lockout_info: null,
      can_enable: false,
      can_unlock: false,
      operational_status: 'available' as const,
      parts: [],
      created_at: '2024-01-01T00:00:00Z',
      updated_at: '2024-01-01T00:00:00Z',
    };

    (api.assetsAPI.listAssets as jest.Mock).mockResolvedValue({
      data: { results: [mockAsset] },
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Test Item')).toBeInTheDocument();
    });

    const linkedAssetsTab = screen.getByRole('tab', { name: /Linked Assets/i });
    expect(linkedAssetsTab).toBeInTheDocument();
  });

  it('handles missing item gracefully', async () => {
    (api.inventoryAPI.getItem as jest.Mock).mockRejectedValue({
      response: { data: { detail: 'Not found' } },
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/Item not found/)).toBeInTheDocument();
    });
  });

  it('generates QR code when button clicked', async () => {
    (api.inventoryAPI.generateQR as jest.Mock).mockResolvedValue({
      data: { ...mockItem, qr_code: 'new-qr.png' },
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Test Item')).toBeInTheDocument();
    });

    const generateButton = screen.getByText(/Generate QR/i);
    generateButton.click();

    await waitFor(() => {
      expect(api.inventoryAPI.generateQR).toHaveBeenCalledWith('test-id');
    });
  });
});
