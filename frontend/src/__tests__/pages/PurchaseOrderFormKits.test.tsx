/**
 * Kits on the purchase-order form (op-8n0): AC-37..AC-41.
 *
 * The payoff screen. A kit is bought as ONE line whose quantity counts kits,
 * placed before the item rows so the operator learns the bundle exists before
 * deciding, with the double-order overlap made visible rather than silently
 * fixed.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import PurchaseOrderFormPage from '../../pages/PurchaseOrderFormPage';
import * as api from '../../services/api';

vi.mock('../../services/api');

const mockNavigate = jest.fn();
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useNavigate: () => mockNavigate,
}));

const CYAN: api.ReorderDataItem = {
  item_supplier_id: 1,
  item_id: 'item-cyan',
  item_name: 'Cyan',
  item_sku: 'SKU-C',
  current_stock: 0,
  minimum_stock: 5,
  reorder_quantity: 5,
  suggested_quantity: 5,
  unit_cost: '20.00',
  package_cost: null,
  quantity_per_package: 1,
  lead_time_days: 7,
  supplier_sku: 'S-C',
  supplier_url: '',
  is_primary: true,
  line_total: '100.00',
};

const PAPER: api.ReorderDataItem = {
  ...CYAN,
  item_supplier_id: 2,
  item_id: 'item-paper',
  item_name: 'Paper',
  item_sku: 'SKU-P',
  supplier_sku: 'S-P',
};

const EUFY_KIT: api.ReorderDataKit = {
  id: 'kit-eufy',
  name: 'Eufy Ink Kit',
  sku: 'KIT-1',
  supplier_sku: 'T3200',
  unit_cost: '89.99',
  item_supplier_id: 99,
  low_component_count: 1,
  components: [
    { id: 'item-cyan', name: 'Cyan', sku: 'SKU-C', quantity_per_kit: 1, is_low: true },
    { id: 'item-magenta', name: 'Magenta', sku: 'SKU-M', quantity_per_kit: 1, is_low: false },
    { id: 'item-yellow', name: 'Yellow', sku: 'SKU-Y', quantity_per_kit: 1, is_low: false },
    { id: 'item-black', name: 'Black', sku: 'SKU-K', quantity_per_kit: 1, is_low: false },
    { id: 'item-clean', name: 'Cleaning Kit', sku: 'SKU-X', quantity_per_kit: 1, is_low: false },
  ],
};

const supplierWith = (overrides: Partial<api.ReorderDataSupplier> = {}) => ({
  id: 1,
  name: 'Eufy Direct',
  supplier_type: 'online',
  website: '',
  items: [CYAN, PAPER],
  assets: [],
  total_items: 2,
  estimated_total: '100.00',
  avg_lead_time: 5,
  ...overrides,
});

const loadForm = async (supplier: Partial<api.ReorderDataSupplier>) => {
  (api.purchaseOrderAPI.getReorderData as jest.Mock).mockResolvedValue({
    data: { suppliers: [supplier] },
  });
  (api.purchaseOrderAPI.createOrder as jest.Mock).mockResolvedValue({
    data: { id: 42, po_number: 'PO-1', supplier: 1, status: 'draft' },
  });

  render(
    <MemoryRouter>
      <PurchaseOrderFormPage />
    </MemoryRouter>,
  );

  const card = await screen.findByText('Eufy Direct');
  fireEvent.click(card);
};

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
  localStorage.setItem('token', 'test-token');
});

describe('AC-37 — kits are presented before ordinary items', () => {
  it('renders the Kits section ahead of Inventory Items', async () => {
    await loadForm(supplierWith({ kits: [EUFY_KIT] }));

    await waitFor(() => {
      expect(screen.getByTestId('po-kits-section')).toBeInTheDocument();
    });

    const headings = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent ?? '');
    const kitsIndex = headings.findIndex((text) => text.includes('Kits'));
    const itemsIndex = headings.findIndex((text) => text.includes('Inventory Items'));
    expect(kitsIndex).toBeGreaterThanOrEqual(0);
    expect(itemsIndex).toBeGreaterThanOrEqual(0);
    expect(kitsIndex).toBeLessThan(itemsIndex);
  });

  it('renders a supplier with no kits at all without crashing', async () => {
    // The backend always sends `kits`, but every pre-existing fixture omits it.
    await loadForm(supplierWith());

    await waitFor(() => {
      expect(screen.getByText(/No kits from this supplier/i)).toBeInTheDocument();
    });
    // The rest of the form is intact.
    expect(screen.getByText('Cyan')).toBeInTheDocument();
    expect(screen.getByText('Paper')).toBeInTheDocument();
  });
});

describe('AC-38 — quantity counts kits and rolls into the totals', () => {
  it('orders 2 kits as one line at 2 x unit cost', async () => {
    await loadForm(supplierWith({ kits: [EUFY_KIT] }));
    await waitFor(() => expect(screen.getByTestId('po-kits-table')).toBeInTheDocument());

    fireEvent.change(screen.getByTestId('po-kit-quantity-kit-eufy'), { target: { value: '2' } });

    await waitFor(() => {
      expect(screen.getByTestId('po-kit-total-kit-eufy')).toHaveTextContent('179.98');
    });
    expect(screen.getByTestId('po-kits-subtotal')).toHaveTextContent('179.98');
    // Exactly one kit line, not five component lines.
    expect(screen.getAllByTestId(/^po-kit-row-/)).toHaveLength(1);
  });

  it('counts the kit as a single line item in the summary', async () => {
    await loadForm(supplierWith({ kits: [EUFY_KIT], items: [] }));
    await waitFor(() => expect(screen.getByTestId('po-kits-table')).toBeInTheDocument());

    expect(screen.getByTestId('po-summary-kits-total')).toHaveTextContent('89.99');
    expect(screen.getByText(/Grand Total \(1 line items\)/i)).toBeInTheDocument();
  });

  it('submits one purchase-order line for the kit', async () => {
    await loadForm(supplierWith({ kits: [EUFY_KIT], items: [] }));
    await waitFor(() => expect(screen.getByTestId('po-kits-table')).toBeInTheDocument());

    fireEvent.change(screen.getByTestId('po-kit-quantity-kit-eufy'), { target: { value: '2' } });
    fireEvent.click(screen.getByRole('button', { name: /create purchase order/i }));

    await waitFor(() => {
      expect(api.purchaseOrderAPI.createOrder).toHaveBeenCalled();
    });
    const payload = (api.purchaseOrderAPI.createOrder as jest.Mock).mock.calls[0][0];
    expect(payload.items).toHaveLength(1);
    expect(payload.items[0]).toMatchObject({ item_supplier_id: 99, quantity: 2 });
  });
});

describe('AC-39 — default selection follows whether a component is low', () => {
  it('checks the kit when something inside it is low', async () => {
    await loadForm(supplierWith({ kits: [EUFY_KIT] }));
    await waitFor(() => expect(screen.getByTestId('po-kits-table')).toBeInTheDocument());
    expect(screen.getByTestId('po-kit-checkbox-kit-eufy')).toBeChecked();
  });

  it('leaves the kit unchecked when nothing inside it is low', async () => {
    const nothingLow: api.ReorderDataKit = {
      ...EUFY_KIT,
      low_component_count: 0,
      components: EUFY_KIT.components.map((c) => ({ ...c, is_low: false })),
    };
    await loadForm(supplierWith({ kits: [nothingLow] }));
    await waitFor(() => expect(screen.getByTestId('po-kits-table')).toBeInTheDocument());
    expect(screen.getByTestId('po-kit-checkbox-kit-eufy')).not.toBeChecked();
  });
});

describe('AC-40 — the breakdown is accessible and the summary is live', () => {
  it('updates the "N kits -> M units" summary as the quantity changes', async () => {
    await loadForm(supplierWith({ kits: [EUFY_KIT] }));
    await waitFor(() => expect(screen.getByTestId('po-kits-table')).toBeInTheDocument());

    expect(screen.getByTestId('po-kit-summary-kit-eufy')).toHaveTextContent(
      /1 kit → 5 units across 5 items/,
    );

    fireEvent.change(screen.getByTestId('po-kit-quantity-kit-eufy'), { target: { value: '2' } });

    await waitFor(() => {
      expect(screen.getByTestId('po-kit-summary-kit-eufy')).toHaveTextContent(
        /2 kits → 10 units across 5 items/,
      );
    });
  });

  it('expands into a real table with per-component "You get" quantities', async () => {
    await loadForm(supplierWith({ kits: [EUFY_KIT] }));
    await waitFor(() => expect(screen.getByTestId('po-kits-table')).toBeInTheDocument());

    fireEvent.change(screen.getByTestId('po-kit-quantity-kit-eufy'), { target: { value: '2' } });
    fireEvent.click(screen.getByTestId('po-kit-expand-kit-eufy'));

    const breakdown = await screen.findByTestId('po-kit-breakdown-kit-eufy');
    // A real table, not a tooltip — reachable on touch, tabular to a reader.
    expect(within(breakdown).getByRole('table')).toBeInTheDocument();
    expect(screen.getByTestId('po-kit-yousget-kit-eufy-item-cyan')).toHaveTextContent('+2');
    expect(screen.getByTestId('po-kit-yousget-kit-eufy-item-black')).toHaveTextContent('+2');
  });
});

describe('AC-41 — the double-order conflict is visible and reversible', () => {
  it('names overlapping items and deselects them on request', async () => {
    await loadForm(supplierWith({ kits: [EUFY_KIT] }));
    await waitFor(() => expect(screen.getByTestId('po-kits-table')).toBeInTheDocument());

    // Cyan is low, so it is checked by default AND lives in the checked kit —
    // the exact collision this guard exists for.
    const banner = await screen.findByTestId('po-kit-conflict-banner');
    expect(banner).toHaveTextContent('Cyan');
    expect(banner).not.toHaveTextContent('Paper');

    fireEvent.click(screen.getByTestId('po-kit-conflict-deselect'));

    await waitFor(() => {
      expect(screen.queryByTestId('po-kit-conflict-banner')).not.toBeInTheDocument();
    });
  });

  it('keeps the "in kit" chip on the item row even when the kit is unchecked', async () => {
    await loadForm(supplierWith({ kits: [EUFY_KIT] }));
    await waitFor(() => expect(screen.getByTestId('po-kits-table')).toBeInTheDocument());

    expect(screen.getByTestId('po-item-in-kit-item-cyan')).toBeInTheDocument();
    // Paper is not in any kit.
    expect(screen.queryByTestId('po-item-in-kit-item-paper')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('po-kit-checkbox-kit-eufy'));

    await waitFor(() => {
      expect(screen.queryByTestId('po-kit-conflict-banner')).not.toBeInTheDocument();
    });
    // The chip is permanent — the overlap is still worth knowing about.
    expect(screen.getByTestId('po-item-in-kit-item-cyan')).toBeInTheDocument();
  });

  it('does not auto-deselect the overlapping item', async () => {
    await loadForm(supplierWith({ kits: [EUFY_KIT] }));
    await waitFor(() => expect(screen.getByTestId('po-kits-table')).toBeInTheDocument());

    // Buying a kit plus a spare cartridge is legitimate, so the row stays
    // checked until the operator says otherwise.
    expect(screen.getByTestId('po-kit-conflict-banner')).toBeInTheDocument();
    const cyanCheckbox = screen.getByLabelText(/select cyan/i) as HTMLInputElement;
    expect(cyanCheckbox.checked).toBe(true);
  });
});
