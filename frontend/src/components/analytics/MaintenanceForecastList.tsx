import { Badge, Card, Table, Text, Title } from '@mantine/core';
import React from 'react';

import type { AnalyticsForecastEntry } from '../../services/api';

interface MaintenanceForecastListProps {
  entries: AnalyticsForecastEntry[];
}

const REASON_COLORS: Record<string, string> = {
  both: 'red',
  days: 'orange',
  hours: 'yellow',
};

const REASON_LABELS: Record<string, string> = {
  both: 'Hours + days',
  days: 'Days',
  hours: 'Hours',
};

export const MaintenanceForecastList: React.FC<MaintenanceForecastListProps> = ({ entries }) => (
  <Card withBorder p="md" radius="md" data-testid="maintenance-forecast-list">
    <Title order={4}>Maintenance forecast</Title>
    {entries.length === 0 ? (
      <Text size="sm" c="dimmed" mt="sm">No assets are due for service.</Text>
    ) : (
      <Table mt="sm" striped highlightOnHover>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Asset</Table.Th>
            <Table.Th>Trigger</Table.Th>
            <Table.Th ta="right">Hours used</Table.Th>
            <Table.Th ta="right">Days since last WO</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {entries.map((e) => (
            <Table.Tr key={e.asset_id}>
              <Table.Td>{e.asset_name}</Table.Td>
              <Table.Td>
                <Badge color={REASON_COLORS[e.due_reason] ?? 'gray'} variant="light">
                  {REASON_LABELS[e.due_reason] ?? e.due_reason}
                </Badge>
              </Table.Td>
              <Table.Td ta="right">
                {e.hours_used}
                {e.interval_hours !== null && <Text component="span" size="xs" c="dimmed"> / {e.interval_hours}</Text>}
              </Table.Td>
              <Table.Td ta="right">
                {e.days_since_last_wo ?? '—'}
                {e.interval_days !== null && <Text component="span" size="xs" c="dimmed"> / {e.interval_days}</Text>}
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    )}
  </Card>
);
