/**
 * AC-45 — "Supplied by kits" appears on item detail only when it is relevant.
 *
 * Three cases, one test each: the item is in kits (card shown), the item is in
 * no kits (card omitted), and the lookup fails (card omitted AND the rest of
 * the page still renders — the card is joined into the page's existing
 * Promise.allSettled precisely so a rejection cannot block the item).
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import { NotificationProvider } from '../../contexts/NotificationContext';
import InventoryItemDetailPage from '../../pages/InventoryItemDetailPage';
import * as api from '../../services/api';

vi.mock('../../services/api');

// Mirrors the fixture in InventoryItemDetailPage.test.tsx — the page renders
// enough of the record that a minimal stub crashes on formatting.
const ITEM = {
  id: 'i-cyan',
  name: 'Cyan Cartridge',
  description: 'Ink',
  sku: 'SKU-CYAN',
  category: 1,
  category_name: 'Ink',
  location: 'Shelf A',
  current_stock: 1,
  minimum_stock: 5,
  reorder_quantity: 5,
  unit_cost: 15.99,
  supplier_name: 'Eufy Direct',
  needs_reorder: true,
  has_pending_reorder: false,
  is_active: true,
  is_kit: false,
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
  total_value: '15.99',
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

const KITS = [
  {
    id: 'k1',
    name: 'Eufy Ink Kit',
    sku: 'KIT-1',
    is_active: true,
    quantity_in_kit: 1,
    supplier_name: 'Eufy Direct',
    supplier_sku: 'T3200',
    // A NUMBER: `get_unit_cost` is a SerializerMethodField returning a Decimal,
    // which DRF renders as a JSON number, not the decimal string every other
    // price on this page arrives as (op-9m2v).
    unit_cost: 89.99,
    component_count: 5,
  },
];

const mockBaseCalls = () => {
  const inv = api.inventoryAPI as unknown as Record<string, ReturnType<typeof vi.fn>>;
  inv.getItem.mockResolvedValue({ data: ITEM });
  inv.getItemMetrics.mockResolvedValue({
    data: {
      current_stock: 1,
      quantity_on_order: 0,
      quantity_available: 1,
      quantity_committed: 0,
      committed_breakdown: [],
      quantity_in_transit: 0,
      reorder_point: 5,
      lead_time_days: 7,
      unit_cost: '15.99',
      cost_trend: 'flat' as const,
      last_po_unit_cost: '15.99',
      is_case_based: false,
      case_size: null,
      supplier_scored_without_price: false,
      supplier_scored_without_history: false,
    },
  });
  inv.getUsageLogs.mockResolvedValue({ data: [] });
  inv.getStockHistory.mockResolvedValue({
    data: { series: [], cycle_counts: [], reorder_events: [], reorder_point: 5, desired_level: 10 },
  });
  inv.getPurchaseHistory.mockResolvedValue({ data: { orders: [], deliveries: [] } });

  (api.reorderAPI as unknown as Record<string, ReturnType<typeof vi.fn>>)
    .listRequests.mockResolvedValue({ data: { results: [] } });
  (api.assetsAPI as unknown as Record<string, ReturnType<typeof vi.fn>>)
    .listAssets.mockResolvedValue({ data: { results: [] } });
};

const renderPage = () =>
  render(
    <MantineProvider>
      <NotificationProvider>
        <MemoryRouter initialEntries={['/inventory/items/i-cyan']}>
          <Routes>
            <Route path="/inventory/items/:id" element={<InventoryItemDetailPage />} />
          </Routes>
        </MemoryRouter>
      </NotificationProvider>
    </MantineProvider>,
  );

beforeEach(() => {
  vi.clearAllMocks();
  mockBaseCalls();
});

describe('AC-45 — Supplied by kits card', () => {
  it('shows the card when the item belongs to a kit', async () => {
    (api.inventoryAPI.getItemKits as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: KITS,
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('supplied-by-kits-card')).toBeInTheDocument();
    });
    expect(screen.getByTestId('supplied-by-kit-k1')).toHaveAttribute(
      'href',
      '/inventory/kits/k1',
    );
    expect(screen.getByText(/1 per kit/i)).toBeInTheDocument();
    expect(screen.getByText('$89.99')).toBeInTheDocument();
  });

  it('BEFORE/AFTER: shows a donated kit as costing $0.00 rather than hiding it', async () => {
    // `{kit.unit_cost && <Text/>}` is falsy at a numeric 0, so a kit the
    // supplier gives away showed no price at all — indistinguishable from one
    // nobody has priced — and printed a stray "0" beside it, because in JSX
    // `0 && <Text/>` evaluates to the number itself (op-9m2v).
    (api.inventoryAPI.getItemKits as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: [{ ...KITS[0], unit_cost: 0 }],
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('supplied-by-kits-card')).toBeInTheDocument();
    });
    const card = screen.getByTestId('supplied-by-kits-card');
    expect(card).toHaveTextContent('$0.00');
    expect(card).not.toHaveTextContent(/no price on file/i);
  });

  it('writes a trailing zero cent in full, not as "$5.1"', async () => {
    // The card used to interpolate the string DRF would have sent for a
    // `DecimalField` (`"5.10"`). It is a NUMBER, so `${kit.unit_cost}` renders
    // JavaScript's shortest form and drops the trailing zero — a price that
    // reads as five dollars ten rather than five dollars and ten cents
    // (op-9m2v). `.toFixed(2)` is what holds the cent column.
    (api.inventoryAPI.getItemKits as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: [{ ...KITS[0], unit_cost: 5.1 }],
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('supplied-by-kits-card')).toBeInTheDocument();
    });
    const card = screen.getByTestId('supplied-by-kits-card');
    expect(card).toHaveTextContent('$5.10');
    expect(card).not.toHaveTextContent('$5.1 ');
  });

  it('says so when nobody has priced the kit', async () => {
    (api.inventoryAPI.getItemKits as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: [{ ...KITS[0], unit_cost: null }],
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('supplied-by-kits-card')).toBeInTheDocument();
    });
    const card = screen.getByTestId('supplied-by-kits-card');
    expect(card).toHaveTextContent(/no price on file/i);
    expect(card).not.toHaveTextContent('$');
  });

  it('omits the card when the item belongs to no kits', async () => {
    (api.inventoryAPI.getItemKits as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: [],
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Cyan Cartridge')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('supplied-by-kits-card')).not.toBeInTheDocument();
  });

  it('omits the card and still renders the page when the lookup fails', async () => {
    (api.inventoryAPI.getItemKits as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error('boom'),
    );

    renderPage();

    // The item itself still loads — a rejected kits lookup must not block it.
    await waitFor(() => {
      expect(screen.getByText('Cyan Cartridge')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('supplied-by-kits-card')).not.toBeInTheDocument();
  });
});
