/**
 * A kit's supplier SKU says WHOSE it is (op-3xsp).
 *
 * `KitSerializer` subclasses `InventoryItemSerializer`, so `kit.supplier_sku`
 * is the flat legacy accessor: one particular vendor's part number for the kit,
 * resolved through the shared derivation but arriving with no vendor attached.
 *
 * That is worse than an unattributed price. A price is read; a SKU is PASTED
 * INTO A VENDOR'S ORDER FORM. An operator who copies this cell and enters it at
 * a different supplier has ordered the wrong thing, and the kit form made it
 * worse still by seeding the SKU and the cost while leaving Supplier blank — so
 * saving wrote vendor A's part number onto vendor B's relationship.
 *
 * The SKU itself is unchanged in both places. What is new is the attribution.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import KitDetailPage from '../../pages/KitDetailPage';
import KitListPage from '../../pages/KitListPage';
import { inventoryAPI, kitAPI } from '../../services/api';

vi.mock('../../services/api', () => ({
  kitAPI: { listKits: vi.fn(), getKit: vi.fn(), createKit: vi.fn(), updateKit: vi.fn() },
  inventoryAPI: { listSuppliers: vi.fn() },
}));

const choice = (overrides: Record<string, unknown> = {}) => ({
  item_supplier_id: 11,
  supplier_id: 50,
  supplier_name: 'Acme Supplies',
  basis: 'best_scored',
  reason: null,
  flagged_primary_unorderable: false,
  scored_without_price: false,
  scored_without_history: false,
  alternatives: [],
  ...overrides,
});

const KIT = {
  id: 'k1',
  name: 'Ink Kit',
  sku: 'KIT-1',
  supplier_sku: 'ACME-INK-9',
  unit_cost: 42,
  component_count: 3,
  components: [],
  is_active: true,
  is_kit: true as const,
};

describe('the kit list supplier-SKU column', () => {
  const renderList = async (kit: Record<string, unknown>) => {
    (kitAPI.listKits as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: { results: [kit] },
    });
    render(
      <MantineProvider>
        <MemoryRouter>
          <KitListPage />
        </MemoryRouter>
      </MantineProvider>,
    );
    await waitFor(() => expect(screen.getByTestId('kit-row-k1')).toBeInTheDocument());
  };

  it('BEFORE/AFTER: names the vendor whose part number this is', async () => {
    await renderList({ ...KIT, supplier_choice: choice() });

    expect(screen.getByTestId('kit-supplier-sku-k1')).toHaveTextContent('ACME-INK-9');
    // The cell that did not exist: paste ACME-INK-9 at Beta Parts and you have
    // ordered the wrong thing.
    expect(screen.getByTestId('kit-supplier-k1')).toHaveTextContent('Acme Supplies');
  });

  it('BEFORE/AFTER: says the kit is stocked by others, so this is not THE SKU', async () => {
    await renderList({
      ...KIT,
      supplier_choice: choice({
        alternatives: [
          { id: 12, supplier_name: 'Beta Parts' },
          { id: 13, supplier_name: 'Gamma Wholesale' },
        ],
      }),
    });

    expect(screen.getByTestId('kit-supplier-k1')).toHaveTextContent('Acme Supplies, or 2 others');
  });

  it('attributes nothing where there is no SKU to attribute', async () => {
    await renderList({ ...KIT, supplier_sku: '', supplier_choice: choice() });

    expect(within(screen.getByTestId('kit-supplier-k1')).getByText('—')).toBeInTheDocument();
  });

  it('says the field was missing rather than naming a vendor it was not told', async () => {
    await renderList({ ...KIT, supplier_choice: undefined });

    expect(screen.getByTestId('kit-supplier-k1')).toHaveTextContent(
      /was not included in this response/i,
    );
    expect(screen.getByTestId('kit-supplier-k1')).not.toHaveTextContent('Acme');
  });
});

describe('the kit form supplier terms', () => {
  const renderForm = async (kit: Record<string, unknown>) => {
    (inventoryAPI.listSuppliers as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: { results: [{ id: 50, name: 'Acme Supplies' }, { id: 51, name: 'Beta Parts' }] },
    });
    (kitAPI.getKit as ReturnType<typeof vi.fn>).mockResolvedValue({ data: kit });
    render(
      <MantineProvider>
        <MemoryRouter initialEntries={['/inventory/kits/k1']}>
          <Routes>
            <Route path="/inventory/kits/:kitId" element={<KitDetailPage />} />
          </Routes>
        </MemoryRouter>
      </MantineProvider>,
    );
    await waitFor(() =>
      expect(screen.getByTestId('kit-supplier-sku')).toHaveValue('ACME-INK-9'),
    );
  };

  /**
   * The write-side defect. Supplier started blank while the SKU and cost were
   * pre-filled from one particular vendor, so typing another vendor's id and
   * saving silently retagged that vendor's part number and price.
   */
  it('BEFORE/AFTER: pre-fills the vendor the seeded SKU and cost belong to', async () => {
    await renderForm({ ...KIT, supplier_choice: choice() });

    expect(screen.getByTestId('kit-supplier')).toHaveValue('50');
  });

  it('BEFORE/AFTER: names on screen whose terms are being edited', async () => {
    await renderForm({ ...KIT, supplier_choice: choice() });

    expect(screen.getByTestId('kit-supplier-attribution')).toHaveTextContent(
      /Showing Acme Supplies’s terms/,
    );
    expect(screen.getByTestId('kit-supplier-attribution')).toHaveTextContent(
      /Changing Supplier below saves these terms against that vendor instead/,
    );
  });

  it('names the other vendors that stock the kit', async () => {
    await renderForm({
      ...KIT,
      supplier_choice: choice({ alternatives: [{ id: 12, supplier_name: 'Beta Parts' }] }),
    });

    expect(screen.getByTestId('kit-supplier-attribution')).toHaveTextContent(
      /also stocked by Beta Parts/,
    );
  });

  it('CONTROL: leaves Supplier blank when the server named no vendor', async () => {
    await renderForm({
      ...KIT,
      supplier_choice: choice({
        item_supplier_id: null,
        supplier_id: null,
        supplier_name: null,
        basis: null,
        reason: 'none_orderable',
      }),
    });

    expect(screen.getByTestId('kit-supplier')).toHaveValue('');
    expect(screen.queryByTestId('kit-supplier-attribution')).not.toBeInTheDocument();
  });
});

