/**
 * Item detail — Suppliers card (op-item-suppliers).
 *
 * The page used to render `item.supplier_name`, the READ-ONLY legacy accessor
 * for the item's primary supplier that `InventoryItemSerializer` documents as
 * superseded by the `suppliers[]` array. An item with three suppliers therefore
 * showed exactly one name — incomplete data presented as complete. These tests
 * pin the corrected behaviour: every linked supplier, each with its own SKU,
 * package/unit UPC and lead time, primary flagged, and discontinued/inactive
 * links visibly separated.
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor, within } from '@testing-library/react';
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
  LineChart: () => <div />,
  Line: () => <div />,
  XAxis: () => <div />,
  YAxis: () => <div />,
  Tooltip: () => <div />,
}));

const supplierLink = (overrides: Record<string, unknown>) => ({
  id: 1,
  item: 'test-id',
  item_name: 'Test Item',
  supplier: 1,
  supplier_name: 'Supplier One',
  supplier_sku: 'SKU-1',
  supplier_url: '',
  package_upc: '',
  unit_upc: '',
  quantity_per_package: 1,
  package_height: null,
  package_width: null,
  package_length: null,
  package_weight: null,
  package_volume: null,
  unit_weight: null,
  package_dimensions_display: '',
  unit_cost: '1.00',
  package_cost: '1.00',
  average_lead_time: 7,
  is_primary: false,
  is_active: true,
  is_discontinued: false,
  notes: '',
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
  ...overrides,
});

const baseItem = {
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
  // A NUMBER on the wire; the ItemSupplier rows above are real
  // `DecimalField`s and stay strings (op-9m2v).
  unit_cost: 15.99,
  // Legacy primary-supplier accessor. Present on every payload; must NOT be
  // what the page shows.
  supplier_name: 'Legacy Accessor Co.',
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
  suppliers: [] as ReturnType<typeof supplierLink>[],
  // Which link the API says to buy through, and why (op-3xsp). Present on a
  // SIGNED-IN payload only — `anonymousItem` below strips it, which is what the
  // server does (op-anonymous-read-posture).
  supplier_choice: {
    item_supplier_id: 1,
    supplier_name: 'Derived Supply Co.',
    basis: 'best_scored' as const,
    reason: null,
    flagged_primary_unorderable: false,
    scored_without_price: false,
    scored_without_history: false,
    alternatives: [] as { id: number; supplier_name: string }[],
  },
};

const renderWith = (suppliers: unknown[], itemOverrides: Record<string, unknown> = {}) => {
  (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({
    data: { ...baseItem, ...itemOverrides, suppliers },
  });
  return render(
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
};

const suppliersCard = () => screen.getByTestId('item-suppliers-card');
const supplierRow = (id: number) => screen.getByTestId(`item-supplier-${id}`);

describe('InventoryItemDetailPage — suppliers card', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    // The card is signed-in only: this route is not behind RequireAuth, and a
    // logged-out visitor gets a payload the server has already stripped of
    // every vendor key. The JWT in localStorage is the app's auth signal.
    localStorage.setItem('token', 'test-access-token');
    (api.inventoryAPI.getItemMetrics as jest.Mock).mockResolvedValue({ data: null });
    (api.inventoryAPI.getUsageLogs as jest.Mock).mockResolvedValue({ data: { results: [] } });
    (api.reorderAPI.listRequests as jest.Mock).mockResolvedValue({ data: { results: [] } });
    (api.assetsAPI.listAssets as jest.Mock).mockResolvedValue({ data: { results: [] } });
    (api.inventoryAPI.getPurchaseHistory as jest.Mock).mockResolvedValue({
      data: { order_costs: [], deliveries: [] },
    });
  });

  afterEach(() => {
    localStorage.removeItem('token');
  });

  it('lists every linked supplier with its own SKU, UPCs and lead time', async () => {
    renderWith([
      supplierLink({
        id: 1,
        supplier_name: 'Acme Supplies',
        supplier_sku: 'ACME-9',
        package_upc: '012345678905',
        unit_upc: '012345678912',
        average_lead_time: 14,
        is_primary: true,
      }),
      supplierLink({
        id: 2,
        supplier_name: 'Beta Parts',
        supplier_sku: 'BP-77',
        package_upc: '987654321098',
        average_lead_time: 3,
      }),
      supplierLink({
        id: 3,
        supplier_name: 'Gamma Wholesale',
        supplier_sku: 'GW-12',
        unit_upc: '555555555550',
        average_lead_time: 21,
      }),
    ]);

    await waitFor(() => expect(suppliersCard()).toBeInTheDocument());

    // All three, not just the primary one.
    const one = within(supplierRow(1));
    expect(one.getByText('Acme Supplies')).toBeInTheDocument();
    expect(one.getByText('ACME-9')).toBeInTheDocument();
    expect(one.getByText('012345678905')).toBeInTheDocument();
    expect(one.getByText('012345678912')).toBeInTheDocument();
    expect(one.getByText('14 days')).toBeInTheDocument();

    const two = within(supplierRow(2));
    expect(two.getByText('Beta Parts')).toBeInTheDocument();
    expect(two.getByText('BP-77')).toBeInTheDocument();
    expect(two.getByText('987654321098')).toBeInTheDocument();
    expect(two.getByText('3 days')).toBeInTheDocument();

    const three = within(supplierRow(3));
    expect(three.getByText('Gamma Wholesale')).toBeInTheDocument();
    expect(three.getByText('GW-12')).toBeInTheDocument();
    expect(three.getByText('555555555550')).toBeInTheDocument();
    expect(three.getByText('21 days')).toBeInTheDocument();
  });

  it('stops treating the legacy supplier_name accessor as the source of truth', async () => {
    renderWith([supplierLink({ id: 1, supplier_name: 'Acme Supplies', is_primary: true })], {
      supplier_name: 'Legacy Accessor Co.',
    });

    await waitFor(() => expect(suppliersCard()).toBeInTheDocument());

    expect(screen.getByText('Acme Supplies')).toBeInTheDocument();
    expect(screen.queryByText('Legacy Accessor Co.')).not.toBeInTheDocument();
  });

  /**
   * A LOGGED-OUT VISITOR NOW GETS NO VENDOR NAME AT ALL
   * (op-anonymous-read-posture).
   *
   * There used to be six cases here pinning an anonymous-only block that named
   * ONE supplier off `item.supplier_choice` (op-3xsp), each feeding a mock
   * payload that carried that key. The captain put vendor identity behind a
   * login, `supplier_choice` is in `InventoryItemSerializer.VENDOR_ONLY_FIELDS`,
   * and an anonymous payload no longer carries it — so those mocks asserted a
   * payload shape the server had stopped producing, and the block they exercised
   * could only ever have rendered for a caller the server had already decided
   * must not see a vendor's name.
   *
   * `anonymousItem` is what the server actually sends now: the vendor keys are
   * ABSENT, not null, with `vendor_data_withheld: true` in their place.
   */
  const anonymousItem = () => {
    const item: Record<string, unknown> = { ...baseItem, vendor_data_withheld: true };
    for (const key of [
      'supplier_choice',
      'supplier_name',
      'supplier_sku',
      'supplier_url',
      'unit_cost',
      'average_lead_time',
      'total_value',
      'suppliers',
    ]) {
      delete item[key];
    }
    return item;
  };

  it('names no supplier anywhere for a logged-out visitor', async () => {
    localStorage.removeItem('token');
    (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({ data: anonymousItem() });
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

    await waitFor(() => expect(screen.getByText('Test Item')).toBeInTheDocument());

    expect(screen.queryByTestId('anonymous-supplier-block')).not.toBeInTheDocument();
    expect(screen.queryByTestId('item-suppliers-card')).not.toBeInTheDocument();
    expect(screen.queryByText('Derived Supply Co.')).not.toBeInTheDocument();
    expect(screen.queryByText('Legacy Accessor Co.')).not.toBeInTheDocument();
    expect(screen.queryByText('We order this from')).not.toBeInTheDocument();
  });

  it('says the price was withheld rather than that none is recorded', async () => {
    // The two are different facts, and '-' / "no price on file" would state the
    // one about the ITEM where the truth is about the READER.
    localStorage.removeItem('token');
    (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({ data: anonymousItem() });
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

    await waitFor(() => expect(screen.getByTestId('unit-cost-withheld')).toBeInTheDocument());
    expect(screen.queryByText('no price on file')).not.toBeInTheDocument();
  });

  it('shows a signed-in visitor the suppliers card instead of the legacy line', async () => {
    renderWith([
      supplierLink({ id: 1, supplier_name: 'Acme Supplies', is_primary: true }),
      supplierLink({ id: 2, supplier_name: 'Beta Parts' }),
    ]);

    await waitFor(() => expect(suppliersCard()).toBeInTheDocument());

    expect(screen.getByText('Acme Supplies')).toBeInTheDocument();
    expect(screen.getByText('Beta Parts')).toBeInTheDocument();
    expect(screen.queryByTestId('anonymous-supplier-block')).not.toBeInTheDocument();
    expect(screen.queryByText('Legacy Accessor Co.')).not.toBeInTheDocument();
  });

  it('marks the primary supplier and only the primary supplier', async () => {
    renderWith([
      supplierLink({ id: 1, supplier_name: 'Acme Supplies', is_primary: true }),
      supplierLink({ id: 2, supplier_name: 'Beta Parts', is_primary: false }),
    ]);

    await waitFor(() => expect(suppliersCard()).toBeInTheDocument());

    expect(within(supplierRow(1)).getByText('Primary')).toBeInTheDocument();
    expect(within(supplierRow(2)).queryByText('Primary')).not.toBeInTheDocument();
  });

  it('says so when no supplier is flagged primary, and names the remedy', async () => {
    renderWith([
      supplierLink({ id: 1, supplier_name: 'Acme Supplies', is_primary: false }),
      supplierLink({ id: 2, supplier_name: 'Beta Parts', is_primary: false }),
    ]);

    await waitFor(() => expect(suppliersCard()).toBeInTheDocument());

    const note = within(suppliersCard()).getByTestId('no-primary-supplier-note');
    expect(note).toHaveTextContent(/no supplier you can order from is flagged primary/i);
    // It must describe the rule the backend actually applies. The fallback
    // weighs lead time as well as price, so calling it "the cheapest" would be
    // contradicted by the very table this note sits above.
    expect(note).toHaveTextContent(/price and lead time/i);
    expect(note.textContent).not.toMatch(/cheapest/i);
    // The note used to stop at the fact, because `is_primary` had no write path
    // in this app and naming one would have described an action the operator
    // could not take. #1034 made the item form persist it, so the remedy is
    // real and withholding it is now the defect.
    expect(note).toHaveTextContent(/flag one on the item form/i);
    expect(within(suppliersCard()).queryByText('Primary')).not.toBeInTheDocument();
  });

  it('treats a discontinued flagged primary as unflagged, not as the choice', async () => {
    // `mark_discontinued` does not clear `is_primary`, so an operator can flag a
    // supplier and later mark it discontinued. The badge stays, but the backend
    // skips the row — so the note has to say a choice is being made for them.
    renderWith([
      supplierLink({ id: 1, supplier_name: 'Acme Supplies', is_primary: true, is_discontinued: true }),
      supplierLink({ id: 2, supplier_name: 'Beta Parts', is_primary: false }),
    ]);

    await waitFor(() => expect(suppliersCard()).toBeInTheDocument());

    const note = within(suppliersCard()).getByTestId('no-primary-supplier-note');
    expect(note).toHaveTextContent(/no supplier you can order from is flagged primary/i);
  });

  it('says outright when nothing on the table can be ordered from', async () => {
    // Distinct from "nobody flagged one": there is no cheapest-available row to
    // fall back to, so the operator has to change something before this item can
    // be bought at all. The two notes must not collapse into one.
    renderWith([
      supplierLink({ id: 1, supplier_name: 'Acme Supplies', is_discontinued: true }),
      supplierLink({ id: 2, supplier_name: 'Beta Parts', is_active: false }),
    ]);

    await waitFor(() => expect(suppliersCard()).toBeInTheDocument());

    const note = within(suppliersCard()).getByTestId('no-orderable-supplier-note');
    expect(note).toHaveTextContent(/no supplier here can be ordered from/i);
    expect(note).toHaveTextContent(/reactivate one, or add a supplier/i);
    // Not the softer "we picked one for you" note — nothing was picked.
    expect(
      within(suppliersCard()).queryByTestId('no-primary-supplier-note')
    ).not.toBeInTheDocument();
  });

  it('shows neither note when an orderable supplier is flagged primary', async () => {
    renderWith([
      supplierLink({ id: 1, supplier_name: 'Acme Supplies', is_primary: true }),
      supplierLink({ id: 2, supplier_name: 'Beta Parts', is_discontinued: true }),
    ]);

    await waitFor(() => expect(suppliersCard()).toBeInTheDocument());

    expect(
      within(suppliersCard()).queryByTestId('no-primary-supplier-note')
    ).not.toBeInTheDocument();
    expect(
      within(suppliersCard()).queryByTestId('no-orderable-supplier-note')
    ).not.toBeInTheDocument();
  });

  it('separates discontinued and inactive links from ones that can be ordered', async () => {
    renderWith([
      supplierLink({ id: 1, supplier_name: 'Acme Supplies', is_primary: true }),
      supplierLink({ id: 2, supplier_name: 'Beta Parts', is_discontinued: true }),
      supplierLink({ id: 3, supplier_name: 'Gamma Wholesale', is_active: false }),
    ]);

    await waitFor(() => expect(suppliersCard()).toBeInTheDocument());

    expect(within(supplierRow(2)).getByText('Discontinued')).toBeInTheDocument();
    expect(within(supplierRow(3)).getByText('Inactive')).toBeInTheDocument();
    // The orderable one carries neither label.
    expect(within(supplierRow(1)).queryByText('Discontinued')).not.toBeInTheDocument();
    expect(within(supplierRow(1)).queryByText('Inactive')).not.toBeInTheDocument();
  });

  it('dims an unorderable link where it states a lead time, not where it states an identifier', async () => {
    renderWith([
      supplierLink({ id: 1, supplier_name: 'Acme Supplies', average_lead_time: 14, is_primary: true }),
      supplierLink({
        id: 2,
        supplier_name: 'Beta Parts',
        supplier_sku: 'BP-77',
        package_upc: '987654321098',
        unit_upc: '987654321081',
        average_lead_time: 3,
        is_discontinued: true,
      }),
      supplierLink({ id: 3, supplier_name: 'Gamma Wholesale', average_lead_time: 21, is_active: false }),
    ]);

    await waitFor(() => expect(suppliersCard()).toBeInTheDocument());

    // The figure you would plan around is de-emphasised on both unorderable
    // links, so "3 days" cannot read as beating an orderable "14 days".
    expect(screen.getByTestId('supplier-lead-time-2')).toHaveAttribute('data-emphasis', 'dimmed');
    expect(screen.getByTestId('supplier-name-2')).toHaveAttribute('data-emphasis', 'dimmed');
    expect(screen.getByTestId('supplier-lead-time-3')).toHaveAttribute('data-emphasis', 'dimmed');
    expect(screen.getByTestId('supplier-name-3')).toHaveAttribute('data-emphasis', 'dimmed');

    // The identifiers on that same discontinued link stay fully legible —
    // they remain true for anyone looking up what was bought last year.
    expect(screen.getByTestId('supplier-sku-2')).toHaveAttribute('data-emphasis', 'full');
    expect(screen.getByTestId('supplier-package-upc-2')).toHaveAttribute('data-emphasis', 'full');
    expect(screen.getByTestId('supplier-unit-upc-2')).toHaveAttribute('data-emphasis', 'full');
    expect(screen.getByTestId('supplier-sku-2')).toHaveTextContent('BP-77');

    // An orderable link is dimmed nowhere.
    expect(screen.getByTestId('supplier-name-1')).toHaveAttribute('data-emphasis', 'full');
    expect(screen.getByTestId('supplier-lead-time-1')).toHaveAttribute('data-emphasis', 'full');
  });

  it('renders that emphasis as a difference an operator can see', async () => {
    // The rule is guarded above by `data-emphasis`; this pins the RENDERING of
    // the rule. Colours are compared against a sibling element rather than
    // against a named colour value: what has to hold is that the operator sees
    // a difference, not which particular colour carries it.
    renderWith([
      supplierLink({
        id: 1,
        supplier_name: 'Acme Supplies',
        supplier_sku: 'ACME-9',
        average_lead_time: 14,
        is_primary: true,
      }),
      supplierLink({
        id: 2,
        supplier_name: 'Beta Parts',
        supplier_sku: 'BP-77',
        average_lead_time: 3,
        is_discontinued: true,
      }),
    ]);

    await waitFor(() => expect(suppliersCard()).toBeInTheDocument());

    const declaredColor = (el: HTMLElement) => el.style.color;

    const orderableName = within(supplierRow(1)).getByText('Acme Supplies');
    const orderableSku = within(supplierRow(1)).getByText('ACME-9');
    const orderableLeadTime = within(supplierRow(1)).getByText('14 days');
    const unorderableName = within(supplierRow(2)).getByText('Beta Parts');
    const unorderableSku = within(supplierRow(2)).getByText('BP-77');
    const unorderableLeadTime = within(supplierRow(2)).getByText('3 days');

    // The separation the rule exists for: a discontinued link's "3 days" does
    // not look like an orderable link's "14 days".
    expect(declaredColor(unorderableLeadTime)).not.toBe(declaredColor(orderableLeadTime));
    expect(declaredColor(unorderableName)).not.toBe(declaredColor(orderableName));

    // The identifier on that same discontinued row is rendered exactly like the
    // orderable row's, and unlike its own row's lead time.
    expect(declaredColor(unorderableSku)).toBe(declaredColor(orderableSku));
    expect(declaredColor(unorderableSku)).not.toBe(declaredColor(unorderableLeadTime));
  });

  it('distinguishes an unrecorded value from an empty one', async () => {
    renderWith([
      supplierLink({
        id: 1,
        supplier_name: 'Sparse Supply',
        supplier_sku: '',
        package_upc: '',
        unit_upc: '',
        average_lead_time: null,
      }),
    ]);

    await waitFor(() => expect(suppliersCard()).toBeInTheDocument());

    const row = within(supplierRow(1));
    expect(row.getByTestId('supplier-sku-1')).toHaveTextContent('Not recorded');
    expect(row.getByTestId('supplier-package-upc-1')).toHaveTextContent('Not recorded');
    expect(row.getByTestId('supplier-unit-upc-1')).toHaveTextContent('Not recorded');
    expect(row.getByTestId('supplier-lead-time-1')).toHaveTextContent('Not recorded');
  });

  it('shows a zero-day lead time as zero, not as unrecorded', async () => {
    renderWith([supplierLink({ id: 1, supplier_name: 'Same Day Co.', average_lead_time: 0 })]);

    await waitFor(() => expect(suppliersCard()).toBeInTheDocument());

    const leadTime = screen.getByTestId('supplier-lead-time-1');
    expect(leadTime).toHaveTextContent(/^0 days$/);
    expect(leadTime).not.toHaveTextContent('Not recorded');
  });

  it('uses the singular for a one-day lead time', async () => {
    renderWith([supplierLink({ id: 1, supplier_name: 'Overnight Co.', average_lead_time: 1 })]);

    await waitFor(() => expect(suppliersCard()).toBeInTheDocument());

    // Anchored: `toHaveTextContent('1 day')` also passes for the wrong "1 days".
    expect(screen.getByTestId('supplier-lead-time-1')).toHaveTextContent(/^1 day$/);
  });

  it('states plainly that an item has no suppliers linked', async () => {
    renderWith([]);

    await waitFor(() => expect(suppliersCard()).toBeInTheDocument());

    expect(within(suppliersCard()).getByTestId('no-suppliers-note')).toHaveTextContent(
      /no suppliers are linked to this item/i
    );
    expect(screen.queryByText('Legacy Accessor Co.')).not.toBeInTheDocument();
  });

  it('does not read a payload with no suppliers key as "this item has none"', async () => {
    // "We were not told" and "there are none" are different facts. The detail
    // endpoint always sends the key, so this only fires for a narrowed payload
    // — but it must not fall back to the legacy single name either.
    (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({
      data: { ...baseItem, suppliers: undefined },
    });
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

    await waitFor(() => expect(suppliersCard()).toBeInTheDocument());
    expect(within(suppliersCard()).getByTestId('suppliers-unknown-note')).toHaveTextContent(
      /was not included in this response/i
    );
    expect(within(suppliersCard()).queryByTestId('no-suppliers-note')).not.toBeInTheDocument();
    expect(screen.queryByText('Legacy Accessor Co.')).not.toBeInTheDocument();
  });
});
