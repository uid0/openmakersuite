/**
 * A kit's supplier SKU says WHOSE it is (op-3xsp).
 *
 * `KitSerializer` subclasses `InventoryItemSerializer`, so `kit.supplier_sku`
 * is the flat legacy accessor: one particular vendor's part number for the kit,
 * resolved through the shared derivation but arriving with no vendor attached.
 *
 * That is worse than an unattributed price. A price is read; a SKU is PASTED
 * INTO A VENDOR'S ORDER FORM. An operator who copies this cell and enters it at
 * a different supplier has ordered the wrong thing.
 *
 * The SKU itself is unchanged in both places, and so is what saving does. What
 * is new is the attribution: both screens now name the vendor the SKU is for.
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
   * The read-side defect this fixes. The SKU and the cost were pre-filled from
   * one particular vendor with nothing on screen saying which, so the part
   * number an operator copied out carried no vendor beside it.
   */
  it('BEFORE/AFTER: names on screen whose terms are being read', async () => {
    await renderForm({ ...KIT, supplier_choice: choice() });

    expect(screen.getByTestId('kit-supplier-attribution')).toHaveTextContent(
      /Showing Acme Supplies’s terms/,
    );
    expect(screen.getByTestId('kit-supplier-attribution')).toHaveTextContent(
      /Enter a Supplier below to save these terms against that vendor/,
    );
  });

  it('does not pre-fill Supplier — naming the vendor is read-only', async () => {
    await renderForm({ ...KIT, supplier_choice: choice() });

    expect(screen.getByTestId('kit-supplier')).toHaveValue('');
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

  it('CONTROL: attributes nothing when the server named no vendor', async () => {
    await renderForm({
      ...KIT,
      supplier_choice: choice({
        item_supplier_id: null,
        supplier_name: null,
        basis: null,
        reason: 'none_orderable',
      }),
    });

    expect(screen.queryByTestId('kit-supplier-attribution')).not.toBeInTheDocument();
  });
});
