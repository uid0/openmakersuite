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
import { render, screen, waitFor, within } from '@testing-library/react';
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
