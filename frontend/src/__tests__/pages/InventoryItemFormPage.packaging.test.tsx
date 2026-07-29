/**
 * Tests for the packaging matrix on InventoryItemFormPage (op-lkxl, phase 3):
 * the pack-chain editor, the count-mode + counting-level pickers, the
 * mode-aware threshold labels, and the two-step save (multipart item, then the
 * nested chain + counting level as JSON — `count_level` is a pk that cannot
 * exist until the chain has been written).
 *
 * The load-bearing invariant, asserted here as well as in the untouched
 * baseline suite: an each-mode item sends exactly what it always sent.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import InventoryItemFormPage from '../../pages/InventoryItemFormPage';
import * as api from '../../services/api';
import { COUNT_MODE_LABELS } from '../../utils/packaging';

vi.mock('../../services/api');

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

vi.mock('../../components/SupplierRelationshipForm', async () => ({
  __esModule: true,
  default: () => <div data-testid="supplier-form" />,
}));

const mockNavigate = jest.fn();
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useNavigate: () => mockNavigate,
}));

const baseItem = {
  id: 'test-id',
  name: 'Copy paper',
  description: '',
  sku: 'PAPER-001',
  category: 1,
  category_name: 'Supplies',
  location: 'Shelf A',
  current_stock: 450,
  minimum_stock: 2,
  reorder_quantity: 4,
  unit_cost: '0.01',
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
  total_value: '4.50',
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
  ownership_type: 'space' as const,
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
  base_unit: 'sheet',
  count_mode: 'each' as const,
  count_level: null,
  open_container_count: 0,
  packaging_levels: [],
};

const PAPER_CHAIN = [
  { id: 11, name: 'case', sort_order: 0, base_units: 1000, per_parent: 10 },
  { id: 12, name: 'ream', sort_order: 1, base_units: 100, per_parent: 100 },
  { id: 13, name: 'sheet', sort_order: 2, base_units: 1, per_parent: null },
];

beforeEach(() => {
  jest.clearAllMocks();
  (api.inventoryAPI.listCategories as jest.Mock).mockResolvedValue({
    data: { results: [{ id: 1, name: 'Supplies', slug: 'supplies', description: '', parent: null }] },
  });
  (api.inventoryAPI.listLocations as jest.Mock).mockResolvedValue({
    data: { results: [{ id: 1, name: 'Shelf A', description: '', is_active: true }] },
  });
  (api.inventoryAPI.listSuppliers as jest.Mock).mockResolvedValue({ data: { results: [] } });
  (api.inventoryAPI.getItemSuppliers as jest.Mock).mockResolvedValue({ data: { results: [] } });
});

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

const renderEdit = (item: Record<string, unknown>) => {
  (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({ data: item });
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

// Only the name has no usable default, and every fireEvent re-renders the whole
// form — so the create tests stay under the 5s budget by typing just that.
const fillRequiredFields = () => {
  fireEvent.change(screen.getAllByLabelText(/^Name/i)[0], { target: { value: 'Copy paper' } });
};

/** Type a whole chain into the editor, adding rows as needed. */
const enterChain = (rows: [string, string][]) => {
  rows.forEach(([name, size], index) => {
    fireEvent.click(screen.getByTestId('packaging-add-level'));
    fireEvent.change(screen.getByTestId(`packaging-row-name-${index}`), {
      target: { value: name },
    });
    fireEvent.change(screen.getByTestId(`packaging-row-base-units-${index}`), {
      target: { value: size },
    });
  });
};

/**
 * Open a Mantine Select and return its own options.
 *
 * Deliberately plain attribute queries rather than `findByRole('option')`: role
 * queries compute the accessibility tree over the whole document, and this
 * form's DOM is big enough that a handful of them push a test past vitest's 5s
 * budget when the suite runs in parallel. Scoped through the input's
 * `aria-controls` because every select on the page keeps its options mounted
 * once opened.
 */
const openOptions = (testId: string): HTMLElement[] => {
  const input = screen.getByTestId(testId);
  fireEvent.click(input);
  const dropdownId = input.getAttribute('aria-controls');
  const dropdown = dropdownId ? document.getElementById(dropdownId) : null;
  return Array.from(
    (dropdown ?? document).querySelectorAll<HTMLElement>('[data-combobox-option]')
  );
};

/** Pick an option out of a Mantine Select by its label. */
const chooseOption = (testId: string, label: string) => {
  const option = openOptions(testId).find((element) => element.textContent === label);
  if (!option) {
    throw new Error(`no "${label}" option in ${testId}`);
  }
  fireEvent.click(option);
};

