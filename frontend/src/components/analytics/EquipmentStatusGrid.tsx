import { Badge, Card, Table, Text, Title } from '@mantine/core';
import React from 'react';

import type { AnalyticsUtilizationRow } from '../../services/api';

interface EquipmentStatusGridProps {
  rows: AnalyticsUtilizationRow[];
}

const STATUS_COLORS: Record<string, string> = {
  active: 'green',
  maintenance: 'yellow',
  testing: 'blue',
  implementing: 'cyan',
  retired: 'gray',
  lost: 'red',
  donated_out: 'gray',
};

export const EquipmentStatusGrid: React.FC<EquipmentStatusGridProps> = ({ rows }) => (
  <Card withBorder p="md" radius="md" data-testid="equipment-status-grid">
    <Title order={4}>Equipment touched this period</Title>
    {rows.length === 0 ? (
      <Text size="sm" c="dimmed" mt="sm">No assets had completed work orders in this window.</Text>
    ) : (
      <Table mt="sm" striped highlightOnHover>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Asset</Table.Th>
            <Table.Th>Category</Table.Th>
            <Table.Th>Status</Table.Th>
            <Table.Th ta="right">Hours used</Table.Th>
            <Table.Th ta="right">Completed WOs</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rows.map((r) => (
            <Table.Tr key={r.asset_id}>
              <Table.Td>{r.asset_name}</Table.Td>
              <Table.Td>{r.category ?? '—'}</Table.Td>
              <Table.Td>
                <Badge color={STATUS_COLORS[r.status] ?? 'gray'} variant="light">
                  {r.status}
                </Badge>
              </Table.Td>
              <Table.Td ta="right">{r.hours_used}</Table.Td>
              <Table.Td ta="right">{r.completed_wo_count}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    )}
  </Card>
);
