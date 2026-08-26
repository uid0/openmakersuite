/**
 * The receiving workflow's web surface (oms-po-receiving).
 *
 * What is proven here is the part an ordinary member actually sees: that a
 * mismatch is announced before it is recorded and stays visible on the order
 * afterwards, that the tracking barcode the operator scans is sent, that a
 * short line can be declared finished without pretending the missing units
 * arrived, and that closing an order out is never confused with saying
 * everything turned up.
 *
 * Every assertion goes through the real page against a mocked API, so what is
 * checked is the payload the server will actually receive.
 */

import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import PurchaseOrderPage from '../../pages/PurchaseOrderPage';
import * as api from '../../services/api';
import { promptInput } from '../../utils/dialogs';

vi.mock('../../services/api');

vi.mock('../../utils/dialogs', () => ({
  showError: jest.fn(),
  showSuccess: jest.fn(),
  // Auto-confirm by default so the happy path runs; the tests that care about
  // the operator DECLINING override this per case.
  confirmAction: jest.fn((_title, _msg, onConfirm) => {
    void onConfirm();
  }),
  promptInput: jest.fn(),
}));

const renderPage = () =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={['/purchase-orders/po-1']}>
        <Routes>
          <Route path="/purchase-orders/:orderId" element={<PurchaseOrderPage />} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>
  );

/** A sent order: one line 10 ordered / 3 received, one line already settled. */
const makeOrder = (overrides: Record<string, unknown> = {}) => ({
  id: 'po-1',
  po_number: 'PO-2026-0500',
  supplier_details: 'Grainger',
  status: 'partially_received',
  status_label: 'Partially Received',
  can_receive: true,
  is_settled: false,
  is_fully_received: false,
  has_receipt_variance: false,
  outstanding_line_count: 1,
  variance_line_count: 0,
  order_date: '2026-04-01T00:00:00Z',
  expected_delivery_date: '2026-05-15',
  supplier_order_number: '',
  sales_order_number: '',
  estimated_total: '100.00',
  voided_at: null,
  voided_by_username: null,
  void_reason: '',
  attachments: [],
  items: [
    {
      id: 301,
      item_type: 'inventory_item',
      description: null,
      item_details: { id: 'inv-1', name: 'Stocked Bolt', sku: 'BOLT-1' },
      asset_details: null,
      quantity_ordered: 10,
      quantity_received: 3,
      quantity_pending: 7,
      quantity_variance: -7,
      receipt_state: 'partially_received',
      receipt_state_label: 'Partially received',
      is_fully_received: false,
      is_settled: false,
      has_receipt_variance: false,
      is_over_received: false,
      is_short_received: false,
      is_closed_short: false,
      closed_short_at: null,
      closed_short_by_username: null,
      closed_short_reason: '',
      serial_targets: [],
      serials_recorded: 0,
      unit_cost_ordered: '1.00',
      unit_cost_actual: null,
      estimated_cost: '10.00',
      actual_cost: null,
      expected_shipment_date: null,
      notes: '',
      is_voided: false,
      voided_at: null,
      void_reason: '',
    },
  ],
  ...overrides,
});

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
  localStorage.setItem('token', 'test-token');
  localStorage.setItem('is_staff', 'true');
});

describe('the tracking barcode reaches the API', () => {
  test('a scanned tracking barcode is sent with the receipt', async () => {
    // The receive endpoint has always accepted `tracking_number`; the form did
    // not collect it, so a barcode scanned at the bench went nowhere unless the
    // operator used the separate mark-delivered path instead.
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: makeOrder() });
    (api.purchaseOrderAPI.receiveItems as jest.Mock).mockResolvedValue({ data: makeOrder() });

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /^receive items$/i }));
    fireEvent.change(screen.getByLabelText(/tracking barcode/i), {
      target: { value: '1Z999AA10123456784' },
    });
    fireEvent.change(screen.getByLabelText(/^carrier/i), { target: { value: 'UPS' } });
    fireEvent.click(screen.getByRole('button', { name: /confirm receipt/i }));

    await waitFor(() => {
      expect(api.purchaseOrderAPI.receiveItems).toHaveBeenCalled();
    });
    const [, body] = (api.purchaseOrderAPI.receiveItems as jest.Mock).mock.calls[0];
    expect(body.tracking_number).toBe('1Z999AA10123456784');
    expect(body.carrier).toBe('UPS');
  });
});

