/**
 * Kit lines on the purchase-order detail and receive flow (op-8n0):
 * AC-42, AC-43, AC-44.
 *
 * The consistent theme: everything shown about a kit line comes from THAT
 * LINE's payload, never from a live lookup of the kit. Receiving must credit
 * what was ordered, and the operator must see the consequence before they
 * commit to it.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import PurchaseOrderPage from '../../pages/PurchaseOrderPage';
import * as api from '../../services/api';

vi.mock('../../services/api');

vi.mock('../../utils/dialogs', () => ({
  confirmDialog: vi.fn().mockResolvedValue(true),
  alertDialog: vi.fn().mockResolvedValue(undefined),
  promptDialog: vi.fn().mockResolvedValue(''),
  showError: vi.fn(),
  showSuccess: vi.fn(),
}));

const renderPage = () =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={['/purchase-orders/po-1']}>
        <Routes>
          <Route path="/purchase-orders/:orderId" element={<PurchaseOrderPage />} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );

/** The BOM as it was at ORDER time — 1 of each of five cartridges. */
const ORDERED_BREAKDOWN = [
  { component: 'c1', component_name: 'Cyan', component_sku: 'SKU-C', quantity_per_kit: 1, quantity: 2 },
  { component: 'c2', component_name: 'Magenta', component_sku: 'SKU-M', quantity_per_kit: 1, quantity: 2 },
  { component: 'c3', component_name: 'Yellow', component_sku: 'SKU-Y', quantity_per_kit: 1, quantity: 2 },
  { component: 'c4', component_name: 'Black', component_sku: 'SKU-K', quantity_per_kit: 1, quantity: 2 },
  { component: 'c5', component_name: 'Cleaning Kit', component_sku: 'SKU-X', quantity_per_kit: 1, quantity: 2 },
];

const kitLine = {
  id: '101',
  item_type: 'inventory_item' as const,
  description: null,
  item_details: { id: 'kit-1', name: 'Eufy Ink Kit', sku: 'KIT-1' },
  asset_details: null,
  is_kit_line: true,
  kit_components: ORDERED_BREAKDOWN,
  quantity_ordered: 2,
  quantity_received: 0,
  quantity_pending: 2,
  is_fully_received: false,
  unit_cost_ordered: '89.99',
  unit_cost_actual: null,
  estimated_cost: '179.98',
  actual_cost: null,
  expected_shipment_date: null,
  notes: '',
  is_voided: false,
  voided_at: null,
  void_reason: '',
};

const ordinaryLine = {
  ...kitLine,
  id: '102',
  item_details: { id: 'item-paper', name: 'Copy Paper', sku: 'SKU-P' },
  is_kit_line: false,
  kit_components: null,
  quantity_ordered: 3,
  quantity_pending: 3,
  unit_cost_ordered: '10.00',
  estimated_cost: '30.00',
};

const order = {
  id: 'po-1',
  po_number: 'PO-2026-0001',
  supplier_details: 'Eufy Direct',
  status: 'sent',
  status_label: 'Sent',
  order_date: '2026-04-01T00:00:00Z',
  expected_delivery_date: '2026-05-15',
  supplier_order_number: '',
  sales_order_number: '',
  estimated_total: '209.98',
  voided_at: null,
  voided_by_username: null,
  void_reason: '',
  items: [kitLine, ordinaryLine],
  attachments: [],
};

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
  localStorage.setItem('token', 'test-token');
  localStorage.setItem('is_staff', 'true');
  (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: order });
});

const openReceivePanel = async () => {
  renderPage();
  await waitFor(() => expect(screen.getByText('PO-2026-0001', { exact: false })).toBeInTheDocument());
  fireEvent.click(screen.getByRole('button', { name: /receive items/i }));
  await waitFor(() =>
    expect(screen.getByLabelText(/receive quantity for eufy ink kit/i)).toBeInTheDocument(),
  );
};

describe('AC-42 — the line renders the ordered snapshot, not a live kit lookup', () => {
  it('shows the breakdown carried on the line payload', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText('PO-2026-0001', { exact: false })).toBeInTheDocument());

    const breakdown = await screen.findByTestId('line-kit-breakdown-101');
    expect(breakdown).toHaveTextContent('Cyan x2');
    expect(breakdown).toHaveTextContent('Cleaning Kit x2');
  });

  it('never fetches the kit definition', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText('PO-2026-0001', { exact: false })).toBeInTheDocument());

    // If someone edits the kit between ordering and receiving, this page must
    // still show what was ORDERED — which it can only do by not asking.
    expect(api.kitAPI.getKit).not.toHaveBeenCalled();
  });

  it('leaves ordinary lines without a breakdown', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText('PO-2026-0001', { exact: false })).toBeInTheDocument());

    expect(screen.queryByTestId('line-kit-breakdown-102')).not.toBeInTheDocument();
  });
});

