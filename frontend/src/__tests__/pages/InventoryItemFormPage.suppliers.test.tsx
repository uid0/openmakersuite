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
  { id: 3, name: 'McMaster', supplier_type: 'other', website: '', notes: '' },
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
  version: 1,
  ...overrides,
});

/** The server's refusal of a write made from a stale copy (`docs/API_ERROR_CONTRACT.md`). */
const STALE_REFUSAL = {
  error: {
    code: 'stale_version',
    message:
      'Someone else changed this supplier link after you loaded it, so your changes were not ' +
      'saved. Your copy is out of date: reload to see the current values, then make your change again.',
    details: { id: 91, sent_version: 1, current_version: 2 },
  },
};

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

/** How many times a phrase appears in what the banner renders. */
const occurrencesIn = (element: HTMLElement, phrase: RegExp): number =>
  (element.textContent ?? '').match(phrase)?.length ?? 0;

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
    // With the version it was loaded at, so a row changed since is refused.
    expect(JSON.parse(request.data as string)).toMatchObject({ supplier_sku: 'ACME-2', version: 1 });
  });

  describe('promoting a supplier to primary', () => {
    const acme = itemSupplier();
    const boltDepot = itemSupplier({
      id: 92,
      supplier: 2,
      supplier_name: 'Bolt Depot',
      supplier_sku: 'BD-9',
      is_primary: false,
    });
    // What the server holds once the promotion has landed: it demoted Acme
    // itself, and that demotion moved Acme's version on.
    const acmeDemoted = { ...acme, is_primary: false, version: 2 };
    const boltDepotPromoted = { ...boltDepot, is_primary: true, version: 2 };

    /** The page loads `[acme, boltDepot]`; every later read answers `afterPromotion`. */
    const renderPromotion = (afterPromotion: Record<string, unknown>[]) => {
      mock.onGet(/\/inventory\/item-suppliers\/\?item_id=/).replyOnce(200, { results: [acme, boltDepot] });
      mock.onPatch('/inventory/item-suppliers/92/').reply(200, boltDepotPromoted);
      renderEdit(afterPromotion);
    };

    it('writes the promotion first and lets the server do the demoting', async () => {
      renderPromotion([acmeDemoted, boltDepotPromoted]);

      await waitFor(() => expect(screen.getByDisplayValue('BD-9')).toBeInTheDocument());
      fireEvent.click(screen.getByRole('button', { name: 'Set as Primary' }));
      save();

      await waitFor(() => expect(mockNavigate).toHaveBeenCalled());
      // The promotion goes first, so the server's own single-primary enforcement
      // does the demoting: the item has exactly one primary from that request on,
      // whatever happens to the rest of the save. Acme's demotion is all that
      // changed on it, and the server has already made it — so nothing more is
      // sent, and in particular nothing the server would refuse as stale.
      expect(supplierWrites('patch').map((request) => request.url)).toEqual([
        '/inventory/item-suppliers/92/',
      ]);
      expect(JSON.parse(supplierWrites('patch')[0].data as string)).toMatchObject({
        is_primary: true,
        version: 1,
      });
      expect(screen.queryByText(/out of date/)).not.toBeInTheDocument();
    });

    it('sends an edit to the demoted row with the version the demotion left it at', async () => {
      mock.onPatch('/inventory/item-suppliers/91/').reply(200, { ...acmeDemoted, supplier_sku: 'ACME-2', version: 3 });
      renderPromotion([acmeDemoted, boltDepotPromoted]);

      await waitFor(() => expect(screen.getByDisplayValue('BD-9')).toBeInTheDocument());
      fireEvent.click(screen.getByRole('button', { name: 'Set as Primary' }));
      fireEvent.change(screen.getByDisplayValue('ACME-1'), { target: { value: 'ACME-2' } });
      save();

      await waitFor(() => expect(mockNavigate).toHaveBeenCalled());
      const [promotion, edit] = supplierWrites('patch');
      expect(promotion.url).toBe('/inventory/item-suppliers/92/');
      expect(edit.url).toBe('/inventory/item-suppliers/91/');
      expect(JSON.parse(edit.data as string)).toMatchObject({
        supplier_sku: 'ACME-2',
        is_primary: false,
        version: 2,
      });
    });

    it('does not adopt a demoted row someone else also changed, so that write is refused', async () => {
      // Someone else re-priced Acme after the page loaded; the demotion then moved
      // its version on again. Adopting the fresh copy would let this page's stale
      // case price overwrite theirs.
      const repricedAndDemoted = { ...acmeDemoted, package_cost: '15.00', version: 3 };
      mock.onPatch('/inventory/item-suppliers/91/').reply(409, STALE_REFUSAL);
      renderPromotion([repricedAndDemoted, boltDepotPromoted]);

      await waitFor(() => expect(screen.getByDisplayValue('BD-9')).toBeInTheDocument());
      fireEvent.click(screen.getByRole('button', { name: 'Set as Primary' }));
      fireEvent.change(screen.getByDisplayValue('ACME-1'), { target: { value: 'ACME-2' } });
      save();

      await waitFor(() => expect(screen.getByText(/Your copy is out of date/)).toBeInTheDocument());
      expect(JSON.parse(supplierWrites('patch')[1].data as string)).toMatchObject({ version: 1 });
      expect(mockNavigate).not.toHaveBeenCalled();
    });
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
    expect(supplierWrites('delete')[0].params).toEqual({ version: 1 });
  });

  it('surfaces a stale removal and never retries it', async () => {
    mock.onDelete('/inventory/item-suppliers/91/').reply(409, STALE_REFUSAL);
    renderEdit([itemSupplier()]);

    await waitFor(() => expect(screen.getByDisplayValue('ACME-1')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /Remove supplier #1/ }));
    save();

    await waitFor(() => expect(screen.getByText(/Your copy is out of date/)).toBeInTheDocument());
    expect(supplierWrites('delete')).toHaveLength(1);
    expect(supplierWrites('delete')[0].params).toEqual({ version: 1 });
    expect(screen.getByRole('button', { name: 'Reload suppliers' })).toBeInTheDocument();
    expect(mockNavigate).not.toHaveBeenCalled();
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

  describe('a supplier changed by someone else after the page loaded', () => {
    // The page loaded 7 days (the planning default) at version 1. Someone else
    // — or the measuring task — has since written 12 days, moving the row to
    // version 2. This page's copy is out of date.
    const asLoaded = itemSupplier({ average_lead_time: 7, average_lead_time_source: 'default' });
    const asStoredNow = itemSupplier({
      supplier_sku: 'ACME-9',
      average_lead_time: 12,
      average_lead_time_source: 'measured',
      version: 2,
    });

    const refuseTheStaleCopy = async () => {
      mock.onGet(/\/inventory\/item-suppliers\/\?item_id=/).replyOnce(200, { results: [asLoaded] });
      mock.onPatch('/inventory/item-suppliers/91/').replyOnce(409, STALE_REFUSAL);
      renderEdit([asStoredNow]);

      await waitFor(() => expect(screen.getByDisplayValue('ACME-1')).toBeInTheDocument());
      fireEvent.change(screen.getByLabelText(/Supplier SKU/), { target: { value: 'ACME-2' } });
      save();
      await waitFor(() => expect(screen.getByText(/Your copy is out of date/)).toBeInTheDocument());
    };

    it('tells the operator their copy is out of date instead of overwriting the newer values', async () => {
      await refuseTheStaleCopy();

      // The write carried the version it loaded — that is what the server refused.
      expect(supplierWrites('patch')).toHaveLength(1);
      expect(JSON.parse(supplierWrites('patch')[0].data as string)).toMatchObject({
        supplier_sku: 'ACME-2',
        average_lead_time: 7,
        version: 1,
      });
      const banner = screen.getByRole('alert');
      expect(banner).toHaveTextContent(/Acme Fasteners — Someone else changed this supplier link/);
      expect(banner).toHaveTextContent(/Saving again will not overwrite it/);
      expect(mockNavigate).not.toHaveBeenCalled();
      // What the operator typed is still in front of them.
      expect(screen.getByLabelText(/Supplier SKU/)).toHaveValue('ACME-2');
    });

    it('never retries on its own, and saving again is refused again rather than forced', async () => {
      await refuseTheStaleCopy();
      mock.onPatch('/inventory/item-suppliers/91/').replyOnce(409, STALE_REFUSAL);

      save();

      await waitFor(() => expect(supplierWrites('patch')).toHaveLength(2));
      expect(JSON.parse(supplierWrites('patch')[1].data as string)).toMatchObject({ version: 1 });
      await waitFor(() => expect(screen.getByText(/Your copy is out of date/)).toBeInTheDocument());
      expect(mockNavigate).not.toHaveBeenCalled();
    });

    it('reloads the suppliers on request, and the next save starts from the current copy', async () => {
      await refuseTheStaleCopy();

      fireEvent.click(screen.getByRole('button', { name: 'Reload suppliers' }));

      await waitFor(() => expect(screen.getByLabelText(/Supplier SKU/)).toHaveValue('ACME-9'));
      expect(screen.getByLabelText(/Average Lead Time/)).toHaveValue(12);
      expect(screen.queryByText(/out of date/)).not.toBeInTheDocument();

      mock.onPatch('/inventory/item-suppliers/91/').reply(200, { ...asStoredNow, supplier_sku: 'ACME-2', version: 3 });
      fireEvent.change(screen.getByLabelText(/Supplier SKU/), { target: { value: 'ACME-2' } });
      save();

      await waitFor(() => expect(mockNavigate).toHaveBeenCalled());
      expect(JSON.parse(supplierWrites('patch')[1].data as string)).toMatchObject({
        supplier_sku: 'ACME-2',
        average_lead_time: 12,
        version: 2,
      });
    });
  });

  it('persists every field the editor offers', async () => {
    mock.onPatch('/inventory/item-suppliers/91/').reply(200, itemSupplier());
    renderEdit([itemSupplier()]);

    await waitFor(() => expect(screen.getByDisplayValue('ACME-1')).toBeInTheDocument());

    // Derived from the DOM, not from a list kept in this file: every text or
    // number input the editor renders gets a distinct value, and every one of
    // them has to come back in the request body. An offered control that is not
    // wired to the payload fails here. Numbers start above anything the fixture
    // loaded: a box typed back to its loaded value is not a change.
    const typed = new Map<string, string>();
    editableInputs().forEach((input, index) => {
      const value =
        input.getAttribute('type') === 'number' ? String(index + 100) : `edited-${index}`;
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
      // Not primary: the editor posted it as a second, non-primary row, and the
      // server never answers with two primaries on one item.
      return [201, itemSupplier({ id: 94, supplier: 2, item: 'new-id', is_primary: false })];
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

    // The button now reads 'Save Changes': the item exists, so the retry
    // updates it rather than creating a second one.
    save();

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

  it('stops promising a create once the partially-failed save has made the item', async () => {
    mock.onPost('/inventory/items/').reply(201, { ...baseItem, id: 'new-id' });
    mock.onPatch('/inventory/items/new-id/').reply(200, { ...baseItem, id: 'new-id' });
    mock.onPost('/inventory/item-suppliers/').reply(500, {});
    renderCreate();

    await waitFor(() => expect(screen.getByTestId('page-hero-title')).toBeInTheDocument());
    fireEvent.change(screen.getAllByLabelText(/^Name/i)[0], { target: { value: 'Hex bolt' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add Supplier' }));
    await chooseSupplier('Acme Fasteners');
    fireEvent.change(screen.getByLabelText(/Supplier SKU/), { target: { value: 'A-NEW' } });
    create();

    // The item landed and the supplier row did not, so the operator is still
    // on the form — but the item now exists and the next press PATCHes it.
    await waitFor(() => expect(screen.getByText(/Acme Fasteners —/)).toBeInTheDocument());
    expect(itemWrites('post')).toHaveLength(1);
    expect(mockNavigate).not.toHaveBeenCalled();

    // The button must say what it will do. Left reading 'Create Item', it
    // tells the operator they are making something that already exists.
    expect(screen.queryByRole('button', { name: /create item/i })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /save changes/i })).toBeInTheDocument();
    expect(screen.getByTestId('page-hero-title')).toHaveTextContent('Edit item');

    save();
    await waitFor(() => expect(itemWrites('patch')).toHaveLength(1));
    expect(itemWrites('patch')[0].url).toBe('/inventory/items/new-id/');
  });

  describe('rows that exchange suppliers', () => {
    const boltDepot = itemSupplier({
      id: 92,
      supplier: 2,
      supplier_name: 'Bolt Depot',
      supplier_sku: 'BD-9',
      is_primary: false,
    });
    const batchWrites = () =>
      mock.history.post.filter((request) => request.url === '/inventory/item-suppliers/batch/');

    /**
     * Load Acme (primary), Bolt Depot and McMaster, then give the first two rows
     * each other's supplier. McMaster is left alone.
     */
    const swapThem = async () => {
      renderEdit([
        itemSupplier(),
        boltDepot,
        itemSupplier({ id: 94, supplier: 3, supplier_name: 'McMaster', supplier_sku: 'MM-1', is_primary: false }),
      ]);
      await waitFor(() => expect(screen.getByDisplayValue('BD-9')).toBeInTheDocument());
      await chooseSupplier('Bolt Depot', 0);
      await chooseSupplier('Acme Fasteners', 1);
      save();
    };

    it('sends the swap as one atomic request instead of refusing it', async () => {
      mock.onPost('/inventory/item-suppliers/batch/').reply(200, {
        links: [
          itemSupplier({ supplier: 2, supplier_name: 'Bolt Depot', version: 2 }),
          { ...boltDepot, supplier: 1, supplier_name: 'Acme Fasteners', version: 2 },
        ],
      });

      await swapThem();

      await waitFor(() => expect(mockNavigate).toHaveBeenCalled());
      // Written one row at a time, whichever PATCH went first would land on a
      // pair the other row still holds — a 400 every retry reproduces. One
      // request carries both, each with the version its row was loaded at, and
      // nothing for the row nobody touched.
      expect(batchWrites()).toHaveLength(1);
      const body = JSON.parse(batchWrites()[0].data as string);
      expect(body.item).toBe('test-id');
      expect(body.links).toHaveLength(2);
      expect(body.links[0]).toMatchObject({ id: 91, supplier: 2, version: 1 });
      expect(body.links[1]).toMatchObject({ id: 92, supplier: 1, version: 1 });
      expect(body.links[0]).not.toHaveProperty('item');
      expect(supplierWrites('patch')).toHaveLength(0);
      expect(supplierWrites('delete')).toHaveLength(0);
      expect(itemWrites('patch')).toHaveLength(1);
      expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });

    it('is refused whole when either row changed since the page loaded, and offers a reload', async () => {
      mock.onPost('/inventory/item-suppliers/batch/').reply(409, {
        error: { ...STALE_REFUSAL.error, details: { id: 92, sent_version: 1, current_version: 2 } },
      });

      await swapThem();

      const banner = await screen.findByRole('alert');
      await waitFor(() =>
        expect(banner).toHaveTextContent(
          /Item saved, but a supplier relationship was not: Acme Fasteners — Someone else changed this supplier link/
        )
      );
      expect(screen.getByRole('button', { name: 'Reload suppliers' })).toBeInTheDocument();
      // Nothing is retried or sent row by row behind the refusal.
      expect(batchWrites()).toHaveLength(1);
      expect(supplierWrites('patch')).toHaveLength(0);
      expect(mockNavigate).not.toHaveBeenCalled();
      // The exchange the operator made is still on the page.
      expect(screen.getByDisplayValue('ACME-1')).toBeInTheDocument();
    });

    it('reports each row a refused batch names, against that row', async () => {
      mock.onPost('/inventory/item-suppliers/batch/').reply(400, {
        error: {
          code: 'validation_failed',
          message: 'One or more fields failed validation.',
          details: { links: [{}, { supplier_url: ['Enter a valid URL.'] }] },
        },
      });

      await swapThem();

      const banner = await screen.findByRole('alert');
      await waitFor(() =>
        expect(banner).toHaveTextContent('Acme Fasteners — Supplier URL: Enter a valid URL.')
      );
      expect(banner).not.toHaveTextContent('Bolt Depot —');
      expect(mockNavigate).not.toHaveBeenCalled();
    });

    it('sends a new row claiming the supplier an existing row vacates in the same request', async () => {
      mock.onPost('/inventory/item-suppliers/batch/').reply(200, {
        links: [
          itemSupplier({ id: 93, supplier_sku: 'A-NEW', is_primary: true }),
          itemSupplier({ supplier: 2, supplier_name: 'Bolt Depot', is_primary: false, version: 2 }),
        ],
      });
      renderEdit([itemSupplier()]);

      await waitFor(() => expect(screen.getByDisplayValue('ACME-1')).toBeInTheDocument());
      // The persisted row moves to Bolt Depot; a brand-new primary row claims the
      // Acme pair it is vacating. Written row by row, the new primary goes first
      // onto a pair the old row still holds — the same dead end as a swap.
      await chooseSupplier('Bolt Depot', 0);
      fireEvent.click(screen.getByRole('button', { name: 'Add Supplier' }));
      await chooseSupplier('Acme Fasteners', 1);
      fireEvent.change(screen.getAllByLabelText(/Supplier SKU/)[1], { target: { value: 'A-NEW' } });
      fireEvent.click(screen.getByRole('button', { name: 'Set as Primary' }));
      save();

      await waitFor(() => expect(mockNavigate).toHaveBeenCalled());
      const [newRow, oldRow] = JSON.parse(batchWrites()[0].data as string).links;
      expect(newRow).toMatchObject({ supplier: 1, supplier_sku: 'A-NEW', is_primary: true });
      expect(newRow).not.toHaveProperty('id');
      expect(newRow).not.toHaveProperty('version');
      expect(oldRow).toMatchObject({ id: 91, supplier: 2, is_primary: false, version: 1 });
      expect(supplierWrites('post').filter((request) => request.url === '/inventory/item-suppliers/')).toHaveLength(0);
      expect(supplierWrites('patch')).toHaveLength(0);
    });

    it('still refuses a supplier two rows would share once the save is complete', async () => {
      renderEdit([itemSupplier(), boltDepot]);

      await waitFor(() => expect(screen.getByDisplayValue('BD-9')).toBeInTheDocument());
      // Not an exchange: Bolt Depot keeps its supplier, so no batch can make
      // room — a real conflict, refused even though only the other row changed.
      await chooseSupplier('Bolt Depot', 0);
      save();

      const banner = await screen.findByText(/listed twice/);
      expect(banner).toHaveTextContent(
        'Bolt Depot is listed twice (Supplier #1 and #2); an item can only link a supplier once.'
      );
      expect(batchWrites()).toHaveLength(0);
      expect(supplierWrites('patch')).toHaveLength(0);
      expect(itemWrites('patch')).toHaveLength(0);
      expect(mockNavigate).not.toHaveBeenCalled();
    });
  });

  it('permits a chain reassignment the write order makes legal', async () => {
    mock.onPatch(/\/inventory\/item-suppliers\/9[12]\/$/).reply(200, itemSupplier());
    renderEdit([
      itemSupplier({ is_primary: false }),
      itemSupplier({
        id: 92,
        supplier: 2,
        supplier_name: 'Bolt Depot',
        supplier_sku: 'BD-9',
        is_primary: true,
      }),
    ]);

    await waitFor(() => expect(screen.getByDisplayValue('BD-9')).toBeInTheDocument());
    // Not a swap: the primary row vacates Bolt Depot for McMaster and is written
    // first, so the pair the other row moves onto is already free by its turn.
    // The server accepts both, so refusing this would cost two extra saves for
    // nothing — and would throw away this submit's item edits as well.
    await chooseSupplier('McMaster', 1);
    await chooseSupplier('Bolt Depot', 0);
    save();

    await waitFor(() => expect(supplierWrites('patch')).toHaveLength(2));
    const [first, second] = supplierWrites('patch');
    expect(first.url).toBe('/inventory/item-suppliers/92/');
    expect(JSON.parse(first.data as string)).toMatchObject({ supplier: 3 });
    expect(second.url).toBe('/inventory/item-suppliers/91/');
    expect(JSON.parse(second.data as string)).toMatchObject({ supplier: 2 });
    expect(itemWrites('patch')).toHaveLength(1);
    await waitFor(() => expect(mockNavigate).toHaveBeenCalled());
  });

  it('turns a server unique-together rejection into a reason the operator can act on', async () => {
    // Every collision between the page's own rows is batched or refused before
    // sending, so one the server still reports is a row this page does not show
    // — and DRF's own sentence names nothing.
    mock.onPatch('/inventory/item-suppliers/91/').reply(400, {
      error: {
        code: 'validation_failed',
        message: 'One or more fields failed validation.',
        details: {
          non_field_errors: ['The fields item, supplier must make a unique set.'],
        },
      },
    });
    renderEdit([itemSupplier()]);

    await waitFor(() => expect(screen.getByDisplayValue('ACME-1')).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText(/Supplier SKU/), { target: { value: 'ACME-2' } });
    save();

    const banner = await screen.findByText(/Acme Fasteners — this supplier is already linked/);
    expect(banner).toHaveTextContent(
      "Another row on this item that this page does not show already holds it — reload the page to see the item's current suppliers, then choose again."
    );
    expect(screen.queryByText(/must make a unique set/)).not.toBeInTheDocument();
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

  it('locks the relationship editor while a supplier write is in flight', async () => {
    let release: (reply: [number, unknown]) => void = () => {};
    mock
      .onPatch('/inventory/item-suppliers/91/')
      .reply(() => new Promise<[number, unknown]>((resolve) => {
        release = resolve;
      }));
    renderEdit([itemSupplier()]);

    await waitFor(() => expect(screen.getByDisplayValue('ACME-1')).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText(/Supplier SKU/), { target: { value: 'ACME-2' } });
    save();

    // The save writes back the rows it captured when it started, so anything
    // typed into the editor meanwhile would be reverted without a word.
    await waitFor(() => expect(supplierWrites('patch')).toHaveLength(1));
    await waitFor(() => expect(screen.getByLabelText(/Supplier SKU/)).toBeDisabled());
    expect(screen.getByRole('button', { name: 'Add Supplier' })).toBeDisabled();
    expect(screen.getByRole('button', { name: /Remove supplier #1/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Primary Supplier' })).toBeDisabled();

    release([500, {}]);

    // Back in the operator's hands once it settles — the banner names a row
    // they now have to fix.
    await waitFor(() => expect(screen.getByText(/Acme Fasteners —/)).toBeInTheDocument());
    expect(screen.getByLabelText(/Supplier SKU/)).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Add Supplier' })).toBeEnabled();
    expect(screen.getByRole('button', { name: /Remove supplier #1/ })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Primary Supplier' })).toBeEnabled();
  });

  it('saves an item whose untouched supplier row has a blank SKU', async () => {
    // A blank SKU is reachable on stored rows (the kit serializer writes the
    // relationship without one), and this row sends no request at all — so
    // refusing the save would block an item edit the server would have taken.
    renderEdit([itemSupplier({ supplier_sku: '' })]);

    await loaded();
    fireEvent.change(screen.getAllByLabelText(/^Name/i)[0], { target: { value: 'Hex bolt M6' } });
    save();

    await waitFor(() => expect(mockNavigate).toHaveBeenCalled());
    expect(screen.queryByText(/needs a supplier SKU/)).not.toBeInTheDocument();
    expect(itemWrites('patch')).toHaveLength(1);
    expect(supplierWrites('patch')).toHaveLength(0);
    expect(supplierWrites('post')).toHaveLength(0);
    expect(supplierWrites('delete')).toHaveLength(0);
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

describe('InventoryItemFormPage — derived costs after a partial save', { timeout: 30000 }, () => {
  /**
   * A retry after a partial save must not re-send the pre-derivation unit cost.
   *
   * `ItemSupplier.save()` derives from the DELTA against the stored row
   * (`inventory.services.suppliers.derive_costs`), so a cost box holding a
   * figure the server has already superseded is not inert — it MOVED, and a
   * moved unit cost governs and re-prices the case price. The trace:
   *
   * stored (unit 3.33, package 10.00, pack 3); the operator raises only the
   * case price to 12.00; the server derives and returns (4.00, 12.00); a later
   * row in the same save fails, so the page stays mounted and tells the
   * operator to fix that row and save again. If the Unit Cost box still holds
   * 3.33 on that retry, the row looks dirty, the PATCH carries 3.33, and the
   * case price the operator just set is silently re-priced to 3.33 x 3 = 9.99.
   *
   * That is symptom 5 arriving through the fix for symptom 5, so it is pinned
   * here rather than left to the derivation's own suite: the defect is that
   * this screen keeps a figure the write response already corrected.
   */
  const staleRow = (overrides: Record<string, unknown> = {}) =>
    itemSupplier({
      id: 91,
      supplier: 1,
      supplier_name: 'Acme Fasteners',
      supplier_sku: 'ACME-1',
      unit_cost: '3.33',
      package_cost: '10.00',
      quantity_per_package: 3,
      is_primary: true,
      ...overrides,
    });

  const failingRow = () =>
    itemSupplier({
      id: 92,
      supplier: 2,
      supplier_name: 'Bolt Depot',
      supplier_sku: 'BOLT-9',
      unit_cost: '5.00',
      package_cost: '5.00',
      quantity_per_package: 1,
      is_primary: false,
    });

  /** PATCHes the page sent to the row whose costs the server re-derived. */
  const writesToStaleRow = () =>
    mock.history.patch.filter((request) =>
      /\/inventory\/item-suppliers\/91\/$/.test(request.url ?? '')
    );

  /**
   * PATCHes to the row that fails — the anchor the retry assertions wait on.
   *
   * Strictly DOWNSTREAM of where row 91 would be written: `relationshipWriteOrder`
   * puts the primary row first, so by the time a second write reaches row 92 the
   * write loop has already passed row 91. Waiting on row 91's own count instead
   * would be satisfied by the FIRST save's write and resolve before the retry had
   * issued anything.
   */
  const writesToFailingRow = () =>
    mock.history.patch.filter((request) =>
      /\/inventory\/item-suppliers\/92\/$/.test(request.url ?? '')
    );

  it('adopts the derived unit cost so a retry does not re-price the package', async () => {
    // The server's answer to "package cost moved to 12.00 at pack 3": the case
    // price stands and the unit cost is re-derived from it.
    mock
      .onPatch(/\/inventory\/item-suppliers\/91\/$/)
      .reply(200, staleRow({ unit_cost: '4.00', package_cost: '12.00' }));
    // The second row fails, which is what keeps the page mounted: `onSubmit`
    // reports and returns without navigating.
    mock.onPatch(/\/inventory\/item-suppliers\/92\/$/).reply(400, {
      error: { code: 'validation_failed', message: 'nope', details: { supplier_sku: ['taken'] } },
    });
    renderEdit([staleRow(), failingRow()]);

    await waitFor(() => expect(screen.getByDisplayValue('ACME-1')).toBeInTheDocument());

    fireEvent.change(screen.getAllByLabelText(/^Package Cost$/)[0], {
      target: { value: '12.00' },
    });
    // Make the second row dirty too, so it is actually written and can fail.
    fireEvent.change(screen.getAllByLabelText(/Supplier SKU/)[1], {
      target: { value: 'BOLT-10' },
    });
    save();

    await waitFor(() =>
      expect(screen.getByText(/supplier relationship did not/)).toBeInTheDocument()
    );
    expect(mockNavigate).not.toHaveBeenCalled();
    expect(writesToStaleRow()).toHaveLength(1);

    // The screen must now show what the server stored, not what was typed:
    // this is the value the write response carries so the re-derivation is
    // observable, and the box is what the next save reads from.
    expect(screen.getAllByLabelText(/^Unit Cost$/)[0]).toHaveValue(4);

    save();

    // The operator fixed nothing on row 91, so the retry has nothing to say
    // about it. Before the fix the box still held 3.33, the row looked dirty,
    // and this second PATCH re-priced the case price to 9.99.
    await waitFor(() => expect(writesToFailingRow()).toHaveLength(2));
    expect(writesToStaleRow()).toHaveLength(1);
  });
});