/**
 * Seeding Supplier changed what a SAVE means, not just what the form shows.
 *
 * `supplier_terms` is a write, and a destructive one: `_apply_supplier_terms`
 * upserts the link with `is_primary=True` and a default `quantity_per_package`
 * of 1, and `ItemSupplier.save()` then recomputes `package_cost` and logs a
 * price change. A guard that asked "is Supplier filled in?" was true after
 * every load once the field was seeded, so editing the description alone reset
 * a pack size of 25 to 1 and promoted a scored pick to somebody's standing
 * decision — silently, and with no way for the operator to undo it.
 */
describe('what a kit save writes to the supplier link', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  const renderFormFor = async (kit: Record<string, unknown>) => {
    (inventoryAPI.listSuppliers as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: { results: [{ id: 50, name: 'Acme Supplies' }, { id: 51, name: 'Beta Parts' }] },
    });
    (kitAPI.getKit as ReturnType<typeof vi.fn>).mockResolvedValue({ data: kit });
    (kitAPI.updateKit as ReturnType<typeof vi.fn>).mockResolvedValue({ data: kit });
    render(
      <MantineProvider>
        <MemoryRouter initialEntries={['/inventory/kits/k1']}>
          <Routes>
            <Route path="/inventory/kits/:kitId" element={<KitDetailPage />} />
          </Routes>
        </MemoryRouter>
      </MantineProvider>,
    );
    await waitFor(() => expect(screen.getByTestId('kit-supplier')).toHaveValue('50'));
  };

  const save = async () => {
    fireEvent.click(screen.getByTestId('kit-save'));
    await waitFor(() => expect(kitAPI.updateKit).toHaveBeenCalledTimes(1));
    return (kitAPI.updateKit as ReturnType<typeof vi.fn>).mock.calls[0][1] as Record<
      string,
      unknown
    >;
  };

  const KIT_WITH_TERMS = {
    ...KIT,
    components: [{ id: 'c1', component: 'i1', component_name: 'Ink', quantity: 2 }],
    supplier_choice: choice(),
  };

  it('BEFORE/AFTER: an unrelated edit sends no supplier terms at all', async () => {
    await renderFormFor(KIT_WITH_TERMS);

    fireEvent.change(screen.getByTestId('kit-description'), {
      target: { value: 'Now with a longer description' },
    });
    const payload = await save();

    expect(payload.description).toBe('Now with a longer description');
    expect(payload).not.toHaveProperty('supplier_terms');
  });

  it('BEFORE/AFTER: saving a freshly loaded kit untouched writes no terms', async () => {
    await renderFormFor(KIT_WITH_TERMS);

    expect(await save()).not.toHaveProperty('supplier_terms');
  });

  it('CONTROL: pointing the terms at another vendor still writes them', async () => {
    await renderFormFor(KIT_WITH_TERMS);

    fireEvent.change(screen.getByTestId('kit-supplier'), { target: { value: '51' } });
    const payload = await save();

    expect(payload.supplier_terms).toEqual({
      supplier: 51,
      supplier_sku: 'ACME-INK-9',
      unit_cost: '42',
    });
  });

  it('CONTROL: editing the SKU alone still writes the terms', async () => {
    await renderFormFor(KIT_WITH_TERMS);

    fireEvent.change(screen.getByTestId('kit-supplier-sku'), { target: { value: 'ACME-INK-10' } });
    const payload = await save();

    expect(payload.supplier_terms).toMatchObject({ supplier: 50, supplier_sku: 'ACME-INK-10' });
  });

  it('CONTROL: editing the cost alone still writes the terms', async () => {
    await renderFormFor(KIT_WITH_TERMS);

    fireEvent.change(screen.getByTestId('kit-unit-cost'), { target: { value: '43' } });
    const payload = await save();

    expect(payload.supplier_terms).toMatchObject({ supplier: 50, unit_cost: '43' });
  });

  /**
   * op-9m2v: a recorded 0.00 is a KNOWN price — donated stock, a free sample —
   * and has to stay tellable apart from "nobody has priced this". The chosen
   * link's flat `unit_cost` is null when it carries no price, so the box loads
   * empty; the payload must not turn that into the number zero.
   */
  it('BEFORE/AFTER: an unpriced link stays unpriced through a save', async () => {
    await renderFormFor({ ...KIT_WITH_TERMS, unit_cost: null });

    fireEvent.change(screen.getByTestId('kit-supplier-sku'), { target: { value: 'ACME-INK-11' } });
    const payload = await save();

    expect(payload.supplier_terms).toEqual({ supplier: 50, supplier_sku: 'ACME-INK-11' });
    expect(payload.supplier_terms).not.toHaveProperty('unit_cost');
  });

  it('CONTROL: a price the operator actually typed as zero is still sent', async () => {
    await renderFormFor({ ...KIT_WITH_TERMS, unit_cost: null });

    fireEvent.change(screen.getByTestId('kit-unit-cost'), { target: { value: '0' } });
    const payload = await save();

    expect(payload.supplier_terms).toMatchObject({ unit_cost: '0' });
  });
});
