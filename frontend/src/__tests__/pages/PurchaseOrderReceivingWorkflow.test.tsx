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
import { promptInput, showSuccess } from '../../utils/dialogs';

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
  total_received_quantity: 3,
  serials_outstanding: 0,
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
      serials_outstanding: 0,
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

  test('editing the quantity through an empty box keeps the flag and the typed reason', async () => {
    // Every backspace-and-retype passes through an empty value. Treating that
    // transient state as "no longer short" would throw away the reason the
    // operator had already typed, mid-edit, with no warning.
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: makeOrder() });
    (api.purchaseOrderAPI.receiveItems as jest.Mock).mockResolvedValue({ data: makeOrder() });

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /^receive items$/i }));
    const quantity = screen.getByLabelText('Receive quantity for Stocked Bolt');
    fireEvent.change(quantity, { target: { value: '4' } });

    await screen.findByTestId('receive-short-row-301');
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.change(screen.getByLabelText(/reason for closing Stocked Bolt short/i), {
      target: { value: 'vendor cancelled the backorder' },
    });

    // Correcting 4 to 5: the box is empty for one keystroke, then zero-ish
    // values on the way back up. None of those is a decision.
    fireEvent.change(quantity, { target: { value: '' } });
    fireEvent.change(quantity, { target: { value: '0' } });
    fireEvent.change(quantity, { target: { value: '5' } });

    const row = await screen.findByTestId('receive-short-row-301');
    expect(row).toBeInTheDocument();
    expect(screen.getByRole('checkbox')).toBeChecked();
    expect(screen.getByLabelText(/reason for closing Stocked Bolt short/i)).toHaveValue(
      'vendor cancelled the backorder',
    );

    fireEvent.click(screen.getByRole('button', { name: /confirm receipt/i }));

    await waitFor(() => {
      expect(api.purchaseOrderAPI.receiveItems).toHaveBeenCalled();
    });
    const [, body] = (api.purchaseOrderAPI.receiveItems as jest.Mock).mock.calls[0];
    expect(body.items[0]).toMatchObject({
      quantity_received: 5,
      close_short: true,
      close_short_reason: 'vendor cancelled the backorder',
    });
  });

  test('correcting the quantity back up drops the close-short the operator can no longer see', async () => {
    // The offer only renders while the quantity is short. A flag ticked at 4 of
    // 7 and then corrected to 7 would otherwise still be sent — input the
    // operator can neither see nor untick, against a line with nothing left
    // outstanding.
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: makeOrder() });
    (api.purchaseOrderAPI.receiveItems as jest.Mock).mockResolvedValue({ data: makeOrder() });

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /^receive items$/i }));
    const quantity = screen.getByLabelText('Receive quantity for Stocked Bolt');
    fireEvent.change(quantity, { target: { value: '4' } });

    await screen.findByTestId('receive-short-row-301');
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.change(screen.getByLabelText(/reason for closing Stocked Bolt short/i), {
      target: { value: 'backorder cancelled' },
    });

    // The operator notices the miscount and corrects it: the offer goes away.
    fireEvent.change(quantity, { target: { value: '7' } });
    expect(screen.queryByTestId('receive-short-row-301')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /confirm receipt/i }));

    await waitFor(() => {
      expect(api.purchaseOrderAPI.receiveItems).toHaveBeenCalled();
    });
    const [, body] = (api.purchaseOrderAPI.receiveItems as jest.Mock).mock.calls[0];
    expect(body.items[0].quantity_received).toBe(7);
    expect(body.items[0].close_short).toBeUndefined();
    expect(body.items[0].close_short_reason).toBeUndefined();
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

  test('an order nothing arrived against is not announced as closed out', async () => {
    // The server writes the lines off but leaves the order where it was —
    // `received` is a claim goods turned up. Saying "closed out" over a hero
    // still reading "Sent" would be the page contradicting itself, and would
    // leave the operator with no idea what to do next.
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: makeOrder() });
    (api.purchaseOrderAPI.markReceived as jest.Mock).mockResolvedValue({
      data: makeOrder({
        status: 'sent',
        status_label: 'Sent to Supplier',
        is_settled: true,
        outstanding_line_count: 0,
        total_received_quantity: 0,
      }),
    });
    (promptInput as jest.Mock).mockResolvedValue('vendor never shipped');

    renderPage();

    fireEvent.click(await screen.findByTestId('mark-received-button'));

    await waitFor(() => {
      expect(showSuccess).toHaveBeenCalled();
    });
    const [message] = (showSuccess as jest.Mock).mock.calls.at(-1);
    expect(message).not.toMatch(/closed out/i);
    expect(message).toMatch(/nothing was received against this order/i);
    // Names an exit that exists on this page for this operator (staff here).
    expect(message).toMatch(/void po/i);
  });

  test('a member who cannot void the order is told who can', async () => {
    // `canVoidOrder` requires staff, so pointing a member at a control they
    // cannot see would be the same dead end in a different costume.
    localStorage.setItem('is_staff', 'false');
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: makeOrder() });
    (api.purchaseOrderAPI.markReceived as jest.Mock).mockResolvedValue({
      data: makeOrder({
        status: 'sent',
        status_label: 'Sent to Supplier',
        is_settled: true,
        outstanding_line_count: 0,
        total_received_quantity: 0,
      }),
    });
    (promptInput as jest.Mock).mockResolvedValue('');

    renderPage();

    fireEvent.click(await screen.findByTestId('mark-received-button'));

    await waitFor(() => {
      expect(showSuccess).toHaveBeenCalled();
    });
    const [message] = (showSuccess as jest.Mock).mock.calls.at(-1);
    expect(message).not.toMatch(/closed out/i);
    expect(message).toMatch(/ask a staff member/i);
  });

  test('an order that really did close out still says so', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: makeOrder() });
    (api.purchaseOrderAPI.markReceived as jest.Mock).mockResolvedValue({
      data: makeOrder({
        status: 'received',
        status_label: 'Fully Received',
        can_receive: false,
        is_settled: true,
        outstanding_line_count: 0,
        total_received_quantity: 3,
      }),
    });
    (promptInput as jest.Mock).mockResolvedValue('vendor closed the order');

    renderPage();

    fireEvent.click(await screen.findByTestId('mark-received-button'));

    await waitFor(() => {
      expect(showSuccess).toHaveBeenCalledWith('Purchase order closed out');
    });
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


