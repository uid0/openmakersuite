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
 * The purchase-terms card shows the CHOSEN supplier link's figures — the
 * read-only price line, and a SKU box that still seeds from that link — while
 * the Supplier box is free text the operator types. When those two name
 * different vendors, the card says so.
 *
 * That sentence is a claim made to an operator, so it is held to the same
 * standard as a claim in prose: it has to state something a run can contradict.
 * Both halves are asserted here, because a test that only checks the warning
 * appears cannot tell a working condition from one that is always true.
 *
 * It only warns. Saving still writes the typed figures onto the named link —
 * the remaining cross-supplier routes are filed in
 * docs/oms-supplier-cost-write-path-record.md.
 */
describe('the kit form says when the terms on screen belong to another supplier', () => {
  beforeEach(() => localStorage.setItem('token', 'test-token'));
  afterEach(() => localStorage.removeItem('token'));

  const KIT_WITH_LINKS = {
    ...KIT,
    supplier_choice: {
      item_supplier_id: 11,
      supplier_name: 'Acme Supplies',
      basis: 'best_scored',
      reason: null,
      flagged_primary_unorderable: false,
      scored_without_price: false,
      scored_without_history: false,
      alternatives: [],
    },
    suppliers: [
      {
        id: 11,
        supplier: 50,
        supplier_name: 'Acme Supplies',
        supplier_sku: 'T3200',
        unit_cost: '89.99',
        package_cost: '89.99',
        quantity_per_package: 1,
      },
      {
        id: 12,
        supplier: 51,
        supplier_name: 'Beta Parts',
        supplier_sku: 'BETA-9',
        unit_cost: '5.00',
        package_cost: '20.00',
        quantity_per_package: 4,
      },
    ],
  };

  const nameSupplier = async (id: string) => {
    const user = userEvent.setup();
    (kitAPI.getKit as ReturnType<typeof vi.fn>).mockResolvedValue({ data: KIT_WITH_LINKS });

    renderDetail();
    await waitFor(() => expect(screen.getByTestId('kit-supplier-sku')).toHaveValue('T3200'));

    await user.type(screen.getByTestId('kit-supplier'), id);
    await waitFor(() => expect(screen.getByTestId('kit-supplier')).toHaveValue(id));
  };

  it('says nothing while the Supplier box names the kit\u2019s own supplier link', async () => {
    await nameSupplier('50');

    expect(screen.queryByTestId('kit-supplier-differs')).not.toBeInTheDocument();
  });

  it('BEFORE/AFTER: warns when the Supplier box names a different supplier', async () => {
    await nameSupplier('51');

    expect(screen.getByTestId('kit-supplier-differs')).toHaveTextContent(
      'The price shown above is a different supplier\u2019s. Enter this supplier\u2019s own SKU and price.',
    );
  });
});
