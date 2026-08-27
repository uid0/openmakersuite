/**
 * Deleting vs voiding a purchase-order line (oms-po-line-delete).
 *
 * The captain's boundary: while the order is the shop's own document a line
 * added by mistake is a typo and is deleted outright; once the supplier holds
 * a copy the line is part of a record someone else also has, so it can only be
 * voided. The operator must never have to know which rule applies, and must
 * never be able to destroy a line they believed they were merely marking.
 *
 * So this file asserts three things about the SURFACE, not just the call:
 *
 * 1. exactly one of the two actions is offered, chosen off the API's own
 *    `can_delete_items` rather than a status string parsed here;
 * 2. the two are visually distinct — different class, different label, and
 *    delete carries no reason box;
 * 3. the delete confirmation NAMES what it is about to destroy.
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import PurchaseOrderPage from '../../pages/PurchaseOrderPage';
import * as api from '../../services/api';
import { confirmAction } from '../../utils/dialogs';

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
  // The whole point: the page reads this, never the status string.
  can_delete_items: true,
  can_receive: false,
  order_date: '2026-04-01T00:00:00Z',
  expected_delivery_date: null,
  supplier_order_number: '',
  sales_order_number: '',
  estimated_total: '25.00',
  voided_at: null,
  voided_by_username: null,
  void_reason: '',
  items: [line()],
  attachments: [],
  ...overrides,
});

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

/** Run the callback `confirmAction` was handed, as confirming the dialog does. */
const confirmLatest = async () => {
  const call = (confirmAction as jest.Mock).mock.calls.at(-1)!;
  await call[2]();
};

