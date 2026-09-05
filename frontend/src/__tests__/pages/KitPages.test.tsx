/**
 * Kit management UI behaviour (op-8n0): AC-34, AC-35, AC-36, AC-46.
 *
 * The kit editor exists to make "add five cartridges" fast, so the tests here
 * are about the loop (debounced server search, no duplicates offered, qty
 * defaults to 1, Enter commits and refocuses) and about mutations patching
 * visible state from the response instead of bouncing the page back to a
 * loading placeholder.
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import KitComponentEditor, { KitComponentDraft } from '../../components/inventory/KitComponentEditor';
import KitDetailPage from '../../pages/KitDetailPage';
import { inventoryAPI, kitAPI } from '../../services/api';

vi.mock('../../services/api', () => ({
  kitAPI: {
    listKits: vi.fn(),
    getKit: vi.fn(),
    createKit: vi.fn(),
    updateKit: vi.fn(),
  },
  inventoryAPI: {
    listItems: vi.fn(),
    listSuppliers: vi.fn(),
  },
}));

const CYAN = { id: 'i-cyan', name: 'Cyan', sku: 'SKU-CYAN' };
const MAGENTA = { id: 'i-magenta', name: 'Magenta', sku: 'SKU-MAG' };

const KIT = {
  id: 'k1',
  name: 'Eufy Ink Kit',
  description: 'CMYK + cleaning',
  is_kit: true,
  is_active: true,
  supplier_sku: 'T3200',
  // A NUMBER on the wire: KitSerializer inherits the property-backed
  // ReadOnlyField from InventoryItemSerializer (op-9m2v).
  unit_cost: 89.99,
  // The link the price-guard tests below name (supplier 7). The Supplier box
  // re-seeds the SKU and cost boxes from the named supplier's OWN link, so a
  // kit that offers no link for the supplier being typed would blank the SKU
  // and send no `supplier_terms` at all.
  suppliers: [
    {
      id: 71,
      supplier: 7,
      supplier_name: 'Ink Wholesale',
      supplier_sku: 'T3200-IW',
      unit_cost: null,
      package_cost: null,
      quantity_per_package: 1,
    },
  ],
  component_count: 1,
  components: [
    {
      id: 11,
      component: CYAN.id,
      component_name: 'Cyan',
      component_sku: 'SKU-CYAN',
      component_current_stock: 2,
      component_needs_reorder: true,
      quantity: 1,
    },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  (inventoryAPI.listItems as ReturnType<typeof vi.fn>).mockResolvedValue({
    data: { results: [CYAN, MAGENTA] },
  });
  (inventoryAPI.listSuppliers as ReturnType<typeof vi.fn>).mockResolvedValue({
    data: { results: [] },
  });
  (kitAPI.getKit as ReturnType<typeof vi.fn>).mockResolvedValue({ data: KIT });
});

const renderDetail = (path = '/inventory/kits/k1') =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/inventory/kits/new" element={<KitDetailPage />} />
          <Route path="/inventory/kits/:kitId" element={<KitDetailPage />} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );

const renderEditor = (props: Partial<React.ComponentProps<typeof KitComponentEditor>> = {}) => {
  const onChange = vi.fn();
  const value: KitComponentDraft[] = props.value ?? [];
  render(
    <MantineProvider>
      <KitComponentEditor value={value} onChange={onChange} {...props} />
    </MantineProvider>,
  );
  return { onChange };
};

describe('AC-35 — the component picker is built for adding several items', () => {
  it('searches server-side and debounces keystrokes', async () => {
    const user = userEvent.setup();
    renderEditor();

    await waitFor(() => expect(inventoryAPI.listItems).toHaveBeenCalled());
    const callsAfterMount = (inventoryAPI.listItems as ReturnType<typeof vi.fn>).mock.calls.length;

    await user.type(screen.getByTestId('kit-component-picker'), 'cya');

    // Three keystrokes must not mean three requests.
    await waitFor(() => {
      const calls = (inventoryAPI.listItems as ReturnType<typeof vi.fn>).mock.calls;
      expect(calls.length).toBeLessThanOrEqual(callsAfterMount + 1);
      expect(calls[calls.length - 1][0]).toMatchObject({ search: 'cya' });
    });
  });

  it('filters out items already in the kit, so duplicates are impossible', async () => {
    renderEditor({
      value: [
        { component: CYAN.id, component_name: 'Cyan', quantity: 1 },
      ],
    });

    await waitFor(() => expect(inventoryAPI.listItems).toHaveBeenCalled());
    // Cyan is already a row; only Magenta remains selectable.
    await waitFor(() => {
      expect(screen.getByTestId('kit-editor-row-i-cyan')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('kit-editor-row-i-magenta')).not.toBeInTheDocument();
  });

  it('defaults a new component quantity to 1', async () => {
    renderEditor();
    await waitFor(() => expect(inventoryAPI.listItems).toHaveBeenCalled());
    expect(screen.getByTestId('kit-component-quantity')).toHaveValue('1');
  });
});

describe('AC-36 — Enter in the quantity field commits the row', () => {
  it('adds the row, clears the picker, and refocuses it', async () => {
    const user = userEvent.setup();
    const { onChange } = renderEditor();

    await waitFor(() => expect(inventoryAPI.listItems).toHaveBeenCalled());

    const picker = screen.getByTestId('kit-component-picker');
    await user.type(picker, 'Cyan');
    await user.click(await screen.findByText('Cyan'));

    const quantity = screen.getByTestId('kit-component-quantity');
    await user.clear(quantity);
    await user.type(quantity, '3');
    await user.keyboard('{Enter}');

    await waitFor(() => expect(onChange).toHaveBeenCalled());
    const rows = onChange.mock.calls[onChange.mock.calls.length - 1][0];
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ component: CYAN.id, quantity: 3 });

    // Picker is cleared and focused so the next row can be typed immediately.
    await waitFor(() => expect(picker).toHaveValue(''));
    expect(picker).toHaveFocus();
  });
});

describe('AC-34 / AC-46 — kit mutations are reactive', () => {
  it('patches visible state from the update response without a loading placeholder', async () => {
    const user = userEvent.setup();
    (kitAPI.updateKit as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: { ...KIT, name: 'Eufy Ink Kit v2' },
    });

    renderDetail();
    await waitFor(() => expect(screen.getByTestId('kit-detail-page')).toBeInTheDocument());

    const name = screen.getByTestId('kit-name');
    await user.clear(name);
    await user.type(name, 'Eufy Ink Kit v2');
    await user.click(screen.getByTestId('kit-save'));

    await waitFor(() => {
      expect(screen.getByTestId('kit-save-success')).toBeInTheDocument();
    });
    // The page never fell back to its initial loading state...
    expect(screen.queryByTestId('kit-detail-loading')).not.toBeInTheDocument();
    // ...and the visible title came from the response.
    expect(screen.getByRole('heading', { name: /eufy ink kit v2/i })).toBeInTheDocument();
    // Patched, not refetched.
    expect(kitAPI.getKit).toHaveBeenCalledTimes(1);
  });

  it('keeps the form and shows a scoped error when the save fails', async () => {
    const user = userEvent.setup();
    (kitAPI.updateKit as ReturnType<typeof vi.fn>).mockRejectedValue({
      response: { data: { error: { details: { components: ['A kit must contain at least one component.'] } } } },
    });

    renderDetail();
    await waitFor(() => expect(screen.getByTestId('kit-detail-page')).toBeInTheDocument());

    await user.click(screen.getByTestId('kit-save'));

    await waitFor(() => {
      expect(screen.getByTestId('kit-save-error')).toHaveTextContent(
        /at least one component/i,
      );
    });
    // The user's context survives the failure.
    expect(screen.getByTestId('kit-name')).toHaveValue('Eufy Ink Kit');
    expect(screen.queryByTestId('kit-detail-loading')).not.toBeInTheDocument();
  });

  it('blocks a duplicate submit while one is in flight', async () => {
    const user = userEvent.setup();
    let resolve: ((value: unknown) => void) | undefined;
    (kitAPI.updateKit as ReturnType<typeof vi.fn>).mockImplementation(
      () => new Promise((r) => {
        resolve = r;
      }),
    );

    renderDetail();
    await waitFor(() => expect(screen.getByTestId('kit-detail-page')).toBeInTheDocument());

    const save = screen.getByTestId('kit-save');
    await user.click(save);
    await waitFor(() => expect(save).toBeDisabled());
    await user.click(save).catch(() => undefined);

    expect(kitAPI.updateKit).toHaveBeenCalledTimes(1);
    resolve?.({ data: KIT });
  });

  it('a kit with no components cannot be saved', async () => {
    (kitAPI.getKit as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: { ...KIT, components: [] },
    });

    renderDetail();
    await waitFor(() => expect(screen.getByTestId('kit-detail-page')).toBeInTheDocument());

    expect(screen.getByTestId('kit-save')).toBeDisabled();
    expect(screen.getByTestId('kit-component-editor-empty')).toBeInTheDocument();
  });
});

describe('the kit form never invents a price the operator did not give', () => {
  /**
   * A blank cost box used to send the string `'0'`. That stores a real recorded
   * price of zero — a free item — which the order pad, the stock-value reports
   * and the public transparency feed then present and sum as money. "No price on
   * file" and "this supplier gives it away" are different facts.
   *
   * This guard was written once, on `oms-falsy-zero-money-guards`, and removed as
   * collateral when that branch reverted its whole supplier-terms attempt, so the
   * defect reached main. It is restored here with the write path it belongs to.
   */
  // The Supplier / SKU boxes are signed-in only (`showSupplierAttribution`
  // reads `isAuthenticated`, which reads a token out of localStorage), and the
  // payload only carries `supplier_terms` when both are filled in.
  beforeEach(() => localStorage.setItem('token', 'test-token'));
  afterEach(() => localStorage.removeItem('token'));

  const saveWithTerms = async (typeCost?: string) => {
    const user = userEvent.setup();
    (kitAPI.updateKit as ReturnType<typeof vi.fn>).mockResolvedValue({ data: KIT });

    renderDetail();
    await waitFor(() => expect(screen.getByTestId('kit-detail-page')).toBeInTheDocument());

    await user.type(screen.getByTestId('kit-supplier'), '7');
    const cost = screen.getByTestId('kit-unit-cost');
    await user.clear(cost);
    if (typeCost !== undefined) await user.type(cost, typeCost);

    await user.click(screen.getByTestId('kit-save'));
    await waitFor(() => expect(kitAPI.updateKit).toHaveBeenCalled());
    return (kitAPI.updateKit as ReturnType<typeof vi.fn>).mock.calls[0][1];
  };

  it('BEFORE/AFTER: a blank cost box sends null, not a fabricated "0"', async () => {
    const payload = await saveWithTerms();

    expect(payload.supplier_terms.unit_cost).toBeNull();
    expect(payload.supplier_terms.unit_cost).not.toBe('0');
  });

  it('CONTROL: a typed 0 is a real price and is still sent', async () => {
    const payload = await saveWithTerms('0');

    expect(payload.supplier_terms.unit_cost).toBe('0');
  });

  it('CONTROL: an ordinary typed price is unchanged', async () => {
    const payload = await saveWithTerms('12.50');

    expect(payload.supplier_terms.unit_cost).toBe('12.5');
  });

  it('CONTROL: a save that never touches the purchase terms sends none at all', async () => {
    const user = userEvent.setup();
    (kitAPI.updateKit as ReturnType<typeof vi.fn>).mockResolvedValue({ data: KIT });

    renderDetail();
    await waitFor(() => expect(screen.getByTestId('kit-detail-page')).toBeInTheDocument());

    const name = screen.getByTestId('kit-name');
    await user.clear(name);
    await user.type(name, 'Renamed');
    await user.click(screen.getByTestId('kit-save'));

    await waitFor(() => expect(kitAPI.updateKit).toHaveBeenCalled());
    const payload = (kitAPI.updateKit as ReturnType<typeof vi.fn>).mock.calls[0][1];
    expect(payload).not.toHaveProperty('supplier_terms');
  });
});

