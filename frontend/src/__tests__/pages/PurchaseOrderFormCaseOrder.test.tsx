/**
 * The purchase-order pad names what ordering by the case could not settle.
 *
 * "We are ordering by cases and counting by items" (captain, 2026-09-05). The
 * server sizes a legacy case-based item's `suggested_quantity` as
 * `reorder_cases × case_size` and sends `reorder_display.case_order` beside it.
 * The purchaser is the person about to order, so this is where an item that
 * could NOT be ordered by the case, or whose two reorder columns disagree, has
 * to be said rather than silently prefilled.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import PurchaseOrderFormPage from '../../pages/PurchaseOrderFormPage';
import * as api from '../../services/api';
import { ItemCaseOrder } from '../../types';

vi.mock('../../services/api');

vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useNavigate: () => jest.fn(),
}));

const supplier = {
  id: 1,
  name: 'Test Supplier',
  supplier_type: 'online',
  total_items: 1,
  assets: [] as api.ReorderDataAsset[],
  estimated_total: '0.00',
  avg_lead_time: 5,
};

const itemWith = (caseOrder: ItemCaseOrder | null): api.ReorderDataItem => ({
  item_supplier_id: 1,
  item_id: 'item-1',
  item_name: 'Trash Bags',
  item_sku: 'BAG-001',
  current_stock: 5,
  minimum_stock: 5,
  reorder_quantity: caseOrder?.reorder_quantity ?? 20,
  suggested_quantity: 40,
  unit_cost: '2.50',
  unit_cost_state: 'known',
  unit_cost_detail: null,
  package_cost: null,
  quantity_per_package: 10,
  case_size: 10,
  case_size_state: 'known',
  lead_time_days: 7,
  supplier_sku: 'SUP-001',
  supplier_url: 'https://example.com/item',
  is_primary: true,
  is_selected_ordering_link: true,
  line_total: '100.00',
  count_unit: 'bag',
  reorder_display: {
    mode: 'each',
    unit: 'case',
    threshold: 1,
    current: 0.5,
    reorder_quantity: caseOrder?.reorder_cases ?? 20,
    order_quantity: 40,
    order_text: '4 cases (40 bags)',
    case_order: caseOrder,
    needs_reorder: true,
    text: '0.5 cases on hand · reorder at 1 case',
  },
});

const renderWith = async (item: api.ReorderDataItem) => {
  (api.purchaseOrderAPI.getReorderData as jest.Mock).mockResolvedValue({
    data: { suppliers: [{ ...supplier, items: [item] }] },
  });

  render(
    <MemoryRouter>
      <PurchaseOrderFormPage />
    </MemoryRouter>
  );

  await waitFor(() => expect(screen.getByText('Test Supplier')).toBeInTheDocument());
  fireEvent.click(screen.getByText('Test Supplier').closest('button')!);
  await waitFor(() => expect(screen.getByText('Trash Bags')).toBeInTheDocument());
};

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
  localStorage.setItem('token', 'test-token');
});

test('a line whose columns disagree names both amounts and which one it orders', async () => {
  await renderWith(
    itemWith({
      reorder_cases: 4,
      reorder_quantity: 25,
      order_quantity: 40,
      case_size: 10,
      case_size_state: 'known',
      orders_cases: true,
      columns_disagree: true,
    })
  );

  expect(screen.getByTestId('po-item-case-order-note-item-1')).toHaveTextContent(
    'Reorder Cases and Reorder Quantity disagree: 4 cases of 10 is 40 bags, but Reorder Quantity says 25 bags. Reorders order whole cases (40 bags right now).'
  );
});

test('a line that could not be sized in cases says so and what it ordered instead', async () => {
  await renderWith(
    itemWith({
      reorder_cases: 4,
      reorder_quantity: 25,
      order_quantity: 25,
      case_size: null,
      case_size_state: 'no_orderable_link',
      orders_cases: false,
      columns_disagree: null,
    })
  );

  const note = screen.getByTestId('po-item-case-order-note-item-1');
  expect(note).toHaveTextContent('Cannot order 4 cases: the case size is unknown');
  expect(note).toHaveTextContent('40 bags as before.');
  expect(note).toHaveTextContent(/reactivate a supplier relationship/i);
});

test('a line ordered by the case with agreeing columns carries no note', async () => {
  await renderWith(
    itemWith({
      reorder_cases: 4,
      reorder_quantity: 40,
      order_quantity: 40,
      case_size: 10,
      case_size_state: 'known',
      orders_cases: true,
      columns_disagree: false,
    })
  );

  expect(screen.queryByTestId('po-item-case-order-note-item-1')).not.toBeInTheDocument();
});

test('an alternate supplier line names its own rounded prefill', async () => {
  await renderWith({
    ...itemWith({
      reorder_cases: 4,
      reorder_quantity: 25,
      order_quantity: 40,
      case_size: 10,
      case_size_state: 'known',
      orders_cases: true,
      columns_disagree: true,
    }),
    suggested_quantity: 42,
    quantity_per_package: 6,
    case_size: 6,
    is_primary: false,
    is_selected_ordering_link: false,
  });

  expect(screen.getByTestId('po-item-case-order-note-item-1')).toHaveTextContent(
    "Order sizing uses the selected supplier's 4 cases of 10. Rounded to this supplier's packages of 6, this line prefills 42 bags."
  );
});

test('an alternate supplier with an invalid package size names the unrounded prefill', async () => {
  await renderWith({
    ...itemWith({
      reorder_cases: 4,
      reorder_quantity: 25,
      order_quantity: 40,
      case_size: 10,
      case_size_state: 'known',
      orders_cases: true,
      columns_disagree: true,
    }),
    suggested_quantity: 40,
    quantity_per_package: 0,
    case_size: null,
    case_size_state: 'recorded_zero',
    is_primary: false,
    is_selected_ordering_link: false,
  });

  const note = screen.getByTestId('po-item-case-order-note-item-1');
  expect(note).toHaveTextContent(
    "Order sizing uses the selected supplier's 4 cases of 10. This supplier's package size is unknown, so this line keeps the unrounded prefill of 40 bags."
  );
  expect(note).not.toHaveTextContent('packages of 0');
});

test('an item the case rule does not govern carries no note', async () => {
  await renderWith(itemWith(null));

  expect(screen.queryByTestId('po-item-case-order-note-item-1')).not.toBeInTheDocument();
});
