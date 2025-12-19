import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import TransparencyPage from '../../pages/TransparencyPage';
import { analyticsAPI } from '../../services/api';

jest.mock('../../services/api', () => ({
  analyticsAPI: {
    getTransparencyLedger: jest.fn(),
  },
}));

const mockTransparencyData = {
  summary: {
    total_orders_with_financial_data: 1,
    total_amount_spent: 200.0,
    last_updated: '2024-01-15T12:00:00Z',
    transparency_note: 'We keep our logistics purchases public.',
  },
  orders: [
    {
      id: 1,
      item_id: 'test-item-id-1',
      item_name: 'Laser Cutter Belt',
      item_category: 'Machinery',
      quantity_ordered: 2,
      status: 'received',
      requested_at: '2024-01-10T10:00:00Z',
      ordered_at: '2024-01-11T10:00:00Z',
      delivered_at: '2024-01-14T10:00:00Z',
      estimated_cost: 180.0,
      actual_cost: 200.0,
      cost_per_unit: 100.0,
      cost_variance: 20.0,
      order_number: 'ORD-2024-001',
      invoice_number: 'INV-2024-001',
      invoice_url: 'https://example.com/invoice.pdf',
      purchase_order_url: 'https://example.com/po.pdf',
      delivery_tracking_url: 'https://carrier.example.com/track',
      supplier_url: 'https://supplier.example.com/item',
      public_notes: 'Installed in the laser shop.',
      supplier_name: 'Makersupply Co.',
    },
  ],
  ledger: [
    {
      id: 1,
      item_id: 'test-item-id-1',
      item_name: 'Laser Cutter Belt',
      supplier_name: 'Makersupply Co.',
      quantity: 2,
      requested_at: '2024-01-10T10:00:00Z',
      ordered_at: '2024-01-11T10:00:00Z',
      delivered_at: '2024-01-14',
      actual_cost: 200.0,
      estimated_cost: 180.0,
      status: 'received',
      order_number: 'ORD-2024-001',
      invoice_number: 'INV-2024-001',
    },
  ],
};

describe('TransparencyPage', () => {
  afterEach(() => {
    jest.clearAllMocks();
  });

  it('renders logistics ledger data after loading transparency report', async () => {
    const getTransparencyLedgerMock = analyticsAPI.getTransparencyLedger as jest.Mock;
    getTransparencyLedgerMock.mockResolvedValue({ data: mockTransparencyData });

    render(
      <MemoryRouter>
        <TransparencyPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(getTransparencyLedgerMock).toHaveBeenCalledTimes(1);
    });

    expect(await screen.findByText('Logistics Purchase Ledger')).toBeInTheDocument();
    // "Laser Cutter Belt" appears in both orders and ledger sections, so use getAllByText
    expect(screen.getAllByText('Laser Cutter Belt').length).toBeGreaterThan(0);
    // "Makersupply Co." also appears multiple times (in orders and ledger sections)
    expect(screen.getAllByText('Makersupply Co.').length).toBeGreaterThan(0);
    // "$200.00" appears multiple times (in summary and ledger), so use getAllByText
    expect(screen.getAllByText(/\$200\.00/).length).toBeGreaterThan(0);
    expect(screen.getByText(/Order #ORD-2024-001/)).toBeInTheDocument();
    expect(screen.getByText(/Invoice #INV-2024-001/)).toBeInTheDocument();
  });
});
