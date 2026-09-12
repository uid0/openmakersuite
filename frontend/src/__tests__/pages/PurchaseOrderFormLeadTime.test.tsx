/**
 * A lead time nobody supplied is never rendered as the supplier's own figure
 * (oms-lead-time-default-seven).
 *
 * `ItemSupplier.average_lead_time` is NOT NULL with a default of 7, so the
 * column has exactly two honest readings and this file pins both on the
 * purchase-order form's manually-searched lines:
 *
 * * a recorded `0` means "arrives same day" — a counter-pickup vendor — and
 *   must survive to the screen. The `|| 7` this replaces rewrote it into a
 *   week, so the one supplier an operator could have walked to collect from
 *   read exactly like a supplier who quoted seven days;
 * * a payload carrying NO lead time at all is an absence, and says so. It is
 *   not a seven.
 *
 * The same table cell is filled from two paths — the server's `reorder_data`,
 * which passes `average_lead_time` through verbatim, and the product search
 * below the list, which did not. They now agree.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import PurchaseOrderFormPage from '../../pages/PurchaseOrderFormPage';
import * as api from '../../services/api';

vi.mock('../../services/api');

const mockNavigate = jest.fn();
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useNavigate: () => mockNavigate,
}));

/**
 * One line the server already put on the pad. It is here because the item
 * table — and with it the product-search row below it — renders only once the
 * pad holds something; it is also the control, since `reorder_data` fills the
 * same cell through the path that never fabricated anything.
 */
const seededLine: api.ReorderDataItem = {
  item_supplier_id: 1,
  item_id: 'item-1',
  item_name: 'Already On The Pad',
  item_sku: 'AOP-001',
  current_stock: 5,
  minimum_stock: 10,
  reorder_quantity: 20,
  suggested_quantity: 1,
  unit_cost: '2.50',
  unit_cost_state: 'known',
  unit_cost_detail: null,
  package_cost: null,
  quantity_per_package: 1,
  lead_time_days: 3,
  supplier_sku: 'SUP-001',
  supplier_url: 'https://example.com/item',
  is_primary: true,
  line_total: '2.50',
};

const supplier = {
  id: 1,
  name: 'Test Supplier',
  supplier_type: 'online',
  total_items: 1,
  items: [seededLine],
  assets: [] as api.ReorderDataAsset[],
  estimated_total: '2.50',
  avg_lead_time: 5,
};

const inventoryItem = {
  id: 'item-9',
  name: 'Counter Pickup Widget',
  sku: 'CPW-001',
  current_stock: 5,
  minimum_stock: 10,
  reorder_quantity: 20,
};

/** One `ItemSupplier` row as `/inventory/item-suppliers/` serves it. */
const itemSupplierRow = (overrides: Record<string, unknown>) => ({
  id: 99,
  supplier: 1,
  supplier_sku: 'SUP-009',
  supplier_url: 'https://example.com/widget',
  unit_cost: '2.50',
  package_cost: null,
  quantity_per_package: 1,
  is_primary: true,
  is_active: true,
  is_discontinued: false,
  ...overrides,
});

/**
 * Render the form, pick the supplier, then add `row` through the product
 * search — the path that used to invent the seven.
 */
const addBySearch = async (row: Record<string, unknown>) => {
  (api.purchaseOrderAPI.getReorderData as jest.Mock).mockResolvedValue({
    data: { suppliers: [supplier] },
  });
  (api.inventoryAPI.listItems as jest.Mock).mockResolvedValue({
    data: { results: [inventoryItem] },
  });
  (api.inventoryAPI.getItem as jest.Mock).mockResolvedValue({ data: inventoryItem });
  (api.inventoryAPI.getItemSuppliers as jest.Mock).mockResolvedValue({
    data: { results: [row] },
  });

  render(
    <MemoryRouter>
      <PurchaseOrderFormPage />
    </MemoryRouter>
  );

  await waitFor(() => expect(screen.getByText('Test Supplier')).toBeInTheDocument());
  fireEvent.click(screen.getByText('Test Supplier').closest('button')!);
  await waitFor(() => expect(screen.getByText('Already On The Pad')).toBeInTheDocument());

  const search = await screen.findByPlaceholderText(/Search by product name or SKU/i);
  fireEvent.change(search, { target: { value: 'Counter Pickup Widget' } });
  fireEvent.click(screen.getByRole('button', { name: /^Add$/ }));

  await waitFor(() => expect(screen.getByText('Counter Pickup Widget')).toBeInTheDocument());
};

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
  localStorage.setItem('token', 'test-token');
});

describe('a supplier that quotes a same-day lead time', () => {
  test('keeps its recorded 0 rather than being rewritten as a week', async () => {
    await addBySearch(itemSupplierRow({ average_lead_time: 0 }));

    expect(screen.getByTestId('po-line-lead-time-99')).toHaveTextContent('0 days');
    expect(screen.getByTestId('po-line-lead-time-99')).not.toHaveTextContent('7');
  });
});

describe('a supplier link carrying no lead time at all', () => {
  test('says the lead time is not recorded instead of asserting seven days', async () => {
    await addBySearch(itemSupplierRow({ average_lead_time: null }));

    const cell = screen.getByTestId('po-line-lead-time-99');
    expect(cell).toHaveTextContent(/not recorded/i);
    expect(cell).not.toHaveTextContent('7');
  });
});

describe('the line the server put on the pad', () => {
  test('still renders the lead time it was sent, through the same cell', async () => {
    await addBySearch(itemSupplierRow({ average_lead_time: 7 }));

    expect(screen.getByTestId('po-line-lead-time-1')).toHaveTextContent('3 days');
  });
});

describe('a supplier that really did quote a week', () => {
  test('still shows its own seven days', async () => {
    await addBySearch(
      itemSupplierRow({ average_lead_time: 7, average_lead_time_provenance: 'recorded' })
    );

    expect(screen.getByTestId('po-line-lead-time-99')).toHaveTextContent('7 days');
    expect(screen.getByTestId('po-line-lead-time-99')).not.toHaveTextContent('default');
  });

  test('marks the model-provided seven as a planning default', async () => {
    await addBySearch(
      itemSupplierRow({ average_lead_time: 7, average_lead_time_provenance: 'default' })
    );

    expect(screen.getByTestId('po-line-lead-time-99')).toHaveTextContent(
      '7 days (planning default)'
    );
  });

  test('does not guess the provenance of existing rows', async () => {
    await addBySearch(
      itemSupplierRow({ average_lead_time: 7, average_lead_time_provenance: 'unknown' })
    );

    expect(screen.getByTestId('po-line-lead-time-99')).toHaveTextContent(
      '7 days (provenance unknown)'
    );
  });

  test('and a one-day quote is not pluralised', async () => {
    await addBySearch(itemSupplierRow({ average_lead_time: 1 }));

    expect(screen.getByTestId('po-line-lead-time-99')).toHaveTextContent('1 day');
  });
});
