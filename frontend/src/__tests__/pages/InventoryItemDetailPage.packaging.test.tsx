/**
 * Tests for the packaging matrix on InventoryItemDetailPage (op-lkxl, phase 3):
 * on-hand rendered at the item's counting granularity, the pack chain, counting
 * at the level (`at_level`), and the open/finish pack transitions.
 *
 * The invariant asserted alongside them: an each-mode item reads and posts in
 * base units exactly as it did before the matrix existed — no `at_level`, no
 * pack controls.
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

const PAPER_CHAIN = [
  { id: 11, name: 'case', sort_order: 0, base_units: 1000, per_parent: 10 },
  { id: 12, name: 'ream', sort_order: 1, base_units: 100, per_parent: 100 },
  { id: 13, name: 'sheet', sort_order: 2, base_units: 1, per_parent: null },
];

const makeItem = (overrides: Record<string, unknown> = {}) => ({
  id: 'test-id',
  name: 'Copy paper',
  description: '',
  sku: 'PAPER-001',
  category: 1,
  category_name: 'Supplies',
  location: 'Shelf A',
  current_stock: 450,
  minimum_stock: 2,
  reorder_quantity: 4,
  unit_cost: '0.02',
  supplier_name: '',
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
  total_value: '9.00',
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
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
  base_unit: 'unit',
  count_mode: 'each' as const,
  count_level: null,
  open_container_count: 0,
  packaging_levels: [],
  ...overrides,
});

/** A by_level paper item: 450 sheets = 4 whole reams. */
const byLevelItem = (overrides: Record<string, unknown> = {}) =>
  makeItem({
    base_unit: 'sheet',
    count_mode: 'by_level',
    count_level: 12,
    packaging_levels: PAPER_CHAIN,
    on_hand_display: {
      mode: 'by_level',
      level: 'ream',
      level_count: 4,
      remainder_base: 50,
      text: '4 ream(s)',
    },
    ...overrides,
  });

/** An open_closed glove item: 3 sealed boxes plus one opened. */
const openClosedItem = (overrides: Record<string, unknown> = {}) =>
  makeItem({
    name: 'Nitrile gloves',
    base_unit: 'glove',
    current_stock: 300,
    open_container_count: 1,
    count_mode: 'open_closed',
    count_level: 21,
    packaging_levels: [
      { id: 21, name: 'box', sort_order: 0, base_units: 100, per_parent: 100 },
      { id: 22, name: 'glove', sort_order: 1, base_units: 1, per_parent: null },
    ],
    on_hand_display: {
      mode: 'open_closed',
      level: 'box',
      sealed: 3,
      open: 1,
      text: '3 sealed + 1 open',
    },
    ...overrides,
  });