// This page is the repo's biggest form, and these tests drive it hard (chain
// rows + two selects + a submit). One render already costs ~1s here, so the
// default 5s per-test budget is genuinely tight once the whole suite runs in
// parallel — hence the explicit, generous timeout rather than thinner coverage.
describe('InventoryItemFormPage — units & packaging', { timeout: 30000 }, () => {
  it('renders the packaging section with the chain editor and count-mode picker', async () => {
    renderCreate();

    await waitFor(() => expect(screen.getByTestId('page-hero-title')).toBeInTheDocument());
    expect(screen.getByTestId('packaging-chain-editor')).toBeInTheDocument();
    expect(screen.getByTestId('item-count-mode')).toBeInTheDocument();
    expect(screen.getByLabelText(/Base unit/i)).toBeInTheDocument();
    // The counting level only exists for the pack-counting modes.
    expect(screen.queryByTestId('item-count-level')).not.toBeInTheDocument();
  });

  it('reveals the counting-level select when a pack-counting mode is chosen', async () => {
    renderCreate();

    await waitFor(() => expect(screen.getByTestId('item-count-mode')).toBeInTheDocument());
    enterChain([
      ['case', '1000'],
      ['sheet', '1'],
    ]);
    chooseOption('item-count-mode', COUNT_MODE_LABELS.by_level);

    expect(screen.getByTestId('item-count-level')).toBeInTheDocument();
    // The chain's rungs are the options, in chain order.
    expect(openOptions('item-count-level').map((option) => option.textContent)).toEqual([
      'case',
      'sheet',
    ]);
  });

  it('relabels the reorder thresholds in the counting unit', async () => {
    renderCreate();

    await waitFor(() => expect(screen.getByTestId('item-count-mode')).toBeInTheDocument());
    // Each-mode: today's bare labels, with no unit parenthetical.
    expect(screen.getAllByLabelText(/Minimum Stock/i).length).toBeGreaterThan(0);
    expect(screen.queryByLabelText(/Minimum Stock \(/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Reorder Quantity \(/)).not.toBeInTheDocument();

    enterChain([
      ['case', '1000'],
      ['sheet', '1'],
    ]);
    chooseOption('item-count-mode', COUNT_MODE_LABELS.by_level);
    chooseOption('item-count-level', 'case');

    expect(await screen.findByLabelText(/Minimum Stock \(cases\)/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Reorder Quantity \(cases\)/)).toBeInTheDocument();
  });

  it('blocks a save whose chain breaks the shrinking rule, before any request', async () => {
    renderCreate();

    await waitFor(() => expect(screen.getByTestId('item-count-mode')).toBeInTheDocument());
    fillRequiredFields();
    enterChain([
      ['case', '10'],
      ['ream', '10'],
      ['sheet', '1'],
    ]);

    fireEvent.click(screen.getByText('Create Item'));

    expect(
      await screen.findByText(/must hold fewer base units than 'case'/)
    ).toBeInTheDocument();
    expect(api.inventoryAPI.createItem).not.toHaveBeenCalled();
  });

  it('blocks a pack-counting save with no counting level chosen', async () => {
    renderCreate();

    await waitFor(() => expect(screen.getByTestId('item-count-mode')).toBeInTheDocument());
    fillRequiredFields();
    enterChain([
      ['case', '1000'],
      ['sheet', '1'],
    ]);
    chooseOption('item-count-mode', COUNT_MODE_LABELS.open_closed);

    fireEvent.click(screen.getByText('Create Item'));

    expect(await screen.findByText(/Choose which packaging level/)).toBeInTheDocument();
    expect(api.inventoryAPI.createItem).not.toHaveBeenCalled();
  });

  it('saves the chain then the counting level, in that order', async () => {
    (api.inventoryAPI.createItem as jest.Mock).mockResolvedValue({
      data: { ...baseItem, id: 'new-id', packaging_levels: [] },
    });
    // What the server gives back for the two rungs typed below.
    (api.inventoryAPI.updateItem as jest.Mock).mockResolvedValue({
      data: {
        ...baseItem,
        id: 'new-id',
        packaging_levels: [
          { id: 11, name: 'case', sort_order: 0, base_units: 1000, per_parent: 1000 },
          { id: 13, name: 'sheet', sort_order: 1, base_units: 1, per_parent: null },
        ],
      },
    });

    renderCreate();

    await waitFor(() => expect(screen.getByTestId('item-count-mode')).toBeInTheDocument());
    fillRequiredFields();
    fireEvent.change(screen.getByLabelText(/Base unit/i), { target: { value: 'sheet' } });
    enterChain([
      ['case', '1000'],
      ['sheet', '1'],
    ]);
    chooseOption('item-count-mode', COUNT_MODE_LABELS.by_level);
    // The INNER rung, so resolving its pk cannot pass by accident on "first".
    chooseOption('item-count-level', 'sheet');

    fireEvent.click(screen.getByText('Create Item'));

    await waitFor(() => expect(api.inventoryAPI.updateItem).toHaveBeenCalledTimes(2));

    // The item itself still goes as multipart, and carries the base unit.
    const formData = (api.inventoryAPI.createItem as jest.Mock).mock.calls[0][0] as FormData;
    expect(formData.get('base_unit')).toBe('sheet');
    expect(formData.get('count_mode')).toBeNull();

    // Then the chain, largest rung first with sort_order = position.
    expect((api.inventoryAPI.updateItem as jest.Mock).mock.calls[0]).toEqual([
      'new-id',
      {
        packaging_levels: [
          { name: 'case', sort_order: 0, base_units: 1000 },
          { name: 'sheet', sort_order: 1, base_units: 1 },
        ],
      },
    ]);

    // Then the mode + the pk of the chosen rung, resolved from the saved chain
    // by sort_order (position), which is the identity the serializer upserts on.
    expect((api.inventoryAPI.updateItem as jest.Mock).mock.calls[1]).toEqual([
      'new-id',
      { count_mode: 'by_level', count_level: 13 },
    ]);
    expect(mockNavigate).toHaveBeenCalledWith('/inventory/items/new-id');
  });

  it('folds the mode into the chain write when dropping back to each', async () => {
    (api.inventoryAPI.updateItem as jest.Mock).mockResolvedValue({
      data: { ...baseItem, packaging_levels: [] },
    });

    renderEdit({
      ...baseItem,
      count_mode: 'by_level',
      count_level: 11,
      packaging_levels: PAPER_CHAIN,
    });

    await waitFor(() => expect(screen.getByDisplayValue('Copy paper')).toBeInTheDocument());
    // Hydrated: three rungs, counted in cases.
    expect(screen.getAllByTestId(/^packaging-row-\d+$/)).toHaveLength(3);
    expect(screen.getByTestId('item-count-level')).toHaveValue('case');

    // Remove the middle rung and go back to counting base units.
    fireEvent.click(screen.getByTestId('packaging-row-remove-1'));
    chooseOption('item-count-mode', COUNT_MODE_LABELS.each);

    fireEvent.click(screen.getByText('Save Changes'));

    // The multipart write, then ONE JSON write carrying chain + mode together:
    // dropping to each clears count_level, so no pk has to be resolved first.
    await waitFor(() => expect(api.inventoryAPI.updateItem).toHaveBeenCalledTimes(2));
    expect((api.inventoryAPI.updateItem as jest.Mock).mock.calls[1]).toEqual([
      'test-id',
      {
        packaging_levels: [
          { name: 'case', sort_order: 0, base_units: 1000 },
          { name: 'sheet', sort_order: 1, base_units: 1 },
        ],
        count_mode: 'each',
        count_level: null,
      },
    ]);
  });

  it('hydrates an open_closed item and re-sends its counting level unchanged', async () => {
    (api.inventoryAPI.updateItem as jest.Mock).mockResolvedValue({
      data: { ...baseItem, packaging_levels: PAPER_CHAIN },
    });

    renderEdit({
      ...baseItem,
      count_mode: 'open_closed',
      count_level: 12,
      open_container_count: 1,
      packaging_levels: PAPER_CHAIN,
    });

    await waitFor(() => expect(screen.getByDisplayValue('Copy paper')).toBeInTheDocument());
    expect(screen.getByTestId('item-count-mode')).toHaveValue(COUNT_MODE_LABELS.open_closed);
    expect(screen.getByTestId('item-count-level')).toHaveValue('ream');
    // Thresholds are read in the counting unit for this mode.
    expect(screen.getByLabelText(/Minimum Stock \(reams\)/)).toBeInTheDocument();

    fireEvent.click(screen.getByText('Save Changes'));

    // Nothing packaging-related changed, so only the multipart write happens.
    await waitFor(() => expect(api.inventoryAPI.updateItem).toHaveBeenCalledTimes(1));
    expect((api.inventoryAPI.updateItem as jest.Mock).mock.calls[0][1]).toBeInstanceOf(FormData);
  });

  it('sends no packaging follow-up for an each-mode item with no chain', async () => {
    (api.inventoryAPI.updateItem as jest.Mock).mockResolvedValue({ data: baseItem });

    renderEdit(baseItem);

    await waitFor(() => expect(screen.getByDisplayValue('Copy paper')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Save Changes'));

    await waitFor(() => expect(api.inventoryAPI.updateItem).toHaveBeenCalledTimes(1));
    expect((api.inventoryAPI.updateItem as jest.Mock).mock.calls[0][1]).toBeInstanceOf(FormData);
    expect(mockNavigate).toHaveBeenCalledWith('/inventory/items/test-id');
  });

  it('reports a failed packaging follow-up without pretending the item did not save', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    (api.inventoryAPI.createItem as jest.Mock).mockResolvedValue({
      data: { ...baseItem, id: 'new-id' },
    });
    (api.inventoryAPI.updateItem as jest.Mock).mockRejectedValue({
      response: { status: 400, data: { detail: 'chain rejected' } },
    });

    renderCreate();

    await waitFor(() => expect(screen.getByTestId('item-count-mode')).toBeInTheDocument());
    fillRequiredFields();
    enterChain([
      ['case', '1000'],
      ['sheet', '1'],
    ]);
    fireEvent.click(screen.getByText('Create Item'));

    expect(
      await screen.findByText(/Item saved, but the packaging setup failed: chain rejected/)
    ).toBeInTheDocument();
    expect(mockNavigate).not.toHaveBeenCalledWith('/inventory/items/new-id');
    consoleError.mockRestore();
  });
});