describe('PurchaseOrderPage — deleting vs voiding a line', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('token', 'test-token');
    (api.workOrderAPI.listWorkOrders as jest.Mock).mockResolvedValue({ data: { results: [] } });
    (api.sigAPI.listMySIGs as jest.Mock).mockResolvedValue({ data: { results: [] } });
  });

  describe('while the order is still the shop’s own', () => {
    beforeEach(() => {
      (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: order() });
    });

    test('offers Delete and NOT Void', async () => {
      renderPage();

      expect(await screen.findByTestId('delete-line-line-1')).toBeInTheDocument();
      // Never both. An operator must not be able to pick the wrong one.
      expect(screen.queryByTestId('void-line-line-1')).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /void item/i })).not.toBeInTheDocument();
    });

    test('the delete control is visually distinct from the void control', async () => {
      renderPage();

      const button = await screen.findByTestId('delete-line-line-1');

      // Its own class, so the stylesheet can make destruction look like
      // destruction rather than like a louder void.
      expect(button).toHaveClass('btn-delete-item');
      expect(button).not.toHaveClass('btn-void-item');
      // And it says what it does.
      expect(button).toHaveTextContent(/delete line/i);
    });

    test('deleting asks for no reason — that friction is what made void wrong here', async () => {
      renderPage();

      fireEvent.click(await screen.findByTestId('delete-line-line-1'));

      // Void opens a reason textarea before it will proceed. Delete must not.
      expect(screen.queryByPlaceholderText(/reason for voiding/i)).not.toBeInTheDocument();
      expect(confirmAction).toHaveBeenCalledTimes(1);
    });

    test('the confirmation names exactly what is about to be destroyed', async () => {
      renderPage();

      fireEvent.click(await screen.findByTestId('delete-line-line-1'));

      const [title, message] = (confirmAction as jest.Mock).mock.calls[0];
      expect(title).toMatch(/delete this line/i);
      // The line, its quantity, its price and its cost — an operator who
      // clicked the wrong row can see that from the sentence alone.
      expect(message).toContain('M3 hex bolt');
      expect(message).toContain('10 ×');
      expect(message).toContain('$2.50');
      expect(message).toContain('$25.00');
      // And that it is irreversible.
      expect(message).toMatch(/cannot be undone/i);
    });

    test('confirming calls the delete endpoint and patches the page in place', async () => {
      (api.purchaseOrderAPI.deleteLineItem as jest.Mock).mockResolvedValue({
        data: {
          deleted: { line_item: 'line-1' },
          purchase_order: order({ items: [], estimated_total: '0.00' }),
        },
      });

      renderPage();
      await screen.findByTestId('delete-line-line-1');
      expect(api.purchaseOrderAPI.getOrder).toHaveBeenCalledTimes(1);

      fireEvent.click(screen.getByTestId('delete-line-line-1'));
      await confirmLatest();

      await waitFor(() => {
        expect(api.purchaseOrderAPI.deleteLineItem).toHaveBeenCalledWith('po-1', 'line-1');
      });
      // docs/REACTIVE_MUTATIONS.md — patched from the response, not reloaded.
      expect(api.purchaseOrderAPI.getOrder).toHaveBeenCalledTimes(1);
      await waitFor(() => {
        expect(screen.queryByTestId('delete-line-line-1')).not.toBeInTheDocument();
      });
    });

    test('dismissing the confirmation destroys nothing', async () => {
      renderPage();

      fireEvent.click(await screen.findByTestId('delete-line-line-1'));
      // The confirm callback is simply never invoked.

      expect(api.purchaseOrderAPI.deleteLineItem).not.toHaveBeenCalled();
      expect(screen.getByTestId('delete-line-line-1')).toBeInTheDocument();
    });

    test('a refusal from the server is shown in words the operator can act on', async () => {
      (api.purchaseOrderAPI.deleteLineItem as jest.Mock).mockRejectedValue(
        Object.assign(new Error('request failed'), {
          response: {
            status: 400,
            data: {
              code: 'not_draft',
              error:
                'Line items can only be deleted while a purchase order is a draft. ' +
                'PO-2026-0001 is Sent to Supplier, so the supplier already has this ' +
                'line — void it instead to strike it off while keeping it on the record.',
            },
          },
        })
      );
      const { showError } = await import('../../utils/dialogs');

      renderPage();
      fireEvent.click(await screen.findByTestId('delete-line-line-1'));
      await confirmLatest();

      await waitFor(() => {
        expect(showError).toHaveBeenCalledWith(expect.stringMatching(/void it instead/i));
      });
      // The line is still there — a failed delete leaves the page intact.
      expect(screen.getByTestId('delete-line-line-1')).toBeInTheDocument();
    });
  });

  describe('once the supplier has the order', () => {
    beforeEach(() => {
      (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({
        data: order({
          status: 'sent',
          status_label: 'Sent to Supplier',
          can_delete_items: false,
          can_receive: true,
        }),
      });
    });

    test('offers Void and NOT Delete', async () => {
      renderPage();

      expect(await screen.findByTestId('void-line-line-1')).toBeInTheDocument();
      expect(screen.queryByTestId('delete-line-line-1')).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /delete line/i })).not.toBeInTheDocument();
    });

    test('void is unchanged: it still demands a written reason', async () => {
      renderPage();

      fireEvent.click(await screen.findByTestId('void-line-line-1'));

      const reason = await screen.findByPlaceholderText(/reason for voiding/i);
      expect(reason).toBeInTheDocument();
      // Disabled until a reason is typed — exactly as before this change.
      const confirm = screen.getByRole('button', { name: /confirm void/i });
      expect(confirm).toBeDisabled();

      fireEvent.change(reason, { target: { value: 'discontinued by supplier' } });
      expect(screen.getByRole('button', { name: /confirm void/i })).toBeEnabled();
    });
  });

  describe('the page never decides the rule for itself', () => {
    test('a draft flagged non-deletable by the API offers void, not delete', async () => {
      // A contrived pairing on purpose: if the page were parsing `status`
      // rather than reading the flag, this would offer Delete.
      (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({
        data: order({ status: 'draft', status_label: 'Draft', can_delete_items: false }),
      });

      renderPage();

      expect(await screen.findByTestId('void-line-line-1')).toBeInTheDocument();
      expect(screen.queryByTestId('delete-line-line-1')).not.toBeInTheDocument();
    });

    test('a sent order flagged deletable by the API offers delete', async () => {
      // The mirror of the above. The server is the single authority on the
      // boundary; the page renders whatever it is told.
      (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({
        data: order({ status: 'sent', status_label: 'Sent to Supplier', can_delete_items: true }),
      });

      renderPage();

      expect(await screen.findByTestId('delete-line-line-1')).toBeInTheDocument();
      expect(screen.queryByTestId('void-line-line-1')).not.toBeInTheDocument();
    });
  });

  describe('a voided line', () => {
    test('offers neither action — there is nothing left to do to it', async () => {
      (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({
        data: order({
          status: 'sent',
          can_delete_items: false,
          items: [line({ is_voided: true, void_reason: 'discontinued' })],
        }),
      });

      renderPage();

      await screen.findByText(/discontinued/);
      expect(screen.queryByTestId('delete-line-line-1')).not.toBeInTheDocument();
      expect(screen.queryByTestId('void-line-line-1')).not.toBeInTheDocument();
    });
  });
});