const setupItem = (item: Record<string, unknown>) => {
  (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({ data: item });
  (api.inventoryAPI.getItemMetrics as jest.Mock).mockResolvedValue({ data: null });
  (api.inventoryAPI.getUsageLogs as jest.Mock).mockResolvedValue({ data: { results: [] } });
  (api.reorderAPI.listRequests as jest.Mock).mockResolvedValue({ data: { results: [] } });
  (api.assetsAPI.listAssets as jest.Mock).mockResolvedValue({ data: { results: [] } });
  (api.sigAPI.listMySIGs as jest.Mock).mockResolvedValue({ data: { results: [] } });
};

const cycleCountResponse = (overrides: Record<string, unknown> = {}) => ({
  data: {
    id: 'test-id',
    current_stock: 300,
    last_counted_at: '2026-07-29T00:00:00Z',
    days_since_last_count: 0,
    reconciliation: {
      id: 1,
      projected_count: 450,
      actual_count: 300,
      delta: -150,
      reason: 'miscounted',
      reconciled_at: '2026-07-29T00:00:00Z',
      reconciled_by: 1,
    },
    ...overrides,
  },
});

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

const loaded = () => waitFor(() => expect(screen.getByTestId('item-on-hand')).toBeInTheDocument());

describe('InventoryItemDetailPage — on-hand at the count level', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('renders base units for an each-mode item, as it always has', async () => {
    setupItem(makeItem({ current_stock: 10 }));
    renderPage();
    await loaded();

    expect(screen.getByTestId('item-on-hand')).toHaveTextContent('10 units');
    expect(screen.getByTestId('item-minimum-stock')).toHaveTextContent('2 units');
    expect(screen.getByTestId('item-reorder-quantity')).toHaveTextContent('4 units');
    // None of the pack surfaces appear for an each-mode item.
    expect(screen.queryByTestId('item-pack-chain')).not.toBeInTheDocument();
    expect(screen.queryByTestId('item-base-units')).not.toBeInTheDocument();
    expect(screen.queryByTestId('pack-container-controls')).not.toBeInTheDocument();
  });

  it("renders the server's pack text plus the base-unit total for a by_level item", async () => {
    setupItem(byLevelItem());
    renderPage();
    await loaded();

    expect(screen.getByTestId('item-on-hand')).toHaveTextContent('4 ream(s)');
    expect(screen.getByTestId('item-base-units')).toHaveTextContent('450 sheets');
    // Thresholds are read in the counting unit for a pack-counting item.
    expect(screen.getByTestId('item-minimum-stock')).toHaveTextContent('2 reams');
    expect(screen.getByTestId('item-reorder-quantity')).toHaveTextContent('4 reams');
  });

  it('shows the pack chain and what the item is counted in', async () => {
    setupItem(byLevelItem());
    renderPage();
    await loaded();

    const chain = screen.getByTestId('item-pack-chain');
    expect(chain).toHaveTextContent('1 case = 10 reams');
    expect(chain).toHaveTextContent('1 ream = 100 sheets');
    expect(chain).toHaveTextContent('Counted in reams');
  });

  it('renders sealed + open for an open_closed item', async () => {
    setupItem(openClosedItem());
    renderPage();
    await loaded();

    expect(screen.getByTestId('item-on-hand')).toHaveTextContent('3 sealed + 1 open');
    expect(screen.getByTestId('item-pack-chain')).toHaveTextContent(
      'Counted in boxes (sealed + open)'
    );
  });

  it('falls back to base units for a half-configured pack item', async () => {
    setupItem(
      byLevelItem({
        count_level: null,
        on_hand_display: { mode: 'each', base_units: 450, unit: 'sheet', text: '450 sheet' },
      })
    );
    renderPage();
    await loaded();

    expect(screen.getByTestId('item-on-hand')).toHaveTextContent('450 sheets');
    expect(screen.getByTestId('item-minimum-stock')).toHaveTextContent('2 units');
    expect(screen.queryByTestId('pack-container-controls')).not.toBeInTheDocument();
  });
});

