/**
 * Tests for the Cycle Count feature on InventoryItemDetailPage (op-c7y4):
 * the days-since-last-count display and the CycleCountModal (renders +
 * submits through inventoryAPI.cycleCount).
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { NotificationProvider } from '../../contexts/NotificationContext';
import InventoryItemDetailPage from '../../pages/InventoryItemDetailPage';
import * as api from '../../services/api';

vi.mock('../../services/api');

vi.mock('../../utils/dialogs', async () => ({
  showError: vi.fn(),
}));

vi.mock('qrcode.react', async () => ({
  QRCodeSVG: () => <div data-testid="qr-code">QR Code</div>,
}));

vi.mock('recharts', async () => ({
  ResponsiveContainer: ({ children }: any) => <div>{children}</div>,
  LineChart: () => <div data-testid="line-chart" />,
  Line: () => <div />,
  XAxis: () => <div />,
  YAxis: () => <div />,
  Tooltip: () => <div />,
}));

const mockNavigate = jest.fn();
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useNavigate: () => mockNavigate,
}));

const makeItem = (overrides: Record<string, unknown> = {}) => ({
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
  notes: '',
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
  ...overrides,
});

const cycleCountResponse = {
  data: {
    id: 'test-id',
    current_stock: 8,
    last_counted_at: '2026-07-07T00:00:00Z',
    days_since_last_count: 0,
    reconciliation: {
      id: 1,
      projected_count: 10,
      actual_count: 8,
      delta: -2,
      reason: 'miscounted',
      reconciled_at: '2026-07-07T00:00:00Z',
      reconciled_by: 1,
    },
  },
};

const setupItem = (item: Record<string, unknown>) => {
  (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({ data: item });
  (api.inventoryAPI.getUsageLogs as jest.Mock).mockResolvedValue({ data: { results: [] } });
  (api.reorderAPI.listRequests as jest.Mock).mockResolvedValue({ data: { results: [] } });
  (api.assetsAPI.listAssets as jest.Mock).mockResolvedValue({ data: { results: [] } });
};

const renderPage = () =>
  render(
    <MantineProvider>
      <NotificationProvider>
        <MemoryRouter initialEntries={['/inventory/items/test-id']}>
          <Routes>
            <Route path="/inventory/items/:id" element={<InventoryItemDetailPage />} />
          </Routes>
        </MemoryRouter>
      </NotificationProvider>
    </MantineProvider>
  );

describe('InventoryItemDetailPage — cycle count', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('shows "Never counted" when the item has never been cycle-counted', async () => {
    setupItem(makeItem({ last_counted_at: null, days_since_last_count: null }));
    renderPage();

    await waitFor(() => expect(screen.getByText('Test Item')).toBeInTheDocument());
    expect(screen.getByTestId('days-since-count')).toHaveTextContent('Never counted');
  });

  it('shows days-since-last-count when the item has been counted', async () => {
    setupItem(
      makeItem({ last_counted_at: '2026-07-04T00:00:00Z', days_since_last_count: 3 })
    );
    renderPage();

    await waitFor(() => expect(screen.getByText('Test Item')).toBeInTheDocument());
    expect(screen.getByTestId('days-since-count')).toHaveTextContent('3 days ago');
  });

  it('opens the Cycle Count modal with its form controls', async () => {
    setupItem(makeItem());
    renderPage();

    await waitFor(() => expect(screen.getByText('Test Item')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('cycle-count-button'));

    expect(await screen.findByTestId('cycle-count-submit')).toBeInTheDocument();
    expect(screen.getByLabelText(/Counted quantity/i)).toBeInTheDocument();
    expect(screen.getByTestId('cycle-count-reason')).toBeInTheDocument();
    expect(screen.getByTestId('cycle-count-notes')).toBeInTheDocument();
    expect(screen.getByTestId('cycle-count-skip-reorder')).toBeInTheDocument();
  });

  it('submits the cycle count and reloads the item', async () => {
    setupItem(makeItem());
    (api.inventoryAPI.cycleCount as jest.Mock).mockResolvedValue(cycleCountResponse);
    renderPage();

    await waitFor(() => expect(screen.getByText('Test Item')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('cycle-count-button'));
    fireEvent.click(await screen.findByTestId('cycle-count-submit'));

    await waitFor(() => expect(api.inventoryAPI.cycleCount).toHaveBeenCalledTimes(1));
    expect(api.inventoryAPI.cycleCount).toHaveBeenCalledWith(
      'test-id',
      expect.objectContaining({
        counted_qty: 10, // seeded from current_stock
        reason: 'miscounted',
        skip_reorder: false,
      })
    );
    // The item is reloaded after a successful count (initial load + reload).
    await waitFor(() =>
      expect((api.inventoryAPI.getItem as jest.Mock).mock.calls.length).toBeGreaterThanOrEqual(2)
    );
  });

  it('sends the operator-entered counted quantity', async () => {
    setupItem(makeItem());
    (api.inventoryAPI.cycleCount as jest.Mock).mockResolvedValue(cycleCountResponse);
    renderPage();

    await waitFor(() => expect(screen.getByText('Test Item')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('cycle-count-button'));
    await screen.findByTestId('cycle-count-submit');

    fireEvent.change(screen.getByLabelText(/Counted quantity/i), { target: { value: '8' } });
    fireEvent.click(screen.getByTestId('cycle-count-submit'));

    await waitFor(() =>
      expect(api.inventoryAPI.cycleCount).toHaveBeenCalledWith(
        'test-id',
        expect.objectContaining({ counted_qty: 8 })
      )
    );
  });
});
