/**
 * Adding a line to a draft purchase order (oms-po-add-item).
 *
 * One entry field takes whatever the operator has in front of them — item
 * name, item SKU, package or unit barcode, or the vendor's SKU — and Enter
 * submits it, which is exactly what a keyboard-wedge scanner produces. The
 * server owns every rule; this page's job is to offer the control only where
 * it applies, show what matched, and put the refusal in front of the operator
 * in words they can act on.
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import PurchaseOrderPage from '../../pages/PurchaseOrderPage';
import * as api from '../../services/api';

vi.mock('../../services/api');

vi.mock('../../utils/dialogs', () => ({
  showError: jest.fn(),
  showSuccess: jest.fn(),
  confirmAction: jest.fn(),
  promptInput: jest.fn(),
}));

const line = (overrides: Record<string, unknown> = {}) => ({
  id: 'line-1',
  item_type: 'inventory_item',
  description: null,
  item_details: { id: 'item-1', name: 'M3 hex bolt', sku: 'OMS-M3-HEX' },
  asset_details: null,
  quantity_ordered: 10,
  quantity_received: 0,
  quantity_pending: 10,
  is_fully_received: false,
  unit_cost_ordered: '2.50',
  unit_cost_actual: null,
  estimated_cost: '25.00',
  actual_cost: null,
  expected_shipment_date: null,
  notes: '',
  is_voided: false,
  voided_at: null,
  void_reason: '',
  work_order: null,
  work_order_details: null,
  owning_group: null,
  owning_group_details: null,
  ...overrides,
});

const order = (overrides: Record<string, unknown> = {}) => ({
  id: 'po-1',
  po_number: 'PO-2026-0001',
  supplier_details: 'Acme Fasteners',
  supplier_agreement: null,
  supplier_agreement_details: null,
  work_order: null,
  work_order_details: null,
  owning_group: null,
  owning_group_details: null,
  status: 'draft',
  status_label: 'Draft',
  order_date: '2026-04-01T00:00:00Z',
  expected_delivery_date: null,
  supplier_order_number: '',
  sales_order_number: '',
  estimated_total: '0.00',
  voided_at: null,
  voided_by_username: null,
  void_reason: '',
  items: [],
  attachments: [],
  ...overrides,
});

const candidate = (overrides: Record<string, unknown> = {}) => ({
  item_supplier: 12,
  match_kind: 'package_barcode',
  match_label: 'package barcode',
  matched_value: '012345678905',
  is_exact: true,
  item: { id: 'item-1', name: 'M3 hex bolt', sku: 'OMS-M3-HEX', is_kit: false },
  supplier_sku: 'ACME-M3-100',
  package_upc: '012345678905',
  unit_upc: '998877665544',
  quantity_per_package: 5,
  suggested_quantity: 10,
  suggested_unit_cost: '2.50',
  already_on_order: null,
  ...overrides,
});

/** An axios-shaped rejection, which is what the page branches on. */
const apiError = (status: number, data: Record<string, unknown>) =>
  Object.assign(new Error('request failed'), { response: { status, data } });

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

const entryField = () => screen.getByLabelText(/add an item/i);