/**
 * The terms on screen belong to the supplier the form is about to write.
 *
 * `unit_cost` and `supplier_sku` at the top level of a kit payload are the
 * CHOSEN supplier's figures (`order_unit_price`, op-3xsp), and the Supplier box
 * is free text the operator types. So the boxes and the target link could name
 * different vendors — and under the delta rule in
 * `inventory.services.suppliers.derive_costs` a `unit_cost` that MOVED against
 * the named link GOVERNS and re-prices that link's stored case price. Handing
 * vendor A's 3.33 to vendor B's link rewrites B's 20.00 case price to 13.32 and
 * files a PriceHistory row claiming B re-quoted.
 *
 * On base this was harmless: an echoed unit cost was discarded whenever the link
 * had a package cost (symptom 3). Fixing symptom 3 is what gave a stale box
 * authority, so the amplification arrives with this branch and is pinned here.
 */
describe('the kit form shows the terms of the supplier it will write', () => {
  beforeEach(() => localStorage.setItem('token', 'test-token'));
  afterEach(() => localStorage.removeItem('token'));

  /** Two vendors stock this kit, on their own terms. Acme (50) is the chosen one. */
  const KIT_TWO_VENDORS = {
    ...KIT,
    suppliers: [
      {
        id: 91,
        supplier: 50,
        supplier_name: 'Acme Supplies',
        supplier_sku: 'ACME-INK-9',
        unit_cost: '3.33',
        package_cost: '10.00',
        quantity_per_package: 3,
      },
      {
        id: 92,
        supplier: 51,
        supplier_name: 'Beta Parts',
        supplier_sku: 'BETA-INK-2',
        unit_cost: '5.00',
        package_cost: '20.00',
        quantity_per_package: 4,
      },
    ],
    supplier_choice: {
      item_supplier_id: 91,
      supplier_name: 'Acme Supplies',
      reason: null,
    },
  };

  it('re-seeds both boxes when the operator names a different supplier', async () => {
    const user = userEvent.setup();
    (inventoryAPI.listSuppliers as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: { results: [{ id: 50, name: 'Acme Supplies' }, { id: 51, name: 'Beta Parts' }] },
    });
    (kitAPI.getKit as ReturnType<typeof vi.fn>).mockResolvedValue({ data: KIT_TWO_VENDORS });
    (kitAPI.updateKit as ReturnType<typeof vi.fn>).mockResolvedValue({ data: KIT_TWO_VENDORS });

    renderDetail();
    await waitFor(() => expect(screen.getByTestId('kit-detail-page')).toBeInTheDocument());

    // The kit names its chosen vendor on load, so the box holds ACME's OWN link
    // SKU — the terms shown belong to the supplier named beside them.
    await waitFor(() =>
      expect(screen.getByTestId('kit-supplier')).toHaveValue('50'),
    );
    expect(screen.getByTestId('kit-supplier-sku')).toHaveValue('ACME-INK-9');

    await user.clear(screen.getByTestId('kit-supplier'));
    await user.type(screen.getByTestId('kit-supplier'), '51');

    // Beta is named now, so Beta's part number is what is on screen — not Acme's.
    expect(screen.getByTestId('kit-supplier-sku')).toHaveValue('BETA-INK-2');

    await user.click(screen.getByTestId('kit-save'));
    await waitFor(() => expect(kitAPI.updateKit).toHaveBeenCalled());

    const payload = (kitAPI.updateKit as ReturnType<typeof vi.fn>).mock.calls[0][1];
    expect(payload.supplier_terms.supplier).toBe(51);
    expect(payload.supplier_terms.supplier_sku).toBe('BETA-INK-2');
    // Acme's 3.33 against Beta's stored 5.00 would read as a MOVED unit cost and
    // re-price Beta's 20.00 case to 13.32.
    expect(payload.supplier_terms.unit_cost).not.toBe('3.33');
    expect(payload.supplier_terms.unit_cost).toBe('5.00');
  });

  it('blanks both boxes for a supplier that has no link yet, and still sends no fabricated zero', async () => {
    const user = userEvent.setup();
    (inventoryAPI.listSuppliers as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: { results: [{ id: 50, name: 'Acme Supplies' }, { id: 99, name: 'New Vendor' }] },
    });
    (kitAPI.getKit as ReturnType<typeof vi.fn>).mockResolvedValue({ data: KIT_TWO_VENDORS });
    (kitAPI.updateKit as ReturnType<typeof vi.fn>).mockResolvedValue({ data: KIT_TWO_VENDORS });

    renderDetail();
    await waitFor(() => expect(screen.getByTestId('kit-detail-page')).toBeInTheDocument());

    await user.clear(screen.getByTestId('kit-supplier'));
    await user.type(screen.getByTestId('kit-supplier'), '99');

    // No link for 99, so there are no terms to show for it. Blank, not Acme's.
    expect(screen.getByTestId('kit-supplier-sku')).toHaveValue('');

    // The operator gives 99 its own part number and leaves the price alone.
    await user.type(screen.getByTestId('kit-supplier-sku'), 'NEW-1');
    await user.click(screen.getByTestId('kit-save'));
    await waitFor(() => expect(kitAPI.updateKit).toHaveBeenCalled());

    const payload = (kitAPI.updateKit as ReturnType<typeof vi.fn>).mock.calls[0][1];
    expect(payload.supplier_terms.supplier_sku).toBe('NEW-1');
    // Blank stays blank: "no price on file", never a recorded zero.
    expect(payload.supplier_terms.unit_cost).toBeNull();
  });
  it('attributes the terms to the supplier currently named, not the chosen one', async () => {
    const user = userEvent.setup();
    (inventoryAPI.listSuppliers as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: { results: [{ id: 50, name: 'Acme Supplies' }, { id: 51, name: 'Beta Parts' }] },
    });
    (kitAPI.getKit as ReturnType<typeof vi.fn>).mockResolvedValue({ data: KIT_TWO_VENDORS });

    renderDetail();
    await waitFor(() => expect(screen.getByTestId('kit-detail-page')).toBeInTheDocument());

    // Acme is the chosen supplier and the boxes hold Acme's terms, so the line names Acme.
    await waitFor(() =>
      expect(screen.getByTestId('kit-supplier-attribution')).toHaveTextContent(
        /Showing Acme Supplies/,
      ),
    );

    await user.clear(screen.getByTestId('kit-supplier'));
    await user.type(screen.getByTestId('kit-supplier'), '51');

    // The boxes now hold Beta's terms, so the label has to name Beta. A part
    // number shown under the wrong vendor's name is worse than an unattributed
    // one: it gets pasted into an order form with no cue that it is wrong.
    await waitFor(() =>
      expect(screen.getByTestId('kit-supplier-attribution')).toHaveTextContent(
        /Showing Beta Parts/,
      ),
    );
    expect(screen.getByTestId('kit-supplier-attribution')).not.toHaveTextContent(/Acme/);
  });
});
