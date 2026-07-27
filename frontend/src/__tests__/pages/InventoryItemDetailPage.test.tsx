/**
 * Tests for InventoryItemDetailPage component
 */
import { MantineProvider } from '@mantine/core';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import SessionExpiredBanner, {
  consumePendingReturnTo,
} from '../../components/SessionExpiredBanner';
import { NotificationProvider } from '../../contexts/NotificationContext';
import InventoryItemDetailPage from '../../pages/InventoryItemDetailPage';
import * as api from '../../services/api';
import { showError } from '../../utils/dialogs';

// Mock the API
vi.mock('../../services/api');

vi.mock('../../utils/dialogs', async () => ({
  showError: vi.fn(),
}));

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
    last_counted_at: null,
    days_since_last_count: null,
  };

  const mockMetrics = {
    current_stock: 10,
    quantity_on_order: 20,
    quantity_available: 8,
    quantity_committed: 2,
    committed_breakdown: [],
    quantity_in_transit: 5,
    reorder_point: 20,
    lead_time_days: 7,
    unit_cost: '15.99',
    cost_trend: 'flat' as const,
    last_po_unit_cost: '15.99',
    is_case_based: false,
    case_size: null,
  };

  beforeEach(() => {
    jest.clearAllMocks();
    (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({
      data: mockItem,
    });
    (api.inventoryAPI.getItemMetrics as jest.Mock).mockResolvedValue({
      data: mockMetrics,
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
    (api.inventoryAPI.getPurchaseHistory as jest.Mock).mockResolvedValue({
      data: { order_costs: [], deliveries: [] },
    });
  });

  const renderPage = (itemId = 'test-id') => {
    return render(
      <MantineProvider>
        <NotificationProvider>
          <MemoryRouter initialEntries={[`/inventory/items/${itemId}`]}>
            <Routes>
              <Route path="/inventory/items/:id" element={<InventoryItemDetailPage />} />
            </Routes>
          </MemoryRouter>
        </NotificationProvider>
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

  it('always offers a Serialized Units tab and guides enabling tracking when the item is not serialized', async () => {
    // mockItem has no is_serialized flag, so the item is not serialized.
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Test Item')).toBeInTheDocument();
    });

    // The tab is present even for a non-serialized item — no dead end.
    const serializedTab = screen.getByRole('tab', { name: /Serialized Units/i });
    expect(serializedTab).toBeInTheDocument();

    fireEvent.click(serializedTab);

    // Instead of an empty panel, the user sees why and a way forward.
    expect(await screen.findByTestId('serialized-not-tracked')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('serialized-enable-tracking'));
    expect(mockNavigate).toHaveBeenCalledWith('/inventory/items/test-id/edit');
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

  // ---- #457 R3 resilience ----

  it('renders empty-state copy for each history tab when there is no activity', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Test Item')).toBeInTheDocument();
    });

    // Each history tab shows its own empty-state copy once activated.
    fireEvent.click(screen.getByRole('tab', { name: /Reorder History/i }));
    expect(
      await screen.findByText('No reorder history available.')
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole('tab', { name: /Usage Logs/i }));
    expect(await screen.findByText('No usage logs available.')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('tab', { name: /Linked Assets/i }));
    expect(
      await screen.findByText('No assets use this item as a part.')
    ).toBeInTheDocument();
    expect(
      screen.getByText('No assets are an instance of this inventory item.')
    ).toBeInTheDocument();
  });

  // ---- op-qdfr: assets that USE this item as a part (AssetPart) ----

  it('requests the assets that use this item as a part and renders them with AssetPart detail', async () => {
    const consumingAsset = {
      id: 'asset-consumer',
      name: 'Laser Cutter',
      asset_tag: 'AT-LASER',
      status: 'active' as const,
      location_name: 'Fab Lab',
      inventory_item: null,
      parts: [
        {
          id: 'part-link-1',
          asset: 'asset-consumer',
          part: 'test-id',
          part_name: 'Test Item',
          quantity_needed: 3,
          is_required: true,
          maintenance_interval_days: 90,
          last_replaced_at: null,
        },
      ],
    };

    (api.assetsAPI.listAssets as jest.Mock).mockImplementation((params: any) =>
      Promise.resolve({
        data: { results: params?.consumable_for_item ? [consumingAsset] : [] },
      })
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Test Item')).toBeInTheDocument();
    });

    // The page asks for the AssetPart relationship, not just inventory_item.
    expect(api.assetsAPI.listAssets).toHaveBeenCalledWith({
      consumable_for_item: 'test-id',
    });
    expect(api.assetsAPI.listAssets).toHaveBeenCalledWith({ inventory_item: 'test-id' });

    fireEvent.click(screen.getByRole('tab', { name: /Linked Assets/i }));

    // The two relationships are labeled distinctly so they aren't conflated.
    const usingSection = await screen.findByTestId('assets-using-item');
    expect(usingSection).toHaveTextContent('Assets that use this item');
    expect(screen.getByTestId('assets-of-this-type')).toHaveTextContent(
      'Assets of this type'
    );

    // The consuming asset lands in the "uses this item" section, enriched with
    // the through-model detail — and not in the "is this item" section.
    expect(usingSection).toHaveTextContent('Laser Cutter');
    expect(usingSection).toHaveTextContent('AT-LASER');
    expect(usingSection).toHaveTextContent('3');
    expect(usingSection).toHaveTextContent('Required');
    expect(screen.getByTestId('assets-of-this-type')).toHaveTextContent(
      'No assets are an instance of this inventory item.'
    );
  });

  it('still renders the item when the used-by-assets call fails', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    (api.assetsAPI.listAssets as jest.Mock).mockImplementation((params: any) =>
      params?.consumable_for_item
        ? Promise.reject(new Error('boom'))
        : Promise.resolve({ data: { results: [] } })
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Test Item')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('tab', { name: /Linked Assets/i }));
    expect(
      await screen.findByText('No assets use this item as a part.')
    ).toBeInTheDocument();
    consoleError.mockRestore();
  });

  it('renders the not-found state when the item load is forbidden (403)', async () => {
    (api.inventoryAPI.getItem as jest.Mock).mockRejectedValue({
      response: { status: 403, data: { detail: 'You do not have permission.' } },
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/Item not found/)).toBeInTheDocument();
    });
  });

  it('shows an error when QR generation fails and keeps the item view intact', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    (api.inventoryAPI.generateQR as jest.Mock).mockRejectedValue(new Error('boom'));

    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Test Item')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText(/Generate QR/i));

    await waitFor(() => {
      expect(showError).toHaveBeenCalledWith(
        'Failed to generate QR code. Please try again.'
      );
    });
    // The page still shows the item — a failed action is not a crash.
    expect(screen.getByText('Test Item')).toBeInTheDocument();
    consoleError.mockRestore();
  });

  it('shows the re-login banner with a return path on session expiry, keeping the item', async () => {
    sessionStorage.clear();
    render(
      <MantineProvider>
        <NotificationProvider>
          <MemoryRouter initialEntries={['/inventory/items/test-id']}>
            <SessionExpiredBanner />
            <Routes>
              <Route path="/inventory/items/:id" element={<InventoryItemDetailPage />} />
            </Routes>
          </MemoryRouter>
        </NotificationProvider>
      </MantineProvider>
    );

    await waitFor(() => {
      expect(screen.getByText('Test Item')).toBeInTheDocument();
    });

    // The API client raises this on a 401 once token refresh fails.
    act(() => {
      window.dispatchEvent(
        new CustomEvent('oms:session-expired', {
          detail: { pathname: '/inventory/items/test-id' },
        })
      );
    });

    expect(screen.getByTestId('session-expired-banner')).toBeInTheDocument();
    expect(screen.getByText(/where you left off/i)).toBeInTheDocument();
    expect(consumePendingReturnTo()).toBe('/inventory/items/test-id');
    expect(screen.getByText('Test Item')).toBeInTheDocument();
  });

  // ---- issue-5 metrics strip ----

  it('renders the computed metrics strip near the top of the detail view', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Test Item')).toBeInTheDocument();
    });

    const row = await screen.findByTestId('inventory-metrics-row');
    expect(row).toBeInTheDocument();
    // A couple of computed values from the mocked /metrics/ payload.
    expect(screen.getByTestId('metric-qoo')).toHaveTextContent('20');
    expect(screen.getByTestId('metric-qit')).toHaveTextContent('5');
  });

  it('still renders the item when the metrics call fails (graceful degradation)', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    (api.inventoryAPI.getItemMetrics as jest.Mock).mockRejectedValue(new Error('boom'));

    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Test Item')).toBeInTheDocument();
    });

    // The item view is intact; only the metrics strip is absent.
    expect(screen.queryByTestId('inventory-metrics-row')).not.toBeInTheDocument();
    consoleError.mockRestore();
  });

  // ---- op-u9ap: Purchase / Receipts tab (#969) ----

  it('loads the purchase history and renders order costs plus deliveries grouped by order', async () => {
    (api.inventoryAPI.getPurchaseHistory as jest.Mock).mockResolvedValue({
      data: {
        order_costs: [
          {
            purchase_order: 7,
            po_number: 'PO-0007',
            order_date: '2026-01-05T10:00:00Z',
            status: 'received',
            quantity_ordered: 12,
            unit_cost_ordered: '4.5000',
            unit_cost_actual: '4.7500',
          },
        ],
        deliveries: [
          {
            purchase_order: 7,
            po_number: 'PO-0007',
            delivery_date: '2026-01-10T10:00:00Z',
            tracking_number: '1Z-FIRST',
            carrier: 'UPS',
            quantity_received: 8,
            receipt_notes: 'Short shipped',
            is_complete: false,
          },
          {
            purchase_order: 7,
            po_number: 'PO-0007',
            delivery_date: '2026-01-17T10:00:00Z',
            tracking_number: '1Z-SECOND',
            carrier: 'UPS',
            quantity_received: 4,
            receipt_notes: '',
            is_complete: true,
          },
        ],
      },
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Test Item')).toBeInTheDocument();
    });
    expect(api.inventoryAPI.getPurchaseHistory).toHaveBeenCalledWith('test-id');

    fireEvent.click(screen.getByRole('tab', { name: /Purchase \/ Receipts/i }));

    const costs = await screen.findByTestId('order-cost-history');
    expect(costs).toHaveTextContent('PO-0007');
    expect(costs).toHaveTextContent('$4.50');
    expect(costs).toHaveTextContent('$4.75');

    // One order, two tracking numbers — the whole point of grouping by PO.
    const group = screen.getByTestId('delivery-group-7');
    expect(group).toHaveTextContent('2 deliveries');
    expect(group).toHaveTextContent('1Z-FIRST');
    expect(group).toHaveTextContent('1Z-SECOND');
    expect(group).toHaveTextContent('Short shipped');
  });

  it('shows the purchase/receipts empty states when the item has no orders', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Test Item')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('tab', { name: /Purchase \/ Receipts/i }));

    expect(await screen.findByText('This item has never been ordered.')).toBeInTheDocument();
    expect(screen.getByText('No deliveries recorded for this item.')).toBeInTheDocument();
  });

  it('still renders the item when the purchase-history call fails', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    (api.inventoryAPI.getPurchaseHistory as jest.Mock).mockRejectedValue(new Error('boom'));

    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Test Item')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('tab', { name: /Purchase \/ Receipts/i }));
    expect(await screen.findByText('This item has never been ordered.')).toBeInTheDocument();
    consoleError.mockRestore();
  });

  // ---- op-u9ap: committed-quantity attribution (#970) ----

  it('attributes the committed quantity to its work orders under the metrics row', async () => {
    (api.inventoryAPI.getItemMetrics as jest.Mock).mockResolvedValue({
      data: {
        ...mockMetrics,
        quantity_committed: 4,
        committed_breakdown: [
          {
            work_order_id: 'wo-1',
            work_order_short_id: 'WO-1A2B3C4D',
            asset_id: 'asset-1',
            asset_name: 'Laser Cutter',
            quantity: 3,
          },
          {
            work_order_id: 'wo-2',
            work_order_short_id: 'WO-9F8E7D6C',
            asset_id: null,
            asset_name: null,
            quantity: 1,
          },
        ],
      },
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Test Item')).toBeInTheDocument();
    });

    const breakdown = await screen.findByTestId('committed-breakdown');
    expect(breakdown).toHaveTextContent('Committed to');
    // The rows sum to the QC cell shown in the strip above them.
    expect(screen.getByTestId('metric-qc')).toHaveTextContent('4');
    expect(breakdown).toHaveTextContent('WO-1A2B3C4D');
    expect(breakdown).toHaveTextContent('Laser Cutter');
    expect(breakdown).toHaveTextContent('WO-9F8E7D6C');
    expect(breakdown).toHaveTextContent('No asset');
  });

  it('explains an empty committed breakdown rather than hiding it', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Test Item')).toBeInTheDocument();
    });

    expect(await screen.findByTestId('committed-breakdown-empty')).toHaveTextContent(
      'Nothing is committed to an open work order.'
    );
  });
});
