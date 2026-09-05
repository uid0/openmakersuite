/**
 * The item page for a visitor with no session (op-anonymous-read-posture).
 *
 * `/inventory/items/:id` is not behind `RequireAuth` and `retrieve` stays
 * `AllowAny` — the QR-scan flow runs through here — so the server keeps serving
 * the page and withholds the vendor keys instead, marking the payload
 * `vendor_data_withheld: true`.
 *
 * WHY THIS FILE EXISTS RATHER THAN AN ASSERTION ADDED TO THE MAIN SUITE. The
 * keys are ABSENT, not null, and the page's guards were spelled `=== null`.
 * `undefined === null` is false, so `item.unit_cost.toFixed(2)` ran on
 * `undefined` and the page rendered nothing at all for a logged-out visitor.
 * A whole-page render on the withheld payload is what catches that class; a
 * field assertion on a fixture that still has the key does not.
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { NotificationProvider } from '../../contexts/NotificationContext';
import InventoryItemDetailPage from '../../pages/InventoryItemDetailPage';
import * as api from '../../services/api';

vi.mock('../../services/api');
vi.mock('../../utils/dialogs', async () => ({ showError: vi.fn() }));
vi.mock('qrcode.react', async () => ({
  QRCodeSVG: () => <div data-testid="qr-code">QR Code</div>,
}));
vi.mock('recharts', async () => ({
  ResponsiveContainer: ({ children }: any) => <div>{children}</div>,
  LineChart: () => <div />,
  Line: () => <div />,
  XAxis: () => <div />,
  YAxis: () => <div />,
  Tooltip: () => <div />,
}));

/**
 * Exactly what the server sends a caller with no session: every item key, and
 * NONE of `supplier_name`, `supplier_sku`, `supplier_url`, `unit_cost`,
 * `package_cost`, `average_lead_time`, `suppliers`, `supplier_choice` or
 * `total_value`. Written as omissions rather than nulls on purpose — a fixture
 * that nulled them would pass while the real payload crashed the page.
 */
const withheldItem = {
  id: 'anon-item',
  name: 'Shelf Filament',
  description: 'A publicly scannable item',
  sku: 'PUB-1',
  category: 1,
  category_name: 'Tools',
  location: 'Shelf A',
  current_stock: 10,
  minimum_stock: 5,
  reorder_quantity: 20,
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
  notes: '',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
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
  vendor_data_withheld: true,
};

/** The metrics row for the same caller: the shelf half, and the marker. */
const withheldMetrics = {
  current_stock: 10,
  quantity_on_order: 0,
  quantity_available: 10,
  quantity_committed: 0,
  committed_breakdown: [],
  quantity_in_transit: 0,
  reorder_point: 20,
  is_case_based: false,
  case_size: null,
  vendor_data_withheld: true,
};

const renderPage = () =>
  render(
    <MantineProvider>
      <NotificationProvider>
        <MemoryRouter initialEntries={['/inventory/items/anon-item']}>
          <Routes>
            <Route path="/inventory/items/:id" element={<InventoryItemDetailPage />} />
          </Routes>
        </MemoryRouter>
      </NotificationProvider>
    </MantineProvider>,
  );

describe('InventoryItemDetailPage — vendor data withheld', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.removeItem('token');
    (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({ data: withheldItem });
    (api.inventoryAPI.getItemMetrics as jest.Mock).mockResolvedValue({ data: withheldMetrics });
    (api.inventoryAPI.getUsageLogs as jest.Mock).mockResolvedValue({ data: { results: [] } });
    (api.reorderAPI.listRequests as jest.Mock).mockResolvedValue({ data: { results: [] } });
    (api.assetsAPI.listAssets as jest.Mock).mockResolvedValue({ data: { results: [] } });
    (api.inventoryAPI.getPurchaseHistory as jest.Mock).mockResolvedValue({
      data: { order_costs: [], deliveries: [] },
    });
    (api.inventoryAPI.getItemKits as jest.Mock)?.mockResolvedValue?.({ data: [] });
  });

  it('still renders the item a logged-out visitor came to look at', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Shelf Filament')).toBeInTheDocument();
    });
    // The reason the endpoint stays open at all: identify it, see the stock.
    expect(screen.getByText(/SKU PUB-1/)).toBeInTheDocument();
    expect(screen.getByText(/Current Stock:/)).toBeInTheDocument();
  });

  it('says the price is withheld rather than claiming none is on file', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('unit-cost-withheld')).toBeInTheDocument();
    });
    // "no price on file" is a claim about the ITEM. Saying it here would tell a
    // visitor something false about the makerspace's records.
    expect(screen.queryByText('no price on file')).not.toBeInTheDocument();
  });

  it('names no supplier anywhere on the page', async () => {
    const { container } = renderPage();

    await waitFor(() => {
      expect(screen.getByText('Shelf Filament')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('item-suppliers-card')).not.toBeInTheDocument();
  });
});
