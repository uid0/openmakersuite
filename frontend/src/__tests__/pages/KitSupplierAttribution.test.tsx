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
 * anyone, so a logged-out visitor reaches both screens and gets neither the SKU
 * nor the vendor. The describes below sign in, because the columns they exist
 * to test are the signed-in ones.
 *
 * WHERE THE UNIT COST GOES DIFFERS BY SCREEN, and the difference is the server
 * (op-anonymous-read-posture). `unit_cost` is in
 * `InventoryItemSerializer.VENDOR_ONLY_FIELDS`, which `KitSerializer` inherits,
 * so the LIST — which renders a whole column of it — drops that column for a
 * withheld payload, exactly as its sibling `InventoryListPage` does. The kit
 * FORM still reads `kit.unit_cost` on its read-only line; that surface is not
 * rebuilt here, so its anonymous cases still run against a signed-in fixture.
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
  // Call history, not implementations — each render helper sets its own
  // resolved value after this runs. Needed so "did this page call
  // listSuppliers?" is a question about the test that asks it.
  vi.clearAllMocks();
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

  /**
   * WHAT THE SERVER ACTUALLY SENDS a logged-out reader of `/inventory/kits/`
   * (op-anonymous-read-posture). `KitSerializer` inherits
   * `InventoryItemSerializer.VENDOR_ONLY_FIELDS`, so `supplier_sku`,
   * `supplier_choice` AND `unit_cost` are ABSENT, with `vendor_data_withheld`
   * in their place.
   *
   * The anonymous list cases below used to feed `KIT_WITH_ALTERNATIVES` — a
   * signed-in payload — and passed because the page read `isAuthenticated()`
   * instead of the payload. That made them unable to fail for the real reason:
   * the page now reads the marker off the rows, so a fixture carrying vendor
   * keys and no marker would render the columns, which is exactly what these
   * assert it must not do.
   */
  const WITHHELD_KIT = (() => {
    const kit: Record<string, unknown> = {
      ...KIT_WITH_ALTERNATIVES,
      vendor_data_withheld: true,
    };
    for (const key of ['supplier_sku', 'supplier_choice', 'unit_cost']) {
      delete kit[key];
    }
    return kit;
  })();

  const renderListAnonymously = async () => {
    localStorage.removeItem('token');
    (kitAPI.listKits as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: { results: [WITHHELD_KIT] },
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

  /**
   * REGRESSION (op-anonymous-read-posture). `SupplierViewSet` became
   * `IsAuthenticated`, and this page fetched `listSuppliers()` on mount
   * unconditionally. For a logged-out visitor that answered 401, and the
   * response interceptor — finding no refresh token — cleared localStorage and
   * dispatched `oms:session-expired`, so `SessionExpiredBanner` told somebody
   * who had never signed in that their session had expired. The kit detail
   * route stays PUBLIC (`KitViewSet` is `IsAuthenticatedOrReadOnly`), so the
   * fix is to skip the request, not to guard the page.
   */
  it('does not fetch the supplier list at all', async () => {
    await renderFormAnonymously();

    expect(inventoryAPI.listSuppliers).not.toHaveBeenCalled();
    expect(screen.getByTestId('kit-name')).toHaveValue('Ink Kit');
  });

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

  /**
   * THE UNIT COST COLUMN DROPS TOO, and that is a change from what this case
   * used to assert. `unit_cost` is in `VENDOR_ONLY_FIELDS`, so the key is
   * absent for this reader and `kit.unit_cost != null ? ... : '—'` printed
   * '—' in every row — "no price recorded" about the KIT, where the truth is
   * about the READER. Its sibling `InventoryListPage` drops the same column
   * for the same reason; the two screens have to agree.
   */
  it('drops the Unit cost column rather than dashing every row', async () => {
    await renderListAnonymously();

    expect(screen.queryByText('Unit cost')).not.toBeInTheDocument();
    expect(screen.queryByText(/\$/)).not.toBeInTheDocument();
    // ...and the row loses the cell with it, so nothing slides left under the
    // wrong header.
    const headers = screen.getAllByRole('columnheader').length;
    const cells = screen.getByTestId('kit-row-k1').querySelectorAll('td').length;
    expect(cells).toBe(headers);
  });

  // The gate withholds the vendor block, not the kit.
  it('CONTROL: the kit itself and its status stay visible', async () => {
    await renderListAnonymously();

    expect(screen.getByText('Ink Kit')).toBeInTheDocument();
    expect(screen.getByText('Active')).toBeInTheDocument();
    expect(screen.getByTestId('kit-row-k1')).toBeInTheDocument();
  });

  it('BEFORE/AFTER: the kit form shows a logged-out visitor no SKU and no vendor', async () => {
    await renderFormAnonymously();

    expect(screen.queryByTestId('kit-supplier-sku')).not.toBeInTheDocument();
    expect(screen.queryByTestId('kit-supplier')).not.toBeInTheDocument();
    expect(screen.queryByTestId('kit-supplier-attribution')).not.toBeInTheDocument();
    expect(screen.queryByText('ACME-INK-9')).not.toBeInTheDocument();
    expect(screen.queryByText(/Acme Supplies/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Beta Parts/)).not.toBeInTheDocument();
  });

  // The boundary is NAMING a supplier, not showing a number that came from one.
  // The kit list has always shown this same visitor the same $42.00 one click
  // earlier; withholding it here would make the two screens disagree about one
  // kit, and it is not disclosure the acceptance record asks to narrow.
  //
  // The assertion MOVED from the editable Unit cost box to the read-only line
  // beside it, because the box no longer seeds from the kit: it held the CHOSEN
  // supplier's figure while the Supplier box named whoever was typed, so a save
  // wrote one vendor's price onto another's link. The intent is unchanged — an
  // anonymous visitor still sees what the kit costs, and this still fails if the
  // price stops being displayed. Only the element carrying it moved.
  it('CONTROL: the kit form still shows a logged-out visitor the unit cost', async () => {
    await renderFormAnonymously();

    expect(screen.getByTestId('kit-unit-cost-current')).toHaveTextContent('$42.00 per unit');
    expect(screen.getByText('Purchase terms')).toBeInTheDocument();
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
