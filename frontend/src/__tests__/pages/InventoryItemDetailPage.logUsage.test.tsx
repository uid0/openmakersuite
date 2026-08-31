/**
 * Tests for the "Use / Log Usage" feature on InventoryItemDetailPage (op-27wa):
 * the LogUsageModal renders its controls, shows a projected committee charge,
 * and submits through inventoryAPI.logUsage with an optional charged_group.
 *
 * MantineProvider is rendered with env="test" so the committee Select's
 * dropdown options are queryable in jsdom (Mantine 9.4 Combobox otherwise
 * keeps the dropdown display:none).
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
  // A NUMBER on the wire; the usage-log record below is a real
  // `DecimalField` and stays a string (op-9m2v).
  unit_cost: 15.99,
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

const sigs = [
  { id: 3, name: 'Woodshop' },
  { id: 5, name: 'Metal Shop' },
];

const logUsageResponse = {
  data: {
    id: 10,
    item: 'test-id',
    quantity_used: 2,
    usage_date: '2026-07-18T00:00:00Z',
    notes: '',
    charged_group: 3,
    unit_cost: '15.99',
    total_cost: '31.98',
    ledger_transaction: 99,
    warning: undefined,
  },
};

const setupItem = (item: Record<string, unknown>) => {
  (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({ data: item });
  (api.inventoryAPI.getItemMetrics as jest.Mock).mockResolvedValue({ data: null });
  (api.inventoryAPI.getUsageLogs as jest.Mock).mockResolvedValue({ data: { results: [] } });
  (api.reorderAPI.listRequests as jest.Mock).mockResolvedValue({ data: { results: [] } });
  (api.assetsAPI.listAssets as jest.Mock).mockResolvedValue({ data: { results: [] } });
  (api.sigAPI.listMySIGs as jest.Mock).mockResolvedValue({ data: { results: sigs } });
};

const renderPage = () =>
  render(
    <MantineProvider env="test">
      <NotificationProvider>
        <MemoryRouter initialEntries={['/inventory/items/test-id']}>
          <Routes>
            <Route path="/inventory/items/:id" element={<InventoryItemDetailPage />} />
          </Routes>
        </MemoryRouter>
      </NotificationProvider>
    </MantineProvider>
  );

const openModal = async () => {
  await waitFor(() => expect(screen.getByText('Test Item')).toBeInTheDocument());
  fireEvent.click(screen.getByTestId('log-usage-button'));
  await screen.findByTestId('log-usage-submit');
};

describe('InventoryItemDetailPage — log usage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('opens the Use / Log Usage modal with its form controls', async () => {
    setupItem(makeItem());
    renderPage();
    await openModal();

    expect(screen.getByTestId('log-usage-qty')).toBeInTheDocument();
    expect(screen.getByTestId('log-usage-committee')).toBeInTheDocument();
    expect(screen.getByTestId('log-usage-notes')).toBeInTheDocument();
  });

  it('logs usage without a committee charge', async () => {
    setupItem(makeItem());
    (api.inventoryAPI.logUsage as jest.Mock).mockResolvedValue({
      data: { ...logUsageResponse.data, charged_group: null, total_cost: null, ledger_transaction: null },
    });
    renderPage();
    await openModal();

    fireEvent.change(screen.getByTestId('log-usage-qty'), { target: { value: '2' } });
    fireEvent.click(screen.getByTestId('log-usage-submit'));

    await waitFor(() => expect(api.inventoryAPI.logUsage).toHaveBeenCalledTimes(1));
    expect(api.inventoryAPI.logUsage).toHaveBeenCalledWith(
      'test-id',
      expect.objectContaining({ quantity: 2 })
    );
    // No committee selected → charged_group left off the payload.
    const [, body] = (api.inventoryAPI.logUsage as jest.Mock).mock.calls[0];
    expect(body.charged_group).toBeUndefined();
    // The item is reloaded after a successful log (initial load + reload).
    await waitFor(() =>
      expect((api.inventoryAPI.getItem as jest.Mock).mock.calls.length).toBeGreaterThanOrEqual(2)
    );
  });

  it('shows the projected charge and submits charged_group when a committee is picked', async () => {
    setupItem(makeItem());
    (api.inventoryAPI.logUsage as jest.Mock).mockResolvedValue(logUsageResponse);
    renderPage();
    await openModal();

    fireEvent.change(screen.getByTestId('log-usage-qty'), { target: { value: '2' } });

    // Open the committee Select and pick Woodshop (id 3).
    fireEvent.click(await screen.findByPlaceholderText('Select a committee (optional)'));
    fireEvent.click(await screen.findByRole('option', { name: 'Woodshop' }));

    // Projected charge = unit_cost 15.99 × qty 2 = 31.98.
    await waitFor(() =>
      expect(screen.getByTestId('log-usage-projected')).toHaveTextContent('31.98')
    );

    fireEvent.click(screen.getByTestId('log-usage-submit'));

    await waitFor(() =>
      expect(api.inventoryAPI.logUsage).toHaveBeenCalledWith(
        'test-id',
        expect.objectContaining({ quantity: 2, charged_group: 3 })
      )
    );
  });

  it('hints that nothing is charged when the item has no unit cost', async () => {
    setupItem(makeItem({ unit_cost: null }));
    renderPage();
    await openModal();

    fireEvent.click(await screen.findByPlaceholderText('Select a committee (optional)'));
    fireEvent.click(await screen.findByRole('option', { name: 'Woodshop' }));

    expect(await screen.findByTestId('log-usage-no-cost')).toBeInTheDocument();
    expect(screen.queryByTestId('log-usage-projected')).not.toBeInTheDocument();
  });

  it('surfaces a 403 as an inline permission error', async () => {
    setupItem(makeItem());
    (api.inventoryAPI.logUsage as jest.Mock).mockRejectedValue({ response: { status: 403 } });
    renderPage();
    await openModal();

    fireEvent.click(await screen.findByPlaceholderText('Select a committee (optional)'));
    fireEvent.click(await screen.findByRole('option', { name: 'Woodshop' }));
    fireEvent.click(screen.getByTestId('log-usage-submit'));

    expect(await screen.findByTestId('log-usage-error')).toHaveTextContent(/permission/i);
  });
});
