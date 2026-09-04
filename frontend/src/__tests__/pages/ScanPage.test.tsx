/**
 * Tests for ScanPage component
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { Link, MemoryRouter, Route, Routes } from 'react-router-dom';
import ScanPage, { autoSubmitRetry } from '../../pages/ScanPage';
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
    // The serializer sends this on every item, and the anonymous auto-submit
    // reads `order_quantity`/`order_text` from it — the server's answer to
    // "what would a reorder for this item order, in the base units a request is
    // stored in?". For a plain each item it is `reorder_quantity` unchanged.
    reorder_display: {
      mode: 'each',
      unit: 'unit',
      threshold: 10,
      current: 50,
      reorder_quantity: 25,
      order_quantity: 25,
      order_text: '25 units',
      needs_reorder: false,
      text: '50 units on hand · reorder at 10 units',
    },
  };

  // What these tests pin about the retry is its BOUND, not its pace — the
  // attempt count is asserted as a literal below. Driving the real 400/800 ms
  // backoff through every failure-path case buys nothing but wall-clock and
  // thins the headroom against the default test timeout, so the gap is shrunk
  // here and restored after; the loop's behaviour is untouched.
  const productionRetryDelayMs = autoSubmitRetry.delayMs;

  beforeEach(() => {
    jest.clearAllMocks();
    // Clear localStorage to ensure clean state
    localStorage.clear();
    autoSubmitRetry.delayMs = 1;
    // Mock checklist API calls
    (api.inventoryAPI.getItemChecklists as jest.Mock).mockResolvedValue({
      data: [],
    });
  });

  afterEach(() => {
    autoSubmitRetry.delayMs = productionRetryDelayMs;
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
    order_quantity: 40,
    order_text: '40 units',
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
  // on its own — never back to the bare minimum_stock. A signed-in reader still
  // gets `reorderQuantityLabel`'s client twin here; what a payload without that
  // block CANNOT do is let the anonymous path file a quantity (see
  // "files nothing, and says so, when the payload carries no order quantity").
  test('falls back to base units when reorder_display is absent', async () => {
    localStorage.setItem('token', 'test-token');

    const { reorder_display: _omitted, ...withoutDisplay } = mockItem;
    (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({
      data: { ...withoutDisplay, ...unknownCaseItem },
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

    // The auto-submit's terminal failure is the one settled state that shows
    // this block; the page renders it after AUTO_SUBMIT_ATTEMPTS tries.
    (api.reorderAPI.createRequest as jest.Mock).mockRejectedValue(new Error('offline'));
    (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({
      data: { ...mockItem, ...unknownCaseItem, reorder_display: unknownCaseDisplay },
    });
    (api.inventoryAPI.getItemSuppliers as jest.Mock).mockResolvedValue({
      data: { results: [] },
    });

    await renderWithRouter();

    await screen.findByTestId('auto-submit-failed', {}, { timeout: 3000 });
    await screen.findByText('Test Widget');
    const message = await screen.findByTestId('reorder-quantity');
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

  // --- What the scan SHOWS is what the scan FILES -------------------------
  // The page both names a reorder quantity and files one on a logged-out
  // member's behalf, and the two used to be different derivations: it printed
  // `reorderQuantityLabel` (the item's CONFIGURED amount, in the item's own
  // counting unit — "3 cases") and POSTed the raw `reorder_quantity` column,
  // which for a pack-counting item is a count of PACKS. A member read "3 cases"
  // off the shelf label and had three bottles ordered. Both halves read the
  // server's `reorder_display.order_quantity`/`order_text` now.

  // Exactly the payload `inventory/tests/test_reorder_filing.py` produces for a
  // case of 12 bottles: the configured amount is 3 (cases), the amount a filed
  // request stores is 36 (bottles). Three different numbers were available to
  // get wrong, which is why this is the fixture.
  const packCountedItem = {
    ...mockItem,
    has_pending_reorder: false,
    base_unit: 'bottle',
    count_mode: 'by_level' as const,
    count_level: 1,
    packaging_levels: [
      { id: 1, name: 'case', sort_order: 0, base_units: 12, per_parent: 12 },
      { id: 2, name: 'bottle', sort_order: 1, base_units: 1, per_parent: null },
    ],
    current_stock: 35,
    minimum_stock: 2,
    reorder_quantity: 3,
    needs_reorder: true,
    reorder_display: {
      mode: 'by_level',
      unit: 'case',
      threshold: 2,
      current: 2,
      reorder_quantity: 3,
      order_quantity: 36,
      order_text: '3 cases (36 bottles)',
      needs_reorder: true,
      text: '2 cases on hand · reorder at 2 cases',
    },
  };

  const anonymousScanOf = async (data: Record<string, unknown>) => {
    localStorage.removeItem('token');
    (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({ data });
    (api.inventoryAPI.getItemSuppliers as jest.Mock).mockResolvedValue({
      data: { results: [] },
    });
    return renderWithRouter();
  };

  test('an anonymous scanner — no token, no account — still files a reorder', async () => {
    // The PRIMARY user of this page. Most people who scan a shelf label are not
    // registered members, and anonymous scan-to-reorder is what the printed
    // labels exist for, so the fix above must make the request TRUTHFUL without
    // making the path smaller: no login step, no gate, still exactly one POST.
    (api.reorderAPI.createRequest as jest.Mock).mockResolvedValue({ data: { id: 1 } });

    await anonymousScanOf(packCountedItem);

    expect(localStorage.getItem('token')).toBeNull();
    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/thanks'));
    expect(api.reorderAPI.createRequest).toHaveBeenCalledTimes(1);
  });

  test('files the quantity it shows, not the raw reorder_quantity column', async () => {
    (api.reorderAPI.createRequest as jest.Mock).mockResolvedValue({ data: { id: 1 } });

    await anonymousScanOf(packCountedItem);

    await waitFor(() => {
      expect(api.reorderAPI.createRequest).toHaveBeenCalledTimes(1);
    });
    const filed = (api.reorderAPI.createRequest as jest.Mock).mock.calls[0][0];

    // 3 cases of 12 bottles. The column says 3; a request is stored in bottles.
    expect(filed.quantity).toBe(36);
    expect(filed.quantity).not.toBe(packCountedItem.reorder_quantity);
    expect(filed.quantity).toBe(packCountedItem.reorder_display.order_quantity);
  });

  test('the quantity on screen names the quantity that was filed', async () => {
    // THE acceptance criterion: one number, reachable from both sides. The
    // rendered text must name the POSTed quantity, so a divergence between the
    // two derivations shows up here rather than on a purchase order.
    (api.reorderAPI.createRequest as jest.Mock).mockRejectedValue(new Error('offline'));

    await anonymousScanOf(packCountedItem);

    await screen.findByTestId('auto-submit-failed', {}, { timeout: 3000 });
    const shown = screen.getByTestId('reorder-quantity');
    const filed = (api.reorderAPI.createRequest as jest.Mock).mock.calls[0][0];

    expect(shown).toHaveTextContent(String(filed.quantity));
    expect(shown).toHaveTextContent('3 cases (36 bottles)');
  });

  test('the submitting screen names the quantity in flight', async () => {
    (api.reorderAPI.createRequest as jest.Mock).mockReturnValue(new Promise(() => {}));

    await anonymousScanOf(packCountedItem);

    const waiting = await screen.findByText(/please wait while we submit a request/i);
    expect(waiting).toHaveTextContent('3 cases (36 bottles)');
  });

  test('a signed-in operator sees the item\'s configured amount, and the form owns the filed one', async () => {
    // The other half of the same rule. A signed-in reorder is sized by the form
    // — package count x the selected supplier\'s pack size — not by
    // `order_quantity`, so the "Reorder Quantity" row must not name a number
    // this page will not file. It describes the ITEM instead ("3 cases", the
    // configured amount every operator-facing surface shows), while the Order
    // Summary states, and the POST sends, the form\'s own total.
    localStorage.setItem('token', 'test-token');
    (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({ data: packCountedItem });
    (api.inventoryAPI.getItemSuppliers as jest.Mock).mockResolvedValue({
      data: {
        results: [
          {
            id: 7,
            supplier: 1,
            supplier_name: 'Test Supplier',
            unit_cost: '2.00',
            package_cost: '16.00',
            quantity_per_package: 8,
            lead_time_days: 5,
            is_active: true,
            is_primary: true,
          },
        ],
      },
    });

    await renderWithRouter();

    await screen.findByText('Test Widget');
    const shown = await screen.findByTestId('reorder-quantity');
    expect(shown).toHaveTextContent('3 cases');
    expect(shown).not.toHaveTextContent('36');

    // The number this half actually files, stated by the form and then sent.
    expect(screen.getByText('8 units')).toBeInTheDocument();
    (api.reorderAPI.createRequest as jest.Mock).mockResolvedValue({ data: { id: 1 } });
    fireEvent.click(screen.getByRole('button', { name: /request 8 units/i }));

    await waitFor(() => expect(api.reorderAPI.createRequest).toHaveBeenCalled());
    expect((api.reorderAPI.createRequest as jest.Mock).mock.calls[0][0].quantity).toBe(8);
  });

  // --- A failed auto-submit is bounded, and it is stated ------------------
  // The catch used to clear `submitting` while `submitting` was a dependency of
  // the effect, so a failed submit re-entered it for as long as the page was
  // open: 19 POSTs to the public reorder endpoint in 150 ms, measured in jsdom
  // against a rejection delayed 5 ms. Latching it to one attempt was tried and
  // reverted — an anonymous visitor has no manual submit path, so a bare latch
  // parks them on "redirecting shortly" with nothing filed.

  test('a failing auto-submit stops after exactly three attempts', async () => {
    (api.reorderAPI.createRequest as jest.Mock).mockRejectedValue(new Error('offline'));

    await anonymousScanOf({ ...mockItem, has_pending_reorder: false });

    await screen.findByTestId('auto-submit-failed', {}, { timeout: 3000 });
    // The bound, written out rather than imported: changing
    // AUTO_SUBMIT_ATTEMPTS must fail this test, not silently move it.
    expect(api.reorderAPI.createRequest).toHaveBeenCalledTimes(3);
  });

  test('and files nothing further once it has stopped', async () => {
    (api.reorderAPI.createRequest as jest.Mock).mockRejectedValue(new Error('offline'));

    await anonymousScanOf({ ...mockItem, has_pending_reorder: false });

    await screen.findByTestId('auto-submit-failed', {}, { timeout: 3000 });
    const settled = (api.reorderAPI.createRequest as jest.Mock).mock.calls.length;

    // Well past the longest backoff the loop would have waited, so an
    // unbounded loop shows up as a growing count rather than as a slow test.
    await new Promise((resolve) => setTimeout(resolve, autoSubmitRetry.delayMs * 200));

    expect(api.reorderAPI.createRequest).toHaveBeenCalledTimes(settled);
  });

  test('the member is told the reorder was not filed, and what to do', async () => {
    (api.reorderAPI.createRequest as jest.Mock).mockRejectedValue(new Error('offline'));

    await anonymousScanOf({ ...mockItem, has_pending_reorder: false });

    const notice = await screen.findByTestId('auto-submit-failed', {}, { timeout: 3000 });
    // Never a silent drop: the item, the fact that nothing was ordered, and an
    // action the member already has.
    expect(notice).toHaveTextContent('Test Widget');
    expect(notice).toHaveTextContent(/nothing has been ordered/i);
    expect(notice).toHaveTextContent(/reload this page|member of staff/i);
    expect(mockNavigate).not.toHaveBeenCalledWith('/thanks');
  });

  test('a retry inside the bound still files the request', async () => {
    // The half a latch gets wrong: one transient failure must not cost the
    // member their reorder.
    (api.reorderAPI.createRequest as jest.Mock)
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce({ data: { id: 1 } });

    await anonymousScanOf({ ...mockItem, has_pending_reorder: false });

    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/thanks'), {
      timeout: 3000,
    });
    expect(api.reorderAPI.createRequest).toHaveBeenCalledTimes(2);
    expect(screen.queryByTestId('auto-submit-failed')).toBeNull();
  });

  // --- A retry asks whether the first attempt landed ----------------------
  // `/reorders/requests/` is not idempotent, so a POST whose row committed but
  // whose response was lost looks exactly like a failure here. Re-reading the
  // item before re-POSTing NARROWS that duplicate window; it does not close it
  // (a commit landing after the re-read still files twice — closing it needs
  // idempotency at the public endpoint, routed separately).

  const anonymousScanWhere = async (
    getItem: (id: string) => Promise<{ data: Record<string, unknown> }>
  ) => {
    localStorage.removeItem('token');
    (api.inventoryAPI.getItem as jest.Mock).mockImplementation(getItem);
    (api.inventoryAPI.getItemSuppliers as jest.Mock).mockResolvedValue({
      data: { results: [] },
    });
    return renderWithRouter();
  };

  test('a retry that finds the reorder already pending files nothing further', async () => {
    // The lost-response case: the server committed, the client saw a rejection.
    const unfiled = { ...mockItem, has_pending_reorder: false };
    const filed = { ...mockItem, has_pending_reorder: true };
    let reads = 0;
    (api.reorderAPI.createRequest as jest.Mock).mockRejectedValue(new Error('lost response'));

    await anonymousScanWhere(async () => {
      reads += 1;
      return { data: reads === 1 ? unfiled : filed };
    });

    // The member's scan DID result in a filed request, so they end where a
    // successful submit ends — not on a notice telling them nothing was ordered.
    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/thanks'), {
      timeout: 3000,
    });
    expect(api.reorderAPI.createRequest).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId('auto-submit-failed')).toBeNull();
  });

  test('a re-read that itself fails cannot silently drop the reorder', async () => {
    // The re-read is a question, not a gate: unanswered, the retry proceeds,
    // because a missed reorder is worse than a possible duplicate. It also
    // spends no attempt of its own — the bound is still three POSTs.
    let reads = 0;
    (api.reorderAPI.createRequest as jest.Mock).mockRejectedValue(new Error('offline'));

    await anonymousScanWhere(async () => {
      reads += 1;
      if (reads === 1) return { data: { ...mockItem, has_pending_reorder: false } };
      throw networkError();
    });

    const notice = await screen.findByTestId('auto-submit-failed', {}, { timeout: 3000 });
    expect(notice).toHaveTextContent(/nothing has been ordered/i);
    expect(api.reorderAPI.createRequest).toHaveBeenCalledTimes(3);
  });

  test('scanning a second item leaves the first item\'s retry loop abandoned', async () => {
    // `/scan/:itemId` → `/scan/:otherId` reuses this component: the route
    // element is not keyed, so React Router changes the param without
    // remounting. The abandon signal the cleanup raises must therefore survive
    // the effect re-run that follows it — only a re-entry for the SAME item is
    // the StrictMode double-invoke that may revive a run.
    localStorage.removeItem('token');
    const itemA = { ...mockItem, id: 'item-a', name: 'Widget A', has_pending_reorder: false };
    const itemB = { ...mockItem, id: 'item-b', name: 'Widget B', has_pending_reorder: false };

    (api.inventoryAPI.getItem as jest.Mock).mockImplementation(async (id: string) => ({
      data: id === 'item-a' ? itemA : itemB,
    }));
    (api.inventoryAPI.getItemSuppliers as jest.Mock).mockResolvedValue({
      data: { results: [] },
    });

    // A's first POST hangs until the test releases it, so the item change
    // happens while that attempt is genuinely in flight.
    let failA: (err: Error) => void = () => {};
    const aInFlight = new Promise((_resolve, reject) => {
      failA = reject;
    });
    aInFlight.catch(() => {});
    (api.reorderAPI.createRequest as jest.Mock).mockImplementation(
      ({ item }: { item: string }) =>
        item === 'item-a' ? aInFlight : Promise.resolve({ data: { id: 2 } })
    );

    render(
      <MemoryRouter initialEntries={['/scan/item-a']}>
        <Link to="/scan/item-b">scan widget B</Link>
        <Routes>
          <Route path="/scan/:itemId" element={<ScanPage />} />
        </Routes>
      </MemoryRouter>
    );

    // A's attempt is in flight (its POST never settles until `failA` below).
    await waitFor(() =>
      expect(api.reorderAPI.createRequest).toHaveBeenCalledWith(
        expect.objectContaining({ item: 'item-a' })
      )
    );

    fireEvent.click(screen.getByText('scan widget B'));
    // B's POST proves the param changed, the item reloaded and the effect
    // re-ran — the exact sequence that used to revive A's loop.
    await waitFor(() =>
      expect(api.reorderAPI.createRequest).toHaveBeenCalledWith(
        expect.objectContaining({ item: 'item-b' })
      )
    );

    failA(new Error('offline'));
    // Well past every backoff A's loop would have waited had it been revived.
    await new Promise((resolve) => setTimeout(resolve, autoSubmitRetry.delayMs * 200));

    // A's loop is dead: it neither re-POSTs for A nor writes A's name over the
    // page now showing B.
    const postedForA = (api.reorderAPI.createRequest as jest.Mock).mock.calls.filter(
      ([payload]: [{ item: string }]) => payload.item === 'item-a'
    );
    expect(postedForA).toHaveLength(1);
    expect(screen.queryByTestId('auto-submit-failed')).toBeNull();
    expect(screen.queryByText(/Widget A/)).toBeNull();
  });

  test('files nothing, and says so, when the payload carries no order quantity', async () => {
    // `reorder_display` is optional on the wire. A page that cannot learn what
    // it would file must not invent a number — that is the defect above — and
    // must not stall silently either.
    const { reorder_display: _omitted, ...withoutDisplay } = mockItem;

    await anonymousScanOf({ ...withoutDisplay, has_pending_reorder: false });

    const notice = await screen.findByTestId('auto-submit-failed');
    expect(notice).toHaveTextContent(/did not tell us how much a reorder should order/i);
    expect(api.reorderAPI.createRequest).not.toHaveBeenCalled();
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

  // --- The anonymous scanner learns nothing about the alternatives ---------
  // This route is not behind RequireAuth. A logged-out visitor always saw ONE
  // supplier name here, with the lead time and the price beside it, and keeps
  // all three — that is the whole of what this route grants them. They must
  // gain neither the roster of every vendor that stocks the item NOR a count
  // of it: a count is authorised on the item detail page and nowhere else, and
  // "exactly what they saw before this branch" carried no sign that any other
  // vendor existed. Widening anonymous disclosure is not this change's to
  // authorise, by analogy to a nearby surface or otherwise.

  const renderAnonymouslyWithChoice = async (choice: Record<string, unknown>) => {
    localStorage.removeItem('token');
    // A failed auto-submit is the ONLY anonymous state that renders the item
    // block: `has_pending_reorder` short-circuits to the "already requested"
    // screen, and a successful submit redirects to /thanks. It SETTLES now —
    // the auto-submit stops after AUTO_SUBMIT_ATTEMPTS tries and renders the
    // failure notice — so these assertions run against a page that has stopped
    // doing anything, and the wait below is for that terminal state.
    (api.reorderAPI.createRequest as jest.Mock).mockRejectedValue(new Error('offline'));
    (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({
      data: { ...mockItem, supplier_choice: choice },
    });
    await renderWithRouter();
    await screen.findByTestId('auto-submit-failed', {}, { timeout: 3000 });
    await screen.findByText('Test Widget');
  };

  const threeSuppliers = {
    ...baseChoice,
    alternatives: [
      { id: 2, supplier_name: 'Beta Parts' },
      { id: 3, supplier_name: 'Gamma Wholesale' },
    ],
  };

  test('BEFORE/AFTER: a logged-out scanner is not told who the other vendors are', async () => {
    await renderAnonymouslyWithChoice(threeSuppliers);

    expect(screen.queryByText(/Beta Parts/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Gamma Wholesale/)).not.toBeInTheDocument();
    expect(screen.queryByTestId('supplier-choice-alternatives')).not.toBeInTheDocument();
  });

  test('BEFORE/AFTER: a logged-out scanner is not told that other vendors exist at all', async () => {
    await renderAnonymouslyWithChoice(threeSuppliers);

    // Not the names, and not the count that stood in for them either. The
    // whole supplier block must read as it did before `supplier_choice`
    // existed: one vendor, its lead time, its price.
    expect(screen.queryByTestId('supplier-choice-alternative-count')).not.toBeInTheDocument();
    expect(screen.queryByText(/other supplier/i)).not.toBeInTheDocument();
    // The whole row, label included — nothing trails the chosen name.
    expect(screen.getByTestId('supplier-choice-name').textContent).toBe(
      'We order this from:Acme Supplies'
    );
  });

  test('a logged-out scanner with exactly one other supplier is told nothing either', async () => {
    await renderAnonymouslyWithChoice({
      ...baseChoice,
      alternatives: [{ id: 2, supplier_name: 'Beta Parts' }],
    });

    expect(screen.queryByText(/Beta Parts/)).not.toBeInTheDocument();
    expect(screen.queryByText(/also stocks this item/i)).not.toBeInTheDocument();
    expect(screen.queryByTestId('supplier-choice-alternative-count')).not.toBeInTheDocument();
  });

  // Gating the alternatives must not NARROW what a logged-out visitor already
  // had: the chosen supplier's own name, its lead time and its unit cost.
  test('CONTROL: the chosen supplier, lead time and cost stay visible anonymously', async () => {
    await renderAnonymouslyWithChoice(threeSuppliers);

    expect(screen.getByTestId('supplier-choice-name')).toHaveTextContent('Acme Supplies');
    expect(screen.getByText('Their Lead Time:')).toBeInTheDocument();
    expect(screen.getByText('Their Unit Cost:')).toBeInTheDocument();
  });

  test('CONTROL: a signed-in operator still gets the names', async () => {
    await renderWithChoice(threeSuppliers);

    expect(screen.getByTestId('supplier-choice-alternatives')).toHaveTextContent(
      'also available from Beta Parts, Gamma Wholesale'
    );
  });

  // --- The note has an audience too ---------------------------------------
  // The three caveats describe the DERIVATION and are addressed to whoever
  // maintains the supplier links: a logged-out member has no flagged primary,
  // and cannot order at all. What they keep is the half they can act on —
  // that there is nothing to order — worded to name no vendor.

  test('BEFORE/AFTER: a logged-out scanner is told none of the operator caveats', async () => {
    await renderAnonymouslyWithChoice({
      ...baseChoice,
      scored_without_price: true,
      scored_without_history: true,
      flagged_primary_unorderable: true,
    });

    expect(screen.queryByTestId('supplier-choice-note')).not.toBeInTheDocument();
    expect(screen.queryByText(/no price on file/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/delivery history/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/flagged primary/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Before you order/i)).not.toBeInTheDocument();
  });

  test('CONTROL: the caveated item still shows its supplier, lead time and cost', async () => {
    await renderAnonymouslyWithChoice({
      ...baseChoice,
      scored_without_price: true,
      flagged_primary_unorderable: true,
    });

    expect(screen.getByTestId('supplier-choice-name')).toHaveTextContent('Acme Supplies');
    expect(screen.getByText('Their Lead Time:')).toBeInTheDocument();
    expect(screen.getByText('Their Unit Cost:')).toBeInTheDocument();
  });

  test('BEFORE/AFTER: an unorderable item still says so, in the member\'s words', async () => {
    await renderAnonymouslyWithChoice({
      ...baseChoice,
      supplier_name: null,
      item_supplier_id: null,
      basis: null,
      reason: 'none_orderable',
    });

    const note = screen.getByTestId('supplier-choice-note');
    expect(note).toHaveTextContent('This item cannot currently be ordered.');
    // The operator wording describes the LINKS; a member learns none of that.
    expect(note).not.toHaveTextContent(/inactive|discontinued|flagged|primary/i);
    expect(note).not.toHaveTextContent(/Acme|Beta|Gamma/);
  });

  test('an item nobody sourced is told apart from one whose sources are dead', async () => {
    await renderAnonymouslyWithChoice({
      ...baseChoice,
      supplier_name: null,
      item_supplier_id: null,
      basis: null,
      reason: 'no_suppliers',
    });

    expect(screen.getByTestId('supplier-choice-note')).toHaveTextContent(
      'No supplier is listed for this item.'
    );
  });

  test('a payload missing the field says nothing to a logged-out visitor', async () => {
    await renderAnonymouslyWithChoice(undefined as unknown as Record<string, unknown>);

    expect(screen.queryByTestId('supplier-choice-note')).not.toBeInTheDocument();
  });

  test('CONTROL: a signed-in operator still gets every caveat', async () => {
    await renderWithChoice({
      ...baseChoice,
      scored_without_price: true,
      flagged_primary_unorderable: true,
    });

    const note = screen.getByTestId('supplier-choice-note');
    expect(note).toHaveTextContent('chosen without a price on file');
    expect(note).toHaveTextContent(/flagged primary supplier cannot be ordered from/);
  });
});