describe('AC-43 — the receive preview updates before submit', () => {
  it('recomputes the consequence row as the quantity is typed', async () => {
    await openReceivePanel();

    const input = screen.getByLabelText(/receive quantity for eufy ink kit/i);

    fireEvent.change(input, { target: { value: '1' } });
    await waitFor(() => {
      expect(screen.getByTestId('receive-kit-consequence-101')).toHaveTextContent(
        /Receiving 1 kit adds 5 units across 5 items/i,
      );
    });

    fireEvent.change(input, { target: { value: '2' } });
    await waitFor(() => {
      expect(screen.getByTestId('receive-kit-consequence-101')).toHaveTextContent(
        /Receiving 2 kits adds 10 units across 5 items/i,
      );
    });

    // Nothing has been submitted yet — this is a preview.
    expect(api.purchaseOrderAPI.receiveItems).not.toHaveBeenCalled();
  });

  it('hides the consequence row at quantity zero, and never shows one for ordinary lines', async () => {
    await openReceivePanel();

    // The panel opens pre-filled with the pending quantity, so the consequence
    // is on screen immediately.
    expect(screen.getByTestId('receive-kit-consequence-101')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/receive quantity for eufy ink kit/i), {
      target: { value: '' },
    });
    await waitFor(() => {
      expect(screen.queryByTestId('receive-kit-consequence-101')).not.toBeInTheDocument();
    });

    // An ordinary line never gets one, whatever the quantity.
    fireEvent.change(screen.getByLabelText(/receive quantity for copy paper/i), {
      target: { value: '3' },
    });
    await waitFor(() => {
      expect(screen.queryByTestId('receive-kit-consequence-102')).not.toBeInTheDocument();
    });
  });
});

describe('AC-44 — the receive mutation is reactive', () => {
  it('patches the visible order from the response without a full reload', async () => {
    const receivedOrder = {
      ...order,
      status: 'received',
      status_label: 'Received',
      items: [
        { ...kitLine, quantity_received: 2, quantity_pending: 0, is_fully_received: true },
        ordinaryLine,
      ],
    };
    (api.purchaseOrderAPI.receiveItems as jest.Mock).mockResolvedValue({ data: receivedOrder });

    await openReceivePanel();
    fireEvent.change(screen.getByLabelText(/receive quantity for eufy ink kit/i), {
      target: { value: '2' },
    });
    fireEvent.click(screen.getByRole('button', { name: /confirm receipt/i }));

    await waitFor(() => expect(api.purchaseOrderAPI.receiveItems).toHaveBeenCalledTimes(1));
    const [orderId, body] = (api.purchaseOrderAPI.receiveItems as jest.Mock).mock.calls[0];
    expect(orderId).toBe('po-1');
    // One line per receipt entry; the kit is a single line, not five.
    const kitEntry = body.items.find(
      (entry: { purchase_order_item: number }) => entry.purchase_order_item === 101,
    );
    expect(kitEntry).toMatchObject({ quantity_received: 2 });

    // The order the page shows was never re-fetched from scratch: getOrder ran
    // once, on mount. The component credit lands on a resource this page does
    // not render, so there is nothing to reconcile.
    await waitFor(() => {
      expect(api.purchaseOrderAPI.getOrder).toHaveBeenCalledTimes(1);
    });
  });

  it('keeps the panel and the typed quantity visible when the receipt fails', async () => {
    (api.purchaseOrderAPI.receiveItems as jest.Mock).mockRejectedValue({
      response: { data: { error: 'Quantity 5 exceeds pending 2 for line item 101' } },
    });

    await openReceivePanel();
    const input = screen.getByLabelText(/receive quantity for eufy ink kit/i);
    fireEvent.change(input, { target: { value: '2' } });
    fireEvent.click(screen.getByRole('button', { name: /confirm receipt/i }));

    await waitFor(() => expect(api.purchaseOrderAPI.receiveItems).toHaveBeenCalled());

    // The receive panel survives the failure with the typed quantity intact,
    // so the operator can correct and retry rather than start over.
    await waitFor(() => {
      expect(screen.getByLabelText(/receive quantity for eufy ink kit/i)).toHaveValue(2);
    });
  });
});
