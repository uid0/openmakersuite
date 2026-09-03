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
 *
 * Neither kit route is behind `RequireAuth` and `KitViewSet` serves reads to
 * anyone, so both screens gate on a token: a logged-out visitor gets neither
 * the SKU nor the vendor. The describes below sign in, because the columns
 * they exist to test are the signed-in ones.
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

beforeEach(() => {
  localStorage.clear();
});

describe('the kit list supplier-SKU column', () => {
  const renderList = async (kit: Record<string, unknown>) => {
    localStorage.setItem('token', 'test-token');
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
    localStorage.setItem('token', 'test-token');
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

/**
 * `/inventory/kits`, `/inventory/kits/new` and `/inventory/kits/:kitId` are
 * registered in App.tsx with NO `RequireAuth`, and `KitViewSet` serves reads to
 * anyone, so a logged-out visitor reaches both screens.
 *
 * They must see NEITHER the part number NOR the vendor. A count of the other
 * vendors would not have been a fix: the hazard is a SKU nobody can trace back
 * to a vendor, and a count names none. For a viewer we will not attribute the
 * SKU for, withhold it — which narrows what anonymous visitors see rather than
 * widening it.
 */
describe('what a logged-out visitor sees on the kit surfaces', () => {
  const KIT_WITH_ALTERNATIVES = {
    ...KIT,
    supplier_choice: choice({
      alternatives: [
        { id: 12, supplier_name: 'Beta Parts' },
        { id: 13, supplier_name: 'Gamma Wholesale' },
      ],
    }),
  };

  const renderListAnonymously = async () => {
    localStorage.removeItem('token');
    (kitAPI.listKits as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: { results: [KIT_WITH_ALTERNATIVES] },
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

  const renderFormAnonymously = async () => {
    localStorage.removeItem('token');
    (inventoryAPI.listSuppliers as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: { results: [{ id: 50, name: 'Acme Supplies' }] },
    });
    (kitAPI.getKit as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: KIT_WITH_ALTERNATIVES,
    });
    render(
      <MantineProvider>
        <MemoryRouter initialEntries={['/inventory/kits/k1']}>
          <Routes>
            <Route path="/inventory/kits/:kitId" element={<KitDetailPage />} />
          </Routes>
        </MemoryRouter>
      </MantineProvider>,
    );
    await waitFor(() => expect(screen.getByTestId('kit-name')).toHaveValue('Ink Kit'));
  };

  it('BEFORE/AFTER: the kit list shows a logged-out visitor no SKU and no vendor', async () => {
    await renderListAnonymously();

    expect(screen.queryByTestId('kit-supplier-sku-k1')).not.toBeInTheDocument();
    expect(screen.queryByTestId('kit-supplier-k1')).not.toBeInTheDocument();
    expect(screen.queryByText('ACME-INK-9')).not.toBeInTheDocument();
    expect(screen.queryByText(/Acme Supplies/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Beta Parts/)).not.toBeInTheDocument();
  });

  it('drops the headers too, rather than leaving empty columns', async () => {
    await renderListAnonymously();

    expect(screen.queryByText('Supplier SKU')).not.toBeInTheDocument();
    expect(screen.queryByText('From')).not.toBeInTheDocument();
  });

  // The rest of the row is what it always was — the gate withholds the SKU and
  // its attribution, not the kit.
  it('CONTROL: the kit itself, its cost and its status stay visible', async () => {
    await renderListAnonymously();

    expect(screen.getByText('Ink Kit')).toBeInTheDocument();
    expect(screen.getByText('$42.00')).toBeInTheDocument();
    expect(screen.getByText('Active')).toBeInTheDocument();
  });

  it('BEFORE/AFTER: the kit form shows a logged-out visitor no terms at all', async () => {
    await renderFormAnonymously();

    expect(screen.queryByTestId('kit-supplier-sku')).not.toBeInTheDocument();
    expect(screen.queryByTestId('kit-supplier')).not.toBeInTheDocument();
    expect(screen.queryByTestId('kit-unit-cost')).not.toBeInTheDocument();
    expect(screen.queryByTestId('kit-supplier-attribution')).not.toBeInTheDocument();
    expect(screen.queryByText('Purchase terms')).not.toBeInTheDocument();
    expect(screen.queryByText(/Acme Supplies/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Beta Parts/)).not.toBeInTheDocument();
  });

  it('CONTROL: a signed-in operator still sees both on both screens', async () => {
    localStorage.setItem('token', 'test-token');
    (kitAPI.listKits as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: { results: [KIT_WITH_ALTERNATIVES] },
    });
    const { unmount } = render(
      <MantineProvider>
        <MemoryRouter>
          <KitListPage />
        </MemoryRouter>
      </MantineProvider>,
    );
    await waitFor(() => expect(screen.getByTestId('kit-row-k1')).toBeInTheDocument());
    expect(screen.getByTestId('kit-supplier-sku-k1')).toHaveTextContent('ACME-INK-9');
    expect(screen.getByTestId('kit-supplier-k1')).toHaveTextContent('Acme Supplies, or 2 others');
    unmount();

    (inventoryAPI.listSuppliers as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: { results: [{ id: 50, name: 'Acme Supplies' }] },
    });
    (kitAPI.getKit as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: KIT_WITH_ALTERNATIVES,
    });
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
    expect(screen.getByTestId('kit-supplier-attribution')).toHaveTextContent(
      /Showing Acme Supplies/,
    );
  });
});

/**
 * The note attributes and stops. The save guard is `supplierId && supplierSku`
 * and the SKU box is pre-filled from the CHOSEN link, so an operator who types
 * a different vendor's id writes this vendor's part number onto that one. Copy
 * that reads as an instruction to do exactly that is the defect, not a fix.
 */
describe('the attribution note does not steer the operator into a write', () => {
  it('BEFORE/AFTER: never tells the operator to enter a Supplier', async () => {
    localStorage.setItem('token', 'test-token');
    (inventoryAPI.listSuppliers as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: { results: [{ id: 50, name: 'Acme Supplies' }] },
    });
    (kitAPI.getKit as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: {
        ...KIT,
        supplier_choice: choice({ alternatives: [{ id: 12, supplier_name: 'Beta Parts' }] }),
      },
    });
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
      expect(screen.getByTestId('kit-supplier-attribution')).toBeInTheDocument(),
    );

    const note = screen.getByTestId('kit-supplier-attribution');
    // It still attributes, which is the whole point of the note.
    expect(note).toHaveTextContent(/Showing Acme Supplies/);
    expect(note).toHaveTextContent(/also stocked by Beta Parts/);
    // But it does not direct a write against the vendor it just named.
    expect(note).not.toHaveTextContent(/Enter a Supplier/i);
    expect(note).not.toHaveTextContent(/save these terms/i);
  });
});
