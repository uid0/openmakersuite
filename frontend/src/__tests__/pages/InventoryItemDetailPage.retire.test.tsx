/**
 * Tests for the Retire / Unretire action on InventoryItemDetailPage (op-jv7r):
 * the hero button (label + icon toggle by is_retired, calls the retire/unretire
 * API then reloads) and the Retired status badge.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { NotificationProvider } from '../../contexts/NotificationContext';
import InventoryItemDetailPage from '../../pages/InventoryItemDetailPage';
import * as api from '../../services/api';
import { showError } from '../../utils/dialogs';

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
  unit_cost: 15.99,
  supplier_name: 'Test Supplier',
  needs_reorder: false,
  has_pending_reorder: false,
  is_active: true,
  is_retired: false,
  retired_at: null,
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

const setupItem = (item: Record<string, unknown>) => {
  (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({ data: item });
  (api.inventoryAPI.getItemMetrics as jest.Mock).mockResolvedValue({ data: null });
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

describe('InventoryItemDetailPage — retire / unretire', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('shows a "Retire" button (not the badge) for an active item', async () => {
    setupItem(makeItem({ is_retired: false }));
    renderPage();

    await waitFor(() => expect(screen.getByText('Test Item')).toBeInTheDocument());
    expect(screen.getByTestId('retire-button')).toHaveTextContent('Retire');
    // No Retired status badge for a non-retired item.
    expect(screen.queryByText('Retired')).not.toBeInTheDocument();
  });

  it('retires an active item and reloads', async () => {
    setupItem(makeItem({ is_retired: false }));
    (api.inventoryAPI.retireItem as jest.Mock).mockResolvedValue({
      data: makeItem({ is_retired: true, retired_at: '2026-07-10T00:00:00Z' }),
    });
    renderPage();

    await waitFor(() => expect(screen.getByText('Test Item')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('retire-button'));

    await waitFor(() => expect(api.inventoryAPI.retireItem).toHaveBeenCalledWith('test-id'));
    expect(api.inventoryAPI.unretireItem).not.toHaveBeenCalled();
    // The item is reloaded after retiring (initial load + reload).
    await waitFor(() =>
      expect((api.inventoryAPI.getItem as jest.Mock).mock.calls.length).toBeGreaterThanOrEqual(2)
    );
  });

  it('shows the Retired badge + an "Unretire" button for a retired item', async () => {
    setupItem(makeItem({ is_retired: true, retired_at: '2026-07-10T00:00:00Z' }));
    renderPage();

    await waitFor(() => expect(screen.getByText('Test Item')).toBeInTheDocument());
    expect(screen.getByText('Retired')).toBeInTheDocument();
    expect(screen.getByTestId('retire-button')).toHaveTextContent('Unretire');
  });

  it('unretires a retired item and reloads', async () => {
    setupItem(makeItem({ is_retired: true, retired_at: '2026-07-10T00:00:00Z' }));
    (api.inventoryAPI.unretireItem as jest.Mock).mockResolvedValue({
      data: makeItem({ is_retired: false }),
    });
    renderPage();

    await waitFor(() => expect(screen.getByText('Test Item')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('retire-button'));

    await waitFor(() => expect(api.inventoryAPI.unretireItem).toHaveBeenCalledWith('test-id'));
    expect(api.inventoryAPI.retireItem).not.toHaveBeenCalled();
  });

  it('surfaces an error when the retire action fails', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    setupItem(makeItem({ is_retired: false }));
    (api.inventoryAPI.retireItem as jest.Mock).mockRejectedValue(new Error('boom'));
    renderPage();

    await waitFor(() => expect(screen.getByText('Test Item')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('retire-button'));

    await waitFor(() =>
      expect(showError).toHaveBeenCalledWith('Failed to update retirement status. Please try again.')
    );
    consoleError.mockRestore();
  });
});
