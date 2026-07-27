/**
 * Per-supplier purchase/pricing agreements on a purchase order (op-yoos).
 *
 * Create form: the picker only appears once a supplier with active agreements
 * on file is chosen, the choice rides along in the create payload, and it is
 * cleared when the supplier changes (an agreement belongs to one supplier and
 * the backend rejects a mismatch).
 *
 * Detail page: the attached agreement's name is shown, with an em dash when
 * the order was placed at list price.
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import PurchaseOrderFormPage from '../../pages/PurchaseOrderFormPage';
import PurchaseOrderPage from '../../pages/PurchaseOrderPage';
import * as api from '../../services/api';

vi.mock('../../services/api');

vi.mock('../../utils/dialogs', () => ({
  showError: jest.fn(),
  showSuccess: jest.fn(),
  confirmAction: jest.fn((_title: string, _msg: string, onConfirm: () => void) => {
    void onConfirm();
  }),
  promptInput: jest.fn(),
}));

const mockNavigate = jest.fn();
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useNavigate: () => mockNavigate,
}));

const mockItem: api.ReorderDataItem = {
  item_supplier_id: 1,
  item_id: 'item-1',
  item_name: 'Test Item',
  item_sku: 'TEST-001',
  current_stock: 5,
  minimum_stock: 10,
  reorder_quantity: 20,
  suggested_quantity: 10,
  unit_cost: '2.50',
  package_cost: '2.50',
  quantity_per_package: 1,
  lead_time_days: 7,
  supplier_sku: 'SUP-001',
  supplier_url: 'https://example.com/item',
  is_primary: true,
  line_total: '25.00',
};

const supplier = (id: number, name: string) => ({
  id,
  name,
  supplier_type: 'online',
  website: '',
  total_items: 1,
  items: [{ ...mockItem, item_supplier_id: id }],
  assets: [],
  estimated_total: '25.00',
  avg_lead_time: 5,
});

const agreement = (id: number, supplierId: number, name: string, notes = '') => ({
  id,
  supplier: supplierId,
  supplier_name: 'Acme Supplies',
  name,
  notes,
  document: null,
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
});

const renderForm = () =>
  render(
    <MemoryRouter>
      <PurchaseOrderFormPage />
    </MemoryRouter>
  );

const selectSupplier = async (name: string) => {
  await waitFor(() => {
    expect(screen.getByText(name)).toBeInTheDocument();
  });
  fireEvent.click(screen.getByText(name).closest('button')!);
};

describe('PurchaseOrderFormPage — supplier agreement picker', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('token', 'test-token');
    (api.purchaseOrderAPI.createOrder as jest.Mock).mockResolvedValue({
      data: { id: 42, po_number: 'PO-2026-0042' },
    });
  });

  test('offers the selected supplier’s active agreements', async () => {
    (api.purchaseOrderAPI.getReorderData as jest.Mock).mockResolvedValue({
      data: { suppliers: [supplier(1, 'Acme Supplies')] },
    });
    (api.supplierAgreementAPI.listBySupplier as jest.Mock).mockResolvedValue({
      data: { results: [agreement(7, 1, '2026 nonprofit pricing', '15% off list')] },
    });

    renderForm();
    await selectSupplier('Acme Supplies');

    const picker = await screen.findByLabelText(/purchase \/ pricing agreement/i);
    expect(picker).toBeInTheDocument();
    expect(api.supplierAgreementAPI.listBySupplier).toHaveBeenCalledWith(1);
    expect(screen.getByRole('option', { name: '2026 nonprofit pricing' })).toBeInTheDocument();
    // Optional — "No agreement" is the default.
    expect((picker as HTMLSelectElement).value).toBe('');
  });

  test('hides the picker when the supplier has no agreements on file', async () => {
    (api.purchaseOrderAPI.getReorderData as jest.Mock).mockResolvedValue({
      data: { suppliers: [supplier(1, 'Acme Supplies')] },
    });
    (api.supplierAgreementAPI.listBySupplier as jest.Mock).mockResolvedValue({
      data: { results: [] },
    });

    renderForm();
    await selectSupplier('Acme Supplies');

    await waitFor(() => {
      expect(screen.getByText('Test Item')).toBeInTheDocument();
    });
    expect(screen.queryByLabelText(/purchase \/ pricing agreement/i)).not.toBeInTheDocument();
  });

  test('sends the chosen agreement with the created order', async () => {
    (api.purchaseOrderAPI.getReorderData as jest.Mock).mockResolvedValue({
      data: { suppliers: [supplier(1, 'Acme Supplies')] },
    });
    (api.supplierAgreementAPI.listBySupplier as jest.Mock).mockResolvedValue({
      data: { results: [agreement(7, 1, '2026 nonprofit pricing')] },
    });

    renderForm();
    await selectSupplier('Acme Supplies');

    const picker = await screen.findByLabelText(/purchase \/ pricing agreement/i);
    fireEvent.change(picker, { target: { value: '7' } });
    fireEvent.click(screen.getByRole('button', { name: /create purchase order/i }));

    await waitFor(() => {
      expect(api.purchaseOrderAPI.createOrder).toHaveBeenCalled();
    });
    expect((api.purchaseOrderAPI.createOrder as jest.Mock).mock.calls[0][0]).toMatchObject({
      supplier: 1,
      supplier_agreement: 7,
    });
  });

  test('omits supplier_agreement when none is picked', async () => {
    (api.purchaseOrderAPI.getReorderData as jest.Mock).mockResolvedValue({
      data: { suppliers: [supplier(1, 'Acme Supplies')] },
    });
    (api.supplierAgreementAPI.listBySupplier as jest.Mock).mockResolvedValue({
      data: { results: [agreement(7, 1, '2026 nonprofit pricing')] },
    });

    renderForm();
    await selectSupplier('Acme Supplies');
    await screen.findByLabelText(/purchase \/ pricing agreement/i);

    fireEvent.click(screen.getByRole('button', { name: /create purchase order/i }));

    await waitFor(() => {
      expect(api.purchaseOrderAPI.createOrder).toHaveBeenCalled();
    });
    expect(
      (api.purchaseOrderAPI.createOrder as jest.Mock).mock.calls[0][0]
    ).not.toHaveProperty('supplier_agreement');
  });

  test('clears the picked agreement when the supplier changes', async () => {
    (api.purchaseOrderAPI.getReorderData as jest.Mock).mockResolvedValue({
      data: { suppliers: [supplier(1, 'Acme Supplies'), supplier(2, 'Beta Parts')] },
    });
    (api.supplierAgreementAPI.listBySupplier as jest.Mock).mockImplementation(
      (supplierId: number) =>
        Promise.resolve({
          data: {
            results:
              supplierId === 1
                ? [agreement(7, 1, 'Acme contract')]
                : [agreement(9, 2, 'Beta contract')],
          },
        })
    );

    renderForm();
    await selectSupplier('Acme Supplies');

    const picker = await screen.findByLabelText(/purchase \/ pricing agreement/i);
    fireEvent.change(picker, { target: { value: '7' } });
    expect((picker as HTMLSelectElement).value).toBe('7');

    fireEvent.click(screen.getByText('Beta Parts').closest('button')!);

    await waitFor(() => {
      expect(screen.getByRole('option', { name: 'Beta contract' })).toBeInTheDocument();
    });
    const repicked = screen.getByLabelText(
      /purchase \/ pricing agreement/i
    ) as HTMLSelectElement;
    expect(repicked.value).toBe('');
  });

  test('a failing agreement lookup never blocks creating the order', async () => {
    (api.purchaseOrderAPI.getReorderData as jest.Mock).mockResolvedValue({
      data: { suppliers: [supplier(1, 'Acme Supplies')] },
    });
    (api.supplierAgreementAPI.listBySupplier as jest.Mock).mockRejectedValue(
      new Error('boom')
    );

    renderForm();
    await selectSupplier('Acme Supplies');

    await waitFor(() => {
      expect(screen.getByText('Test Item')).toBeInTheDocument();
    });
    expect(screen.queryByLabelText(/purchase \/ pricing agreement/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /create purchase order/i }));
    await waitFor(() => {
      expect(api.purchaseOrderAPI.createOrder).toHaveBeenCalled();
    });
  });
});

describe('PurchaseOrderPage — attached agreement', () => {
  const baseOrder = {
    id: 'po-1',
    po_number: 'PO-2026-0001',
    supplier_details: 'Acme Supplies',
    supplier_agreement: null,
    supplier_agreement_details: null,
    status: 'sent',
    status_label: 'Sent',
    order_date: '2026-04-01T00:00:00Z',
    expected_delivery_date: '2026-05-15',
    supplier_order_number: 'SUP-9',
    sales_order_number: 'SO-7',
    estimated_total: '100.00',
    voided_at: null,
    voided_by_username: null,
    void_reason: '',
    items: [],
    attachments: [],
  };

  const renderDetail = () =>
    render(
      <MantineProvider>
        <MemoryRouter initialEntries={['/purchase-orders/po-1']}>
          <Routes>
            <Route path="/purchase-orders/:orderId" element={<PurchaseOrderPage />} />
          </Routes>
        </MemoryRouter>
      </MantineProvider>
    );

  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('token', 'test-token');
  });

  test('shows the agreement the order was placed under', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({
      data: {
        ...baseOrder,
        supplier_agreement: 7,
        supplier_agreement_details: { id: 7, name: '2026 nonprofit pricing' },
      },
    });

    renderDetail();

    await waitFor(() => {
      expect(screen.getByText('Agreement:')).toBeInTheDocument();
    });
    expect(screen.getByText('2026 nonprofit pricing')).toBeInTheDocument();
  });

  test('shows an em dash when the order has no agreement', async () => {
    (api.purchaseOrderAPI.getOrder as jest.Mock).mockResolvedValue({ data: baseOrder });

    renderDetail();

    await waitFor(() => {
      expect(screen.getByText('Agreement:')).toBeInTheDocument();
    });
    const row = screen.getByText('Agreement:').closest('.info-item')!;
    expect(row.querySelector('.info-value')!.textContent).toBe('—');
  });
});
