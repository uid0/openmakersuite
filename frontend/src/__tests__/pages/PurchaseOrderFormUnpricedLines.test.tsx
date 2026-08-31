/**
 * A price the form does not know must never be summed or shown as $0.00 (op-9m2v).
 *
 * The frontend half of the money falsy-guard class. `reorder_data` now sends
 * `unit_cost: null` where nobody recorded a price — it used to send the string
 * `"0.00"` for that AND for a vendor that charges nothing — and the server
 * REFUSES a purchase-order line it cannot price. This file pins that the form
 * agrees with both halves:
 *
 * * an unpriced line is not folded into the running total as zero, is named to
 *   the operator with the remedy, and blocks submit rather than posting a line
 *   the server would reject;
 * * a genuinely free line (`"0.00"`) is priced, sums as `$0.00`, and submits.
 *
 * The pack-size branch shipped a server-side null against untyped frontend
 * consumers twice and blanked a member-facing page each time; these are the
 * consumer-side tests that class of defect needed.
 */
import { render, screen, waitFor } from '@testing-library/react';
import { fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import PurchaseOrderFormPage from '../../pages/PurchaseOrderFormPage';
import * as api from '../../services/api';

vi.mock('../../services/api');

const mockNavigate = jest.fn();
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useNavigate: () => mockNavigate,
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

const baseItem: api.ReorderDataItem = {
  item_supplier_id: 1,
  item_id: 'item-1',
  item_name: 'Test Item',
  item_sku: 'TEST-001',
  current_stock: 5,
  minimum_stock: 10,
  reorder_quantity: 20,
  suggested_quantity: 4,
  unit_cost: '2.50',
  unit_cost_state: 'known',
  unit_cost_detail: null,
  package_cost: null,
  quantity_per_package: 1,
  lead_time_days: 7,
  supplier_sku: 'SUP-001',
  supplier_url: 'https://example.com/item',
  is_primary: true,
  line_total: '10.00',
};

const renderWith = async (
  item: api.ReorderDataItem,
  supplierOverrides: Partial<typeof supplier> = {}
) => {
  (api.purchaseOrderAPI.getReorderData as jest.Mock).mockResolvedValue({
    data: { suppliers: [{ ...supplier, ...supplierOverrides, items: [item] }] },
  });
  (api.purchaseOrderAPI.createOrder as jest.Mock).mockResolvedValue({
    data: { id: 42, po_number: 'PO-2024-0042', supplier: 1, status: 'draft' },
  });

  render(
    <MemoryRouter>
      <PurchaseOrderFormPage />
    </MemoryRouter>
  );

  await waitFor(() => expect(screen.getByText('Test Supplier')).toBeInTheDocument());
  fireEvent.click(screen.getByText('Test Supplier').closest('button')!);
  await waitFor(() => expect(screen.getByText('Test Item')).toBeInTheDocument());
};

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
  localStorage.setItem('token', 'test-token');
});

describe('a line with no price on file', () => {
  const unpriced: api.ReorderDataItem = {
    ...baseItem,
    unit_cost: null,
    unit_cost_state: 'not_recorded',
    unit_cost_detail:
      'No price is recorded for Test Item from Test Supplier. Add a unit or package cost to that supplier link, or enter the price on this line.',
    line_total: null,
  };

  test('is named to the operator with what to do about it', async () => {
    await renderWith(unpriced);

    const warning = screen.getByTestId('po-unpriced-warning');
    expect(warning).toHaveTextContent('1 line with no price on file');
    expect(warning).toHaveTextContent('Test Item');
    expect(warning).toHaveTextContent(/enter a unit cost/i);
  });

  test('blocks submit rather than posting a line the server refuses', async () => {
    await renderWith(unpriced);

    expect(screen.getByRole('button', { name: /create purchase order/i })).toBeDisabled();
  });

  test('is not summed into the grand total as zero', async () => {
    await renderWith(unpriced);

    // The line contributes nothing to the number AND says so with the "+".
    const total = screen.getByText(/Grand Total/i).closest('.summary-row')!;
    expect(total).toHaveTextContent('$0.00 +');
  });

  test('offers an empty cost box rather than a zero to accept by reflex', async () => {
    await renderWith(unpriced);

    const costInput = screen.getByLabelText(/unit cost for Test Item/i) as HTMLInputElement;
    expect(costInput.value).toBe('');
    expect(costInput.placeholder).toBe('no price on file');
  });

  test('unblocks the moment the operator types a price', async () => {
    await renderWith(unpriced);

    fireEvent.change(screen.getByLabelText(/unit cost for Test Item/i), {
      target: { value: '3.00' },
    });

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /create purchase order/i })).toBeEnabled()
    );
    expect(screen.queryByTestId('po-unpriced-warning')).not.toBeInTheDocument();
  });

  test('marks the supplier card total as only part of the order', async () => {
    await renderWith(unpriced, { estimated_total: '0.00', unpriced_item_count: 1 });

    expect(screen.getByText(/estimated total \(1 unpriced\)/i)).toBeInTheDocument();
  });
});