describe('PurchaseOrderPage — adding a line to a draft order', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('token', 'test-token');
    (api.workOrderAPI.listWorkOrders as jest.Mock).mockResolvedValue({ data: { results: [] } });
    (api.sigAPI.listMySIGs as jest.Mock).mockResolvedValue({ data: { results: [] } });
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: order() });
  });

  test('a scanned barcode plus Enter adds the line without touching the mouse', async () => {
    (api.purchaseOrderAPI.addLineItem as jest.Mock).mockResolvedValue({
      data: {
        created: true,
        line_item: line(),
        match: candidate(),
        purchase_order: order({ items: [line()], estimated_total: '25.00' }),
      },
    });

    renderPage();

    const field = await screen.findByLabelText(/add an item/i);
    // A keyboard-wedge scanner types the payload then presses Enter.
    fireEvent.change(field, { target: { value: '012345678905' } });
    fireEvent.submit(field.closest('form')!);

    await waitFor(() => {
      expect(api.purchaseOrderAPI.addLineItem).toHaveBeenCalledWith('po-1', {
        identifier: '012345678905',
      });
    });
    // What matched, so the operator can see the scan landed on the right item.
    expect(
      await screen.findByText(/Added M3 hex bolt × 10 \(matched on package barcode 012345678905\)/)
    ).toBeInTheDocument();
    // The field is cleared and ready for the next scan.
    expect((entryField() as HTMLInputElement).value).toBe('');
  });

  test('the page is patched from the response instead of being reloaded', async () => {
    (api.purchaseOrderAPI.addLineItem as jest.Mock).mockResolvedValue({
      data: {
        created: true,
        line_item: line(),
        match: candidate(),
        purchase_order: order({ items: [line()], estimated_total: '25.00' }),
      },
    });

    renderPage();
    await screen.findByLabelText(/add an item/i);
    expect(api.purchaseOrderAPI.getOrder).toHaveBeenCalledTimes(1);

    fireEvent.change(entryField(), { target: { value: 'ACME-M3-100' } });
    fireEvent.submit(entryField().closest('form')!);

    expect(await screen.findByText('M3 hex bolt')).toBeInTheDocument();
    // docs/REACTIVE_MUTATIONS.md: no re-run of the initial loader.
    expect(api.purchaseOrderAPI.getOrder).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/loading purchase order/i)).not.toBeInTheDocument();
  });

  test('an item the supplier does not carry is refused in words the operator can act on', async () => {
    (api.purchaseOrderAPI.addLineItem as jest.Mock).mockRejectedValue(
      apiError(400, {
        code: 'not_supplied',
        error:
          'Acme Fasteners does not supply M5 carriage bolt. Add Acme Fasteners as a supplier ' +
          'for that item, or order it on a purchase order for a supplier that carries it.',
      })
    );

    renderPage();
    fireEvent.change(await screen.findByLabelText(/add an item/i), { target: { value: 'BD-M5' } });
    fireEvent.submit(entryField().closest('form')!);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Acme Fasteners does not supply M5 carriage bolt');
    // Failure preserves context: the typed text is still there to correct.
    expect((entryField() as HTMLInputElement).value).toBe('BD-M5');
  });

  test('an unmatched identifier is reported rather than silently ignored', async () => {
    (api.purchaseOrderAPI.addLineItem as jest.Mock).mockRejectedValue(
      apiError(400, {
        code: 'no_match',
        error: 'Nothing matching "wrench" is supplied by Acme Fasteners.',
      })
    );

    renderPage();
    fireEvent.change(await screen.findByLabelText(/add an item/i), { target: { value: 'wrench' } });
    fireEvent.submit(entryField().closest('form')!);

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Nothing matching "wrench" is supplied by Acme Fasteners.'
    );
  });

  test('an ambiguous identifier offers the candidates instead of guessing', async () => {
    (api.purchaseOrderAPI.addLineItem as jest.Mock).mockRejectedValueOnce(
      apiError(409, {
        code: 'ambiguous',
        error: '"M3 hex" matches 2 items Acme Fasteners supplies. Choose which one to add.',
        candidates: [
          candidate({ match_kind: 'partial_item_name', match_label: 'item name (partial)' }),
          candidate({
            item_supplier: 13,
            item: { id: 'item-2', name: 'M3 hex nut', sku: 'OMS-M3-NUT', is_kit: false },
            supplier_sku: 'ACME-M3-200',
          }),
        ],
      })
    );

    renderPage();
    fireEvent.change(await screen.findByLabelText(/add an item/i), { target: { value: 'M3 hex' } });
    fireEvent.submit(entryField().closest('form')!);

    expect(await screen.findByRole('alert')).toHaveTextContent('matches 2 items');
    const nutButton = await screen.findByRole('button', { name: /add m3 hex nut/i });

    // Picking one re-posts by the exact catalogue row.
    (api.purchaseOrderAPI.addLineItem as jest.Mock).mockResolvedValueOnce({
      data: {
        created: true,
        line_item: line({
          id: 'line-2',
          item_details: { id: 'item-2', name: 'M3 hex nut', sku: 'OMS-M3-NUT' },
          quantity_ordered: 20,
        }),
        match: null,
        purchase_order: order({ items: [line({ id: 'line-2' })] }),
      },
    });
    fireEvent.click(nutButton);

    await waitFor(() => {
      expect(api.purchaseOrderAPI.addLineItem).toHaveBeenLastCalledWith('po-1', {
        item_supplier: 13,
      });
    });
    expect(screen.queryByRole('button', { name: /add m3 hex nut/i })).not.toBeInTheDocument();
  });

  /**
   * The candidate "Add <item>" controls must carry the same class as this
   * page's own submit control, so the two cannot silently drift apart.
   *
   * These buttons once used `btn-edit`, which on this page is
   * `background: none; opacity: 0.6` while a global `color: white` won the
   * cascade — white text on the white candidate card, so the operator could
   * not see the control the choose-one flow depends on.
   *
   * Limit, stated honestly: this pins class PARITY between the two controls,
   * which is what would have caught that regression. It does NOT prove
   * computed visibility — jsdom does not apply the external stylesheets whose
   * cascade produced the white-on-white result. That is the lesson the bug
   * carries: every component-level test here passed while the rendered page
   * was broken, and only rendering the real page surfaced it.
   */
  test('candidate buttons look like the page’s own submit control', async () => {
    (api.purchaseOrderAPI.addLineItem as jest.Mock).mockRejectedValueOnce(
      apiError(409, {
        code: 'ambiguous',
        error: '"M3 hex" matches 2 items Acme Fasteners supplies. Choose which one to add.',
        candidates: [
          candidate({ match_kind: 'partial_item_name', match_label: 'item name (partial)' }),
          candidate({
            item_supplier: 13,
            item: { id: 'item-2', name: 'M3 hex nut', sku: 'OMS-M3-NUT', is_kit: false },
            supplier_sku: 'ACME-M3-200',
          }),
        ],
      })
    );

    renderPage();
    fireEvent.change(await screen.findByLabelText(/add an item/i), { target: { value: 'M3 hex' } });
    fireEvent.submit(entryField().closest('form')!);

    await screen.findByRole('button', { name: /add m3 hex nut/i });

    // Derived from the rendered submit button, not hard-coded, so the
    // assertion survives a rename of the page's button class.
    const submitClasses = screen
      .getByRole('button', { name: /add to order/i })
      .className.split(/\s+/)
      .filter(Boolean);
    expect(submitClasses).not.toHaveLength(0);

    for (const name of [/add m3 hex bolt/i, /add m3 hex nut/i]) {
      expect(screen.getByRole('button', { name }).className.split(/\s+/).filter(Boolean)).toEqual(
        submitClasses
      );
    }
  });

  test('re-adding something already on the order reports the grown line', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({
      data: order({ items: [line()] }),
    });
    (api.purchaseOrderAPI.addLineItem as jest.Mock).mockResolvedValue({
      data: {
        created: false,
        line_item: line({ quantity_ordered: 20 }),
        match: candidate(),
        purchase_order: order({ items: [line({ quantity_ordered: 20 })] }),
      },
    });

    renderPage();
    fireEvent.change(await screen.findByLabelText(/add an item/i), {
      target: { value: '012345678905' },
    });
    fireEvent.submit(entryField().closest('form')!);

    expect(
      await screen.findByText(/M3 hex bolt was already on this order — quantity is now 20/)
    ).toBeInTheDocument();
  });

  test('the entry field takes focus on mount without scrolling the page there', async () => {
    // First scan of the session must need no mouse either — but the control
    // sits below the header, details and attachments, so opening a draft order
    // just to read it must not jump the page down to Line Items.
    const scrolled: unknown[] = [];
    const realFocus = HTMLInputElement.prototype.focus;
    const focusSpy = jest
      .spyOn(HTMLInputElement.prototype, 'focus')
      .mockImplementation(function (this: HTMLInputElement, options?: FocusOptions) {
        scrolled.push(options?.preventScroll);
        return realFocus.call(this, options);
      });

    try {
      renderPage();
      const field = await screen.findByLabelText(/add an item/i);

      await waitFor(() => expect(document.activeElement).toBe(field));
      expect(scrolled).toContain(true);
    } finally {
      focusSpy.mockRestore();
    }
  });

  test('a refused add leaves the failed text selected so the next scan replaces it', async () => {
    (api.purchaseOrderAPI.addLineItem as jest.Mock).mockRejectedValue(
      apiError(409, {
        code: 'ambiguous',
        error: '"M3 hex" matches 2 items Acme Fasteners supplies. Choose which one to add.',
        candidates: [candidate(), candidate({ item_supplier: 13 })],
      })
    );

    renderPage();
    const field = (await screen.findByLabelText(/add an item/i)) as HTMLInputElement;
    fireEvent.change(field, { target: { value: 'M3 hex' } });
    fireEvent.submit(field.closest('form')!);

    await screen.findByRole('alert');
    // The text stays put so it can be corrected by hand...
    expect((entryField() as HTMLInputElement).value).toBe('M3 hex');
    await waitFor(() => expect(document.activeElement).toBe(entryField()));
    // ...but it is selected, so a scanner's burst overwrites rather than
    // appends — otherwise the next scan posts "M3 hex012345678905".
    const refocused = entryField() as HTMLInputElement;
    expect(refocused.selectionStart).toBe(0);
    expect(refocused.selectionEnd).toBe('M3 hex'.length);
  });

  test('focus returns to the entry field after a completed add', async () => {
    // The scanner loop is a burst of characters plus Enter, over and over. If
    // the field loses focus when the add settles, the second scan lands
    // nowhere and the operator has to reach for the mouse.
    (api.purchaseOrderAPI.addLineItem as jest.Mock).mockResolvedValue({
      data: {
        created: true,
        line_item: line(),
        match: candidate(),
        purchase_order: order({ items: [line()], estimated_total: '25.00' }),
      },
    });

    renderPage();
    const field = (await screen.findByLabelText(/add an item/i)) as HTMLInputElement;
    field.focus();
    fireEvent.change(field, { target: { value: '012345678905' } });
    fireEvent.submit(field.closest('form')!);

    await screen.findByText(/Added M3 hex bolt/);
    await waitFor(() => expect(document.activeElement).toBe(entryField()));
  });

  test('picking a candidate puts focus back in the entry field for the next scan', async () => {
    (api.purchaseOrderAPI.addLineItem as jest.Mock).mockRejectedValueOnce(
      apiError(409, {
        code: 'ambiguous',
        error: '"M3 hex" matches 2 items Acme Fasteners supplies. Choose which one to add.',
        candidates: [
          candidate({ match_kind: 'partial_item_name', match_label: 'item name (partial)' }),
          candidate({
            item_supplier: 13,
            item: { id: 'item-2', name: 'M3 hex nut', sku: 'OMS-M3-NUT', is_kit: false },
          }),
        ],
      })
    );

    renderPage();
    fireEvent.change(await screen.findByLabelText(/add an item/i), { target: { value: 'M3 hex' } });
    fireEvent.submit(entryField().closest('form')!);

    const nutButton = await screen.findByRole('button', { name: /add m3 hex nut/i });
    (api.purchaseOrderAPI.addLineItem as jest.Mock).mockResolvedValueOnce({
      data: {
        created: true,
        line_item: line({ id: 'line-2', quantity_ordered: 20 }),
        match: null,
        purchase_order: order({ items: [line({ id: 'line-2' })] }),
      },
    });
    // Clicking the candidate is the one mouse action; the loop resumes on the
    // keyboard from there.
    nutButton.focus();
    fireEvent.click(nutButton);

    await waitFor(() => expect(document.activeElement).toBe(entryField()));
  });

  test('a second Enter while the add is in flight does not double-post', async () => {
    let resolveAdd: (value: unknown) => void = () => {};
    (api.purchaseOrderAPI.addLineItem as jest.Mock).mockReturnValue(
      new Promise((resolve) => {
        resolveAdd = resolve;
      })
    );

    renderPage();
    const field = await screen.findByLabelText(/add an item/i);
    fireEvent.change(field, { target: { value: '012345678905' } });
    fireEvent.submit(field.closest('form')!);
    // The field stays focused (and submittable) during the request, so a
    // trigger-happy second Enter has to be ignored rather than add twice.
    fireEvent.submit(field.closest('form')!);

    expect(api.purchaseOrderAPI.addLineItem).toHaveBeenCalledTimes(1);

    resolveAdd({
      data: {
        created: true,
        line_item: line(),
        match: candidate(),
        purchase_order: order({ items: [line()] }),
      },
    });
    await screen.findByText(/Added M3 hex bolt/);
  });

  test('an empty submit never reaches the API', async () => {
    renderPage();
    const field = await screen.findByLabelText(/add an item/i);
    fireEvent.change(field, { target: { value: '   ' } });
    fireEvent.submit(field.closest('form')!);

    expect(api.purchaseOrderAPI.addLineItem).not.toHaveBeenCalled();
    expect(await screen.findByRole('alert')).toHaveTextContent(
      /type or scan an item name, sku, barcode, or supplier sku/i
    );
  });

  test('a cross-vendor candidate row shows the listing the code came from', async () => {
    // The candidate offered is always THIS supplier's row, so without the
    // provenance nothing on screen contains what the operator scanned — which
    // is the silent substitution the cross-vendor tier exists to rule out.
    (api.purchaseOrderAPI.addLineItem as jest.Mock).mockRejectedValue(
      apiError(409, {
        code: 'ambiguous',
        error: '"BD-" matches 2 items Acme Fasteners supplies. Choose which one to add.',
        candidates: [
          candidate({
            match_kind: 'other_supplier_listing',
            match_label: "Bolt Depot's supplier SKU",
            matched_value: 'BD-M3',
          }),
          candidate({
            item_supplier: 13,
            item: { id: 'item-2', name: 'M5 carriage bolt', sku: 'OMS-M5-CAR', is_kit: false },
            match_kind: 'other_supplier_listing',
            match_label: "Bolt Depot's supplier SKU",
            matched_value: 'BD-M5',
          }),
        ],
      })
    );

    renderPage();
    fireEvent.change(await screen.findByLabelText(/add an item/i), { target: { value: 'BD-' } });
    fireEvent.submit(entryField().closest('form')!);

    const boltRow = (await screen.findByText('M3 hex bolt')).closest('li')!;
    expect(
      within(boltRow).getByText(/matched on Bolt Depot's supplier SKU BD-M3/)
    ).toBeInTheDocument();
    const nutRow = screen.getByText('M5 carriage bolt').closest('li')!;
    expect(
      within(nutRow).getByText(/matched on Bolt Depot's supplier SKU BD-M5/)
    ).toBeInTheDocument();
  });

  test('a voided candidate is shown as a dead end, not an addable choice', async () => {
    // Adding it could only ever come back 400 `line_voided`, and the payload
    // already says so — the screen must not offer an action the server refuses.
    (api.purchaseOrderAPI.addLineItem as jest.Mock).mockRejectedValue(
      apiError(409, {
        code: 'ambiguous',
        error: '"M3 hex" matches 2 items Acme Fasteners supplies. Choose which one to add.',
        candidates: [
          candidate({
            already_on_order: {
              line_item: 'line-1',
              quantity_ordered: 4,
              is_voided: true,
              repeat_increment: null,
              quantity_ordered_after: null,
            },
          }),
          candidate({
            item_supplier: 13,
            item: { id: 'item-2', name: 'M3 hex nut', sku: 'OMS-M3-NUT', is_kit: false },
          }),
        ],
      })
    );

    renderPage();
    fireEvent.change(await screen.findByLabelText(/add an item/i), { target: { value: 'M3 hex' } });
    fireEvent.submit(entryField().closest('form')!);

    const boltRow = (await screen.findByText('M3 hex bolt')).closest('li')!;
    expect(within(boltRow).getByText(/voided on this order/)).toBeInTheDocument();
    expect(within(boltRow).queryByRole('button')).not.toBeInTheDocument();
    // The other candidate is still a live choice.
    expect(screen.getByRole('button', { name: /add m3 hex nut/i })).toBeInTheDocument();
  });

  test('a candidate already on the order says so before it is picked', async () => {
    (api.purchaseOrderAPI.addLineItem as jest.Mock).mockRejectedValue(
      apiError(409, {
        code: 'ambiguous',
        error: '"M3 hex" matches 2 items Acme Fasteners supplies. Choose which one to add.',
        candidates: [
          candidate({
            already_on_order: { line_item: 'line-1', quantity_ordered: 10, is_voided: false },
          }),
          candidate({
            item_supplier: 13,
            item: { id: 'item-2', name: 'M3 hex nut', sku: 'OMS-M3-NUT', is_kit: false },
          }),
        ],
      })
    );

    renderPage();
    fireEvent.change(await screen.findByLabelText(/add an item/i), { target: { value: 'M3 hex' } });
    fireEvent.submit(entryField().closest('form')!);

    const boltRow = (await screen.findByText('M3 hex bolt')).closest('li')!;
    expect(within(boltRow).getByText(/already on this order \(10\)/)).toBeInTheDocument();
  });
});

describe('PurchaseOrderPage — where the add control is not offered', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
    (api.workOrderAPI.listWorkOrders as jest.Mock).mockResolvedValue({ data: { results: [] } });
    (api.sigAPI.listMySIGs as jest.Mock).mockResolvedValue({ data: { results: [] } });
  });

  test.each(['sent', 'confirmed', 'partially_received', 'received', 'cancelled', 'voided'])(
    'a %s order offers no add control at all',
    async (status) => {
      localStorage.setItem('token', 'test-token');
      (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({
        data: order({ status, items: [line()] }),
      });

      renderPage();

      await screen.findByText('Line Items');
      expect(screen.queryByLabelText(/add an item/i)).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /add to order/i })).not.toBeInTheDocument();
    }
  );

  test('an anonymous viewer of a draft order gets no add control', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: order() });

    renderPage();

    await screen.findByText('Line Items');
    expect(screen.queryByLabelText(/add an item/i)).not.toBeInTheDocument();
  });
});