describe('InventoryItemDetailPage — counting at the level', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('counts an each-mode item in base units, with no at_level flag', async () => {
    setupItem(makeItem({ current_stock: 10 }));
    (api.inventoryAPI.cycleCount as jest.Mock).mockResolvedValue(cycleCountResponse());
    renderPage();
    await loaded();

    fireEvent.click(screen.getByTestId('cycle-count-button'));
    await screen.findByTestId('cycle-count-submit');

    // Seeded from base-unit stock, and no open-container field.
    expect(screen.getByTestId('cycle-count-qty')).toHaveValue('10');
    expect(screen.queryByTestId('cycle-count-open-containers')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('cycle-count-submit'));

    await waitFor(() => expect(api.inventoryAPI.cycleCount).toHaveBeenCalledTimes(1));
    const payload = (api.inventoryAPI.cycleCount as jest.Mock).mock.calls[0][1];
    expect(payload.counted_qty).toBe(10);
    expect(payload).not.toHaveProperty('at_level');
    expect(payload).not.toHaveProperty('open_count');
  });

  it('counts a by_level item in packs and posts at_level', async () => {
    setupItem(byLevelItem());
    (api.inventoryAPI.cycleCount as jest.Mock).mockResolvedValue(
      cycleCountResponse({
        counted_unit: 'ream',
        on_hand_display: { mode: 'by_level', level: 'ream', level_count: 3, text: '3 ream(s)' },
      })
    );
    renderPage();
    await loaded();

    fireEvent.click(screen.getByTestId('cycle-count-button'));
    await screen.findByTestId('cycle-count-submit');

    // Labelled and seeded in the item's own unit — 4 whole reams, not 450 sheets.
    expect(screen.getByLabelText(/Counted quantity \(reams\)/)).toBeInTheDocument();
    expect(screen.getByTestId('cycle-count-qty')).toHaveValue('4');

    fireEvent.change(screen.getByTestId('cycle-count-qty'), { target: { value: '3' } });
    fireEvent.click(screen.getByTestId('cycle-count-submit'));

    await waitFor(() => expect(api.inventoryAPI.cycleCount).toHaveBeenCalledTimes(1));
    expect((api.inventoryAPI.cycleCount as jest.Mock).mock.calls[0][1]).toEqual(
      expect.objectContaining({ counted_qty: 3, at_level: true })
    );
    expect((api.inventoryAPI.cycleCount as jest.Mock).mock.calls[0][1]).not.toHaveProperty(
      'open_count'
    );
  });

  it('counts an open_closed item as sealed packs plus the open tally', async () => {
    setupItem(openClosedItem());
    (api.inventoryAPI.cycleCount as jest.Mock).mockResolvedValue(cycleCountResponse());
    renderPage();
    await loaded();

    fireEvent.click(screen.getByTestId('cycle-count-button'));
    await screen.findByTestId('cycle-count-submit');

    // Seeded from the sealed count, with the open tally alongside it.
    expect(screen.getByTestId('cycle-count-qty')).toHaveValue('3');
    expect(screen.getByTestId('cycle-count-open-containers')).toHaveValue('1');

    fireEvent.change(screen.getByTestId('cycle-count-open-containers'), {
      target: { value: '2' },
    });
    fireEvent.click(screen.getByTestId('cycle-count-submit'));

    await waitFor(() => expect(api.inventoryAPI.cycleCount).toHaveBeenCalledTimes(1));
    expect((api.inventoryAPI.cycleCount as jest.Mock).mock.calls[0][1]).toEqual(
      expect.objectContaining({ counted_qty: 3, at_level: true, open_count: 2 })
    );
  });

  it('logs usage in packs for a pack-counting item', async () => {
    setupItem(byLevelItem());
    (api.inventoryAPI.logUsage as jest.Mock).mockResolvedValue({
      data: { quantity_used: 100, charged_group: null, ledger_transaction: null, total_cost: null },
    });
    renderPage();
    await loaded();

    fireEvent.click(screen.getByTestId('log-usage-button'));
    await screen.findByTestId('log-usage-submit');

    expect(screen.getByLabelText(/Quantity used \(reams\)/)).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('log-usage-submit'));

    await waitFor(() => expect(api.inventoryAPI.logUsage).toHaveBeenCalledTimes(1));
    expect((api.inventoryAPI.logUsage as jest.Mock).mock.calls[0][1]).toEqual(
      expect.objectContaining({ quantity: 1, at_level: true })
    );
  });

  it('logs usage in base units for an each-mode item, with no at_level flag', async () => {
    setupItem(makeItem());
    (api.inventoryAPI.logUsage as jest.Mock).mockResolvedValue({
      data: { quantity_used: 1, charged_group: null, ledger_transaction: null, total_cost: null },
    });
    renderPage();
    await loaded();

    fireEvent.click(screen.getByTestId('log-usage-button'));
    await screen.findByTestId('log-usage-submit');
    fireEvent.click(screen.getByTestId('log-usage-submit'));

    await waitFor(() => expect(api.inventoryAPI.logUsage).toHaveBeenCalledTimes(1));
    expect((api.inventoryAPI.logUsage as jest.Mock).mock.calls[0][1]).not.toHaveProperty(
      'at_level'
    );
  });
});