describe('a line a supplier genuinely gives away', () => {
  const free: api.ReorderDataItem = {
    ...baseItem,
    unit_cost: '0.00',
    unit_cost_state: 'known',
    unit_cost_detail: null,
    line_total: '0.00',
  };

  test('is priced, so nothing warns and submit is offered', async () => {
    await renderWith(free);

    expect(screen.queryByTestId('po-unpriced-warning')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /create purchase order/i })).toBeEnabled();
  });

  test('shows a real $0.00 total rather than a dash', async () => {
    await renderWith(free);

    const total = screen.getByText(/Grand Total/i).closest('.summary-row')!;
    expect(total).toHaveTextContent('$0.00');
    expect(total).not.toHaveTextContent('$0.00 +');
  });

  test('prefills the cost box with the price the vendor actually quotes', async () => {
    await renderWith(free);

    const costInput = screen.getByLabelText(/unit cost for Test Item/i) as HTMLInputElement;
    expect(costInput.placeholder).toBe('0.00');
  });
});

describe('an ordinary priced line', () => {
  test('sums and submits exactly as before — the branch invariant', async () => {
    await renderWith(baseItem);

    const total = screen.getByText(/Grand Total/i).closest('.summary-row')!;
    expect(total).toHaveTextContent('$10.00');
    expect(total).not.toHaveTextContent('+');
    expect(screen.queryByTestId('po-unpriced-warning')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /create purchase order/i })).toBeEnabled();
  });
});


/**
 * A vendor donating an ASSET states a price of zero (op-9m2v). `canSubmit`
 * required `parseFloat(a.unit_cost) > 0`, so that line could not go on an order
 * at all — the button sat disabled with nothing saying why — while the freeform
 * half of the same form already accepted `>= 0` and the server refuses only a
 * MISSING cost.
 */
describe('an asset line the vendor is donating', () => {
  const asset = {
    id: 'asset-1',
    name: 'Donated Lathe',
    asset_tag: 'A-001',
    serial_number: 'SN-1',
    product_url: '',
  };

  const renderWithAsset = async () => {
    await renderWith(baseItem, { assets: [asset] });
    fireEvent.click(screen.getByText('Donated Lathe').closest('tr')!.querySelector('input')!);
    return screen
      .getByText('Donated Lathe')
      .closest('tr')!
      .querySelector('.col-cost input') as HTMLInputElement;
  };

  test('BEFORE/AFTER: a typed $0.00 asset cost no longer blocks the order', async () => {
    const costInput = await renderWithAsset();
    fireEvent.change(costInput, { target: { value: '0' } });

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /create purchase order/i })).toBeEnabled()
    );
  });

  test('CONTROL: an empty asset cost still blocks — a blank is not a price', async () => {
    const costInput = await renderWithAsset();
    fireEvent.change(costInput, { target: { value: '' } });

    expect(screen.getByRole('button', { name: /create purchase order/i })).toBeDisabled();
  });

  test('CONTROL: an ordinary asset price still submits — the branch invariant', async () => {
    const costInput = await renderWithAsset();
    fireEvent.change(costInput, { target: { value: '250' } });

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /create purchase order/i })).toBeEnabled()
    );
  });
});

/**
 * CONTROLs. A freeform line priced at $0.00 is on the order and stays there.
 *
 * These pin behaviour that does NOT move, and they exist because review read
 * `.filter((item) => item.description && item.unit_cost)` as dropping such a
 * line. It does not: `unit_cost` here is form state straight off
 * `e.target.value`, so it is the STRING `"0"`, and `Boolean("0")` is TRUE —
 * only `""` is falsy. The falsy-at-zero trap this branch closes needs a
 * NUMBER, which is why it bites `InventoryItem.unit_cost` (a property-backed
 * `ReadOnlyField`) and not a text input. The coverage gap was real even though
 * the defect was not: nothing here asserted the submitted payload before.
 */
