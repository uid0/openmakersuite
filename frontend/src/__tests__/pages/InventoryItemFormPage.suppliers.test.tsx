/**
 * Supplier-relationship saving on InventoryItemFormPage.
 *
 * Deliberately different from the sibling suites: the real
 * `SupplierRelationshipForm` is rendered (not stubbed) and the API layer is
 * intercepted at the axios adapter, so every assertion here is about the real
 * HTTP request the real page issues against the real `item-suppliers`
 * endpoints. The page used to carry a `TODO: Implement supplier relationship
 * saving via ItemSupplier API` where those requests belong, so every edit an
 * operator made in that section was dropped on Save without a word.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import MockAdapter from 'axios-mock-adapter';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import InventoryItemFormPage from '../../pages/InventoryItemFormPage';
import api from '../../services/api';
import { SUPPLIER_FIELD_LABELS } from '../../utils/supplierRelationships';

vi.mock('../../utils/dialogs', async () => ({
  promptInput: vi.fn(() => Promise.resolve(null)),
  showError: vi.fn(),
}));

vi.mock('qrcode.react', async () => ({
  QRCodeSVG: () => <div data-testid="qr-code">QR Code</div>,
}));

vi.mock('../../components/NFPADiamond', async () => ({
  __esModule: true,
  default: () => <div data-testid="nfpa-diamond">NFPA Diamond</div>,
}));

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useNavigate: () => mockNavigate,
}));

const SUPPLIERS = [
  { id: 1, name: 'Acme Fasteners', supplier_type: 'amazon', website: '', notes: '' },
  { id: 2, name: 'Bolt Depot', supplier_type: 'other', website: '', notes: '' },
];

const baseItem = {
  id: 'test-id',
  name: 'Hex bolt',
  description: '',
  sku: 'BOLT-001',
  category: 1,
  category_name: 'Supplies',
  location: null,
  current_stock: 10,
  minimum_stock: 2,
  reorder_quantity: 4,
  unit_cost: '0.10',
  supplier_name: '',
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
  total_value: '1.00',
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
  ownership_type: 'space',
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
  base_unit: 'unit',
  count_mode: 'each',
  count_level: null,
  open_container_count: 0,
  packaging_levels: [],
};

/** One persisted relationship, as `GET /inventory/item-suppliers/?item_id=` returns it. */
const itemSupplier = (overrides: Record<string, unknown> = {}) => ({
  id: 91,
  item: 'test-id',
  item_name: 'Hex bolt',
  supplier: 1,
  supplier_name: 'Acme Fasteners',
  supplier_sku: 'ACME-1',
  supplier_url: '',
  package_upc: '',
  unit_upc: '',
  quantity_per_package: 12,
  package_height: null,
  package_width: null,
  package_length: null,
  package_weight: null,
  package_volume: null,
  unit_weight: null,
  package_dimensions_display: '',
  unit_cost: '1.00',
  package_cost: '12.00',
  average_lead_time: 7,
  is_primary: true,
  is_active: true,
  is_discontinued: false,
  notes: '',
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
  ...overrides,
});

let mock: MockAdapter;

beforeEach(() => {
  vi.clearAllMocks();
  mock = new MockAdapter(api, { onNoMatch: 'throwException' });
  mock.onGet('/inventory/categories/').reply(200, {
    results: [{ id: 1, name: 'Supplies', slug: 'supplies', description: '', parent: null }],
  });
  mock.onGet('/inventory/locations/').reply(200, { results: [] });
  mock.onGet('/inventory/suppliers/').reply(200, { results: SUPPLIERS });
});

afterEach(() => {
  mock.restore();
});

/** Render the edit form for `test-id` with the given persisted relationships. */
const renderEdit = (relationships: Record<string, unknown>[]) => {
  mock.onGet('/inventory/items/test-id/').reply(200, baseItem);
  mock.onGet(/\/inventory\/item-suppliers\/\?item_id=/).reply(200, { results: relationships });
  mock.onPatch('/inventory/items/test-id/').reply(200, baseItem);
  return render(
    <MantineProvider env="test">
      <MemoryRouter initialEntries={['/inventory/items/test-id/edit']}>
        <Routes>
          <Route path="/inventory/items/:id/edit" element={<InventoryItemFormPage />} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>
  );
};