describe('an uncaptured serial is never silent', () => {
  test('units in stock with no serial are flagged on the line and the order', async () => {
    // This is what replaced the old ban on serialized kit components. The ban
    // refused the configuration; this reports the gap, and it covers every
    // receive path rather than just the kit one.
    const withGap = makeOrder({
      serials_outstanding: 3,
      items: [
        {
          ...makeOrder().items[0],
          quantity_received: 3,
          serials_outstanding: 3,
          serial_targets: [
            { item: 'inv-1', item_name: 'Meter', item_sku: 'M-1', quantity: 3, recorded: 0 },
          ],
        },
      ],
    });
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: withGap });

    renderPage();

    expect(await screen.findByTestId('serials-outstanding-warning')).toHaveTextContent(
      /3 received units are in stock with no serial number recorded/i,
    );
    expect(screen.getByTestId('line-serials-outstanding-301')).toHaveTextContent(
      /3 without serials/i,
    );
  });

  test('a line with every serial captured carries no flag', async () => {
    // Zero must read as "nothing owed", not as "we did not look".
    const complete = makeOrder({
      serials_outstanding: 0,
      items: [
        {
          ...makeOrder().items[0],
          quantity_received: 3,
          serials_outstanding: 0,
          serials_recorded: 3,
          serial_targets: [
            { item: 'inv-1', item_name: 'Meter', item_sku: 'M-1', quantity: 3, recorded: 3 },
          ],
        },
      ],
    });
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: complete });

    renderPage();

    await screen.findByRole('button', { name: /^receive items$/i });
    expect(screen.queryByTestId('serials-outstanding-warning')).not.toBeInTheDocument();
    expect(screen.queryByTestId('line-serials-outstanding-301')).not.toBeInTheDocument();
  });
});


