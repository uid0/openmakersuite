/**
 * PurchaseReceiptsPanel — the "Purchase / Receipts" tab of the inventory item
 * detail page (op-u9ap), rendering GET
 * /api/inventory/items/<id>/purchase_history/ (op-96uo).
 *
 * Two views of the same provenance:
 *  - **Order cost history** — one row per purchase-order line, oldest first, so
 *    what we paid per order (and how it drifted) is visible on one screen.
 *  - **Deliveries / tracking** — delivery rows grouped by purchase order, so a
 *    partially-shipped order shows all of its tracking numbers under one
 *    heading. Grouping is by the PO **pk**, never `po_number`, which is
 *    nullable.
 *
 * Purely presentational: the page fetches the payload and passes it in.
 */
import { Anchor, Badge, Card, Stack, Table, Text, Title } from '@mantine/core';
import React from 'react';
import { Link } from 'react-router-dom';

import { ItemDelivery, ItemPurchaseHistory } from '../../types';

interface PurchaseReceiptsPanelProps {
  history: ItemPurchaseHistory | null;
}

// Money arrives as a DRF decimal string ("3.2500"); show it as currency.
const formatMoney = (value: string | null): string =>
  value == null || value === '' ? '—' : `$${parseFloat(value).toFixed(2)}`;

const formatDate = (value: string): string => new Date(value).toLocaleDateString();

// A PO without a number is still a real order — label it by pk so the row is
// never anonymous and the link still works.
const poLabel = (poNumber: string | null, purchaseOrder: number): string =>
  poNumber || `PO #${purchaseOrder}`;

const PurchaseOrderLink: React.FC<{ poNumber: string | null; purchaseOrder: number }> = ({
  poNumber,
  purchaseOrder,
}) => (
  <Anchor component={Link} to={`/purchasing/orders/${purchaseOrder}`}>
    {poLabel(poNumber, purchaseOrder)}
  </Anchor>
);

interface DeliveryGroup {
  purchaseOrder: number;
  poNumber: string | null;
  rows: ItemDelivery[];
}

/**
 * Group deliveries by purchase order, preserving the backend's oldest-first
 * ordering both between groups and within one.
 */
const groupDeliveries = (deliveries: ItemDelivery[]): DeliveryGroup[] => {
  const groups = new Map<number, DeliveryGroup>();
  deliveries.forEach((row) => {
    const existing = groups.get(row.purchase_order);
    if (existing) {
      existing.rows.push(row);
    } else {
      groups.set(row.purchase_order, {
        purchaseOrder: row.purchase_order,
        poNumber: row.po_number,
        rows: [row],
      });
    }
  });
  return Array.from(groups.values());
};

const PurchaseReceiptsPanel: React.FC<PurchaseReceiptsPanelProps> = ({ history }) => {
  const orderCosts = history?.order_costs || [];
  const deliveryGroups = groupDeliveries(history?.deliveries || []);

  return (
    <Stack gap="md">
      <Card withBorder p="md" data-testid="order-cost-history">
        <Title order={4} mb="xs">
          Order cost history
        </Title>
        <Text size="sm" c="dimmed" mb="md">
          What this item cost on each purchase order, oldest first.
        </Text>
        {orderCosts.length === 0 ? (
          <Text c="dimmed">This item has never been ordered.</Text>
        ) : (
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>PO</Table.Th>
                <Table.Th>Ordered</Table.Th>
                <Table.Th>Status</Table.Th>
                <Table.Th>Qty</Table.Th>
                <Table.Th>Unit cost (ordered)</Table.Th>
                <Table.Th>Unit cost (actual)</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {orderCosts.map((line, index) => (
                // A PO can carry the same item on more than one line, so the pk
                // alone is not unique — pair it with the row position.
                <Table.Tr key={`${line.purchase_order}-${index}`}>
                  <Table.Td>
                    <PurchaseOrderLink
                      poNumber={line.po_number}
                      purchaseOrder={line.purchase_order}
                    />
                  </Table.Td>
                  <Table.Td>{formatDate(line.order_date)}</Table.Td>
                  <Table.Td>
                    <Badge variant="light">{line.status}</Badge>
                  </Table.Td>
                  <Table.Td>{line.quantity_ordered}</Table.Td>
                  <Table.Td>{formatMoney(line.unit_cost_ordered)}</Table.Td>
                  <Table.Td>{formatMoney(line.unit_cost_actual)}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Card>

      <Card withBorder p="md" data-testid="deliveries-tracking">
        <Title order={4} mb="xs">
          Deliveries / tracking
        </Title>
        <Text size="sm" c="dimmed" mb="md">
          Every receipt of this item, grouped by order. One order can ship in
          several packages, each with its own tracking number.
        </Text>
        {deliveryGroups.length === 0 ? (
          <Text c="dimmed">No deliveries recorded for this item.</Text>
        ) : (
          <Stack gap="lg">
            {deliveryGroups.map((group) => (
              <div key={group.purchaseOrder} data-testid={`delivery-group-${group.purchaseOrder}`}>
                <Text size="sm" fw={600} mb="xs">
                  <PurchaseOrderLink
                    poNumber={group.poNumber}
                    purchaseOrder={group.purchaseOrder}
                  />{' '}
                  — {group.rows.length} deliver{group.rows.length === 1 ? 'y' : 'ies'}
                </Text>
                <Table>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>Received</Table.Th>
                      <Table.Th>Tracking #</Table.Th>
                      <Table.Th>Carrier</Table.Th>
                      <Table.Th>Qty received</Table.Th>
                      <Table.Th>Complete</Table.Th>
                      <Table.Th>Receipt notes</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {group.rows.map((row, index) => (
                      <Table.Tr key={`${group.purchaseOrder}-${row.delivery_date}-${index}`}>
                        <Table.Td>{formatDate(row.delivery_date)}</Table.Td>
                        <Table.Td>{row.tracking_number || '-'}</Table.Td>
                        <Table.Td>{row.carrier || '-'}</Table.Td>
                        <Table.Td>{row.quantity_received}</Table.Td>
                        <Table.Td>
                          <Badge color={row.is_complete ? 'green' : 'yellow'} variant="light">
                            {row.is_complete ? 'Complete' : 'Partial'}
                          </Badge>
                        </Table.Td>
                        <Table.Td>{row.receipt_notes || '-'}</Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </div>
            ))}
          </Stack>
        )}
      </Card>
    </Stack>
  );
};

export default PurchaseReceiptsPanel;