describe('a freeform line the operator prices at zero', () => {
  const addFreeform = async (description: string, cost: string) => {
    await renderWith(baseItem);
    fireEvent.click(screen.getByRole('button', { name: /add item/i }));
    const row = screen.getByPlaceholderText(/item description/i).closest('tr')!;
    fireEvent.change(row.querySelector('.input-description') as HTMLInputElement, {
      target: { value: description },
    });
    fireEvent.change(row.querySelector('.col-cost input') as HTMLInputElement, {
      target: { value: cost },
    });
    return row;
  };

  const submittedItems = async () => {
    fireEvent.click(screen.getByRole('button', { name: /create purchase order/i }));
    await waitFor(() => expect(api.purchaseOrderAPI.createOrder).toHaveBeenCalled());
    return (api.purchaseOrderAPI.createOrder as jest.Mock).mock.calls[0][0].items;
  };

  test('CONTROL: the $0.00 line reaches the created order', async () => {
    await addFreeform('Donated bracket', '0');

    const items = await submittedItems();
    expect(items).toContainEqual(
      expect.objectContaining({ description: 'Donated bracket', unit_cost: 0 })
    );
  });

  test('CONTROL: it prices at $0.00 rather than the dash for unknown', async () => {
    const row = await addFreeform('Donated bracket', '0');

    expect(row.querySelector('.col-total')!.textContent).toContain('$0.00');
    expect(row.querySelector('.col-total')!.textContent).not.toContain('—');
  });

  test('CONTROL: it counts toward the Additional Items subtotal', async () => {
    await addFreeform('Donated bracket', '0');

    expect(screen.getByText(/Additional Items Subtotal \(1 items\)/)).toBeInTheDocument();
  });

  test('CONTROL: a blank cost still blocks submit and is never sent', async () => {
    await addFreeform('Unpriced bracket', '');

    expect(screen.getByRole('button', { name: /create purchase order/i })).toBeDisabled();
    expect(api.purchaseOrderAPI.createOrder).not.toHaveBeenCalled();
  });

  test('CONTROL: an ordinary freeform price is unchanged', async () => {
    const row = await addFreeform('Bracket', '12.50');

    expect(row.querySelector('.col-total')!.textContent).toContain('$12.50');
    const items = await submittedItems();
    expect(items).toContainEqual(
      expect.objectContaining({ description: 'Bracket', unit_cost: 12.5 })
    );
  });
});

/**
 * CONTROLs on the asset half, for the same reason and with the same result:
 * a typed `"0"` is a truthy string, so the cell prices it and the error box
 * stays off. An empty box is the only falsy one, and it is still flagged.
 */
describe('an asset line priced at zero renders as priced', () => {
  const asset = {
    id: 'asset-2',
    name: 'Comped Mill',
    asset_tag: 'A-002',
    serial_number: 'SN-2',
    product_url: '',
  };

  test('CONTROL: its Line Total reads $0.00, not the dash for unknown', async () => {
    await renderWith(baseItem, { assets: [asset] });
    const row = screen.getByText('Comped Mill').closest('tr')!;
    fireEvent.click(row.querySelector('input')!);
    fireEvent.change(row.querySelector('.col-cost input') as HTMLInputElement, {
      target: { value: '0' },
    });

    expect(row.querySelector('.col-total')!.textContent).toContain('$0.00');
    expect(row.querySelector('.col-total')!.textContent).not.toContain('—');
  });

  test('CONTROL: a typed 0 is not flagged as an input error', async () => {
    await renderWith(baseItem, { assets: [asset] });
    const row = screen.getByText('Comped Mill').closest('tr')!;
    fireEvent.click(row.querySelector('input')!);
    const cost = row.querySelector('.col-cost input') as HTMLInputElement;
    fireEvent.change(cost, { target: { value: '0' } });

    expect(cost.className).not.toContain('input-error');
  });

  test('CONTROL: an empty asset cost is still flagged', async () => {
    await renderWith(baseItem, { assets: [asset] });
    const row = screen.getByText('Comped Mill').closest('tr')!;
    fireEvent.click(row.querySelector('input')!);
    const cost = row.querySelector('.col-cost input') as HTMLInputElement;
    fireEvent.change(cost, { target: { value: '' } });

    expect(cost.className).toContain('input-error');
  });
});
