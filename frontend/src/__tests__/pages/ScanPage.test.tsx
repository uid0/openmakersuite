/**
 * Tests for ScanPage component
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import ScanPage from '../../pages/ScanPage';
import * as api from '../../services/api';
import { networkError } from '../helpers/offline';

// Mock the API
vi.mock('../../services/api');

const mockNavigate = jest.fn();
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useNavigate: () => mockNavigate,
}));

describe('ScanPage', () => {
  const mockItem = {
    id: 'test-id-123',
    name: 'Test Widget',
    description: 'A test item description',
    sku: 'TEST-001',
    location: 'Shelf A',
    reorder_quantity: 25,
    current_stock: 50,
    minimum_stock: 10,
    average_lead_time: 7,
    supplier_name: 'Test Supplier',
    // A NUMBER: `InventoryItem.unit_cost` is a property-backed `ReadOnlyField`,
    // not the `DecimalField` string `ItemSupplier.unit_cost` sends (op-9m2v).
    unit_cost: 15.99,
    needs_reorder: false,
    category_name: 'Tools',
    image: null,
    thumbnail: null,
    qr_code: null,
    is_active: true,
    notes: '',
    total_value: '799.50',
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
    category: null,
    supplier: null,
    supplier_sku: '',
    supplier_url: '',
    // The supplier block reads `supplier_choice`, not the flat `supplier_name`
    // above (op-3xsp). Both are kept on the fixture: the flat keys are
    // deliberately still served, and several tests below still exercise them.
    supplier_choice: {
      item_supplier_id: 1,
      supplier_name: 'Test Supplier',
      basis: 'best_scored',
      reason: null,
      flagged_primary_unorderable: false,
      scored_without_price: false,
      scored_without_history: false,
      alternatives: [],
    },
  };

  beforeEach(() => {
    jest.clearAllMocks();
    // Clear localStorage to ensure clean state
    localStorage.clear();
    // Mock checklist API calls
    (api.inventoryAPI.getItemChecklists as jest.Mock).mockResolvedValue({
      data: [],
    });
  });

  const renderWithRouter = async (itemId = 'test-id-123') => {
    const view = render(
      <MemoryRouter initialEntries={[`/scan/${itemId}`]}>
        <Routes>
          <Route path="/scan/:itemId" element={<ScanPage />} />
        </Routes>
      </MemoryRouter>
    );
    return view;
  };

  test('displays loading state initially', async () => {
    (api.inventoryAPI.getItem as jest.Mock).mockReturnValue(new Promise(() => {}));

    await renderWithRouter();

    expect(screen.getByText(/loading item details/i)).toBeInTheDocument();
  });

  test('displays item details after loading (logged in user)', async () => {
    // Set logged in state
    localStorage.setItem('token', 'test-token');

    (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({
      data: mockItem,
    });

    (api.inventoryAPI.getItemSuppliers as jest.Mock).mockResolvedValue({
      data: { results: [] },
    });

    await renderWithRouter();

    await screen.findByText('Test Widget');

    expect(screen.getByText(/a test item description/i)).toBeInTheDocument();
    expect(screen.getByText(/sku: TEST-001/i)).toBeInTheDocument();
    expect(screen.getByText(/location:/i)).toBeInTheDocument();
    expect(screen.getByText(/shelf a/i)).toBeInTheDocument();
    expect(screen.getByText(/current stock:/i)).toBeInTheDocument();
    expect(screen.getByText(/reorder quantity:/i)).toBeInTheDocument();
    // The lead time is the CHOSEN supplier's quoted wait, and the block says so.
    expect(screen.getByText(/their lead time:/i)).toBeInTheDocument();
    expect(screen.getByText(/we order this from:/i)).toBeInTheDocument();
  });

  // op-c1ke: `current_cases` is null when nothing records how many units a case
  // holds. Round 5 of PR #1035 shipped that null against a frontend that still
  // declared it a number and called `.toFixed(1)`, which threw and blanked this
  // whole page for a scanned item whose supplier had died.
  test('renders a case-based item whose case size is unknown', async () => {
    localStorage.setItem('token', 'test-token');

    (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({
      data: {
        ...mockItem,
        use_case_based_reorder: true,
        minimum_cases: 1,
        reorder_cases: 2,
        current_cases: null,
        needs_reorder: true,
      },
    });
    (api.inventoryAPI.getItemSuppliers as jest.Mock).mockResolvedValue({
      data: { results: [] },
    });

    await renderWithRouter();

    // The page still renders — the item name proves it did not blank.
    await screen.findByText('Test Widget');
    // "unknown", NOT the older "case size not recorded". That wording was a
    // specific claim the payload does not support: a null `current_cases` also
    // covers an item whose link DID record a pack size — of 0 — where the fix
    // is to correct that row, not to add a supplier. "Unknown" is what the
    // derivation actually establishes. Do not restore the old sentence.
    expect(screen.getByText(/case size unknown/i)).toBeInTheDocument();
    expect(screen.queryByText(/not recorded/i)).toBeNull();
    expect(screen.queryByText(/50\.0 cases/)).toBeNull();
  });

  // The DISAGREEING side, which is also the DEFAULT configuration (minimum_stock
  // defaults to 0, minimum_cases to 1). The threshold the flag uses for an
  // unknown case size is max(minimum_stock, minimum_cases) = 3; an earlier
  // version of this test sat on the side where the two coincide and so passed
  // while the page could still contradict its own badge.
  const unknownCaseItem = {
    use_case_based_reorder: true,
    current_stock: 2,
    minimum_stock: 0,
    minimum_cases: 3,
    reorder_quantity: 40,
    reorder_cases: 2,
    current_cases: null,
    needs_reorder: true,
  };

  const unknownCaseDisplay = {
    mode: 'each',
    unit: 'unit',
    threshold: 3,
    current: 2,
    reorder_quantity: 40,
    needs_reorder: true,
    text: '2 units on hand · reorder at 3 units',
  };

  test('names base units, not cases, for an item whose case size is unknown', async () => {
    localStorage.setItem('token', 'test-token');

    (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({
      data: { ...mockItem, ...unknownCaseItem, reorder_display: unknownCaseDisplay },
    });
    (api.inventoryAPI.getItemSuppliers as jest.Mock).mockResolvedValue({
      data: { results: [] },
    });

    await renderWithRouter();

    await screen.findByText('Test Widget');
    expect(screen.getByText('40 units')).toBeInTheDocument();
    expect(screen.queryByText(/2 cases/)).toBeNull();
  });

  // `reorder_display` is optional on the wire, so the fallback must be correct
  // on its own — never back to the bare minimum_stock.
  test('falls back to base units when reorder_display is absent', async () => {
    localStorage.setItem('token', 'test-token');

    (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({
      data: { ...mockItem, ...unknownCaseItem },
    });
    (api.inventoryAPI.getItemSuppliers as jest.Mock).mockResolvedValue({
      data: { results: [] },
    });

    await renderWithRouter();

    await screen.findByText('Test Widget');
    expect(screen.getByText('40 units')).toBeInTheDocument();
    expect(screen.queryByText(/2 cases/)).toBeNull();
  });

  // The anonymous-scan path auto-submits, and its sentence used to say
  // "N cases" ungated — three lines below the page saying the cases cannot be
  // counted. It reads the same owner as every other quantity now.
  test('the anonymous auto-submit message never quotes cases it cannot count', async () => {
    localStorage.removeItem('token');

    // A failed auto-submit is the one settled state that shows this block:
    // the catch sets `submitting` back to false, which is exactly the branch
    // whose comment says "show the form so user can manually submit".
    (api.reorderAPI.createRequest as jest.Mock).mockRejectedValue(new Error('offline'));
    (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({
      data: { ...mockItem, ...unknownCaseItem, reorder_display: unknownCaseDisplay },
    });
    (api.inventoryAPI.getItemSuppliers as jest.Mock).mockResolvedValue({
      data: { results: [] },
    });

    await renderWithRouter();

    await screen.findByText('Test Widget');
    const message = await screen.findByText(/automatically submitting a reorder request/i);
    expect(message).toHaveTextContent('40 units');
    expect(message).not.toHaveTextContent(/case/i);
  });

  test('renders a case-based item whose case size IS recorded', async () => {
    localStorage.setItem('token', 'test-token');

    (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({
      data: {
        ...mockItem,
        use_case_based_reorder: true,
        minimum_cases: 1,
        reorder_cases: 2,
        current_cases: 2.5,
        needs_reorder: false,
      },
    });
    (api.inventoryAPI.getItemSuppliers as jest.Mock).mockResolvedValue({
      data: { results: [] },
    });

    await renderWithRouter();

    await screen.findByText('Test Widget');
    expect(screen.getByText(/2\.5 cases/)).toBeInTheDocument();
  });

  test('displays low stock warning when needed', async () => {
    // Set logged in state to avoid auto-submit
    localStorage.setItem('token', 'test-token');

    const lowStockItem = { ...mockItem, current_stock: 5, needs_reorder: true };

    (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({
      data: lowStockItem,
    });

    (api.inventoryAPI.getItemSuppliers as jest.Mock).mockResolvedValue({
      data: { results: [] },
    });

    await renderWithRouter();

    await screen.findByText('Test Widget');
    expect(screen.getByText(/low stock alert/i)).toBeInTheDocument();
  });

  test('handles form submission for logged in user', async () => {
    // Set logged in state to get the manual form
    localStorage.setItem('token', 'test-token');

    const mockSupplier = {
      id: 1,
      supplier_name: 'Test Supplier',
      unit_cost: '15.99',
      quantity_per_package: 1,
      is_active: true,
      average_lead_time: 7,
    };

    (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({
      data: mockItem,
    });

    (api.inventoryAPI.getItemSuppliers as jest.Mock).mockResolvedValue({
      data: { results: [mockSupplier] },
    });

    (api.reorderAPI.createRequest as jest.Mock).mockResolvedValue({
      data: { id: 1, item: mockItem.id },
    });

    await renderWithRouter();

    await screen.findByText('Test Widget');

    // Fill in the form
    const nameInput = screen.getByLabelText(/your name/i);
    const notesInput = screen.getByLabelText(/notes/i);

    fireEvent.change(nameInput, { target: { value: 'John Doe' } });
    fireEvent.change(notesInput, { target: { value: 'Need more stock' } });

    // Submit the form (should have supplier and proper quantities now)
    const submitButton = screen.getByRole('button', { name: /request \d+ units/i });

    fireEvent.click(submitButton);

    // Wait for the async operations to complete
    await waitFor(() => {
      expect(api.reorderAPI.createRequest).toHaveBeenCalledWith(
        expect.objectContaining({
          item: mockItem.id,
          requested_by: 'John Doe',
          request_notes: 'Need more stock',
          priority: 'normal',
        })
      );
    });
  });

  // --- A pack size of 0 is not a quantity (op-c1ke) -----------------------
  // `quantity_per_package: 0` is `pack_size.py`'s PACK_SIZE_RECORDED_ZERO: a
  // box holding no units. The form used to multiply by it, so asking for 3
  // packages POSTed a 0-unit request and silently discarded the 3.

  const zeroPackSupplier = {
    id: 7,
    supplier_name: 'Zero Pack Vendor',
    unit_cost: '1.25',
    package_cost: '0.00',
    quantity_per_package: 0,
    is_active: true,
    average_lead_time: 7,
  };

  const renderWithSupplier = async (supplier: Record<string, unknown>) => {
    localStorage.setItem('token', 'test-token');
    (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({ data: mockItem });
    (api.inventoryAPI.getItemSuppliers as jest.Mock).mockResolvedValue({
      data: { results: [supplier] },
    });
    (api.reorderAPI.createRequest as jest.Mock).mockResolvedValue({
      data: { id: 1, item: mockItem.id },
    });
    await renderWithRouter();
    await screen.findByText('Test Widget');
  };

  test('a recorded pack size of 0 is shown as unknown, never as the number 0', async () => {
    await renderWithSupplier(zeroPackSupplier);

    // Neither the supplier picker nor the package details may print a
    // confident "0" beside a refusal that says the size is unknown.
    expect(screen.getByRole('option', { name: /case size unknown/i })).toBeInTheDocument();
    expect(screen.queryByText(/0 per package/)).not.toBeInTheDocument();
    expect(screen.getByText(/units per package/i).parentElement).toHaveTextContent(
      /case size unknown/i
    );
  });

  test('the refusal names both the cause and the remedy', async () => {
    await renderWithSupplier(zeroPackSupplier);

    const refusal = screen.getByRole('alert');
    // Cause: the row records a pack size of 0 — a box holding no units.
    expect(refusal).toHaveTextContent(/records a pack size of 0/i);
    expect(refusal).toHaveTextContent(/box holding no units/i);
    // Remedy: exactly what pack_size.py prescribes for PACK_SIZE_RECORDED_ZERO.
    expect(refusal).toHaveTextContent(/correct "Quantity per Package"/i);
    expect(refusal).toHaveTextContent(/choose a different supplier that records one/i);
  });

  test('an unknown pack size cannot be submitted and posts nothing', async () => {
    await renderWithSupplier(zeroPackSupplier);

    const packagesInput = screen.getByLabelText(/number of packages/i);
    fireEvent.change(packagesInput, { target: { value: '3' } });

    expect(screen.queryByRole('button', { name: /request \d+ units/i })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /cannot request/i })).toBeDisabled();

    // Submitting the form directly bypasses the disabled button, so the
    // handler's own guard is what must refuse. The operator's 3 packages must
    // never become a 0-unit request.
    fireEvent.submit(screen.getByRole('button', { name: /cannot request/i }).closest('form')!);

    await waitFor(() => {
      expect(api.reorderAPI.createRequest).not.toHaveBeenCalled();
    });
  });

  test('control: a known pack size still computes and submits unchanged', async () => {
    await renderWithSupplier({ ...zeroPackSupplier, quantity_per_package: 12 });

    const packagesInput = screen.getByLabelText(/number of packages/i);
    fireEvent.change(packagesInput, { target: { value: '3' } });

    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /request 36 units/i }));

    await waitFor(() => {
      expect(api.reorderAPI.createRequest).toHaveBeenCalledWith(
        expect.objectContaining({
          item: mockItem.id,
          quantity: 36,
          package_quantity: 3,
          preferred_supplier: 7,
        })
      );
    });
  });

  test('auto-submits reorder for non-logged users', async () => {
    // Don't set token to simulate non-logged user
    const itemWithoutPending = { ...mockItem, has_pending_reorder: false };

    (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({
      data: itemWithoutPending,
    });

    (api.reorderAPI.createRequest as jest.Mock).mockResolvedValue({
      data: { id: 1 },
    });

    await renderWithRouter();

    // Should show auto-submit processing message
    await screen.findByText(/submitting reorder request/i);

    // Wait for auto-submit to complete
    await waitFor(() => {
      expect(api.reorderAPI.createRequest).toHaveBeenCalledWith({
        item: itemWithoutPending.id,
        quantity: itemWithoutPending.reorder_quantity,
        requested_by: 'Anonymous',
        request_notes: 'Auto-submitted via QR scan',
        priority: 'normal',
      });
    });
  });

  test('handles API error gracefully', async () => {
    (api.inventoryAPI.getItem as jest.Mock).mockRejectedValue({
      response: { data: { detail: 'Item not found' } },
    });

    await renderWithRouter();

    await screen.findByText(/item not found/i);
  });

  // --- R2 resilience (oms-sr1l4): public scan journey ---------------------
  // ScanPage is reached via the QR's encoded URL (/scan/:itemId); there is no
  // in-page camera, so AC-14 "mobile resilience" here means the journey
  // completes from the URL alone, AC-15 prevents duplicate auto-submits, and
  // AC-16 surfaces an actionable state when the network drops.

  test('AC-16: an offline item load shows an actionable error, not a blank screen', async () => {
    (api.inventoryAPI.getItem as jest.Mock).mockRejectedValue(networkError());

    await renderWithRouter();

    await screen.findByText(/failed to load item/i);
    expect(screen.getByRole('button', { name: /go home/i })).toBeInTheDocument();
    // A lost network must never silently fire an auto-submitted reorder.
    expect(api.reorderAPI.createRequest).not.toHaveBeenCalled();
  });

  test('AC-15: the anonymous auto-submit fires exactly once (no duplicate reorder)', async () => {
    const itemWithoutPending = { ...mockItem, has_pending_reorder: false };
    (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({ data: itemWithoutPending });
    (api.reorderAPI.createRequest as jest.Mock).mockResolvedValue({ data: { id: 1 } });

    await renderWithRouter();

    // The (!submitting && !submitted) guard keeps the effect's re-runs from
    // re-submitting, so the request lands exactly once.
    await waitFor(() => {
      expect(api.reorderAPI.createRequest).toHaveBeenCalledTimes(1);
    });
    expect(api.reorderAPI.createRequest).toHaveBeenCalledTimes(1);
  });

  test('AC-14: completes the reorder from the scanned URL with no camera step', async () => {
    const itemWithoutPending = { ...mockItem, has_pending_reorder: false };
    (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({ data: itemWithoutPending });
    (api.reorderAPI.createRequest as jest.Mock).mockResolvedValue({ data: { id: 1 } });

    await renderWithRouter();

    // Reaching /thanks proves the journey is driven entirely by the
    // QR-encoded :itemId — the camera-free, code-entry-by-URL path.
    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/thanks');
    });
  });

  // --- A price nobody recorded is not $0.00 (op-9m2v) ---------------------
  // `parseFloat(supplier.package_cost || '0')` turned a NULL package cost into
  // a confident 0, so this member-facing screen quoted "$0.00 estimated cost"
  // for a link nobody had priced — while telling the truth about the pack size
  // three lines above.

  const unpricedSupplier = {
    id: 9,
    supplier_name: 'Silent Vendor',
    unit_cost: null,
    package_cost: null,
    quantity_per_package: 12,
    is_active: true,
    average_lead_time: 7,
  };

  /**
   * The auto-selection at load only considers suppliers with a truthy
   * `unit_cost`. That is SAFE for a FREE link — DRF sends that nullable
   * DecimalField as the string `"0.00"`, which is truthy, so a free vendor is
   * kept and `parseFloat("0.00") = 0` sorts it first. Only a genuinely UNPRICED
   * link (`null`) is skipped, which is right: it cannot be ranked on price.
   * These tests select by hand because that is the only way to reach an
   * unpriced link, which is the case under test.
   */
  const selectSupplier = (supplier: Record<string, unknown>) =>
    fireEvent.change(screen.getByLabelText(/^supplier$/i), {
      target: { value: String(supplier.id) },
    });

  const packageDetails = () => document.querySelector('.supplier-details')!;
  const estimatedCostRow = () =>
    Array.from(document.querySelectorAll('.order-summary .summary-item')).find((row) =>
      /estimated cost/i.test(row.textContent || '')
    )!;

  test('BEFORE/AFTER: an unrecorded price is shown as unknown, never as $0.00', async () => {
    await renderWithSupplier(unpricedSupplier);
    selectSupplier(unpricedSupplier);

    expect(screen.queryByText(/\$0\.00/)).not.toBeInTheDocument();
    // Both cost cells, which used to render a bare "$" for the same null.
    expect(packageDetails().textContent).toContain('Package cost: — (no price on file)');
    expect(packageDetails().textContent).toContain('Unit cost: — (no price on file)');
    expect(estimatedCostRow().textContent).toContain('— (no price on file)');
  });

  test('the unknown price names what is missing and does not block the request', async () => {
    await renderWithSupplier(unpricedSupplier);
    selectSupplier(unpricedSupplier);

    // Informational, not a refusal: an unpriced request is still legitimate.
    const note = screen.getByRole('status');
    expect(note).toHaveTextContent(/no package cost on file/i);
    expect(note).toHaveTextContent(/can still be submitted/i);
    expect(screen.getByRole('button', { name: /request \d+ units/i })).not.toBeDisabled();
  });

  test('CONTROL: a vendor that genuinely charges nothing still reads $0.00', async () => {
    const free = { ...unpricedSupplier, unit_cost: '0.00', package_cost: '0.00' };
    await renderWithSupplier(free);
    selectSupplier(free);

    expect(screen.queryByText(/no price on file/i)).not.toBeInTheDocument();
    expect(estimatedCostRow().textContent).toContain('$0.00');
  });

  test('CONTROL: an ordinary price is unchanged — the branch invariant', async () => {
    await renderWithSupplier({ ...unpricedSupplier, unit_cost: '1.25', package_cost: '15.00' });

    const packagesInput = screen.getByLabelText(/number of packages/i);
    fireEvent.change(packagesInput, { target: { value: '3' } });

    expect(screen.queryByText(/no price on file/i)).not.toBeInTheDocument();
    expect(estimatedCostRow().textContent).toContain('$45.00');
  });

  test('AC-15: an existing pending reorder is not duplicated and shows a clear final state', async () => {
    const pendingItem = {
      ...mockItem,
      has_pending_reorder: true,
      active_reorder_request: {
        status: 'pending',
        quantity: 25,
        requested_at: '2024-01-01T00:00:00Z',
        requested_by: 'Anonymous',
      },
    };
    (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({ data: pendingItem });

    await renderWithRouter();

    await screen.findByText(/reorder already requested/i);
    expect(api.reorderAPI.createRequest).not.toHaveBeenCalled();
  });

  /**
   * The item's own Unit Cost row (op-9m2v).
   *
   * `item.unit_cost` is a model PROPERTY on `InventoryItemSerializer`, so DRF
   * builds a `ReadOnlyField` and it arrives as a JSON NUMBER. The guard here
   * was `{item.unit_cost && ...}`, which on a donated item is `{0 && ...}` —
   * React renders the `0` itself, so the member saw a bare "0" where a price
   * belonged. The supplier rows beside it are `DecimalField` strings and stay
   * as they are.
   */
  const renderItemPriced = async (unitCost: number | null) => {
    localStorage.setItem('token', 'test-token');
    (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({
      data: { ...mockItem, unit_cost: unitCost },
    });
    (api.inventoryAPI.getItemSuppliers as jest.Mock).mockResolvedValue({
      data: { results: [] },
    });
    await renderWithRouter();
    await screen.findByText('Test Widget');
  };

  // The row is now labelled as the CHOSEN supplier's price rather than the
  // item's (op-3xsp); every op-9m2v assertion below is unchanged.
  const unitCostRow = () => screen.getByText('Their Unit Cost:').parentElement as HTMLElement;

  test('BEFORE/AFTER: a donated item is priced $0.00, not dropped or shown as "0"', async () => {
    await renderItemPriced(0);

    expect(unitCostRow()).toHaveTextContent('$0.00');
    expect(unitCostRow()).not.toHaveTextContent(/no price on file/i);
    // The falsy guard printed the number 0 into the row instead of a price.
    expect(unitCostRow().textContent).not.toMatch(/Unit Cost:\s*0\s*$/);
  });

  test('an item nobody has priced says so rather than showing nothing', async () => {
    await renderItemPriced(null);

    expect(unitCostRow()).toHaveTextContent(/no price on file/i);
    expect(unitCostRow()).not.toHaveTextContent('$');
  });

  test('CONTROL: an ordinary item price is unchanged', async () => {
    await renderItemPriced(15.99);

    expect(unitCostRow()).toHaveTextContent('$15.99');
  });

  test('a price with a trailing zero cent is written in full', async () => {
    await renderItemPriced(5.1);

    // The row rendered `${item.unit_cost}` raw, so 5.1 read as "$5.1".
    expect(unitCostRow()).toHaveTextContent('$5.10');
  });

  // --- One supplier is not THE supplier (op-3xsp) -------------------------
  // This block rendered `item.supplier_name` — the read-only legacy accessor —
  // under a bare "Supplier:" label, with the lead time and unit cost beside it
  // reading as the item's own numbers. An item stocked by three vendors showed
  // exactly one name and a member had no way to tell there were others, nor
  // that the choice had been made without a price on file.

  const renderWithChoice = async (choice: Record<string, unknown> | undefined) => {
    localStorage.setItem('token', 'test-token');
    (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({
      data: { ...mockItem, supplier_choice: choice },
    });
    (api.inventoryAPI.getItemSuppliers as jest.Mock).mockResolvedValue({
      data: { results: [] },
    });
    await renderWithRouter();
    await screen.findByText('Test Widget');
  };

  const baseChoice = {
    item_supplier_id: 1,
    supplier_name: 'Acme Supplies',
    basis: 'best_scored',
    reason: null,
    flagged_primary_unorderable: false,
    scored_without_price: false,
    scored_without_history: false,
    alternatives: [],
  };

  test('BEFORE/AFTER: names the derived supplier, not the legacy accessor', async () => {
    localStorage.setItem('token', 'test-token');
    (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({
      data: {
        ...mockItem,
        // Set apart so the assertion can only pass by reading the right key.
        supplier_name: 'Legacy Accessor Co.',
        supplier_choice: { ...baseChoice, supplier_name: 'Derived Supply Co.' },
      },
    });
    (api.inventoryAPI.getItemSuppliers as jest.Mock).mockResolvedValue({ data: { results: [] } });
    await renderWithRouter();
    await screen.findByText('Test Widget');

    expect(screen.getByTestId('supplier-choice-name')).toHaveTextContent('Derived Supply Co.');
    expect(screen.queryByText('Legacy Accessor Co.')).not.toBeInTheDocument();
  });

  test('BEFORE/AFTER: an item with three suppliers does not read as having one', async () => {
    await renderWithChoice({
      ...baseChoice,
      alternatives: [
        { id: 2, supplier_name: 'Beta Parts' },
        { id: 3, supplier_name: 'Gamma Wholesale' },
      ],
    });

    expect(screen.getByTestId('supplier-choice-alternatives')).toHaveTextContent(
      'also available from Beta Parts, Gamma Wholesale'
    );
  });

  test('a sole supplier gets no phantom alternatives line', async () => {
    await renderWithChoice(baseChoice);

    expect(screen.queryByTestId('supplier-choice-alternatives')).not.toBeInTheDocument();
    expect(screen.queryByTestId('supplier-choice-note')).not.toBeInTheDocument();
  });

  test('a choice made without a price says so beside the blank cost', async () => {
    await renderWithChoice({ ...baseChoice, scored_without_price: true });

    expect(screen.getByTestId('supplier-choice-note')).toHaveTextContent(
      'chosen without a price on file'
    );
  });

  test('a skipped flagged primary is said out loud', async () => {
    await renderWithChoice({ ...baseChoice, flagged_primary_unorderable: true });

    expect(screen.getByTestId('supplier-choice-note')).toHaveTextContent(
      /flagged primary supplier cannot be ordered from/i
    );
  });

  test('an item with nothing buyable says which kind of nothing it is', async () => {
    await renderWithChoice({
      ...baseChoice,
      supplier_name: null,
      item_supplier_id: null,
      basis: null,
      reason: 'none_orderable',
    });

    expect(screen.getByTestId('supplier-choice-note')).toHaveTextContent(
      /inactive or discontinued/i
    );
    // And no supplier name, no lead time and no price are quoted for a vendor
    // that does not exist — the old block rendered a bare " days" here.
    expect(screen.queryByTestId('supplier-choice-name')).not.toBeInTheDocument();
    expect(screen.queryByText('Their Lead Time:')).not.toBeInTheDocument();
    expect(screen.queryByText('Their Unit Cost:')).not.toBeInTheDocument();
  });

  test('a payload with no supplier_choice says so rather than inventing one', async () => {
    await renderWithChoice(undefined);

    expect(screen.getByTestId('supplier-choice-note')).toHaveTextContent(
      /was not included in this response/i
    );
  });
});