describe('InventoryItemDetailPage — open / finish a pack', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('opens a sealed pack and reloads the item', async () => {
    setupItem(openClosedItem());
    (api.inventoryAPI.packContainer as jest.Mock).mockResolvedValue({
      data: {
        transition: 'open',
        id: 'test-id',
        current_stock: 200,
        open_container_count: 2,
        on_hand_display: { mode: 'open_closed', level: 'box', sealed: 2, open: 2, text: '2 sealed + 2 open' },
        usage_log: null,
      },
    });
    renderPage();
    await loaded();

    expect(screen.getByTestId('open-pack-button')).toHaveTextContent('Open a box');
    fireEvent.click(screen.getByTestId('open-pack-button'));

    await waitFor(() => expect(api.inventoryAPI.packContainer).toHaveBeenCalledTimes(1));
    expect(api.inventoryAPI.packContainer).toHaveBeenCalledWith('test-id', 'open');
    // Reloaded so the sealed + open split refreshes (initial load + reload).
    await waitFor(() =>
      expect((api.inventoryAPI.getItem as jest.Mock).mock.calls.length).toBeGreaterThanOrEqual(2)
    );
  });

  it('finishes the open pack', async () => {
    setupItem(openClosedItem());
    (api.inventoryAPI.packContainer as jest.Mock).mockResolvedValue({
      data: {
        transition: 'finish',
        id: 'test-id',
        current_stock: 300,
        open_container_count: 0,
        on_hand_display: { mode: 'open_closed', level: 'box', sealed: 3, open: 0, text: '3 sealed + 0 open' },
        usage_log: null,
      },
    });
    renderPage();
    await loaded();

    fireEvent.click(screen.getByTestId('finish-pack-button'));

    await waitFor(() => expect(api.inventoryAPI.packContainer).toHaveBeenCalledTimes(1));
    expect(api.inventoryAPI.packContainer).toHaveBeenCalledWith('test-id', 'finish');
  });

  it('disables opening with no sealed pack left and finishing with none open', async () => {
    setupItem(
      openClosedItem({
        current_stock: 0,
        open_container_count: 0,
        on_hand_display: {
          mode: 'open_closed',
          level: 'box',
          sealed: 0,
          open: 0,
          text: '0 sealed + 0 open',
        },
      })
    );
    renderPage();
    await loaded();

    expect(screen.getByTestId('open-pack-button')).toBeDisabled();
    expect(screen.getByTestId('finish-pack-button')).toBeDisabled();
  });

  it('leaves the item alone when a transition is rejected', async () => {
    setupItem(openClosedItem());
    (api.inventoryAPI.packContainer as jest.Mock).mockRejectedValue({
      response: { status: 400, data: { detail: 'No sealed pack to open.' } },
    });
    renderPage();
    await loaded();

    fireEvent.click(screen.getByTestId('open-pack-button'));

    await waitFor(() => expect(api.inventoryAPI.packContainer).toHaveBeenCalledTimes(1));
    // The error goes to a toast (not asserted — no <Notifications/> in this
    // tree); what matters here is that nothing reloaded and the button is live
    // again for a retry.
    await waitFor(() => expect(screen.getByTestId('open-pack-button')).not.toBeDisabled());
    expect((api.inventoryAPI.getItem as jest.Mock).mock.calls).toHaveLength(1);
  });

  it('offers no pack controls for a by_level item — the transitions are open_closed only', async () => {
    setupItem(byLevelItem());
    renderPage();
    await loaded();

    expect(screen.queryByTestId('pack-container-controls')).not.toBeInTheDocument();
  });
});
