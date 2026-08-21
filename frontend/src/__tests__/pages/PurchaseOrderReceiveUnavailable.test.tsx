/**
 * Saying WHY receiving is unavailable on a purchase order (salvaged from #1019).
 *
 * The receive affordances used to simply vanish on an order whose status did
 * not allow receiving. A missing button with no explanation reads as a broken
 * app rather than "this order has not been sent yet" — which is exactly how
 * this came up. The detail page now names the state and what to do about it.
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor } from '@testing-library/react';
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

const order = (overrides: Record<string, unknown> = {}) => ({
  id: 'po-1',
  po_number: 'PO-2026-0042',
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

const loadOrder = async (data: Record<string, unknown>) => {
  (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data });
  renderPage();
  await waitFor(() => {
    expect(screen.getByText(String(data.po_number), { exact: false })).toBeInTheDocument();
  });
};

describe('PurchaseOrderPage — why receiving is unavailable', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('token', 'test-token');
    (api.workOrderAPI.listWorkOrders as jest.Mock).mockResolvedValue({ data: { results: [] } });
    (api.sigAPI.listMySIGs as jest.Mock).mockResolvedValue({ data: { results: [] } });
  });

  test('a draft says to send it to the supplier first', async () => {
    await loadOrder(order());

    expect(screen.getByTestId('receive-unavailable-notice')).toHaveTextContent(
      /still a draft.*[Ss]end it to the supplier/,
    );
    expect(screen.queryByRole('button', { name: /Receive items/i })).not.toBeInTheDocument();
  });

  test('a fully received order says every line is already received', async () => {
    await loadOrder(order({ status: 'received', status_label: 'Fully Received' }));

    expect(screen.getByTestId('receive-unavailable-notice')).toHaveTextContent(
      /already been received in full/,
    );
  });

  test('a cancelled order says it was cancelled', async () => {
    await loadOrder(order({ status: 'cancelled', status_label: 'Cancelled' }));

    expect(screen.getByTestId('receive-unavailable-notice')).toHaveTextContent(
      /cancelled, so nothing can be received/,
    );
  });

  test('a voided order says it was voided', async () => {
    await loadOrder(
      order({ status: 'voided', status_label: 'Voided', voided_at: '2026-04-02T00:00:00Z' }),
    );

    expect(screen.getByTestId('receive-unavailable-notice')).toHaveTextContent(
      /voided, so nothing can be received/,
    );
  });

  test('an order that CAN receive gets the buttons and no notice', async () => {
    await loadOrder(order({ status: 'sent', status_label: 'Sent to Supplier' }));

    expect(screen.queryByTestId('receive-unavailable-notice')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Receive items/i })).toBeInTheDocument();
  });

  test('a signed-out viewer gets neither the buttons nor the notice', async () => {
    localStorage.clear();
    await loadOrder(order());

    expect(screen.queryByTestId('receive-unavailable-notice')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Receive items/i })).not.toBeInTheDocument();
  });
});