describe('closing a line short', () => {
  test('a short quantity offers to close the line, and sends the reason', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: makeOrder() });
    (api.purchaseOrderAPI.receiveItems as jest.Mock).mockResolvedValue({ data: makeOrder() });

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /^receive items$/i }));
    fireEvent.change(screen.getByLabelText('Receive quantity for Stocked Bolt'), {
      target: { value: '4' },
    });

    // 4 of the 7 outstanding: the offer appears, and names what is left.
    const row = await screen.findByTestId('receive-short-row-301');
    expect(row).toHaveTextContent(/remaining 3 units are not coming/i);

    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.change(screen.getByLabelText(/reason for closing Stocked Bolt short/i), {
      target: { value: 'backorder cancelled' },
    });
    fireEvent.click(screen.getByRole('button', { name: /confirm receipt/i }));

    await waitFor(() => {
      expect(api.purchaseOrderAPI.receiveItems).toHaveBeenCalled();
    });
    const [, body] = (api.purchaseOrderAPI.receiveItems as jest.Mock).mock.calls[0];
    expect(body.items[0]).toMatchObject({
      purchase_order_item: 301,
      quantity_received: 4,
      close_short: true,
      close_short_reason: 'backorder cancelled',
    });
  });

  test('a short receipt left un-declared does not close the line', async () => {
    // The default is that the rest is still coming — flagging every partial
    // receipt as short would put a vendor query on every backorder.
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: makeOrder() });
    (api.purchaseOrderAPI.receiveItems as jest.Mock).mockResolvedValue({ data: makeOrder() });

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /^receive items$/i }));
    fireEvent.change(screen.getByLabelText('Receive quantity for Stocked Bolt'), {
      target: { value: '4' },
    });
    fireEvent.click(screen.getByRole('button', { name: /confirm receipt/i }));

    await waitFor(() => {
      expect(api.purchaseOrderAPI.receiveItems).toHaveBeenCalled();
    });
    const [, body] = (api.purchaseOrderAPI.receiveItems as jest.Mock).mock.calls[0];
    expect(body.items[0].close_short).toBeUndefined();
  });
});

describe('the mismatch stays visible on the order', () => {
  test('a variance is flagged on the line and banner-ed on the order', async () => {
    // The point of recording a mismatch is that it is still there later, so it
    // must show on the order itself and not only inside the receive form.
    const closed = makeOrder({
      status: 'received',
      status_label: 'Fully Received',
      can_receive: false,
      is_settled: true,
      has_receipt_variance: true,
      outstanding_line_count: 0,
      variance_line_count: 1,
      items: [
        {
          ...makeOrder().items[0],
          quantity_received: 8,
          quantity_pending: 2,
          quantity_variance: -2,
          receipt_state: 'closed_short',
          receipt_state_label: 'Closed short',
          is_settled: true,
          has_receipt_variance: true,
          is_short_received: true,
          is_closed_short: true,
          closed_short_reason: 'backorder cancelled',
        },
      ],
    });
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: closed });

    renderPage();

    expect(await screen.findByTestId('receipt-variance-warning')).toHaveTextContent(
      /1 line did not match what was ordered/i,
    );
    expect(screen.getByTestId('line-variance-301')).toHaveTextContent(/-2 short/i);
  });

  test('an over-receipt is flagged as over, not as pending', async () => {
    const over = makeOrder({
      status: 'received',
      status_label: 'Fully Received',
      can_receive: false,
      has_receipt_variance: true,
      outstanding_line_count: 0,
      variance_line_count: 1,
      items: [
        {
          ...makeOrder().items[0],
          quantity_received: 12,
          quantity_pending: 0,
          quantity_variance: 2,
          receipt_state: 'over_received',
          receipt_state_label: 'Over-received',
          is_settled: true,
          has_receipt_variance: true,
          is_over_received: true,
        },
      ],
    });
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: over });

    renderPage();

    expect(await screen.findByTestId('line-variance-301')).toHaveTextContent(/\+2 over/i);
    // Not offered as something still owed.
    expect(screen.queryByTestId('line-outstanding-301')).not.toBeInTheDocument();
  });

  test('a finished order with a variance does not claim everything arrived', async () => {
    // "Every line has already been received in full" would be a documented
    // untruth on exactly the orders the operator most needs to chase.
    const closed = makeOrder({
      status: 'received',
      status_label: 'Fully Received',
      can_receive: false,
      is_settled: true,
      has_receipt_variance: true,
      outstanding_line_count: 0,
      variance_line_count: 1,
    });
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: closed });

    renderPage();

    const notice = await screen.findByTestId('receive-unavailable-notice');
    expect(notice).toHaveTextContent(/some lines did not match what was ordered/i);
    expect(notice).not.toHaveTextContent(/received in full/i);
  });
});

