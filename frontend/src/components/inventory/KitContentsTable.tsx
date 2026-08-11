/**
 * KitContentsTable — read-only "what's in this kit" table (op-8n0).
 *
 * One component shared by all three places a kit's contents are shown: the kit
 * detail page, the expandable kit row on the purchase-order form, and the
 * purchase-order detail line. Sharing it is what keeps "you get 2 of each"
 * phrased identically everywhere the operator might check it.
 *
 * Deliberately a real `<Table>` rather than a tooltip: a 5x5 grid is
 * unreachable on touch and, in a tooltip, invisible to screen readers as
 * tabular data.
 *
 * When `kitQuantity` is supplied the table gains a "You get" column showing
 * `quantity_per_kit x kitQuantity` per row — the same arithmetic the backend's
 * `kit_component_credits` applies on receipt.
 */
import { Badge, Group, Table, Text } from '@mantine/core';
import React from 'react';

/**
 * The shape both sources of kit contents share.
 *
 * The kit API returns `KitComponent` rows and a purchase-order line returns
 * `KitLineComponent` rows; this is the subset the table renders, so a caller
 * can pass either without mapping.
 */
export interface KitContentsRow {
  component: string;
  component_name: string;
  component_sku?: string;
  quantity_per_kit?: number;
  /** Present on kit-API rows, where the per-kit count is called `quantity`. */
  quantity?: number;
  component_current_stock?: number;
  component_needs_reorder?: boolean;
}

export interface KitContentsTableProps {
  rows: KitContentsRow[];
  /**
   * How many kits are being bought/received. Omit on a plain definition view;
   * supply it to show the "You get" column and totals.
   */
  kitQuantity?: number;
  /** Show each component's current stock and low-stock flag. */
  showStock?: boolean;
  testId?: string;
}

/** Per-kit count, normalising the two payload shapes onto one field. */
export const perKitQuantity = (row: KitContentsRow): number =>
  row.quantity_per_kit ?? row.quantity ?? 0;

/** Total base units delivered by `kitQuantity` kits — the live summary number. */
export const totalUnits = (rows: KitContentsRow[], kitQuantity: number): number =>
  rows.reduce((sum, row) => sum + perKitQuantity(row) * kitQuantity, 0);

const KitContentsTable: React.FC<KitContentsTableProps> = ({
  rows,
  kitQuantity,
  showStock = false,
  testId = 'kit-contents-table',
}) => {
  if (rows.length === 0) {
    return (
      <Text size="sm" c="dimmed" data-testid={`${testId}-empty`}>
        This kit has no components yet.
      </Text>
    );
  }

  const showYouGet = typeof kitQuantity === 'number' && kitQuantity > 0;

  return (
    <Table
      striped
      highlightOnHover
      withTableBorder
      data-testid={testId}
      aria-label="Kit contents"
    >
      <Table.Thead>
        <Table.Tr>
          <Table.Th scope="col">Component</Table.Th>
          <Table.Th scope="col">Per kit</Table.Th>
          {showStock && <Table.Th scope="col">In stock</Table.Th>}
          {showYouGet && <Table.Th scope="col">You get</Table.Th>}
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {rows.map((row) => {
          const perKit = perKitQuantity(row);
          return (
            <Table.Tr key={row.component} data-testid={`kit-component-row-${row.component}`}>
              <Table.Td>
                <Group gap="xs" wrap="nowrap">
                  <Text size="sm">{row.component_name}</Text>
                  {row.component_sku && (
                    <Text size="xs" c="dimmed">
                      {row.component_sku}
                    </Text>
                  )}
                </Group>
              </Table.Td>
              <Table.Td>{perKit}</Table.Td>
              {showStock && (
                <Table.Td>
                  <Group gap="xs" wrap="nowrap">
                    <Text size="sm">{row.component_current_stock ?? '—'}</Text>
                    {row.component_needs_reorder && (
                      <Badge color="orange" size="sm" variant="light">
                        Low
                      </Badge>
                    )}
                  </Group>
                </Table.Td>
              )}
              {showYouGet && (
                <Table.Td data-testid={`kit-you-get-${row.component}`}>
                  <Text size="sm" fw={600}>
                    +{perKit * (kitQuantity as number)}
                  </Text>
                </Table.Td>
              )}
            </Table.Tr>
          );
        })}
      </Table.Tbody>
    </Table>
  );
};

export default KitContentsTable;