/** Render the create form, the way an operator reaches it from the item list. */
const renderCreate = () =>
  render(
    <MantineProvider env="test">
      <MemoryRouter initialEntries={['/inventory/items/new']}>
        <Routes>
          <Route path="/inventory/items/new" element={<InventoryItemFormPage />} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>
  );

const save = () => fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

const create = () => fireEvent.click(screen.getByRole('button', { name: /create item/i }));

/** Item writes the page sent, by verb — a create is a POST to the collection. */
const itemWrites = (method: 'post' | 'patch') =>
  mock.history[method].filter((request) => /^\/inventory\/items\//.test(request.url ?? ''));

/**
 * Wait for the item itself to land in the form.
 *
 * Not for any supplier control: the whole editor renders before `loadItem`
 * resolves, so waiting on one of its buttons would race the `reset()` that
 * fills the item fields and submit an empty, invalid form.
 */
const loaded = () => waitFor(() => expect(screen.getByDisplayValue('Hex bolt')).toBeInTheDocument());

/** Field name for a rendered editor input, via the labels the editor shows. */
const fieldNameFor = (input: HTMLElement): string => {
  const label = document.querySelector(`label[for="${input.getAttribute('id')}"]`);
  const text = (label?.textContent ?? '').replace(/\*$/, '').trim();
  const field = Object.keys(SUPPLIER_FIELD_LABELS).find(
    (name) => SUPPLIER_FIELD_LABELS[name] === text
  );
  if (!field) {
    throw new Error(`the relationship editor offers "${text}" but nothing maps it to a field`);
  }
  return field;
};

/**
 * Every free-text/number control the relationship editor renders for row 0.
 *
 * Read off the DOM rather than listed here, so a control added to the editor is
 * covered by the round-trip test without anyone remembering to add it.
 */
const editableInputs = (): HTMLElement[] =>
  // Every labelled input except the supplier combobox, which is not typed into
  // (its own persistence is covered by the create test). Not filtered by
  // `type`, because Mantine's TextInput renders none.
  Array.from(
    document.querySelectorAll<HTMLElement>('input:not([role="combobox"])')
  ).filter((input) => {
    const label = document.querySelector(`label[for="${input.getAttribute('id')}"]`);
    const text = (label?.textContent ?? '').replace(/\*$/, '').trim();
    return Object.values(SUPPLIER_FIELD_LABELS).includes(text);
  });

/**
 * Pick a supplier in the Mantine Select of the nth relationship row.
 *
 * Scoped through the input's own `aria-controls`, and awaited: every select on
 * this page keeps its options mounted once opened, so an unscoped query would
 * happily click another row's identically-labelled option.
 */
const chooseSupplier = async (name: string, row = 0) => {
  // Mantine's Select labels both the input and its wrapper, so filter to the
  // input or row 1 resolves to row 0's wrapper div.
  const input = screen
    .getAllByLabelText(/^Supplier\s*\*$/)
    .filter((element) => element.tagName === 'INPUT')[row];
  // A click that merely dismisses another row's open dropdown does not also
  // open this one, so click until this input owns a dropdown.
  await waitFor(() => {
    if (!input.getAttribute('aria-controls')) {
      fireEvent.click(input);
    }
    expect(input.getAttribute('aria-controls')).toBeTruthy();
  });
  const dropdown = document.getElementById(input.getAttribute('aria-controls') as string);
  const option = Array.from(
    (dropdown ?? document).querySelectorAll<HTMLElement>('[data-combobox-option]')
  ).find((element) => element.textContent === name);
  if (!option) {
    throw new Error(`no "${name}" option in the supplier select for row ${row}`);
  }
  fireEvent.click(option);
};

/** Requests the page actually sent to the item-suppliers endpoints. */
const supplierWrites = (method: 'get' | 'post' | 'patch' | 'delete') =>
  mock.history[method].filter((request) => request.url?.includes('/inventory/item-suppliers/'));

describe('InventoryItemFormPage — supplier relationships', { timeout: 30000 }, () => {
  it('persists an edited field on an existing relationship', async () => {
    mock.onPatch(/\/inventory\/item-suppliers\/91\/$/).reply(200, itemSupplier());
    renderEdit([itemSupplier()]);

    await waitFor(() => expect(screen.getByDisplayValue('ACME-1')).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText(/Supplier SKU/), { target: { value: 'ACME-2' } });
    save();

    await waitFor(() => expect(supplierWrites('patch')).toHaveLength(1));
    const [request] = supplierWrites('patch');
    expect(request.url).toBe('/inventory/item-suppliers/91/');
    expect(JSON.parse(request.data as string)).toMatchObject({ supplier_sku: 'ACME-2' });
  });

  it('promotes a supplier to primary, writing the promotion before the demotion', async () => {
    mock.onPatch(/\/inventory\/item-suppliers\/9[12]\/$/).reply(200, itemSupplier());
    renderEdit([
      itemSupplier(),
      itemSupplier({ id: 92, supplier: 2, supplier_name: 'Bolt Depot', supplier_sku: 'BD-9', is_primary: false }),
    ]);

    await waitFor(() => expect(screen.getByDisplayValue('BD-9')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Set as Primary' }));
    save();

    await waitFor(() => expect(supplierWrites('patch')).toHaveLength(2));
    const [first, second] = supplierWrites('patch');
    // The promotion goes first, so the server's own single-primary enforcement
    // does the demoting: the item has exactly one primary from that request on,
    // whatever happens to the rest of the save.
    expect(first.url).toBe('/inventory/item-suppliers/92/');
    expect(JSON.parse(first.data as string)).toMatchObject({ is_primary: true });
    expect(second.url).toBe('/inventory/item-suppliers/91/');
    expect(JSON.parse(second.data as string)).toMatchObject({ is_primary: false });
    expect(
      supplierWrites('patch').filter((request) => JSON.parse(request.data as string).is_primary === true)
    ).toHaveLength(1);
  });

  it('creates a relationship the operator added, against the saved item', async () => {
    mock.onPost('/inventory/item-suppliers/').reply(201, itemSupplier({ id: 93, supplier: 2 }));
    renderEdit([]);

    await loaded();
    fireEvent.click(screen.getByRole('button', { name: 'Add Supplier' }));
    await chooseSupplier('Bolt Depot');
    fireEvent.change(screen.getByLabelText(/Supplier SKU/), { target: { value: 'BD-NEW' } });
    save();

    await waitFor(() => expect(supplierWrites('post')).toHaveLength(1));
    expect(JSON.parse(supplierWrites('post')[0].data as string)).toMatchObject({
      item: 'test-id',
      supplier: 2,
      supplier_sku: 'BD-NEW',
      is_primary: true,
    });
  });

  it('creates the relationships of a brand-new item against the item it just saved', async () => {
    mock.onGet(/\/inventory\/item-suppliers\/\?item_id=/).reply(200, { results: [] });
    mock.onPost('/inventory/items/').reply(201, { ...baseItem, id: 'new-id' });
    mock.onPost('/inventory/item-suppliers/').reply(201, itemSupplier({ id: 95, supplier: 1 }));
    render(
      <MantineProvider env="test">
        <MemoryRouter initialEntries={['/inventory/items/new']}>
          <Routes>
            <Route path="/inventory/items/new" element={<InventoryItemFormPage />} />
          </Routes>
        </MemoryRouter>
      </MantineProvider>
    );

    await waitFor(() => expect(screen.getByTestId('page-hero-title')).toBeInTheDocument());
    fireEvent.change(screen.getAllByLabelText(/^Name/i)[0], { target: { value: 'Hex bolt' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add Supplier' }));
    await chooseSupplier('Acme Fasteners');
    fireEvent.change(screen.getByLabelText(/Supplier SKU/), { target: { value: 'ACME-NEW' } });
    fireEvent.click(screen.getByRole('button', { name: /create item/i }));

    await waitFor(() => expect(supplierWrites('post')).toHaveLength(1));
    expect(JSON.parse(supplierWrites('post')[0].data as string)).toMatchObject({
      item: 'new-id',
      supplier: 1,
      supplier_sku: 'ACME-NEW',
    });
  });

  it('deletes a relationship the operator removed', async () => {
    mock.onDelete('/inventory/item-suppliers/91/').reply(204);
    renderEdit([itemSupplier()]);

    await waitFor(() => expect(screen.getByDisplayValue('ACME-1')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /Remove supplier #1/ }));
    save();

    await waitFor(() => expect(supplierWrites('delete')).toHaveLength(1));
    expect(supplierWrites('delete')[0].url).toBe('/inventory/item-suppliers/91/');
  });

  it('sends nothing for a relationship the operator did not touch', async () => {
    renderEdit([itemSupplier()]);

    await waitFor(() => expect(screen.getByDisplayValue('ACME-1')).toBeInTheDocument());
    fireEvent.change(screen.getAllByLabelText(/^Name/i)[0], { target: { value: 'Hex bolt M6' } });
    save();

    await waitFor(() => expect(mockNavigate).toHaveBeenCalled());
    expect(supplierWrites('post')).toHaveLength(0);
    expect(supplierWrites('patch')).toHaveLength(0);
    expect(supplierWrites('delete')).toHaveLength(0);
  });

  it('persists every field the editor offers', async () => {
    mock.onPatch('/inventory/item-suppliers/91/').reply(200, itemSupplier());
    renderEdit([itemSupplier()]);

    await waitFor(() => expect(screen.getByDisplayValue('ACME-1')).toBeInTheDocument());

    // Derived from the DOM, not from a list kept in this file: every text or
    // number input the editor renders gets a distinct value, and every one of
    // them has to come back in the request body. An offered control that is not
    // wired to the payload fails here.
    const typed = new Map<string, string>();
    editableInputs().forEach((input, index) => {
      const value = input.getAttribute('type') === 'number' ? String(index + 2) : `edited-${index}`;
      fireEvent.change(input, { target: { value } });
      typed.set(fieldNameFor(input), value);
    });
    expect(typed.size).toBeGreaterThan(0);
    save();

    await waitFor(() => expect(supplierWrites('patch')).toHaveLength(1));
    const body = JSON.parse(supplierWrites('patch')[0].data as string);
    typed.forEach((value, field) => {
      const sent = body[field];
      expect(String(sent), `${field} was not persisted`).toBe(value);
    });
  });

  it('reports the server\'s reason for a rejected relationship and keeps the entry', async () => {
    // The envelope this backend's exception handler emits (`config.api_errors`):
    // the flat `message` says only that validation failed, so the field reason
    // has to be read out of `details` or the operator learns nothing.
    mock.onPatch('/inventory/item-suppliers/91/').reply(400, {
      error: {
        code: 'validation_failed',
        message: 'One or more fields failed validation.',
        details: {
          quantity_per_package: ['Ensure this value is greater than or equal to 1.'],
        },
      },
    });
    renderEdit([itemSupplier()]);

    await waitFor(() => expect(screen.getByDisplayValue('ACME-1')).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText(/Quantity per Package/), { target: { value: '-1' } });
    save();

    await waitFor(() =>
      expect(
        screen.getByText(
          /Acme Fasteners — Quantity per Package: Ensure this value is greater than or equal to 1\./
        )
      ).toBeInTheDocument()
    );
    // Nothing is thrown away and nothing moves on: the operator stays on the
    // page with what they typed still in front of them.
    expect(mockNavigate).not.toHaveBeenCalled();
    expect(screen.getByLabelText(/Quantity per Package/)).toHaveValue(-1);
  });

  it('reports a bare DRF field error too, for endpoints not on the envelope yet', async () => {
    mock.onPatch('/inventory/item-suppliers/91/').reply(400, {
      supplier_url: ['Enter a valid URL.'],
    });
    renderEdit([itemSupplier()]);
    await loaded();

    fireEvent.change(screen.getByLabelText(/Supplier URL/), { target: { value: 'not-a-url' } });
    save();

    await waitFor(() =>
      expect(
        screen.getByText(/Acme Fasteners — Supplier URL: Enter a valid URL\./)
      ).toBeInTheDocument()
    );
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it('does not create the same relationship twice when a failed save is retried', async () => {
    // Echoed back exactly as the editor holds the row, the way the endpoint
    // does, so the retry has no reason to re-send it.
    const createdAcme = {
      id: 93,
      supplier: 1,
      supplier_name: 'Acme Fasteners',
      supplier_sku: 'A-NEW',
      supplier_url: '',
      unit_cost: null,
      package_cost: null,
      quantity_per_package: 1,
      average_lead_time: 0,
      is_primary: true,
    };
    mock.onPost('/inventory/item-suppliers/').reply((config) => {
      const body = JSON.parse(config.data as string);
      return body.supplier === 1
        ? [201, itemSupplier(createdAcme)]
        : [500, {}];
    });
    renderEdit([]);
    await loaded();

    fireEvent.click(screen.getByRole('button', { name: 'Add Supplier' }));
    await chooseSupplier('Acme Fasteners');
    fireEvent.change(screen.getByLabelText(/Supplier SKU/), { target: { value: 'A-NEW' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add Supplier' }));
    await chooseSupplier('Bolt Depot', 1);
    fireEvent.change(screen.getAllByLabelText(/Supplier SKU/)[1], { target: { value: 'B-NEW' } });
    save();

    await waitFor(() => expect(supplierWrites('post')).toHaveLength(2));
    expect(screen.getByText(/Bolt Depot —/)).toBeInTheDocument();

    // The Acme row landed. A retry must not post it again — that would come
    // back as an (item, supplier) uniqueness error the operator cannot act on.
    mock.onPost('/inventory/item-suppliers/').reply(201, itemSupplier({ id: 94, supplier: 2 }));
    save();

    await waitFor(() => expect(supplierWrites('post')).toHaveLength(3));
    expect(
      supplierWrites('post').filter((request) => JSON.parse(request.data as string).supplier === 1)
    ).toHaveLength(1);
  });

  it('updates the item it already created when a create-mode save is retried', async () => {
    // Echoed back exactly as the editor holds it, so the retry has no reason to
    // re-send this row.
    const createdAcme = {
      id: 93,
      supplier: 1,
      supplier_name: 'Acme Fasteners',
      supplier_sku: 'A-NEW',
      supplier_url: '',
      unit_cost: null,
      package_cost: null,
      quantity_per_package: 1,
      average_lead_time: 0,
      is_primary: true,
    };
    let boltFails = true;
    mock.onPost('/inventory/items/').reply(201, { ...baseItem, id: 'new-id' });
    mock.onPatch('/inventory/items/new-id/').reply(200, { ...baseItem, id: 'new-id' });
    mock.onPost('/inventory/item-suppliers/').reply((config) => {
      const body = JSON.parse(config.data as string);
      if (body.supplier === 1) return [201, itemSupplier({ ...createdAcme, item: 'new-id' })];
      if (boltFails) {
        boltFails = false;
        return [500, {}];
      }
      return [201, itemSupplier({ id: 94, supplier: 2, item: 'new-id' })];
    });
    renderCreate();

    await waitFor(() => expect(screen.getByTestId('page-hero-title')).toBeInTheDocument());
    fireEvent.change(screen.getAllByLabelText(/^Name/i)[0], { target: { value: 'Hex bolt' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add Supplier' }));
    await chooseSupplier('Acme Fasteners');
    fireEvent.change(screen.getByLabelText(/Supplier SKU/), { target: { value: 'A-NEW' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add Supplier' }));
    await chooseSupplier('Bolt Depot', 1);
    fireEvent.change(screen.getAllByLabelText(/Supplier SKU/)[1], { target: { value: 'B-NEW' } });
    create();

    // The item and the Acme row landed; the Bolt row did not, so the operator
    // is told which one and stays on the page with both rows in front of them.
    await waitFor(() => expect(screen.getByText(/Bolt Depot —/)).toBeInTheDocument());
    expect(mockNavigate).not.toHaveBeenCalled();

    create();

    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/inventory/items/new-id'));
    // One item, not two: the retry updates the item the first save created,
    // which is the item the already-created Acme row hangs off.
    expect(itemWrites('post')).toHaveLength(1);
    expect(itemWrites('patch')).toHaveLength(1);
    expect(itemWrites('patch')[0].url).toBe('/inventory/items/new-id/');
    // Acme is not created a second time — that would hit the (item, supplier)
    // uniqueness constraint — and Bolt is created against the same item.
    expect(
      supplierWrites('post').filter((request) => JSON.parse(request.data as string).supplier === 1)
    ).toHaveLength(1);
    const boltPosts = supplierWrites('post').filter(
      (request) => JSON.parse(request.data as string).supplier === 2
    );
    expect(boltPosts).toHaveLength(2);
    boltPosts.forEach((request) =>
      expect(JSON.parse(request.data as string)).toMatchObject({ item: 'new-id', supplier: 2 })
    );
  });

  it('refuses a supplier swap before sending it, and names the way out', async () => {
    renderEdit([
      itemSupplier(),
      itemSupplier({
        id: 92,
        supplier: 2,
        supplier_name: 'Bolt Depot',
        supplier_sku: 'BD-9',
        is_primary: false,
      }),
    ]);

    await waitFor(() => expect(screen.getByDisplayValue('BD-9')).toBeInTheDocument());
    // Exchange the two rows' suppliers. Written one row at a time in a fixed
    // order, the first PATCH would land on a pair the other row still holds:
    // a 400 that every identical retry reproduces forever.
    await chooseSupplier('Bolt Depot', 0);
    await chooseSupplier('Acme Fasteners', 1);
    save();

    await waitFor(() =>
      expect(
        screen.getByText(
          /two rows cannot exchange suppliers in one save\. Remove one of those two rows, save, then add it back with the other supplier\./
        )
      ).toBeInTheDocument()
    );
    expect(supplierWrites('patch')).toHaveLength(0);
    expect(supplierWrites('post')).toHaveLength(0);
    expect(supplierWrites('delete')).toHaveLength(0);
    expect(itemWrites('patch')).toHaveLength(0);
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it('names the supplier whose removal the server refused', async () => {
    mock.onDelete('/inventory/item-suppliers/91/').reply(500, {});
    renderEdit([itemSupplier()]);

    await waitFor(() => expect(screen.getByDisplayValue('ACME-1')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /Remove supplier #1/ }));
    save();

    await waitFor(() => expect(supplierWrites('delete')).toHaveLength(1));
    // Several rows can be removed in one save, so a failed removal has to say
    // which one, exactly as a failed create or update does.
    await waitFor(() => expect(screen.getByText(/Acme Fasteners —/)).toBeInTheDocument());
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it('refuses an unfinished row with a reason, before anything is written', async () => {
    renderEdit([]);

    await loaded();
    fireEvent.click(screen.getByRole('button', { name: 'Add Supplier' }));
    save();

    await waitFor(() =>
      expect(screen.getByText(/Supplier #1 has no supplier selected\./)).toBeInTheDocument()
    );
    expect(mock.history.patch.filter((r) => r.url === '/inventory/items/test-id/')).toHaveLength(0);
    expect(supplierWrites('post')).toHaveLength(0);
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it('refuses a row whose SKU the endpoint would reject as blank', async () => {
    renderEdit([]);

    await loaded();
    fireEvent.click(screen.getByRole('button', { name: 'Add Supplier' }));
    await chooseSupplier('Acme Fasteners');
    save();

    await waitFor(() =>
      expect(screen.getByText(/Acme Fasteners needs a supplier SKU\./)).toBeInTheDocument()
    );
    expect(supplierWrites('post')).toHaveLength(0);
  });
});