describe('closing the order out', () => {
  test('closing out warns that the shortfall is written off, not received', async () => {
    // Distinct from "Mark as delivered", which asserts the opposite. Confusing
    // the two is how a shortfall becomes a tidy record instead of an honest one.
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: makeOrder() });
    (api.purchaseOrderAPI.markReceived as jest.Mock).mockResolvedValue({
      data: makeOrder({ status: 'received', can_receive: false }),
    });
    (promptInput as jest.Mock).mockResolvedValue('vendor closed the order');

    renderPage();

    fireEvent.click(await screen.findByTestId('mark-received-button'));

    await waitFor(() => {
      expect(api.purchaseOrderAPI.markReceived).toHaveBeenCalledWith('po-1', {
        reason: 'vendor closed the order',
      });
    });
    expect(promptInput).toHaveBeenCalledWith(
      expect.any(String),
      expect.stringMatching(/written off, not marked received/i),
    );
  });

  test('cancelling the prompt closes nothing', async () => {
    // `prompt` returns null on Cancel and '' when confirmed with no reason
    // typed. Treating those alike would close an order the operator backed out
    // of.
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: makeOrder() });
    (promptInput as jest.Mock).mockResolvedValue(null);

    renderPage();

    fireEvent.click(await screen.findByTestId('mark-received-button'));

    await waitFor(() => {
      expect(promptInput).toHaveBeenCalled();
    });
    expect(api.purchaseOrderAPI.markReceived).not.toHaveBeenCalled();
  });

  test('an empty reason still closes the order out', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: makeOrder() });
    (api.purchaseOrderAPI.markReceived as jest.Mock).mockResolvedValue({
      data: makeOrder({ status: 'received', can_receive: false }),
    });
    (promptInput as jest.Mock).mockResolvedValue('');

    renderPage();

    fireEvent.click(await screen.findByTestId('mark-received-button'));

    await waitFor(() => {
      expect(api.purchaseOrderAPI.markReceived).toHaveBeenCalledWith('po-1', { reason: '' });
    });
  });

  test('an order with nothing outstanding is not offered a close-out', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({
      data: makeOrder({ outstanding_line_count: 0 }),
    });

    renderPage();

    await screen.findByRole('button', { name: /^receive items$/i });
    expect(screen.queryByTestId('mark-received-button')).not.toBeInTheDocument();
  });
});

describe('kit lines and serial identity', () => {
  test('a kit line offers serial capture per COMPONENT, never for the kit', async () => {
    // The kit is bought as one SKU but stocked as its components, so a serial
    // written against the kit would name a unit that never enters stock.
    const kitOrder = makeOrder({
      items: [
        {
          ...makeOrder().items[0],
          id: 401,
          item_details: { id: 'kit-1', name: 'Meter Kit', sku: 'KIT-1' },
          quantity_ordered: 2,
          quantity_received: 0,
          quantity_pending: 2,
          is_kit_line: true,
          kit_components: [
            {
              component: 'comp-1',
              component_name: 'Meter',
              component_sku: 'M-1',
              quantity_per_kit: 1,
              quantity: 2,
            },
          ],
          // Only the component is offered. The kit's own id is absent.
          serial_targets: [
            {
              item: 'comp-1',
              item_name: 'Meter',
              item_sku: 'M-1',
              serial_tracking_mode: 'reusable',
              quantity: 2,
            },
          ],
        },
      ],
    });
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: kitOrder });
    (api.purchaseOrderAPI.receiveItems as jest.Mock).mockResolvedValue({ data: kitOrder });

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /^receive items$/i }));

    // The box is labelled for the component, and says whose component it is.
    const block = await screen.findByTestId('receive-serials-401:comp-1');
    expect(block).toHaveTextContent(/component of Meter Kit/i);
    expect(screen.queryByTestId('receive-serials-401:kit-1')).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Serial numbers for Meter'), {
      target: { value: 'M-1\nM-2' },
    });
    fireEvent.click(screen.getByRole('button', { name: /confirm receipt/i }));

    await waitFor(() => {
      expect(api.purchaseOrderAPI.receiveItems).toHaveBeenCalled();
    });
    const [, body] = (api.purchaseOrderAPI.receiveItems as jest.Mock).mock.calls[0];
    // Every serial names the COMPONENT.
    expect(body.items[0].serials.map((s: { item: string }) => s.item)).toEqual([
      'comp-1',
      'comp-1',
    ]);
    expect(body.items[0].serials.some((s: { item: string }) => s.item === 'kit-1')).toBe(false);
  });

  test('a partial kit receipt scales the serials asked for', async () => {
    // 2 kits ordered, each with 3 meters = 6 targets; receiving 1 kit asks for
    // 3, not 6. Asking for the ordered count would make a partial receipt
    // impossible to satisfy.
    const kitOrder = makeOrder({
      items: [
        {
          ...makeOrder().items[0],
          id: 402,
          item_details: { id: 'kit-2', name: 'Triple Kit', sku: 'KIT-2' },
          quantity_ordered: 2,
          quantity_received: 0,
          quantity_pending: 2,
          is_kit_line: true,
          serial_targets: [
            { item: 'comp-2', item_name: 'Probe', item_sku: 'P-1', quantity: 6 },
          ],
        },
      ],
    });
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: kitOrder });

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /^receive items$/i }));
    fireEvent.change(screen.getByLabelText('Receive quantity for Triple Kit'), {
      target: { value: '1' },
    });

    expect(await screen.findByTestId('receive-serials-402:comp-2')).toHaveTextContent('(0/3)');
  });
});
