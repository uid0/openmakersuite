/**
 * Tests for PurchaseReceiptsPanel (op-u9ap) — the "Purchase / Receipts" tab
 * rendering GET /inventory/items/<id>/purchase_history/ (op-96uo): per-order
 * unit costs plus every delivery, grouped by order.
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import PurchaseReceiptsPanel from '../../components/inventory/PurchaseReceiptsPanel';
import { ItemPurchaseHistory } from '../../types';

const renderPanel = (history: ItemPurchaseHistory | null) =>
  render(
    <MantineProvider>
      <MemoryRouter>
        <PurchaseReceiptsPanel history={history} />
      </MemoryRouter>
    </MantineProvider>,
  );

// One item, two orders: the second was cheaper per unit and shipped in two
// packages — the case the tab exists to make visible.
const history: ItemPurchaseHistory = {
  order_costs: [
    {
      purchase_order: 11,
      po_number: 'PO-0001',
      order_date: '2026-01-05T10:00:00Z',
      status: 'received',
      quantity_ordered: 10,
      unit_cost_ordered: '3.2500',
      unit_cost_actual: '3.5000',
    },
    {
      purchase_order: 12,
      po_number: 'PO-0002',
      order_date: '2026-02-05T10:00:00Z',
      status: 'ordered',
      quantity_ordered: 25,
      unit_cost_ordered: '2.1000',
      unit_cost_actual: null,
    },
  ],
  deliveries: [
    {
      purchase_order: 11,
      po_number: 'PO-0001',
      delivery_date: '2026-01-12T10:00:00Z',
      tracking_number: '1Z-AAA',
      carrier: 'UPS',
      quantity_received: 10,
      receipt_notes: 'All good',
      is_complete: true,
    },
    {
      purchase_order: 12,
      po_number: 'PO-0002',
      delivery_date: '2026-02-10T10:00:00Z',
      tracking_number: '1Z-BBB',
      carrier: 'FedEx',
      quantity_received: 15,
      receipt_notes: 'Backordered remainder',
      is_complete: false,
    },
    {
      purchase_order: 12,
      po_number: 'PO-0002',
      delivery_date: '2026-02-18T10:00:00Z',
      tracking_number: '1Z-CCC',
      carrier: 'FedEx',
      quantity_received: 10,
      receipt_notes: '',
      is_complete: true,
    },
  ],
};

describe('PurchaseReceiptsPanel', () => {
  it('renders the per-order cost history with ordered and actual unit costs', () => {
    renderPanel(history);

    const costs = screen.getByTestId('order-cost-history');
    expect(within(costs).getByText('PO-0001')).toBeInTheDocument();
    expect(within(costs).getByText('received')).toBeInTheDocument();
    expect(within(costs).getByText('10')).toBeInTheDocument();
    // Cost drifted between the two orders — both numbers are on screen.
    expect(within(costs).getByText('$3.25')).toBeInTheDocument();
    expect(within(costs).getByText('$3.50')).toBeInTheDocument();
    expect(within(costs).getByText('$2.10')).toBeInTheDocument();
    // No actual cost yet on the open order.
    expect(within(costs).getByText('—')).toBeInTheDocument();
  });

  it('links each order line to its purchase order by pk', () => {
    renderPanel(history);

    const costs = screen.getByTestId('order-cost-history');
    expect(within(costs).getByText('PO-0001').closest('a')).toHaveAttribute(
      'href',
      '/purchasing/orders/11',
    );
  });

  it('groups deliveries by order so one order shows all of its tracking numbers', () => {
    renderPanel(history);

    const singleShipment = screen.getByTestId('delivery-group-11');
    expect(singleShipment).toHaveTextContent('1 delivery');
    expect(singleShipment).toHaveTextContent('1Z-AAA');
    expect(singleShipment).toHaveTextContent('UPS');
    expect(singleShipment).toHaveTextContent('All good');
    expect(singleShipment).toHaveTextContent('Complete');

    const splitShipment = screen.getByTestId('delivery-group-12');
    expect(splitShipment).toHaveTextContent('2 deliveries');
    expect(splitShipment).toHaveTextContent('1Z-BBB');
    expect(splitShipment).toHaveTextContent('1Z-CCC');
    expect(splitShipment).toHaveTextContent('Partial');
    // Each package's own receipt is separate — quantities are not merged.
    expect(splitShipment).toHaveTextContent('15');
    expect(splitShipment).toHaveTextContent('10');
  });

  it('labels an order that has no po_number by its pk and still links it', () => {
    renderPanel({
      order_costs: [
        {
          purchase_order: 42,
          po_number: null,
          order_date: '2026-03-01T10:00:00Z',
          status: 'draft',
          quantity_ordered: 4,
          unit_cost_ordered: '1.0000',
          unit_cost_actual: null,
        },
      ],
      deliveries: [],
    });

    const link = screen.getByText('PO #42');
    expect(link.closest('a')).toHaveAttribute('href', '/purchasing/orders/42');
  });

  it('renders empty-state copy for both sections when the item has no history', () => {
    renderPanel({ order_costs: [], deliveries: [] });

    expect(screen.getByText('This item has never been ordered.')).toBeInTheDocument();
    expect(screen.getByText('No deliveries recorded for this item.')).toBeInTheDocument();
  });

  it('degrades to the empty state when the payload never loaded', () => {
    renderPanel(null);

    expect(screen.getByText('This item has never been ordered.')).toBeInTheDocument();
    expect(screen.getByText('No deliveries recorded for this item.')).toBeInTheDocument();
  });
});