describe('correcting a close-short from the purchase-order page', () => {
  /** The same order with its one line already written off at 3 of 10. */
  const closedShortOrder = () =>
    makeOrder({
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
          quantity_pending: 7,
          receipt_state: 'closed_short',
          receipt_state_label: 'Closed short',
          is_settled: true,
          has_receipt_variance: true,
          is_short_received: true,
          is_closed_short: true,
          closed_short_at: '2026-08-20T10:00:00Z',
          closed_short_by_username: 'clerk',
          closed_short_reason: 'backorder cancelled',
        },
      ],
    });

  test('the reopen prompt quotes the balance the server says is written off', async () => {
    // The page used to subtract quantity_ordered - quantity_received itself to
    // fill this sentence — a second derivation of what receiving is still
    // owed, beside the server's. They part company on an over-received line
    // (reachable by lowering quantity_ordered on a line already closed short):
    // the server floors quantity_pending at zero, the subtraction goes
    // negative, and the operator is told "-2 units written off".
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({
      data: makeOrder({
        status: 'received',
        can_receive: false,
        is_settled: true,
        outstanding_line_count: 0,
        items: [
          {
            ...closedShortOrder().items[0],
            quantity_ordered: 10,
            quantity_received: 12,
            quantity_pending: 0,
          },
        ],
      }),
    });
    (promptInput as jest.Mock).mockResolvedValue(null);

    renderPage();

    fireEvent.click(await screen.findByTestId('reopen-short-301'));

    await waitFor(() => {
      expect(promptInput).toHaveBeenCalled();
    });
    const [, message] = (promptInput as jest.Mock).mock.calls[0];
    expect(message).toContain('0 units written off');
    expect(message).not.toContain('-2');
  });

  test('the reopen control is offered only on a line that is closed short', async () => {
    // Without it the mistake is uncorrectable from the browser: the receive
    // panel skips settled lines, so a closed-short line is never offered there.
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: closedShortOrder() });

    renderPage();

    expect(await screen.findByTestId('reopen-short-301')).toHaveTextContent(/reopen/i);
  });

  test('an ordinary outstanding line offers no reopen control', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: makeOrder() });

    renderPage();

    await screen.findByRole('button', { name: /^receive items$/i });
    expect(screen.queryByTestId('reopen-short-301')).not.toBeInTheDocument();
  });

  test('cancelling the reason prompt reopens nothing', async () => {
    // `null` is Cancel and `''` is "confirmed, nothing typed" — different
    // answers, and only one of them is a decision.
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: closedShortOrder() });
    (promptInput as jest.Mock).mockResolvedValue(null);

    renderPage();

    fireEvent.click(await screen.findByTestId('reopen-short-301'));

    await waitFor(() => {
      expect(promptInput).toHaveBeenCalled();
    });
    expect(api.purchaseOrderAPI.reopenShort).not.toHaveBeenCalled();
  });

  test('confirming sends the line and the reason, and the line becomes receivable again', async () => {
    // The trap being removed: after the correction the line is outstanding, so
    // the receive panel offers it.
    const reopened = makeOrder({
      status: 'partially_received',
      status_label: 'Partially Received',
      can_receive: true,
      is_settled: false,
      outstanding_line_count: 1,
      items: [
        {
          ...makeOrder().items[0],
          is_closed_short: false,
          closed_short_at: '2026-08-20T10:00:00Z',
          closed_short_by_username: 'clerk',
          closed_short_reason: 'backorder cancelled',
        },
      ],
    });
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: closedShortOrder() });
    (promptInput as jest.Mock).mockResolvedValue('closed the wrong line');
    (api.purchaseOrderAPI.reopenShort as jest.Mock).mockResolvedValue({ data: reopened });

    renderPage();

    fireEvent.click(await screen.findByTestId('reopen-short-301'));

    await waitFor(() => {
      expect(api.purchaseOrderAPI.reopenShort).toHaveBeenCalled();
    });
    const [, body] = (api.purchaseOrderAPI.reopenShort as jest.Mock).mock.calls[0];
    expect(body).toEqual({
      items: [{ purchase_order_item: 301, reason: 'closed the wrong line' }],
    });

    fireEvent.click(await screen.findByRole('button', { name: /^receive items$/i }));
    expect(await screen.findByLabelText('Receive quantity for Stocked Bolt')).toBeInTheDocument();
  });
});
